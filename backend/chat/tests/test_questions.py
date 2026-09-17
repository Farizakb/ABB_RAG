# ruff: noqa: RUF001 -- genuine Azerbaijani query text, same convention as
# backend/rag/conftest.py and backend/rag/tests/test_chunking.py.
from __future__ import annotations

from typing import Any

from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_exactly_one_row_per_call(db: Any, rag_ok: None) -> None:
    client.post("/api/v1/questions", json={"corpus_id": "c1", "question": "nağd kredit?"})
    assert db.execute("SELECT count(*) FROM app.interactions").fetchone()[0] == 1


def test_a_refusal_still_writes_exactly_one_row(db: Any, rag_refuses: None) -> None:
    client.post("/api/v1/questions", json={"corpus_id": "c1", "question": "hava?"})
    row = db.execute("SELECT refused, refusal_class FROM app.interactions").fetchone()
    assert row == (True, "out_of_scope")


def test_a_rag_error_still_writes_exactly_one_row(db: Any, rag_500: None) -> None:
    r = client.post("/api/v1/questions", json={"corpus_id": "c1", "question": "x"})
    assert r.status_code == 502
    row = db.execute("SELECT refused, error FROM app.interactions").fetchone()
    assert row[0] is True and row[1]


def test_a_malformed_200_still_writes_exactly_one_row(db: Any, rag_malformed: None) -> None:
    """A proxy error page, a truncated body, or a wrong content-type on an
    HTTP 200 must not skip the insert -- json.JSONDecodeError is not an
    httpx.HTTPError subclass, so this is a distinct failure mode from
    test_a_rag_error_still_writes_exactly_one_row's clean 500."""
    r = client.post("/api/v1/questions", json={"corpus_id": "c1", "question": "x"})
    assert r.status_code == 502
    assert db.execute("SELECT count(*) FROM app.interactions").fetchone()[0] == 1
    row = db.execute("SELECT refused, error FROM app.interactions").fetchone()
    assert row[0] is True and row[1]


def test_a_200_missing_required_keys_still_writes_exactly_one_row(
    db: Any, rag_missing_key: None
) -> None:
    """A syntactically valid JSON 200 body missing answer/grounded/refused/
    sources must not skip the insert either -- distinct from the non-JSON
    case above: json.loads succeeds here, so this proves the explicit shape
    check (not just the JSONDecodeError catch) routes into the same
    error-persist branch. Covers the missing-`sources` gap from code review
    too, since `sources` is validated by the same key set as the other
    three."""
    r = client.post("/api/v1/questions", json={"corpus_id": "c1", "question": "x"})
    assert r.status_code == 502
    assert db.execute("SELECT count(*) FROM app.interactions").fetchone()[0] == 1
    row = db.execute("SELECT refused, error FROM app.interactions").fetchone()
    assert row[0] is True and row[1]


def test_the_persisted_question_is_redacted(db: Any, rag_ok: None) -> None:
    client.post(
        "/api/v1/questions",
        json={"corpus_id": "c1", "question": "kartım 4169738812345678 balans?"},
    )
    stored = db.execute("SELECT question FROM app.interactions").fetchone()[0]
    assert "4169" not in stored


def test_prompt_version_and_timings_are_recorded(db: Any, rag_ok: None) -> None:
    client.post("/api/v1/questions", json={"corpus_id": "c1", "question": "kredit?"})
    row = db.execute(
        "SELECT prompt_version, latency_ms, retrieval_ms FROM app.interactions"
    ).fetchone()
    assert row[0] == "answer_v1" and row[1] is not None and row[2] is not None


def test_chat_never_touches_the_rag_schema(db: Any, rag_ok: None) -> None:
    """Invariant 6, asserted rather than trusted."""
    import pathlib

    src = " ".join(p.read_text("utf-8") for p in pathlib.Path("backend/chat/app").rglob("*.py"))
    assert "rag." not in src.replace("rag.py", "")


def test_rate_limit_returns_429_rather_than_burning_the_key(db: Any, rag_ok: None) -> None:
    for _ in range(40):
        r = client.post("/api/v1/questions", json={"corpus_id": "c1", "question": "x"})
    assert r.status_code == 429
