# services/chat/app/routes.py
from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from contracts.models import QuestionRequest, QuestionResponse, RefusalClass, Source
from fastapi import APIRouter, HTTPException, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app import analytics
from app.config import settings
from app.db import get_conn
from app.redaction import redact

router = APIRouter(prefix="/api/v1")


def _client_key(request: Request) -> str:
    """Rate-limit key: the *original* client, not nginx's own address.

    Every request reaches `chat` through our own nginx (apps/web/nginx.conf),
    which sets `proxy_set_header X-Forwarded-For $remote_addr;` on both
    proxied locations -- an unconditional overwrite, not an append. That
    means the header's value is always nginx's own view of the peer, never
    whatever a client sent; a client-supplied X-Forwarded-For is discarded
    before it reaches `chat`. Reading (and stripping) that single value is
    safe -- not merely convenient -- only because of the topology: `chat`
    publishes no port (see docker-compose.yml), so no client can reach it
    directly and set this header on a second path that bypasses nginx.

    Falls back to `get_remote_address` when the header is absent, so local
    direct calls (health checks, the test suite) still work.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(key_func=_client_key)

INSERT = """
INSERT INTO app.interactions
 (id, session_id, corpus_id, question, answer, grounded, refused, refusal_class, error,
  citations, retrieval, facts_used, model, prompt_version,
  prompt_tokens, completion_tokens, cost_usd, retrieval_ms, generation_ms, latency_ms)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
"""


def _corpus_uuid(corpus_id: str) -> uuid.UUID:
    """`app.interactions.corpus_id` is typed uuid in db/migrations/001_schema.sql
    (no foreign key), but the corpus id everywhere else in the system --
    Corpus.corpus_id, the sibling service's /corpora/{corpus_id} route, its
    retrieval query -- is a sha256 hex digest, never a UUID. Deriving a
    stable, deterministic UUID from that string keeps every interaction row
    insertable under the existing column type, with no migration and no
    lossy fallback to NULL, while still letting rows be grouped by corpus.
    """
    return uuid.uuid5(uuid.NAMESPACE_URL, corpus_id)


@router.post("/questions", response_model=QuestionResponse)
@limiter.limit("30/minute")
def ask(request: Request, req: QuestionRequest) -> QuestionResponse:
    started = time.perf_counter()
    interaction_id = uuid.uuid4()
    session_id = req.session_id or str(uuid.uuid4())
    question = redact(req.question)  # redact before anything is persisted

    data: dict[str, Any] | None
    err: str | None
    try:
        resp = httpx.post(
            f"{settings.rag_url}/answer",
            json={"corpus_id": req.corpus_id, "question": req.question},
            timeout=60.0,
        )
        resp.raise_for_status()
        data = resp.json()
        # A 200 with a malformed body (proxy error page, truncated response,
        # wrong content-type) must land in the `data is None` error-persist
        # branch below, not escape as an uncaught exception that skips
        # _persist entirely (Invariant 4: exactly one row per call, including
        # errors). Validated here, inside the guarded region, rather than at
        # the bare data[...] access sites below -- every key read via a bare
        # subscript further down (never a data.get(...), those already
        # tolerate absence) must appear in this set.
        _required_keys = {"answer", "grounded", "refused", "sources"}
        if not isinstance(data, dict) or not _required_keys <= data.keys():
            raise ValueError("malformed answer payload")
        err = None
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        # json.JSONDecodeError subclasses ValueError (a non-JSON 200 body);
        # KeyError is defensive in case resp.json() itself raises one.
        data, err = None, str(exc)[:500]

    latency = int((time.perf_counter() - started) * 1000)

    if data is None:
        # Invariant 4: an error is still exactly one row.
        _persist(
            interaction_id,
            session_id,
            req.corpus_id,
            question,
            "",
            False,
            True,
            None,
            err,
            None,
            latency,
        )
        raise HTTPException(status_code=502, detail="answer service unavailable")

    _persist(
        interaction_id,
        session_id,
        req.corpus_id,
        question,
        data["answer"],
        data["grounded"],
        data["refused"],
        data.get("refusal_class"),
        None,
        data,
        latency,
    )

    return QuestionResponse(
        interaction_id=str(interaction_id),
        answer=data["answer"],
        sources=[Source(**s) for s in data["sources"]],
        grounded=data["grounded"],
        refused=data["refused"],
        refusal_class=data.get("refusal_class"),
        timestamp=datetime.now(UTC),
        timings_ms={**data.get("timings_ms", {}), "total_ms": latency},
    )


def _persist(
    iid: uuid.UUID,
    session_id: str,
    corpus_id: str,
    question: str,
    answer: str,
    grounded: bool,
    refused: bool,
    refusal_class: RefusalClass | None,
    error: str | None,
    data: dict[str, Any] | None,
    latency: int,
) -> None:
    usage = data.get("usage", {}) if data else {}
    timings = data.get("timings_ms", {}) if data else {}
    with get_conn() as conn:
        conn.execute(
            INSERT,
            (
                iid,
                session_id,
                _corpus_uuid(corpus_id),
                question,
                answer,
                grounded,
                refused,
                refusal_class,
                error,
                json.dumps(data.get("citations", []) if data else []),
                json.dumps(_retrieval_from_sources(data) if data else []),
                json.dumps(data.get("facts_used", []) if data else []),
                settings.llm_model,
                data.get("prompt_version") if data else None,
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
                _cost(usage),
                timings.get("retrieval_ms"),
                timings.get("generation_ms"),
                latency,
            ),
        )
        conn.commit()


def _retrieval_from_sources(data: dict[str, Any]) -> list[dict[str, Any]]:
    """P108: persist `retrieval` as the prompt sources the model actually
    saw (`data["sources"]`), not rag's internal dense-candidate list
    (`data.get("retrieval")`, see services/rag/app/retrieval.py's
    `candidates`). The latter is `{document_id, url, score}` with no `n`, so
    analytics.TOP_SOURCES's join on `(s->>'n')::int = c::int` never matched
    anything in production -- `top_sources` was always empty. `sources` is
    `[]` on the error/refusal paths, so this still persists `[]` there,
    same as before.
    """
    return [
        {"n": s["n"], "url": s["url"], "score": s["score"], "source_class": s["source_class"]}
        for s in data.get("sources", [])
    ]


def _cost(usage: dict[str, int]) -> float:
    return round(
        usage.get("prompt_tokens", 0) / 1e6 * settings.price_in
        + usage.get("completion_tokens", 0) / 1e6 * settings.price_out,
        6,
    )


# P106: window must be `<1-3 digits>d` -- anything else is a 422, not a
# silent fallback, since a typo'd window would otherwise quietly report on
# the default 7 days instead.
@router.get("/analytics/summary")
def get_summary(window: str = Query("7d", pattern=r"^\d{1,3}d$")) -> dict[str, Any]:
    return analytics.summary(window)


@router.get("/interactions")
def get_interactions(
    limit: int = Query(50),
    offset: int = Query(0, ge=0),
    q: str = Query(""),
) -> dict[str, Any]:
    # P106: limit is clamped into [1, 200] rather than rejected -- pagination
    # controls are routinely driven by UI state that can overshoot, and that
    # should degrade gracefully rather than 422 the whole screen.
    limit = max(1, min(200, limit))
    return analytics.interactions(limit=limit, offset=offset, q=q)
