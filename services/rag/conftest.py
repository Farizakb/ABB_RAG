# services/rag/conftest.py
"""The `db` fixture used by every ingest test.

Deliberately does NOT touch the dev database (P68): that database is migrated
at EMBEDDING_DIM (1536, from .env) while these tests need vector(8), and
TRUNCATE-ing it on every `make test` would destroy the ingested demo corpus.
Instead this builds and owns a separate `*_test` database on the same
Postgres server, using the real migration file (schema drift must break these
tests, not hide from them -- no hand-written duplicate CREATE TABLE).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import app.db as db_module
import psycopg
import pytest
from app.config import settings
from psycopg_pool import ConnectionPool

MIGRATION_PATH = Path(__file__).resolve().parents[2] / "db" / "migrations" / "001_schema.sql"
TEST_DIM = 8


def _test_database_url() -> str:
    """`$TEST_DATABASE_URL` if set, else `settings.database_url` with `_test`
    appended to the database name (P68)."""
    env_url = os.environ.get("TEST_DATABASE_URL")
    if env_url:
        return env_url
    parts = urlsplit(settings.database_url)
    dbname = parts.path.lstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, f"/{dbname}_test", parts.query, parts.fragment))


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
        # P69: unreachable SKIPS locally (no Postgres on the dev machine is a
        # normal state) but FAILS when $CI is set (Postgres is guaranteed
        # there -- a silent skip in CI is exactly how vacuous coverage hides).
        reason = f"cannot reach test database at {test_url}: {exc}"
        if os.environ.get("CI"):
            pytest.fail(reason)
        pytest.skip(reason)

    # Rebuild the schema from the real migration file every session rather
    # than trusting whatever a previous run left behind -- otherwise a schema
    # change to db/migrations/001_schema.sql would silently not apply to a
    # `*_test` database that already exists on a persistent dev Postgres
    # volume, hiding drift instead of breaking the test that depends on it.
    with psycopg.connect(test_url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS rag CASCADE")
        conn.execute("DROP SCHEMA IF EXISTS app CASCADE")
        sql = MIGRATION_PATH.read_text(encoding="utf-8").replace(":dim", str(TEST_DIM))
        conn.execute(sql)

    pool = ConnectionPool(test_url, min_size=1, max_size=4, open=True)
    # app.db.get_conn() reads the module global `pool` at call time, so
    # rebinding it here redirects every `get_conn()` call -- including inside
    # ingest_corpus -- at the test database for the rest of the session.
    db_module.pool = pool
    yield pool
    pool.close()


@pytest.fixture
def db(_test_pool: ConnectionPool) -> Iterator[psycopg.Connection]:
    with db_module.get_conn() as conn:
        conn.execute("TRUNCATE rag.corpora, rag.documents, rag.chunks, rag.product_facts CASCADE")
        conn.commit()

    with db_module.pool.connection() as conn:
        conn.autocommit = True  # sees rows committed by ingest_corpus on another pooled conn
        yield conn
