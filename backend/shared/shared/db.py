# backend/shared/shared/db.py
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg_pool import ConnectionPool


def make_pool(url: str) -> ConnectionPool:
    # timeout: how long pool.connection() waits for a connection before raising,
    # not how long a query may run. Left at the psycopg_pool default (~30s) this
    # blocks /healthz for that long whenever the host is unreachable (e.g. local
    # pytest, where "db" does not resolve) -- 3s keeps health checks responsive
    # while still comfortably covering a real, briefly slow Postgres.
    return ConnectionPool(url, min_size=1, max_size=8, open=False, timeout=3)


@contextmanager
def get_conn(pool: ConnectionPool) -> Iterator[psycopg.Connection]:
    if pool.closed:
        pool.open()
    with pool.connection() as conn:
        yield conn
