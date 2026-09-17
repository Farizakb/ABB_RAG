# backend/chat/chat/analytics.py
from __future__ import annotations

from typing import Any

from chat.db import get_conn

# VOLUME also splits out `refused_unsafe` so the day-by-day chart and the
# refusal-rate tile both account for all three refusal_class values, not
# just out_of_scope/advisory.
VOLUME = """
SELECT date_trunc('day', created_at)::date AS day,
       count(*) FILTER (WHERE NOT refused)                                AS answered,
       count(*) FILTER (WHERE refused AND refusal_class = 'out_of_scope') AS refused_out_of_scope,
       count(*) FILTER (WHERE refused AND refusal_class = 'advisory')     AS refused_advisory,
       count(*) FILTER (WHERE refused AND refusal_class = 'unsafe')       AS refused_unsafe
FROM app.interactions
WHERE created_at > now() - %s::interval
GROUP BY 1 ORDER BY 1
"""

# Filtered by the same window as VOLUME/TOTALS rather than all-time.
TOP_SOURCES = """
SELECT s->>'url' AS url, count(*) AS count
FROM app.interactions i, jsonb_array_elements(i.citations) AS c,
     jsonb_array_elements(i.retrieval) AS s
WHERE NOT i.refused AND (s->>'n')::int = c::int
  AND i.created_at > now() - %s::interval
GROUP BY 1 ORDER BY 2 DESC LIMIT 10
"""

# A small-talk row (grounded=false AND refused=false AND error IS NULL --
# a friendly, intentionally-ungrounded, non-refused reply) is excluded from
# the grounded-rate average's denominator via FILTER, not
# just from its numerator -- an answered-but-never-meant-to-be-grounded row
# must not dilute the metric at all, and is reported separately as its own
# count instead.
TOTALS = """
SELECT count(*),
       coalesce(percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms), 0)::int,
       coalesce(avg(CASE WHEN grounded THEN 1.0 ELSE 0.0 END)
                 FILTER (WHERE NOT (NOT refused AND NOT grounded AND error IS NULL)), 0)
                 ::numeric(4,3),
       coalesce(sum(cost_usd), 0),
       count(*) FILTER (WHERE NOT refused AND NOT grounded AND error IS NULL)
FROM app.interactions WHERE created_at > now() - %s::interval
"""


def summary(window: str = "7d") -> dict[str, Any]:
    interval = f"{int(window.rstrip('d') or 7)} days"
    with get_conn() as conn:
        volume = [
            {
                "day": str(d),
                "answered": a,
                "refused_out_of_scope": o,
                "refused_advisory": adv,
                "refused_unsafe": u,
            }
            for d, a, o, adv, u in conn.execute(VOLUME, (interval,)).fetchall()
        ]
        top = [{"url": u, "count": c} for u, c in conn.execute(TOP_SOURCES, (interval,)).fetchall()]
        totals_row = conn.execute(TOTALS, (interval,)).fetchone()
        assert totals_row is not None  # count(*) always returns exactly one row
        n, median, grounded, cost, small_talk = totals_row

    # refusal_rate counts all three refusal classes, not just two.
    refused = sum(
        d["refused_out_of_scope"] + d["refused_advisory"] + d["refused_unsafe"] for d in volume
    )
    answered = sum(d["answered"] for d in volume)
    total = answered + refused
    return {
        "volume_by_day": volume,
        "grounded_rate": float(grounded),
        "refusal_rate": round(refused / total, 3) if total else 0.0,
        # Small talk's own count, kept out of grounded_rate and refusal_rate
        # alike (it is neither grounded nor refused).
        "small_talk_count": small_talk,
        "top_sources": top,
        "totals": {
            "questions": n,
            "median_latency_ms": median,
            "grounded_rate": float(grounded),
            "cost_usd": float(cost),
        },
    }


def interactions(limit: int = 50, offset: int = 0, q: str = "") -> dict[str, Any]:
    where = ""
    params: list[str] = []
    if q:
        where = "WHERE question ILIKE %s OR answer ILIKE %s"
        params = [f"%{q}%", f"%{q}%"]
    with get_conn() as conn:
        count_row = conn.execute(
            f"SELECT count(*) FROM app.interactions {where}", params
        ).fetchone()
        assert count_row is not None  # count(*) always returns exactly one row
        total = count_row[0]
        rows = conn.execute(
            "SELECT id, created_at, question, answer, grounded, refused, refusal_class, "
            f"latency_ms FROM app.interactions {where} ORDER BY created_at DESC "
            "LIMIT %s OFFSET %s",
            [*params, limit, offset],
        ).fetchall()
    keys = (
        "id",
        "created_at",
        "question",
        "answer",
        "grounded",
        "refused",
        "refusal_class",
        "latency_ms",
    )
    return {"items": [dict(zip(keys, r, strict=True)) for r in rows], "total": total}
