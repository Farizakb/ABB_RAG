# backend/shared/contracts/models.py
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

SourceClass = Literal["product", "campaign", "corporate", "stub", "volatile", "index"]
RefusalClass = Literal["out_of_scope", "advisory", "unsafe"]
Stage = Literal["validating", "embedding", "indexing", "ready", "failed"]


def _default_source() -> dict[str, object]:
    return {"host": "abb-bank.az", "locales": ["az"]}


class Fact(BaseModel):
    attribute: str
    value_num: float | None = None
    value_text: str | None = None
    unit: str | None = None
    currency: str | None = None
    raw_fragment: str
    source_url: str | None = None

    @model_validator(mode="after")
    def _one_value(self) -> Fact:
        if self.value_num is None and self.value_text is None:
            raise ValueError("fact needs value_num or value_text")
        return self


class Document(BaseModel):
    url: str
    canonical_url: str | None = None
    title: str
    section_path: list[str]
    source_class: SourceClass
    lang: str = "az"
    status: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    text: str
    facts: list[Fact] = Field(default_factory=list)
    content_hash: str
    fetched_at: datetime | None = None


class Corpus(BaseModel):
    corpus_version: str = "1"
    source: dict[str, object] = Field(default_factory=_default_source)
    scraped_at: datetime | None = None
    stats: dict[str, int] = Field(default_factory=dict)
    documents: list[Document]

    @property
    def corpus_id(self) -> str:
        canon = sorted(d.content_hash for d in self.documents)
        return hashlib.sha256(json.dumps(canon).encode()).hexdigest()


class CorpusStatus(BaseModel):
    corpus_id: str
    status: str
    stage: Stage
    doc_count: int = 0
    chunk_count: int = 0
    fact_count: int = 0
    dropped: list[dict[str, object]] = Field(default_factory=list)
    token_stats: dict[str, float] = Field(default_factory=dict)
    embedding_model: str | None = None
    error: str | None = None


class Source(BaseModel):
    n: int
    title: str
    section_path: list[str]
    url: str
    # The ledger-footer target. Derived from `url`, never model output, and
    # never a trim that is absent from the corpus. Optional in the
    # type so a mock can omit it; `rag` always populates it, falling back to
    # `url` itself, so the UI never branches on null.
    listing_url: str | None = None
    score: float
    source_class: SourceClass
    facts: list[Fact] = Field(default_factory=list)


class AnswerResponse(BaseModel):
    """rag -> chat, internal."""

    answer: str
    citations: list[int]
    sources: list[Source]
    facts_used: list[Fact] = Field(default_factory=list)
    grounded: bool
    refused: bool
    refusal_class: RefusalClass | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    retrieval: list[dict[str, object]] = Field(default_factory=list)
    timings_ms: dict[str, int] = Field(default_factory=dict)
    prompt_version: str


class QuestionRequest(BaseModel):
    corpus_id: str
    question: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None


class QuestionResponse(BaseModel):
    interaction_id: str
    answer: str
    sources: list[Source]
    grounded: bool
    refused: bool
    refusal_class: RefusalClass | None = None
    timestamp: datetime
    timings_ms: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _grounded_implies_cited(self) -> QuestionResponse:
        if self.grounded and not self.sources:
            raise ValueError("invariant 1: grounded answer must carry at least one source")
        return self
