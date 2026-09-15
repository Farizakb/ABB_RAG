# services/rag/app/retrieval.py
from __future__ import annotations

import time
from collections.abc import Collection
from typing import Any, NamedTuple

from contracts.models import Fact, Source

from app.config import settings
from app.db import get_conn
from app.embedder import Embedder

HOST = "https://abb-bank.az"

SQL = """
SELECT c.id, c.text, c.source_class, d.title, d.section_path, d.url, d.id AS doc_id,
       1 - (c.embedding <=> %(q)s::vector) AS score
FROM rag.chunks c
JOIN rag.documents d ON d.id = c.document_id
JOIN rag.corpora r   ON r.id = c.corpus_id
WHERE r.content_hash = %(corpus)s AND c.embedding_model = %(model)s
ORDER BY c.embedding <=> %(q)s::vector
LIMIT %(k)s
"""

FACTS_SQL = """
SELECT document_id, attribute, value_num, value_text, unit, currency, raw_fragment, source_url
FROM rag.product_facts WHERE document_id = ANY(%s)
"""

# Invariant 12: a trimmed listing URL is only shown if we actually fetched it.
# ~225 rows per corpus, so one plain select beats a cache that can go stale.
CORPUS_URLS_SQL = """
SELECT d.url, d.canonical_url
FROM rag.documents d
JOIN rag.corpora r ON r.id = d.corpus_id
WHERE r.content_hash = %s
"""


class RetrievalResult(NamedTuple):
    sources: list[Source]
    candidates: list[dict[str, object]]
    took_ms: int
    # Task D: built from the same `above` rows as `sources`, keyed by `n` (unique
    # by construction -- `n` is `i + 1` over `above`) instead of a second query
    # keyed by URL. The old `source_texts()` collapsed same-document chunks onto
    # whichever one a URL-keyed dict fetched last -- measured on the live corpus,
    # 29 of 46 golden questions had >=1 chunk's text discarded this way (see
    # task-D-brief.md). Field added last so positional unpacking still works.
    texts: dict[int, str]


def listing_url_for(url: str, known: Collection[str]) -> str:
    """The ledger-footer target for SPEC §11.2.

    Derived from a URL that was actually retrieved, so it cannot be a fabricated
    value (invariant 12). Two guards make it safe:

    - A single-segment page is its own listing. Trimming it would send the
      customer to the bare homepage, which is worse than not linking.
    - A trim is only used if the result is a URL we actually fetched. Trimming
      invents a plausible path, and `url.startswith(listing_url)` proves the
      prefix relationship, not that the page exists. A 404 on abb-bank.az reads
      worse to a customer than no link, so an unproven trim falls back to the
      page itself — which returned 200 at scrape time.
    """
    if url.rstrip("/") == HOST:  # the homepage is its own listing
        return url
    trimmed = url.rstrip("/")
    parts = trimmed.split("/")
    if len(parts) <= 4:
        return trimmed
    parent = "/".join(parts[:-1])
    return parent if parent in {k.rstrip("/") for k in known} else trimmed


def _best_per_document(rows: list[Any], k: int) -> list[Any]:
    """Keep each document's highest-scoring chunk, so the prompt gets k distinct
    pages instead of k chunks that may all be one page.

    Chunks cluster by document: measured on the live corpus, 29 of 46 golden
    questions filled the prompt with two or more chunks of a single document,
    which spends prompt slots on a page already represented while the page that
    answers the question sits just below the cut. Deduping lifts recall@5 of the
    expected page from 51% to 58% over the 43 golden rows with an expected URL,
    and from 20% to 25% over the 20 informal/typo rows -- pure re-ranking of
    candidates already fetched, no extra query and no re-embed.

    `rows` arrives score-ordered from the SQL, so the first row seen for a
    document is its best chunk.
    """
    out: list[Any] = []
    seen: set[object] = set()
    for r in rows:
        if r[6] in seen:  # r[6] is doc_id
            continue
        seen.add(r[6])
        out.append(r)
        if len(out) == k:
            break
    return out


def retrieve(
    corpus_id: str,
    query: str,
    embedder: Embedder,
    k_candidates: int | None = None,
    k_prompt: int | None = None,
) -> RetrievalResult:
    k_candidates = k_candidates or settings.top_k_candidates
    k_prompt = k_prompt or settings.top_k_prompt
    started = time.perf_counter()

    vector = embedder.embed([query])[0]
    with get_conn() as conn:
        rows = conn.execute(
            SQL, {"q": str(vector), "corpus": corpus_id, "model": embedder.model, "k": k_candidates}
        ).fetchall()

        above = _best_per_document([r for r in rows if r[7] >= settings.retrieval_floor], k_prompt)
        known_urls = (
            {u for row in conn.execute(CORPUS_URLS_SQL, (corpus_id,)) for u in row if u}
            if above
            else set()
        )
        doc_ids = [r[6] for r in above]
        facts_by_doc: dict[object, list[Fact]] = {}
        if doc_ids:
            for doc_id, attr, num, txt, unit, cur, raw, url in conn.execute(FACTS_SQL, (doc_ids,)):
                facts_by_doc.setdefault(doc_id, []).append(
                    Fact(
                        attribute=attr,
                        value_num=float(num) if num is not None else None,
                        value_text=txt,
                        unit=unit,
                        currency=cur,
                        raw_fragment=raw,
                        source_url=url,
                    )
                )

    sources = [
        Source(
            n=i + 1,
            title=r[3],
            section_path=list(r[4] or []),
            url=r[5],
            listing_url=listing_url_for(r[5], known_urls),
            score=round(float(r[7]), 4),
            source_class=r[2],
            facts=facts_by_doc.get(r[6], []),
        )
        for i, r in enumerate(above)
    ]
    candidates = [
        {"chunk_id": str(r[0]), "url": r[5], "score": round(float(r[7]), 4)} for r in rows
    ]
    texts = {i + 1: r[1] for i, r in enumerate(above)}
    return RetrievalResult(sources, candidates, int((time.perf_counter() - started) * 1000), texts)
