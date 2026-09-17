# backend/rag/rag/db.py
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from shared.db import get_conn as _get_conn
from shared.db import make_pool

from rag.config import settings

pool = make_pool(settings.database_url)


@contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    with _get_conn(pool) as conn:
        yield conn
