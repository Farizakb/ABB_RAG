# services/rag/app/main.py
from __future__ import annotations

import logging

from fastapi import FastAPI

from app.config import settings
from app.db import get_conn
from app.routes import router

log = logging.getLogger("rag")
app = FastAPI(title="ABB Assistant — rag")
app.include_router(router)


@app.get("/healthz")
def healthz() -> dict[str, object]:
    db_ok = False
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1")
        db_ok = True
    except Exception:  # health must never raise
        log.warning("healthz: db unreachable")
    return {
        "status": "ok" if db_ok else "degraded",
        "db": db_ok,
        "openai": bool(settings.openai_api_key),  # presence only, never the value
        "model": settings.llm_model,
        "embedding_model": settings.embedding_model,
    }
