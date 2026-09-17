# services/rag/app/config.py
from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    openai_api_key: str = ""
    llm_model: str = "gpt-5.6-luna"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    database_url: str = "postgresql://abb:abb@db:5432/abb"
    top_k_candidates: int = 20
    top_k_prompt: int = 5
    retrieval_floor: float = 0.0  # set from the measured score distribution
    max_output_tokens: int = 700


settings = Settings()
