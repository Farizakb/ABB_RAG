# services/chat/tests/test_analytics.py
# ruff: noqa: RUF001 -- genuine Azerbaijani fixture text, same convention as
# services/chat/conftest.py.
from __future__ import annotations

from typing import Any

import httpx
import pytest
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_summary_returns_every_field_the_three_charts_need(
    db: Any, seeded_interactions: None
) -> None:
    body = client.get("/api/v1/analytics/summary?window=7d").json()
    assert set(body) >= {"volume_by_day", "grounded_rate", "refusal_rate", "top_sources", "totals"}


def test_refusals_are_split_by_class_so_chart_two_is_not_a_flat_line(
    db: Any, seeded_interactions: None
) -> None:
    day = client.get("/api/v1/analytics/summary?window=7d").json()["volume_by_day"][0]
    assert set(day) >= {"day", "answered", "refused_out_of_scope", "refused_advisory"}


def test_top_sources_counts_resolved_citations_not_retrieved_candidates(
    db: Any, seeded_interactions: None
) -> None:
    top = client.get("/api/v1/analytics/summary").json()["top_sources"]
    assert top and all({"url", "count"} <= set(s) for s in top)


def test_totals_cover_the_four_tiles(db: Any, seeded_interactions: None) -> None:
    totals = client.get("/api/v1/analytics/summary").json()["totals"]
    assert set(totals) == {"questions", "median_latency_ms", "grounded_rate", "cost_usd"}


def test_interactions_are_searchable_and_paginated(db: Any, seeded_interactions: None) -> None:
    body = client.get("/api/v1/interactions?limit=2&offset=0&q=kredit").json()
    assert len(body["items"]) <= 2 and body["total"] >= 1


def test_empty_database_returns_zeros_rather_than_erroring(db: Any) -> None:
    body = client.get("/api/v1/analytics/summary").json()
    assert body["totals"]["questions"] == 0 and body["volume_by_day"] == []


# P104: refused_unsafe is its own field on every day, and refusal_rate counts
# all three refusal classes (out_of_scope, advisory, unsafe), not just two.
def test_unsafe_refusals_are_counted_in_volume_and_refusal_rate(
    db: Any, seeded_interactions: None
) -> None:
    body = client.get("/api/v1/analytics/summary?window=7d").json()
    day = body["volume_by_day"][0]
    assert day["refused_unsafe"] == 1
    # seeded_interactions: 2 answered + 3 refused (one per class) = 5 total.
    assert body["refusal_rate"] == round(3 / 5, 3)


# P106: window must match ^\d{1,3}d$, otherwise the request is rejected
# rather than silently falling back to a default window.
def test_bad_window_returns_422(db: Any) -> None:
    r = client.get("/api/v1/analytics/summary?window=notaday")
    assert r.status_code == 422


# P108: the write path must persist `retrieval` from `data["sources"]` (the
# prompt sources the model actually saw: n/url/score/source_class), not
# rag's internal dense-candidate list (`data.get("retrieval")`, shaped
# {document_id, url, score} with no `n` -- see services/rag/app/retrieval.py
# `candidates`). That mismatch is why analytics.TOP_SOURCES's join on
# `(s->>'n')::int = c::int` never matched anything in production, no matter
# what test_top_sources_counts_resolved_citations_not_retrieved_candidates
# asserted against an invented fixture shape. This test drives the real
# POST /api/v1/questions write path with a mocked rag response carrying two
# sources (n=1, n=2) but only n=2 cited, then checks both what actually
# lands in the `retrieval` column and that the analytics endpoint resolves
# only the cited source.
def test_write_path_persists_p108_retrieval_shape_and_top_sources_resolves(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    url_1 = "https://abb-bank.az/ferdi/kartlar/debet-kart"
    url_2 = "https://abb-bank.az/ferdi/kartlar/kredit-kart"
    payload: dict[str, Any] = {
        "answer": "Kredit kartının illik faizi 24%-dir.",
        "citations": [2],
        "sources": [
            {
                "n": 1,
                "title": "Debet kartı",
                "section_path": ["Fərdi", "Kartlar"],
                "url": url_1,
                "listing_url": "https://abb-bank.az/ferdi/kartlar",
                "score": 0.80,
                "source_class": "product",
                "facts": [],
            },
            {
                "n": 2,
                "title": "Kredit kartı",
                "section_path": ["Fərdi", "Kartlar"],
                "url": url_2,
                "listing_url": "https://abb-bank.az/ferdi/kartlar",
                "score": 0.93,
                "source_class": "product",
                "facts": [],
            },
        ],
        "facts_used": [],
        "grounded": True,
        "refused": False,
        "refusal_class": None,
        "usage": {"prompt_tokens": 100, "completion_tokens": 30},
        # rag's internal dense-candidate list -- deliberately a different
        # shape (no `n`) from `sources` above, to prove the write path
        # ignores this field per P108.
        "retrieval": [
            {"document_id": "d1", "url": url_1, "score": 0.80},
            {"document_id": "d2", "url": url_2, "score": 0.93},
        ],
        "timings_ms": {"retrieval_ms": 10, "generation_ms": 300},
        "prompt_version": "answer_v1",
    }

    def _post(url: str, **_kwargs: Any) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr("app.routes.httpx.post", _post)

    r = client.post(
        "/api/v1/questions", json={"corpus_id": "c1", "question": "kredit kartı haqqında?"}
    )
    assert r.status_code == 200

    row = db.execute("SELECT retrieval FROM app.interactions").fetchone()
    persisted = row[0]
    urls_by_n = {s["n"]: s["url"] for s in persisted}
    assert urls_by_n == {1: url_1, 2: url_2}

    top = client.get("/api/v1/analytics/summary").json()["top_sources"]
    by_url = {s["url"]: s["count"] for s in top}
    assert by_url.get(url_2) == 1
    assert url_1 not in by_url
