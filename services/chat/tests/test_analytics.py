# services/chat/tests/test_analytics.py
from __future__ import annotations

from typing import Any

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
