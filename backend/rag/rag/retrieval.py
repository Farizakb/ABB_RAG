# backend/rag/rag/retrieval.py
from __future__ import annotations

import re
import time
from collections.abc import Collection
from typing import Any, NamedTuple

from shared.contracts import Fact, Source

from rag.config import settings
from rag.db import get_conn
from rag.embedder import Embedder

HOST = "https://abb-bank.az"

# Azerbaijani diacritics folded away on both sides, so `neceden` reaches
# `nəçədən`. Must stay character-identical to 002_hybrid_lexical.sql's `fold`.
FOLD_FROM, FOLD_TO = "əıöüçşğ", "eioucsg"

# Reciprocal-rank-fusion constant. 60 is the published default from the original
# RRF paper, deliberately NOT tuned on our 43 golden rows: sweeping it over
# 10/20/30/60 moved the result by at most one row, which is noise at this sample
# size, and a fitted constant would be the kind of number that looks like a
# result and is not.
RRF_K = 60

# How deep each retriever's ranked list goes before fusion.
RANK_DEPTH = 100

# Question words carry no retrieval signal but match nearly every page, so an
# OR-query built from them ranks the corpus at random. This is a stopword list,
# not a relevance cut -- a document-frequency cut was measured and was strictly
# worse, because in a bank corpus the frequent terms (`kredit`, 44% of documents)
# are exactly the discriminative ones.
STOPWORDS = frozenset(
    """ne nece necedir nedir hansi hansilardir var varmi ucun ile olur olar
    mumkundur bilerem edir daha bir the what is are how can i do does of for and
    a to in on my me""".split()  # noqa: SIM905 -- exact wordlist as measured, not a list literal
)

DENSE_DOCS = """
SELECT c.document_id, max(1 - (c.embedding <=> %(q)s::vector)) AS score, d.url
FROM rag.chunks c
JOIN rag.corpora r ON r.id = c.corpus_id
JOIN rag.documents d ON d.id = c.document_id
WHERE r.content_hash = %(corpus)s AND c.embedding_model = %(model)s
GROUP BY c.document_id, d.url ORDER BY score DESC LIMIT %(k)s
"""

FTS_DOCS = """
SELECT c.document_id,
       max(ts_rank_cd(to_tsvector('simple', c.fold), to_tsquery('simple', %(tq)s))) AS score
FROM rag.chunks c JOIN rag.corpora r ON r.id = c.corpus_id
WHERE r.content_hash = %(corpus)s
  AND to_tsvector('simple', c.fold) @@ to_tsquery('simple', %(tq)s)
GROUP BY c.document_id ORDER BY score DESC LIMIT %(k)s
"""

TRGM_DOCS = """
SELECT d.id,
       word_similarity(%(q)s, translate(lower(
           coalesce(d.title, '') || ' ' ||
           coalesce(array_to_string(d.section_path, ' '), '') || ' ' ||
           replace(replace(d.url, 'https://abb-bank.az/', ''), '-', ' ')
       ), %(ff)s, %(ft)s)) AS score
FROM rag.documents d JOIN rag.corpora r ON r.id = d.corpus_id
WHERE r.content_hash = %(corpus)s ORDER BY score DESC LIMIT %(k)s
"""

# Returns each winning document's best chunk for this query, in the same
# 8-column shape the Source/texts/facts code below indexes by position
# (`r[0]`..`r[7]`) -- do not renumber those.
BEST_CHUNKS = """
SELECT DISTINCT ON (c.document_id)
       c.id, c.text, c.source_class, d.title, d.section_path, d.url, c.document_id,
       1 - (c.embedding <=> %(q)s::vector) AS score
FROM rag.chunks c JOIN rag.documents d ON d.id = c.document_id
WHERE c.document_id = ANY(%(ids)s) AND c.embedding_model = %(model)s
ORDER BY c.document_id, c.embedding <=> %(q)s::vector
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
    # Built from the same `above` rows as `sources`, keyed by `n` (unique by
    # construction -- `n` is `i + 1` over `above`) instead of a second query
    # keyed by URL. A URL-keyed dict collapses same-document chunks onto
    # whichever one it fetched last -- measured on the live corpus, 29 of 46
    # golden questions had >=1 chunk's text discarded this way. Field added
    # last so positional unpacking still works.
    texts: dict[int, str]


def listing_url_for(url: str, known: Collection[str]) -> str:
    """The ledger-footer target.

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


def _fold(s: str) -> str:
    return s.lower().translate(str.maketrans(FOLD_FROM, FOLD_TO))


def _tsquery(question: str) -> str:
    """An OR of prefix terms. Prefix matching is what substitutes for the
    Azerbaijani stemmer Postgres does not have."""
    terms = [t for t in re.findall(r"\w+", _fold(question)) if len(t) > 2 and t not in STOPWORDS]
    # A query of nothing but stopwords must match nothing, not everything.
    return " | ".join(f"{t}:*" for t in terms) or "zzzznomatch"


def _rrf(*rankings: list[Any]) -> list[Any]:
    """Reciprocal rank fusion: each list contributes 1/(RRF_K + rank) per id.

    Fusing ranks rather than scores is the point -- the three retrievers produce
    cosine similarity, ts_rank_cd and trigram similarity, which share no scale and
    cannot be compared or averaged directly.
    """
    scores: dict[Any, float] = {}
    for ranking in rankings:
        for i, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (RRF_K + i + 1)
    return sorted(scores, key=lambda d: -scores[d])


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
    tsquery = _tsquery(query)
    folded_query = _fold(query)

    with get_conn() as conn:
        dense_rows = conn.execute(
            DENSE_DOCS,
            {"q": str(vector), "corpus": corpus_id, "model": embedder.model, "k": RANK_DEPTH},
        ).fetchall()
        fts_rows = conn.execute(
            FTS_DOCS, {"corpus": corpus_id, "tq": tsquery, "k": RANK_DEPTH}
        ).fetchall()
        trgm_rows = conn.execute(
            TRGM_DOCS,
            {
                "q": folded_query,
                "ff": FOLD_FROM,
                "ft": FOLD_TO,
                "corpus": corpus_id,
                "k": RANK_DEPTH,
            },
        ).fetchall()

        dense_scores: dict[Any, float] = {doc_id: float(score) for doc_id, score, _ in dense_rows}

        fused = _rrf(
            [doc_id for doc_id, _, _ in dense_rows],
            [doc_id for doc_id, _ in fts_rows],
            [doc_id for doc_id, _ in trgm_rows],
        )

        # Walk fused order and keep documents whose dense score clears the
        # floor. A document absent from the dense leg has no proven dense
        # score and is skipped -- this is what keeps `retrieval_floor`
        # meaningful and keeps the existing floor test honest.
        above_ids: list[Any] = []
        for doc_id in fused:
            score = dense_scores.get(doc_id)
            if score is not None and score >= settings.retrieval_floor:
                above_ids.append(doc_id)
            if len(above_ids) == k_prompt:
                break

        known_urls = (
            {u for row in conn.execute(CORPUS_URLS_SQL, (corpus_id,)) for u in row if u}
            if above_ids
            else set()
        )

        chunk_rows = (
            conn.execute(
                BEST_CHUNKS, {"ids": above_ids, "model": embedder.model, "q": str(vector)}
            ).fetchall()
            if above_ids
            else []
        )
        by_doc = {r[6]: r for r in chunk_rows}
        # Rows are kept in `above_ids`' fused order, not re-sorted by dense
        # score -- fusion exists precisely to promote a document the lexical
        # legs single out ahead of a dense-only near-duplicate, and the prompt
        # presents sources as [1]..[5] with the model weighting earlier ones,
        # so re-sorting here would partially undo that. `Source.score` below
        # is the dense cosine component (what `retrieval_floor` gates on and
        # what analytics compares across queries) -- it is deliberately NOT
        # the display sort key.
        above = [by_doc[doc_id] for doc_id in above_ids if doc_id in by_doc]

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
            # The dense cosine component, not the display sort key: `n` order
            # is the fused rank (see `above`), so `score` can decrease then
            # increase across sources -- that is fusion working as intended.
            score=round(float(r[7]), 4),
            source_class=r[2],
            facts=facts_by_doc.get(r[6], []),
        )
        for i, r in enumerate(above)
    ]
    candidates = [
        {"document_id": str(doc_id), "url": url, "score": round(float(score), 4)}
        for doc_id, score, url in dense_rows[:k_candidates]
    ]
    texts = {i + 1: r[1] for i, r in enumerate(above)}
    return RetrievalResult(sources, candidates, int((time.perf_counter() - started) * 1000), texts)
