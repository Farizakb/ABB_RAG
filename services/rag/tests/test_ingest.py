# services/rag/tests/test_ingest.py
from typing import Any

import psycopg
import pytest
from app.embedder import FakeEmbedder
from app.ingest import ingest_corpus
from contracts.models import Corpus, Document, Fact


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


def _seed_corpus_row(db: psycopg.Connection, content_hash: str, model: str, status: str) -> None:
    """Plants a corpora row as if a previous ingest attempt had reached
    `status` and then stalled, without going through ingest_corpus."""
    db.execute(
        "INSERT INTO rag.corpora (id, content_hash, manifest, status, stage, "
        "embedding_model, embedding_version) "
        "VALUES (gen_random_uuid(), %s, '{}', %s, 'embedding', %s, 1)",
        (content_hash, status, model),
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
    """P66: a prior attempt that stalled and was marked failed (Task 17) must
    be retryable, not permanently stuck — SPEC §6.3 promises the UI a retry."""
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
    """P66: only `failed` rows are retried. A `processing` row means another
    ingest is already in flight — re-ingesting concurrently would race it."""
    c = corpus(1)
    embedder = FakeEmbedder(dim=8)
    _seed_corpus_row(db, c.corpus_id, embedder.model, status="processing")

    ingest_corpus(c, embedder)

    assert db.execute("SELECT count(*) FROM rag.documents").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM rag.chunks").fetchone()[0] == 0
