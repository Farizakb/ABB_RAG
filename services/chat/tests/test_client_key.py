# services/chat/tests/test_client_key.py
"""Finding 1 (Task 37): the rate limiter must key on the client's real
address as forwarded by nginx (X-Forwarded-For), not nginx's own address --
otherwise every request sharing nginx's IP drains one shared bucket. See the
`_client_key` docstring in app/routes.py for the topology/trust argument.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.main import app
from app.routes import limiter
from fastapi.testclient import TestClient

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_limiter() -> None:
    """Isolate each test's bucket from whatever other test modules already
    did to the shared in-memory limiter storage (module-level `limiter` is
    the same object across every test file in this process)."""
    limiter.reset()


def _ask(headers: dict[str, str] | None = None) -> Any:
    return client.post(
        "/api/v1/questions",
        json={"corpus_id": "c1", "question": "x"},
        headers=headers,
    )


def test_different_x_forwarded_for_values_get_independent_buckets(db: Any, rag_ok: None) -> None:
    for _ in range(31):
        r1 = _ask({"X-Forwarded-For": "10.0.0.1"})
    assert r1.status_code == 429  # this client's bucket is exhausted

    r2 = _ask({"X-Forwarded-For": "10.0.0.2"})
    assert r2.status_code == 200  # a different client is unaffected


def test_missing_x_forwarded_for_falls_back_to_remote_address(db: Any, rag_ok: None) -> None:
    r = _ask()
    assert r.status_code == 200
