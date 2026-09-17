# services/chat/conftest.py
# ruff: noqa: RUF001 -- genuine Azerbaijani fixture text (dotless-i and
# friends) in RAG_OK_PAYLOAD/RAG_REFUSAL_PAYLOAD/SEEDED_INTERACTIONS, same
# convention as services/rag/conftest.py.
"""Fixtures shared by every services/chat test.

`db` builds and migrates a dedicated `*_chat_test` database from the real
migration file, the same approach services/rag/conftest.py uses for its own
`*_test` database -- a different suffix so a concurrent `pytest
services/rag` run never truncates tables this suite depends on, and vice
versa.

`rag_ok` / `rag_refuses` / `rag_500` monkeypatch `httpx.post` as seen by
app.routes, so these tests never make a real network call to the sibling
answer service.

`seeded_interactions` inserts a fixed set of `app.interactions` rows for the
analytics tests: answered/grounded rows whose citations resolve
against a retrieval entry, one refusal of each `refusal_class`, and a
question containing "kredit" for the search test. Every value here is
fabricated -- none of it is real PII, and card-shaped digits are avoided
entirely.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import app.db as db_module
import httpx
import psycopg
import pytest
from app.config import settings
from psycopg_pool import ConnectionPool

MIGRATION_PATH = Path(__file__).resolve().parents[2] / "db" / "migrations" / "001_schema.sql"
TEST_DIM = 8


def _reachable(url: str) -> str:
    """Rewrite an unreachable host to `127.0.0.1` -- see the long version of
    this rationale in services/rag/conftest.py: `settings.database_url`
    defaults to the compose service name `db`, which only resolves inside the
    compose network, and `localhost` pays a slow IPv6 fallback on Windows that
    `ConnectionPool`'s background workers don't survive.
    """
    parts = urlsplit(url)
    host = parts.hostname
    if not host:
        return url
    port = parts.port or 5432
    try:
        with socket.create_connection((host, port), timeout=1):
            pass
    except OSError:
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc.replace(host, "127.0.0.1", 1),
                parts.path,
                parts.query,
                parts.fragment,
            )
        )
    return url


def _test_database_url() -> str:
    env_url = os.environ.get("TEST_DATABASE_URL")
    if env_url:
        return env_url
    parts = urlsplit(settings.database_url)
    dbname = parts.path.lstrip("/")
    return _reachable(
        urlunsplit(
            (parts.scheme, parts.netloc, f"/{dbname}_chat_test", parts.query, parts.fragment)
        )
    )


def _maintenance_url(test_url: str) -> str:
    parts = urlsplit(test_url)
    return urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, parts.fragment))


def _database_name(test_url: str) -> str:
    return urlsplit(test_url).path.lstrip("/")


@pytest.fixture(scope="session")
def _test_pool() -> Iterator[ConnectionPool]:
    test_url = _test_database_url()

    try:
        with psycopg.connect(
            _maintenance_url(test_url), autocommit=True, connect_timeout=3
        ) as conn:
            dbname = _database_name(test_url)
            exists = conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)
            ).fetchone()
            if not exists:
                conn.execute(f'CREATE DATABASE "{dbname}"')
    except psycopg.OperationalError as exc:
        # Unreachable SKIPS locally (no Postgres on the dev machine is a
        # normal state) but FAILS when $CI is set (Postgres is guaranteed
        # there -- a silent skip in CI is exactly how vacuous coverage hides).
        reason = f"cannot reach test database at {test_url}: {exc}"
        if os.environ.get("CI"):
            pytest.fail(reason)
        pytest.skip(reason)

    with psycopg.connect(test_url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS rag CASCADE")
        conn.execute("DROP SCHEMA IF EXISTS app CASCADE")
        sql = MIGRATION_PATH.read_text(encoding="utf-8").replace(":dim", str(TEST_DIM))
        conn.execute(sql)

    pool = ConnectionPool(test_url, min_size=1, max_size=4, open=True)
    # app.db.get_conn() reads the module global `pool` at call time, so
    # rebinding it here redirects every `get_conn()` call at the test
    # database for the rest of the session.
    db_module.pool = pool
    yield pool
    pool.close()


@pytest.fixture
def db(_test_pool: ConnectionPool) -> Iterator[psycopg.Connection]:
    with db_module.get_conn() as conn:
        conn.execute("TRUNCATE app.interactions")
        conn.commit()

    with db_module.pool.connection() as conn:
        conn.autocommit = True  # sees rows committed by the route on another pooled conn
        yield conn


_INSERT_INTERACTION = """
INSERT INTO app.interactions
 (id, session_id, corpus_id, question, answer, grounded, refused, refusal_class, error,
  citations, retrieval, facts_used, model, prompt_version,
  prompt_tokens, completion_tokens, cost_usd, retrieval_ms, generation_ms, latency_ms)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
"""


@pytest.fixture
def seeded_interactions(db: psycopg.Connection) -> None:
    """Rows the analytics tests need, all within the default 7-day window
    (created_at defaults to now()): two answered/grounded rows whose
    `citations` resolve against a `retrieval` entry sharing the same `n`, one
    refusal per `refusal_class`, and a question containing "kredit" for the
    search test.

    `retrieval` entries use the shape the write path actually persists
    (`n`, `url`, `score`, `source_class`, taken from `data["sources"]`), not
    rag's internal dense-candidate list -- see the `_retrieval_from_sources`
    docstring in app/routes.py.
    """
    corpus = uuid.uuid4()
    common = ("gpt-5.6-luna", "answer_v1")

    def _row(
        session: str,
        question: str,
        answer: str,
        grounded: bool,
        refused: bool,
        refusal_class: str | None,
        citations: list[int],
        retrieval: list[dict[str, Any]],
        latency_ms: int,
    ) -> tuple[Any, ...]:
        return (
            uuid.uuid4(),
            session,
            corpus,
            question,
            answer,
            grounded,
            refused,
            refusal_class,
            None,
            json.dumps(citations),
            json.dumps(retrieval),
            json.dumps([]),
            *common,
            120,
            40,
            0.01,
            10,
            max(latency_ms - 10, 0),
            latency_ms,
        )

    rows = [
        _row(
            "s1",
            "Nağd kredit məbləği nə qədərdir?",
            "Maksimum 20 000 AZN-dək.",
            True,
            False,
            None,
            [1],
            [
                {
                    "n": 1,
                    "url": "https://abb-bank.az/ferdi/kreditler/nagd-kredit",
                    "score": 0.91,
                    "source_class": "product",
                }
            ],
            400,
        ),
        _row(
            "s2",
            "Kredit kartının illik faizi neçədir?",
            "İllik faiz dərəcəsi 24%-dir.",
            True,
            False,
            None,
            [1],
            [
                {
                    "n": 1,
                    "url": "https://abb-bank.az/ferdi/kartlar/kredit-kart",
                    "score": 0.87,
                    "source_class": "product",
                }
            ],
            350,
        ),
        _row(
            "s3",
            "Sabah hava necə olacaq?",
            "Bu suala ABB-nin dərc olunmuş məlumatları əsasında cavab verə bilmirəm.",
            False,
            True,
            "out_of_scope",
            [],
            [],
            120,
        ),
        _row(
            "s4",
            "Mənə hansı krediti götürməyi tövsiyə edərsiniz?",
            "Bu fərdi maliyyə məsləhətidir, verə bilmərəm.",
            False,
            True,
            "advisory",
            [],
            [],
            130,
        ),
        _row(
            "s5",
            "Başqasının kartının PIN kodunu necə tapım?",
            "Bu sorğuya cavab verə bilmərəm.",
            False,
            True,
            "unsafe",
            [],
            [],
            100,
        ),
    ]
    for row in rows:
        db.execute(_INSERT_INTERACTION, row)


RAG_OK_PAYLOAD: dict[str, Any] = {
    "answer": "Nağd kredit məbləği maksimum 20 000 AZN-dək təşkil edir.",
    "citations": [1],
    "sources": [
        {
            "n": 1,
            "title": "Nağd kredit",
            "section_path": ["Fərdi", "Kreditlər"],
            "url": "https://abb-bank.az/ferdi/kreditler/nagd-kredit",
            "listing_url": "https://abb-bank.az/ferdi/kreditler",
            "score": 0.91,
            "source_class": "product",
            "facts": [],
        }
    ],
    "facts_used": [],
    "grounded": True,
    "refused": False,
    "refusal_class": None,
    "usage": {"prompt_tokens": 120, "completion_tokens": 40},
    "retrieval": [],
    "timings_ms": {"retrieval_ms": 12, "generation_ms": 340},
    "prompt_version": "answer_v1",
}

RAG_REFUSAL_PAYLOAD: dict[str, Any] = {
    "answer": "Bu suala ABB-nin dərc olunmuş məlumatları əsasında cavab verə bilmirəm.",
    "citations": [],
    "sources": [],
    "facts_used": [],
    "grounded": False,
    "refused": True,
    "refusal_class": "out_of_scope",
    "usage": {"prompt_tokens": 80, "completion_tokens": 20},
    "retrieval": [],
    "timings_ms": {"retrieval_ms": 8, "generation_ms": 0},
    "prompt_version": "answer_v1",
}


def _fake_post(payload: dict[str, Any] | None, status: int) -> Any:
    def _post(url: str, **_kwargs: Any) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(status, json=payload, request=request)

    return _post


def _fake_post_raw(status: int, content: bytes) -> Any:
    def _post(url: str, **_kwargs: Any) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(status, content=content, request=request)

    return _post


@pytest.fixture
def rag_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routes.httpx.post", _fake_post(RAG_OK_PAYLOAD, 200))


@pytest.fixture
def rag_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routes.httpx.post", _fake_post(RAG_REFUSAL_PAYLOAD, 200))


@pytest.fixture
def rag_500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.routes.httpx.post", _fake_post(None, 500))


@pytest.fixture
def rag_malformed(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 200 with a body that is not valid JSON -- a proxy error page, a
    truncated response, a wrong content-type. Distinct from `rag_500`, which
    covers a clean error status the code already handled; this covers the
    json.JSONDecodeError hole flagged in code review."""
    monkeypatch.setattr(
        "app.routes.httpx.post", _fake_post_raw(200, b"<html>502 Bad Gateway</html>")
    )


@pytest.fixture
def rag_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 200 with a syntactically valid JSON body that is missing every
    required key (answer/grounded/refused/sources). Distinct code path from
    `rag_malformed`: json.loads succeeds here, so this exercises the explicit
    shape check in ask() (the `raise ValueError("malformed answer payload")`
    branch) rather than json.JSONDecodeError -- both must land in the same
    error-persist branch, but only this fixture proves the shape check
    itself is wired up."""
    monkeypatch.setattr("app.routes.httpx.post", _fake_post({"foo": "bar"}, 200))
