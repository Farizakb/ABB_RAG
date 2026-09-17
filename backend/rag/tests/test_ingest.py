# backend/rag/tests/test_ingest.py
from datetime import timedelta
from typing import Any

import psycopg
import pytest
import rag.db as db_module
from rag.embedder import FakeEmbedder
from rag.ingest import STALE_PROCESSING_AFTER, ingest_corpus
from shared.contracts import Corpus, Document, Fact


def corpus(n: int = 2) -> Corpus:
    return Corpus(
        documents=[
            Document(
                url=f"https://abb-bank.az/p{i}",
                title=f"P{i}",
                section_path=["Fərdi"],
                source_class="product",
                text="mətn " * 200,
                content_hash=f"sha256:{i}",
                facts=[
                    Fact(
                        attribute="max_amount",
                        value_num=50000,
                        unit="AZN",
                        raw_fragment="50 000 AZN-dək",
                    )
                ],
            )
            for i in range(n)
        ]
    )


def _seed_corpus_row(
    db: psycopg.Connection,
    content_hash: str,
    model: str,
    status: str,
    age: timedelta = timedelta(0),
) -> None:
    """Plants a corpora row as if a previous ingest attempt had reached
    `status` and then stalled, without going through ingest_corpus. `age`
    backdates updated_at so the staleness check can be exercised (default:
    just now, i.e. fresh)."""
    db.execute(
        "INSERT INTO rag.corpora (id, content_hash, manifest, status, stage, "
        "embedding_model, embedding_version, updated_at) "
        "VALUES (gen_random_uuid(), %s, '{}', %s, 'embedding', %s, 1, now() - %s)",
        (content_hash, status, model, age),
    )


def test_ingest_writes_documents_chunks_and_facts(db: Any) -> None:
    ingest_corpus(corpus(), FakeEmbedder(dim=8))
    assert db.execute("SELECT count(*) FROM rag.documents").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM rag.chunks").fetchone()[0] >= 2
    assert db.execute("SELECT count(*) FROM rag.product_facts").fetchone()[0] == 2


def test_reingest_of_the_same_corpus_is_a_no_op(db: Any) -> None:
    c = corpus()
    ingest_corpus(c, FakeEmbedder(dim=8))
    before = db.execute("SELECT count(*) FROM rag.chunks").fetchone()[0]
    ingest_corpus(c, FakeEmbedder(dim=8))
    assert db.execute("SELECT count(*) FROM rag.chunks").fetchone()[0] == before


def test_reingest_under_a_different_embedding_model_is_not_a_no_op(db: Any) -> None:
    """Invariant 8. Idempotency keys on (corpus_id, embedding_model), never the
    hash alone — otherwise a model swap silently leaves a mixed index."""
    c = corpus()
    ingest_corpus(c, FakeEmbedder(dim=8, model="model-a"))
    before = db.execute("SELECT count(*) FROM rag.chunks").fetchone()[0]
    ingest_corpus(c, FakeEmbedder(dim=8, model="model-b"))
    assert db.execute("SELECT count(*) FROM rag.chunks").fetchone()[0] > before


def test_every_chunk_stores_the_model_that_produced_it(db: Any) -> None:
    ingest_corpus(corpus(), FakeEmbedder(dim=8, model="model-a"))
    models = {r[0] for r in db.execute("SELECT DISTINCT embedding_model FROM rag.chunks")}
    assert models == {"model-a"}


def test_embed_input_is_stored_separately_from_text(db: Any) -> None:
    ingest_corpus(corpus(1), FakeEmbedder(dim=8))
    text, embed_input = db.execute("SELECT text, embed_input FROM rag.chunks LIMIT 1").fetchone()
    assert embed_input.startswith("P0 |") and not text.startswith("P0 |")


def test_dimension_mismatch_fails_loudly_rather_than_writing_garbage(db: Any) -> None:
    with pytest.raises(ValueError, match="dimension"):
        ingest_corpus(corpus(1), FakeEmbedder(dim=4))  # schema is 8 in the test fixture


def test_a_failed_corpus_is_retried_and_ends_ready(db: Any) -> None:
    """A prior attempt that stalled and was marked failed must
    be retryable, not permanently stuck — the UI is promised a retry."""
    c = corpus(1)
    embedder = FakeEmbedder(dim=8)
    _seed_corpus_row(db, c.corpus_id, embedder.model, status="failed")

    ingest_corpus(c, embedder)

    status = db.execute(
        "SELECT status FROM rag.corpora WHERE content_hash = %s AND embedding_model = %s",
        (c.corpus_id, embedder.model),
    ).fetchone()[0]
    assert status == "ready"
    assert db.execute("SELECT count(*) FROM rag.chunks").fetchone()[0] >= 1


def test_a_processing_corpus_is_not_re_ingested(db: Any) -> None:
    """A FRESH `processing` row means another ingest is already in
    flight — re-ingesting concurrently would race it. Pins the opposite
    direction from test_a_stale_processing_corpus_is_retried_and_ends_ready so
    neither test is vacuous."""
    c = corpus(1)
    embedder = FakeEmbedder(dim=8)
    _seed_corpus_row(db, c.corpus_id, embedder.model, status="processing")

    ingest_corpus(c, embedder)

    assert db.execute("SELECT count(*) FROM rag.documents").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM rag.chunks").fetchone()[0] == 0


def test_a_stale_processing_corpus_is_retried_and_ends_ready(db: Any) -> None:
    """A `processing` row whose updated_at heartbeat has gone silent for
    longer than STALE_PROCESSING_AFTER means the ingest that owned it crashed.
    The retry promise must reach these rows too, not just ones already
    marked `failed` -- otherwise a crash mid-ingest wedges the corpus forever."""
    c = corpus(1)
    embedder = FakeEmbedder(dim=8)
    _seed_corpus_row(
        db,
        c.corpus_id,
        embedder.model,
        status="processing",
        age=STALE_PROCESSING_AFTER + timedelta(seconds=1),
    )
    old_id = db.execute(
        "SELECT id FROM rag.corpora WHERE content_hash = %s AND embedding_model = %s",
        (c.corpus_id, embedder.model),
    ).fetchone()[0]

    ingest_corpus(c, embedder)

    rows = db.execute(
        "SELECT id, status FROM rag.corpora WHERE content_hash = %s AND embedding_model = %s",
        (c.corpus_id, embedder.model),
    ).fetchall()
    assert len(rows) == 1
    new_id, status = rows[0]
    assert status == "ready"
    assert new_id != old_id


def test_product_slug_is_derived_from_the_url_path_not_the_title(db: Any) -> None:
    """product_facts.product_slug must be a slug -- the golden set
    and the fact-governance path both read it as one. Derived from
    the final non-empty URL path segment (trailing slash/query/fragment
    ignored), falling back to the title when the URL has no usable segment."""
    c = Corpus(
        documents=[
            Document(
                url="https://abb-bank.az/kredit/avtokredit/?utm=x#top",
                title="Avtokredit",
                section_path=["Fərdi"],
                source_class="product",
                text="mətn " * 200,
                content_hash="sha256:slug-a",
                facts=[Fact(attribute="max_amount", value_num=1, raw_fragment="1")],
            ),
            Document(
                url="https://abb-bank.az",
                title="Bare Origin Title",
                section_path=["Fərdi"],
                source_class="product",
                text="mətn " * 200,
                content_hash="sha256:slug-b",
                facts=[Fact(attribute="max_amount", value_num=1, raw_fragment="1")],
            ),
        ]
    )

    ingest_corpus(c, FakeEmbedder(dim=8))

    slugs = {
        r[0] for r in db.execute("SELECT product_slug FROM rag.product_facts ORDER BY product_slug")
    }
    assert slugs == {"avtokredit", "Bare Origin Title"}


class _StageObserver:
    """A fake embedder whose embed() opens its OWN pooled
    connection (a second, independent connection to the same test database)
    and reads the corpus row's committed `stage` -- proving the 'embedding'
    transition was actually committed and visible from outside ingest_corpus's
    own transaction before the (potentially slow) embed call runs, not merely
    written and left pending until the ingest finishes."""

    def __init__(self, content_hash: str, dim: int = 8, model: str = "stage-observer") -> None:
        self.dim = dim
        self.model = model
        self._content_hash = content_hash
        self.observed_stage: str | None = None

    def embed(self, texts: list[str]) -> Any:
        with db_module.pool.connection() as conn:
            conn.autocommit = True
            row = conn.execute(
                "SELECT stage FROM rag.corpora WHERE content_hash = %s AND embedding_model = %s",
                (self._content_hash, self.model),
            ).fetchone()
            self.observed_stage = row[0] if row else None
        return FakeEmbedder(dim=self.dim, model=self.model).embed(texts)


def test_stage_transition_to_embedding_is_committed_before_embed_runs(db: Any) -> None:
    """Stage UPDATEs must commit immediately so a concurrent
    observer -- the stall detector -- can see 'embedding' while embed()
    is still running, not only once the whole ingest finishes."""
    c = corpus(1)
    observer = _StageObserver(c.corpus_id)

    ingest_corpus(c, observer)

    assert observer.observed_stage == "embedding"


def test_concurrent_duplicate_ingest_does_not_clobber_the_winner(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """final-code-review finding 5: two concurrent uploads for the same NEW
    corpus can both pass ingest_corpus's "does a row already exist" check
    before either commits its own INSERT. Only one INSERT can win the
    (content_hash, embedding_model) unique constraint (001_schema.sql); the
    loser must back off, not raise -- otherwise the exception reaches _run's
    except handler, whose failure UPDATE is keyed on the same
    (content_hash, embedding_model) pair and would mark the WINNER's row
    'failed' even though it's ready.

    Reproduces the interleaving deterministically (no thread timing): the
    moment ingest_corpus's own existing-row SELECT returns empty, a second
    connection commits a competing 'ready' row -- exactly what ingest_corpus
    would see if a concurrent request's INSERT had won a moment earlier.
    """
    c = corpus(1)
    embedder = FakeEmbedder(dim=8)
    real_execute = psycopg.Connection.execute
    planted = False

    def racy_execute(self: psycopg.Connection, query: Any, params: Any = None, **kw: Any) -> Any:
        nonlocal planted
        cursor = real_execute(self, query, params, **kw)
        if (
            not planted
            and isinstance(query, str)
            and "SELECT id, status, now() - updated_at" in query
        ):
            planted = True
            with db_module.pool.connection() as winner_conn:
                winner_conn.execute(
                    "INSERT INTO rag.corpora (id, content_hash, manifest, status, stage, "
                    "embedding_model, embedding_version) VALUES "
                    "(gen_random_uuid(), %s, '{}', 'ready', 'ready', %s, 1)",
                    (c.corpus_id, embedder.model),
                )
                winner_conn.commit()
        return cursor

    monkeypatch.setattr(psycopg.Connection, "execute", racy_execute)

    result = ingest_corpus(c, embedder)

    assert result == c.corpus_id
    rows = db.execute(
        "SELECT status FROM rag.corpora WHERE content_hash = %s AND embedding_model = %s",
        (c.corpus_id, embedder.model),
    ).fetchall()
    assert rows == [("ready",)]  # the winner's row survives, untouched
    assert db.execute("SELECT count(*) FROM rag.documents").fetchone()[0] == 0
