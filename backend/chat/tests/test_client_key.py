# backend/chat/tests/test_client_key.py
"""The rate limiter must key on the client's real
address as forwarded by nginx (X-Forwarded-For), not nginx's own address --
otherwise every request sharing nginx's IP drains one shared bucket. nginx
must overwrite that header (`$remote_addr`), never append to a
client-supplied value (`$proxy_add_x_forwarded_for`), or a client can forge
its own rate-limit key. See the `_client_key` docstring in app/routes.py for
the full topology/trust argument.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from app.main import app
from app.routes import limiter
from fastapi.testclient import TestClient

client = TestClient(app)

NGINX_CONF = Path(__file__).resolve().parents[3] / "frontend" / "nginx.conf"


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


def test_nginx_overwrites_x_forwarded_for_instead_of_appending() -> None:
    """The forgery this control depends on defeating happens at the proxy, not
    inside `chat` -- a `TestClient` call bypasses nginx entirely, so no
    in-process request can prove a client-supplied X-Forwarded-For is
    discarded. The honest assertion is against the nginx config itself: every
    `proxy_set_header X-Forwarded-For` must overwrite with `$remote_addr`,
    never append via `$proxy_add_x_forwarded_for` (which preserves whatever a
    client sent). This is the assertion that would have caught the original
    defect.
    """
    conf = NGINX_CONF.read_text()
    forwarded_for_lines = [
        line
        for line in conf.splitlines()
        if re.search(r"proxy_set_header\s+X-Forwarded-For\b", line)
    ]
    assert forwarded_for_lines, "expected at least one X-Forwarded-For proxy_set_header"
    for line in forwarded_for_lines:
        assert "$proxy_add_x_forwarded_for" not in line, (
            f"appends client-supplied value, forgeable: {line!r}"
        )
        assert "$remote_addr" in line, f"must overwrite with $remote_addr: {line!r}"
