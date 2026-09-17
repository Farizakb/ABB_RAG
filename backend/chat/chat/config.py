# backend/chat/chat/config.py
from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://abb:abb@db:5432/abb"
    rag_url: str = "http://rag:8000"
    llm_model: str = "gpt-5.6-luna"
    # Placeholders until real per-token pricing for this model is published.
    # Env-overridable rather than hardcoded either way.
    price_in: float = 0.15
    price_out: float = 0.60


settings = Settings()
