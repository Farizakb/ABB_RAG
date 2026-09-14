# services/rag/conftest.py
# ruff: noqa: RUF001 -- genuine Azerbaijani fixture text (dotless-i and
# friends) in seeded_corpus; see services/rag/tests/test_chunking.py for the
# same convention.
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
import socket
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import app.db as db_module
import psycopg
import pytest
from app.config import settings
from app.embedder import FakeEmbedder
from app.ingest import ingest_corpus
from contracts.models import Corpus, Document, Fact
from psycopg_pool import ConnectionPool

MIGRATION_PATH = Path(__file__).resolve().parents[2] / "db" / "migrations" / "001_schema.sql"
TEST_DIM = 8


def _reachable(url: str) -> str:
    """Rewrite an unreachable host to `127.0.0.1`.

    `settings.database_url` defaults to `postgresql://abb:abb@db:5432/abb`,
    where `db` is the compose SERVICE NAME. It resolves inside the compose
    network and nowhere else -- so on the host, which is where
    `pytest services/rag` is actually run, the P68 fallback dialled a name
    that does not exist and every test in this file skipped. CI never showed
    it, because CI sets `DATABASE_URL` to localhost itself: green everywhere
    it was watched, skipped everywhere it was used.

    P70 published 5432 on the host for exactly this reason. Deciding on
    reachability rather than on an env var keeps ONE default correct in both
    places: inside the container `db` connects and is used unchanged.

    This used to decide on DNS resolvability (`socket.getaddrinfo` raising
    `socket.gaierror`), which is the wrong predicate: a corporate network's
    DNS search suffix or wildcard can resolve `db` anyway.
    `socket.getaddrinfo("db", None)` on this machine returns
    `[('13.248.169.48', 0), ('76.223.54.146', 0)]` -- two public IPs that
    obviously are not the compose network -- so the old check never fired,
    psycopg tried to connect to a public IP on port 5432, timed out, and 20
    database-dependent tests skipped instead of running. A resolvable-but-
    unreachable host is the normal case here, so the predicate has to be
    "can I connect", not "does the name resolve".

    The replacement is `127.0.0.1`, not `localhost`: compose publishes the
    port on `127.0.0.1` only, while `localhost` resolves to `::1` first on
    Windows. Every connection then pays a ~5s failed IPv6 attempt before
    falling back, which a direct connect survives and `ConnectionPool`'s
    background workers do not -- they time out and the pool never fills.
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
    """`$TEST_DATABASE_URL` if set, else `settings.database_url` with `_test`
    appended to the database name and an unreachable host rewritten to
    `127.0.0.1` (P68, and see `_reachable`)."""
    env_url = os.environ.get("TEST_DATABASE_URL")
    if env_url:
        return env_url
    parts = urlsplit(settings.database_url)
    dbname = parts.path.lstrip("/")
    return _reachable(
        urlunsplit((parts.scheme, parts.netloc, f"/{dbname}_test", parts.query, parts.fragment))
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


@pytest.fixture
def seeded_corpus(db: psycopg.Connection) -> str:
    """P80: the brief's test_retrieval.py takes this fixture for granted but
    never defines it. Lives here, not in test_retrieval.py, because Task 20
    (generation) will need the same seeded data.

    Depends on `db` (not `_test_pool`) so TRUNCATE runs before ingest, giving
    a clean, deterministic corpus regardless of test order.

    - one chunk per document (each doc's text is a single line, so
      chunk_document never splits it), and 12 documents -- comfortably above
      both k_candidates=20 (so `candidates` is never truncated: every chunk
      is a candidate) and k_prompt=5, so a test asserting `len(sources) <= 5`
      is pinning a real cap, not an artifact of too little data. See the
      task report for the measured cosine-score distribution these 12
      documents produce under FakeEmbedder (ruling P82).
    - facts on three documents (nagd-kredit, avtokredit, biznes-kredit) so
      test_governed_facts_travel_with_their_source has something to find.
    - https://abb-bank.az/ferdi/kreditler is the parent listing of
      .../nagd-kredit, .../avtokredit, .../ipoteka and .../kart-kredit;
      https://abb-bank.az/kampaniyalar is the parent of two campaign pages.
      Both parents are themselves documents in the corpus, so
      listing_url_for's trim branch is exercised against a URL that was
      actually fetched, not merely a plausible one (invariant 12).
    - https://abb-bank.az/filiallar is a single-segment page with no child in
      this corpus, covering the "top-level page is its own listing" case
      end-to-end (test_every_source_carries_a_listing_url_derived_from_its_own_url).
    """
    docs = [
        Document(
            url="https://abb-bank.az/ferdi/kreditler",
            title="Fərdi kreditlər",
            section_path=["Fərdi"],
            source_class="index",
            text="Bank fərdi müştərilərə müxtəlif növ kreditlər təklif edir.",
            content_hash="sha256:seed-kreditler",
        ),
        Document(
            url="https://abb-bank.az/ferdi/kreditler/nagd-kredit",
            title="Nağd kredit",
            section_path=["Fərdi", "Kreditlər"],
            source_class="product",
            text="Nağd kredit məbləği maksimum 20000 AZN-dək təşkil edir.",
            content_hash="sha256:seed-nagd-kredit",
            facts=[
                Fact(
                    attribute="max_amount",
                    value_num=20000,
                    unit="AZN",
                    raw_fragment="20 000 AZN-dək",
                    source_url="https://abb-bank.az/ferdi/kreditler/nagd-kredit",
                )
            ],
        ),
        Document(
            url="https://abb-bank.az/ferdi/kreditler/avtokredit",
            title="Avtokredit",
            section_path=["Fərdi", "Kreditlər"],
            source_class="product",
            text="Avtokredit məbləği maksimum 50000 AZN-dək təşkil edir.",
            content_hash="sha256:seed-avtokredit",
            facts=[
                Fact(
                    attribute="max_amount",
                    value_num=50000,
                    unit="AZN",
                    raw_fragment="50 000 AZN-dək",
                    source_url="https://abb-bank.az/ferdi/kreditler/avtokredit",
                )
            ],
        ),
        Document(
            url="https://abb-bank.az/ferdi/kreditler/ipoteka",
            title="İpoteka krediti",
            section_path=["Fərdi", "Kreditlər"],
            source_class="product",
            text="İpoteka krediti mənzil almaq üçün istifadə olunur.",
            content_hash="sha256:seed-ipoteka",
        ),
        Document(
            url="https://abb-bank.az/ferdi/kreditler/kart-kredit",
            title="Kart krediti",
            section_path=["Fərdi", "Kreditlər"],
            source_class="product",
            text="Kart krediti gündəlik xərclər üçün nəzərdə tutulub.",
            content_hash="sha256:seed-kart-kredit",
        ),
        Document(
            url="https://abb-bank.az/kampaniyalar",
            title="Kampaniyalar",
            section_path=["Kampaniyalar"],
            source_class="index",
            text="Bankın cari kampaniyalarının siyahısı burada yerləşir.",
            content_hash="sha256:seed-kampaniyalar",
        ),
        Document(
            url="https://abb-bank.az/kampaniyalar/yay-kampaniyasi",
            title="Yay kampaniyası",
            section_path=["Kampaniyalar"],
            source_class="campaign",
            text="Yay kampaniyası çərçivəsində kredit faizləri endirilib.",
            content_hash="sha256:seed-yay-kampaniyasi",
        ),
        Document(
            url="https://abb-bank.az/kampaniyalar/qis-kampaniyasi",
            title="Qış kampaniyası",
            section_path=["Kampaniyalar"],
            source_class="campaign",
            text="Qış kampaniyası müddətində əlavə bonuslar təklif olunur.",
            content_hash="sha256:seed-qis-kampaniyasi",
        ),
        Document(
            url="https://abb-bank.az/biznes/kreditler/biznes-kredit",
            title="Biznes krediti",
            section_path=["Biznes", "Kreditlər"],
            source_class="product",
            text="Biznes krediti məbləği maksimum 200000 AZN-dək təşkil edir.",
            content_hash="sha256:seed-biznes-kredit",
            facts=[
                Fact(
                    attribute="max_amount",
                    value_num=200000,
                    unit="AZN",
                    raw_fragment="200 000 AZN-dək",
                    source_url="https://abb-bank.az/biznes/kreditler/biznes-kredit",
                )
            ],
        ),
        Document(
            url="https://abb-bank.az/biznes/kreditler/lizinq",
            title="Lizinq",
            section_path=["Biznes", "Kreditlər"],
            source_class="product",
            text="Lizinq xidməti avadanlıq alışı üçün istifadə olunur.",
            content_hash="sha256:seed-lizinq",
        ),
        Document(
            url="https://abb-bank.az/haqqimizda",
            title="Haqqımızda",
            section_path=["Haqqımızda"],
            source_class="corporate",
            text="Bank 1992-ci ildən etibarən fəaliyyət göstərir.",
            content_hash="sha256:seed-haqqimizda",
        ),
        Document(
            url="https://abb-bank.az/filiallar",
            title="Filiallar",
            section_path=["Filiallar"],
            source_class="corporate",
            text="Bankın filial şəbəkəsi ölkə üzrə geniş yayılıb.",
            content_hash="sha256:seed-filiallar",
        ),
    ]
    corpus = Corpus(documents=docs)
    return ingest_corpus(corpus, FakeEmbedder(dim=8))
