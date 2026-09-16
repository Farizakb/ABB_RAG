# services/chat/app/main.py
from __future__ import annotations

import logging

from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.db import get_conn
from app.routes import limiter, router

log = logging.getLogger("chat")
app = FastAPI(title="ABB Assistant — chat")
app.state.limiter = limiter
# slowapi's handler is typed for RateLimitExceeded specifically, narrower than
# Starlette's ExceptionHandler signature (Exception) -- a known slowapi/mypy
# --strict mismatch, not a real type error.
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
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
    return {"status": "ok" if db_ok else "degraded", "db": db_ok, "rag_url": settings.rag_url}
