# services/rag/tests/test_ingest_api.py
from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from app.embedder import FakeEmbedder
from app.ingest import STALE_PROCESSING_AFTER, ingest_corpus
from app.main import app
from contracts.models import Corpus, Document
from fastapi.testclient import TestClient

client = TestClient(app)


@pytest.fixture(autouse=True)
def _fake_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    """app.routes.get_embedder is the only seam -- patch it so this
    module never calls OpenAI and never trips ingest_corpus's dimension check
    against the vector(8) test schema (the production embedder is 1536-dim)."""
    monkeypatch.setattr("app.routes.get_embedder", lambda: FakeEmbedder(dim=8))


def _corpus(content_hash: str = "sha256:a") -> Corpus:
    return Corpus(
        documents=[
            Document(
                url="https://abb-bank.az/a",
                title="A",
                section_path=[],
                source_class="product",
                text="mətn " * 200,
                content_hash=content_hash,
            )
        ]
    )


def payload() -> dict[str, Any]:
    # explicit annotation: contracts ships no py.typed marker, so mypy sees
    # model_dump() as returning Any -- same reason app/ingest.py annotates
    # corpus_id explicitly (see its comment).
    body: dict[str, Any] = _corpus().model_dump(mode="json")
    return body


def test_upload_returns_202_immediately(db: Any) -> None:
    r = client.post("/api/v1/corpora", json={"corpus": payload()})
    assert r.status_code == 202 and r.json()["corpus_id"]


def test_status_reaches_ready_and_reports_counts(db: Any) -> None:
    cid = client.post("/api/v1/corpora", json={"corpus": payload()}).json()["corpus_id"]
    body = client.get(f"/api/v1/corpora/{cid}").json()
    assert body["stage"] == "ready"
    assert body["chunk_count"] >= 1 and body["embedding_model"]


def test_unknown_corpus_is_404_with_no_stack_trace(db: Any) -> None:
    r = client.get("/api/v1/corpora/deadbeef")
    assert r.status_code == 404 and "Traceback" not in r.text


def test_malformed_corpus_is_422_not_500(db: Any) -> None:
    r = client.post("/api/v1/corpora", json={"corpus": {"documents": [{"url": "x"}]}})
    assert r.status_code == 422


def test_stalled_status_is_persisted_as_failed_and_enables_retry(db: Any) -> None:
    """The GET stall detector must PERSIST status='failed', not only
    report it in the HTTP response. Verified two ways: (1) read the row back
    directly after the GET, independent of the response body; (2) call
    ingest_corpus on the same corpus afterwards and confirm it reaches
    'ready' -- which only happens if it found a `failed` row and took the
    retry branch (a fresh `processing` row would instead be left alone, per
    test_a_processing_corpus_is_not_re_ingested in test_ingest.py)."""
    embedder = FakeEmbedder(dim=8)
    c = _corpus("sha256:stall")
    db.execute(
        "INSERT INTO rag.corpora (id, content_hash, manifest, status, stage, "
        "embedding_model, embedding_version, updated_at) "
        "VALUES (gen_random_uuid(), %s, '{}', 'processing', 'embedding', %s, 1, "
        "now() - %s)",
        (c.corpus_id, embedder.model, STALE_PROCESSING_AFTER + timedelta(seconds=1)),
    )

    body = client.get(f"/api/v1/corpora/{c.corpus_id}").json()
    assert body["stage"] == "failed" and body["status"] == "failed"

    # (1) the row itself, not just the response, says failed.
    row = db.execute(
        "SELECT status, stage, error FROM rag.corpora WHERE content_hash=%s AND embedding_model=%s",
        (c.corpus_id, embedder.model),
    ).fetchone()
    assert row == ("failed", "failed", "no heartbeat for five minutes")

    # (2) a subsequent ingest takes the retry branch and reaches ready.
    ingest_corpus(c, embedder)
    status = db.execute(
        "SELECT status FROM rag.corpora WHERE content_hash=%s AND embedding_model=%s",
        (c.corpus_id, embedder.model),
    ).fetchone()[0]
    assert status == "ready"


def test_ingest_failure_is_recorded_only_on_the_matching_model_row(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_run's failure UPDATE must filter on embedding_model as well as
    content_hash -- otherwise it would also clobber a different, healthy row
    for the same corpus ingested under a second model."""

    class BrokenEmbedder:
        model = "broken-model"
        dim = 8

        def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("boom")

    other = FakeEmbedder(dim=8, model="other-model")
    c = _corpus("sha256:two-models")
    db.execute(
        "INSERT INTO rag.corpora (id, content_hash, manifest, status, stage, "
        "embedding_model, embedding_version) "
        "VALUES (gen_random_uuid(), %s, '{}', 'ready', 'ready', %s, 1)",
        (c.corpus_id, other.model),
    )

    monkeypatch.setattr("app.routes.get_embedder", lambda: BrokenEmbedder())
    r = client.post("/api/v1/corpora", json={"corpus": c.model_dump(mode="json")})
    assert r.status_code == 202

    rows = dict(
        db.execute(
            "SELECT embedding_model, status FROM rag.corpora WHERE content_hash=%s",
            (c.corpus_id,),
        ).fetchall()
    )
    assert rows == {"other-model": "ready", "broken-model": "failed"}


def test_get_status_reads_only_the_matching_model_row(db: Any) -> None:
    """The GET SELECT must filter on embedding_model as well as
    content_hash -- otherwise, given two rows for the same corpus under
    different models, it could return the wrong one. Plant two
    rows with the SAME content_hash and DIFFERENT embedding_model, in
    distinguishable states, and assert the response matches the row for the
    model app.routes.get_embedder() returns (FakeEmbedder(dim=8), model
    'fake-embedder' per the autouse fixture above)."""
    matching_model = "fake-embedder"
    other_model = "other-model"
    c = _corpus("sha256:two-models-get")

    # Plant the NON-matching row first, on purpose: with no ORDER BY, a plain
    # sequential scan over a freshly truncated table returns rows in
    # insertion order, so a SELECT missing the `embedding_model` filter would
    # return THIS row -- making the assertions below fail loudly rather than
    # passing by insertion-order coincidence.
    db.execute(
        "INSERT INTO rag.corpora (id, content_hash, manifest, doc_count, chunk_count, "
        "status, stage, embedding_model, embedding_version) "
        "VALUES (gen_random_uuid(), %s, '{}', 99, 99, 'ready', 'ready', %s, 1)",
        (c.corpus_id, other_model),
    )
    db.execute(
        "INSERT INTO rag.corpora (id, content_hash, manifest, doc_count, chunk_count, "
        "status, stage, embedding_model, embedding_version) "
        "VALUES (gen_random_uuid(), %s, '{}', 1, 1, 'ready', 'ready', %s, 1)",
        (c.corpus_id, matching_model),
    )

    body = client.get(f"/api/v1/corpora/{c.corpus_id}").json()
    assert body["embedding_model"] == matching_model
    assert body["doc_count"] == 1 and body["chunk_count"] == 1
