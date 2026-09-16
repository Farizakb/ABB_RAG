# services/rag/app/routes.py
from __future__ import annotations

import logging

from contracts.models import AnswerResponse, Corpus, CorpusStatus
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.db import get_conn
from app.embedder import Embedder, OpenAIEmbedder
from app.generate import OpenAIClient
from app.generate import answer as generate_answer
from app.ingest import STALE_PROCESSING_AFTER, ingest_corpus  # P74: one timeout constant

log = logging.getLogger("rag")
router = APIRouter(prefix="/api/v1")
# P90: the brief wrote `@router.post("/../answer", ...)` on the `/api/v1`-prefixed
# router above, which resolves to the nonsense path `/api/v1/../answer` -- while
# its own prose said to register `POST /answer` on the bare app. The prose is
# right: a second, un-prefixed router, included directly in main.py, internal
# only, never routed through nginx.
internal_router = APIRouter()


class UploadRequest(BaseModel):
    corpus: Corpus


def get_embedder() -> Embedder:
    """P78: the sole seam between this module and OpenAI. Tests monkeypatch
    this factory to return FakeEmbedder(dim=8) so pytest never calls OpenAI
    and never trips ingest_corpus's dimension check against the vector(8)
    test schema. Production keeps the OpenAIEmbedder() default."""
    return OpenAIEmbedder()


def _run(corpus: Corpus, embedder: Embedder) -> None:
    try:
        ingest_corpus(corpus, embedder)
    except Exception as exc:  # the stage column is the error channel, so catch broadly
        log.exception("ingest failed")
        with get_conn() as conn:
            # P76: content_hash alone is not unique -- rag.corpora's key is
            # (content_hash, embedding_model), so an UPDATE keyed on
            # content_hash alone would also hit an unrelated row ingested
            # under a second model for the same corpus (Invariant 8).
            conn.execute(
                "UPDATE rag.corpora SET status='failed', stage='failed', error=%s "
                "WHERE content_hash=%s AND embedding_model=%s",
                (str(exc)[:500], corpus.corpus_id, embedder.model),
            )
            conn.commit()


@router.post("/corpora", status_code=202)
def upload(req: UploadRequest, tasks: BackgroundTasks) -> dict[str, str]:
    embedder = get_embedder()
    tasks.add_task(_run, req.corpus, embedder)
    return {"corpus_id": req.corpus.corpus_id, "status": "accepted"}


@router.get("/corpora/{corpus_id}", response_model=CorpusStatus)
def status(corpus_id: str) -> CorpusStatus:
    embedder = get_embedder()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status, stage, doc_count, chunk_count, manifest, embedding_model, error, "
            # P75: `%s` inside a quoted interval literal (e.g. `interval
            # '%s minutes'`) is not a real psycopg bind -- it never
            # substitutes. Bind the timedelta directly against updated_at.
            "  (updated_at < now() - %s) AS stalled "
            # P76: content_hash alone can name two legitimate rows (one per
            # embedding model) -- disambiguate with the model this deployment
            # is currently configured for, same as the ingest that wrote it.
            "FROM rag.corpora WHERE content_hash = %s AND embedding_model = %s",
            (STALE_PROCESSING_AFTER, corpus_id, embedder.model),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="unknown corpus")
        status_, stage, docs, chunks, manifest, model, error, stalled = row
        if stalled and stage not in {"ready", "failed"}:
            # P77: PERSIST the stall as failed, not just report it in this
            # response. ingest_corpus's retry branch (P72) keys off the row's
            # own `status` column, so a caller that never sees this exact
            # response -- or a retry driven by ingest_corpus directly -- must
            # still find the row already marked failed.
            error = error or "no heartbeat for five minutes"
            conn.execute(
                "UPDATE rag.corpora SET status='failed', stage='failed', error=%s, "
                "updated_at=now() WHERE content_hash=%s AND embedding_model=%s",
                (error, corpus_id, embedder.model),
            )
            conn.commit()
            status_, stage = "failed", "failed"
    return CorpusStatus(
        corpus_id=corpus_id,
        status=status_,
        stage=stage,
        doc_count=docs or 0,
        chunk_count=chunks or 0,
        fact_count=int((manifest or {}).get("fact_count", 0)),
        token_stats={"tokens_per_char": float((manifest or {}).get("tokens_per_char", 0.0))},
        embedding_model=model,
        error=error,
    )


class AnswerRequest(BaseModel):
    corpus_id: str
    question: str


@internal_router.post("/answer", response_model=AnswerResponse, include_in_schema=False)
def answer_route(req: AnswerRequest) -> AnswerResponse:
    return generate_answer(req.corpus_id, req.question, OpenAIEmbedder(), OpenAIClient())
