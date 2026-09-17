# scripts/seed_demo.py
# ruff: noqa: RUF001 -- genuine Azerbaijani query text, same convention as
# services/rag/tests/test_retrieval.py.
"""Replay ~85 questions through the real pipeline so the charts have shape.

Costs about four cents (measured: $0.0417 for 85 calls against the live
model) and doubles as an end-to-end smoke test. Deliberately
includes both refusal classes so chart two is not a flat line.

`/api/v1/questions` is rate-limited to 30/minute per remote address
(services/chat/app/routes.py), and every request here arrives from the same
address via nginx, so a 429 is expected mid-run rather than exceptional --
retried after the server's Retry-After rather than counted as a failure.
"""

from __future__ import annotations

import os
import random
import sys
import time
import uuid
from typing import Any

import httpx

CHAT = os.environ.get("CHAT_URL", "http://localhost:8080")

ANSWERABLE = [
    "Nağd kredit üzrə maksimum məbləğ nə qədərdir?",
    "Nağd kreditin illik faiz dərəcəsi neçədir?",
    "Mənə son kampaniyalar haqda məlumat ver",
    "Fərdi müştərilər üçün hansı kredit növləri var?",
    "İpoteka krediti üçün hansı sənədlər tələb olunur?",
    "What is the maximum term for a cash loan?",
    "Biznes üçün hansı kredit məhsulları var?",
    "Kart sifarişini necə edə bilərəm?",
]
OUT_OF_SCOPE = [
    "Kapital Bankın faizi neçədir?",
    "Hava sabah necə olacaq?",
    "Bitcoin qiyməti nədir?",
]
ADVISORY = [
    "Mənim maaşım 1200 manatdır, nə qədər kredit götürə bilərəm?",
    "Mənə hansı kredit daha uyğundur?",
    "Will I be approved for a mortgage?",
]


def _post(client: httpx.Client, payload: dict[str, Any]) -> httpx.Response:
    """POST /questions, retrying on 429 (see module docstring)."""
    for _ in range(10):
        r = client.post(f"{CHAT}/api/v1/questions", json=payload)
        if r.status_code != 429:
            return r
        time.sleep(float(r.headers.get("retry-after", 2)))
    return r


def main() -> int:
    corpus_id = sys.argv[1] if len(sys.argv) > 1 else os.environ["CORPUS_ID"]
    random.seed(11)  # reproducible: the same demo every rehearsal
    plan = ANSWERABLE * 8 + OUT_OF_SCOPE * 4 + ADVISORY * 3  # 85 total
    random.shuffle(plan)

    sessions = [str(uuid.uuid4()) for _ in range(12)]
    answered = refused = 0
    with httpx.Client(timeout=90.0) as client:
        for i, question in enumerate(plan):
            r = _post(
                client,
                {
                    "corpus_id": corpus_id,
                    "question": question,
                    "session_id": random.choice(sessions),
                },
            )
            if r.status_code != 200:
                print(f"  [{i}] HTTP {r.status_code} — {question}")
                continue
            body = r.json()
            refused += body["refused"]
            answered += not body["refused"]
    print(f"seeded: {answered} answered, {refused} refused")
    return 0 if answered and refused else 1


if __name__ == "__main__":
    raise SystemExit(main())
