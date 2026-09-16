# services/chat/app/config.py
from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://abb:abb@db:5432/abb"
    rag_url: str = "http://rag:8000"
    llm_model: str = "gpt-5.6-luna"
    # Not one of the fixed values in plan-global-constraints.md -- SPEC §8.5
    # says cost figures are "restated from published pricing once V-2
    # confirms the model ids", so these are placeholders until that pricing
    # lands. Env-overridable rather than hardcoded either way.
    price_in: float = 0.15
    price_out: float = 0.60


settings = Settings()
