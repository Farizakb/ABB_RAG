# backend/rag/tests/test_health.py
import pytest
from fastapi.testclient import TestClient
from rag.config import settings
from rag.main import app


def test_healthz_reports_db_and_openai_reachability() -> None:
    body = TestClient(app).get("/healthz").json()
    assert set(body) >= {"status", "db", "openai"}


def test_healthz_never_leaks_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # Load-bearing: put a real-shaped key in play so the assertion below can
    # actually fail. With no key configured, settings.openai_api_key == "" and
    # "sk-" not in text is trivially true regardless of what /healthz returns.
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-not-a-real-key-0123456789")

    response = TestClient(app).get("/healthz")

    assert response.json()["openai"] is True  # presence reported...
    assert "sk-" not in response.text  # ...but never the value
