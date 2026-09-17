# backend/rag/app/embedder.py
from __future__ import annotations

import hashlib
from typing import Protocol

from openai import OpenAI

from app.config import settings

BATCH = 100
# Embedding calls serve both a single short query string (on the chat->rag
# `/answer` path, budgeted against routes.py's 60s call timeout) and ingest's
# batches of up to BATCH chunk texts (a background task, not on that budget).
# A longer timeout than generation's needs headroom for the larger batches;
# max_retries keeps a single slow attempt from running under the SDK's
# 10-minute default before this is treated as a failure.
EMBED_TIMEOUT_S = 30.0
EMBED_MAX_RETRIES = 2


class Embedder(Protocol):
    model: str
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    """Behind an interface solely so the §8.3 item 5 bake-off is a config change."""

    def __init__(self, model: str | None = None, dim: int | None = None) -> None:
        self.model = model or settings.embedding_model
        self.dim = dim or settings.embedding_dim
        self._client = OpenAI(
            api_key=settings.openai_api_key,
            timeout=EMBED_TIMEOUT_S,
            max_retries=EMBED_MAX_RETRIES,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            resp = self._client.embeddings.create(model=self.model, input=texts[i : i + BATCH])
            out.extend(d.embedding for d in resp.data)
        return out


class FakeEmbedder:
    """Deterministic, offline, free. CI never calls OpenAI."""

    def __init__(self, dim: int = 1536, model: str = "fake-embedder") -> None:
        self.dim, self.model = dim, model

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for t in texts:
            h = hashlib.sha256(t.encode()).digest()
            vectors.append([(h[i % len(h)] - 128) / 128 for i in range(self.dim)])
        return vectors
