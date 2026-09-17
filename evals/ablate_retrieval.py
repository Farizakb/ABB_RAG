# evals/ablate_retrieval.py
"""Retrieval-only ablations for ADR-0005 items 1, 2 and 4.

Generation-free by design -- these are pure retrieval questions, so embedding
43 short queries costs a fraction of a cent and needs no LLM call. Reuses the
exact legs, fusion constant and helpers `backend/rag/rag/retrieval.py` ships
with (`DENSE_DOCS`, `FTS_DOCS`, `TRGM_DOCS`, `_rrf`, `_tsquery`, `_fold`,
`RANK_DEPTH`) -- imported, never modified, so this script cannot silently
diverge from what production actually runs. Item 2's stub-exclusion variants
are separate SQL strings defined here, because production never excludes
stubs (that is the ADR's own verdict) -- this file is the only place that
losing option is written down, and only long enough to be measured.

The host cannot reach Postgres, so this runs inside the `rag`
container, the same way the `eval` target in Makefile already does:

    docker compose cp evals/ablate_retrieval.py rag:/tmp/ablate_retrieval.py
    docker compose cp evals/golden.jsonl rag:/tmp/golden.jsonl
    docker compose exec -T -e PYTHONPATH=/app -w /tmp rag \\
      python ablate_retrieval.py --corpus <content_hash> --golden golden.jsonl

Read-only: every query here is a SELECT against the already-ingested corpus
named by --corpus. Nothing is written to the database.
"""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

from rag.config import settings
from rag.db import get_conn
from rag.embedder import OpenAIEmbedder
from rag.retrieval import (
    DENSE_DOCS,
    FOLD_FROM,
    FOLD_TO,
    FTS_DOCS,
    RANK_DEPTH,
    TRGM_DOCS,
    _fold,
    _rrf,
    _tsquery,
)

# Maps every document in the corpus to its url and source_class, so a
# document-level hit can be scored against `expected_source_urls` and the
# item-2 product/stub split can be computed -- retrieval.py has no equivalent
# export (CORPUS_URLS_SQL drops the id), so this is this script's own query.
DOC_INFO_SQL = """
SELECT d.id, d.url, d.source_class
FROM rag.documents d JOIN rag.corpora r ON r.id = d.corpus_id
WHERE r.content_hash = %s
"""

# Item 2: the same three legs, with stub documents excluded at the join.
# `c.source_class`/`d.source_class` are set once at ingest and agree by
# construction, so filtering on the chunk's own column needs no extra join.
DENSE_DOCS_NO_STUB = DENSE_DOCS.replace(
    "WHERE r.content_hash = %(corpus)s AND c.embedding_model = %(model)s",
    "WHERE r.content_hash = %(corpus)s AND c.embedding_model = %(model)s "
    "AND c.source_class != 'stub'",
)
FTS_DOCS_NO_STUB = FTS_DOCS.replace(
    "WHERE r.content_hash = %(corpus)s\n  AND to_tsvector",
    "WHERE r.content_hash = %(corpus)s AND c.source_class != 'stub'\n  AND to_tsvector",
)
TRGM_DOCS_NO_STUB = TRGM_DOCS.replace(
    "WHERE r.content_hash = %(corpus)s ORDER BY score DESC",
    "WHERE r.content_hash = %(corpus)s AND d.source_class != 'stub' ORDER BY score DESC",
)
assert DENSE_DOCS_NO_STUB != DENSE_DOCS
assert FTS_DOCS_NO_STUB != FTS_DOCS
assert TRGM_DOCS_NO_STUB != TRGM_DOCS


def load_golden(path: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in pathlib.Path(path).read_text("utf-8").splitlines() if line]


def doc_info(conn: Any, corpus: str) -> dict[str, tuple[str, str]]:
    """document_id -> (url, source_class)."""
    return {row[0]: (row[1], row[2]) for row in conn.execute(DOC_INFO_SQL, (corpus,))}


def run_legs(
    conn: Any,
    corpus: str,
    model: str,
    vector: list[float],
    tsquery: str,
    folded_query: str,
    dense_sql: str,
    fts_sql: str,
    trgm_sql: str,
) -> tuple[list[Any], list[Any], list[Any], dict[Any, float]]:
    """The three per-leg ranked document-id lists, RANK_DEPTH deep, exactly as
    `retrieve()` builds them -- only the SQL text passed in differs (stub-excluded
    or not). Also returns the dense leg's id->score map, which the fused gate in
    `hit()` needs to replicate production's dense-membership walk."""
    dense_rows = conn.execute(
        dense_sql, {"q": str(vector), "corpus": corpus, "model": model, "k": RANK_DEPTH}
    ).fetchall()
    fts_rows = conn.execute(fts_sql, {"corpus": corpus, "tq": tsquery, "k": RANK_DEPTH}).fetchall()
    trgm_rows = conn.execute(
        trgm_sql,
        {"q": folded_query, "ff": FOLD_FROM, "ft": FOLD_TO, "corpus": corpus, "k": RANK_DEPTH},
    ).fetchall()
    return (
        [r[0] for r in dense_rows],
        [r[0] for r in fts_rows],
        [r[0] for r in trgm_rows],
        {r[0]: float(r[1]) for r in dense_rows},
    )


def hit(
    ranked_ids: list[Any],
    expected_ids: set[Any],
    depth: int = 5,
    dense_scores: dict[Any, float] | None = None,
) -> bool:
    """Doc-level hit@depth.

    Per-leg call sites (dense_hit/fts_hit/trgm_hit) pass no `dense_scores` and
    score each leg's own raw top `depth` unfiltered -- those describe a leg
    alone, and the dense-membership gate below does not exist for any leg in
    isolation.

    Fused call sites pass `dense_scores` because production
    (`backend/rag/rag/retrieval.py:203-213`) walks the fused order and keeps
    only documents that carry a proven dense-leg score at or above
    `settings.retrieval_floor`, discarding the rest, before taking the top
    k_prompt. A document the lexical legs alone would rank top-5 but that
    never surfaces in the dense leg's RANK_DEPTH window is skipped in
    production, so scoring the raw fused ranking here would disagree with
    what users actually see.
    """
    if dense_scores is not None:
        ranked_ids = [
            doc_id
            for doc_id in ranked_ids
            if dense_scores.get(doc_id) is not None
            and dense_scores[doc_id] >= settings.retrieval_floor
        ]
    return bool(set(ranked_ids[:depth]) & expected_ids)


def pct(n: int, d: int) -> str:
    return f"{n}/{d} ({round(100 * n / d) if d else 0}%)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, help="content_hash of the shipping corpus")
    ap.add_argument("--golden", default="golden.jsonl")
    ap.add_argument("--out", default="", help="optional path to write the raw JSON results")
    args = ap.parse_args()

    items = load_golden(args.golden)
    answerable = [it for it in items if it.get("expected_source_urls")]
    out_of_scope = [it for it in items if it.get("type") == "out_of_scope"]
    assert len(answerable) == 43, f"expected 43 answerable rows, found {len(answerable)}"

    embedder = OpenAIEmbedder()
    all_questions = [it["question"] for it in answerable] + [it["question"] for it in out_of_scope]
    vectors = embedder.embed(all_questions)
    ans_vectors = dict(
        zip((it["id"] for it in answerable), vectors[: len(answerable)], strict=True)
    )
    oos_vectors = dict(
        zip((it["id"] for it in out_of_scope), vectors[len(answerable) :], strict=True)
    )

    with get_conn() as conn:
        docs = doc_info(conn, args.corpus)
        url_to_id = {url: doc_id for doc_id, (url, _sc) in docs.items()}

        per_item: dict[str, dict[str, Any]] = {}
        for it in answerable:
            qid = it["id"]
            vector = ans_vectors[qid]
            tsquery = _tsquery(it["question"])
            folded = _fold(it["question"])
            expected_ids = {url_to_id[u] for u in it["expected_source_urls"] if u in url_to_id}
            missing_urls = [u for u in it["expected_source_urls"] if u not in url_to_id]

            dense_ids, fts_ids, trgm_ids, dense_scores = run_legs(
                conn,
                args.corpus,
                embedder.model,
                vector,
                tsquery,
                folded,
                DENSE_DOCS,
                FTS_DOCS,
                TRGM_DOCS,
            )
            fused_ids = _rrf(dense_ids, fts_ids, trgm_ids)

            dense_ns, fts_ns, trgm_ns, dense_ns_scores = run_legs(
                conn,
                args.corpus,
                embedder.model,
                vector,
                tsquery,
                folded,
                DENSE_DOCS_NO_STUB,
                FTS_DOCS_NO_STUB,
                TRGM_DOCS_NO_STUB,
            )
            fused_no_stub_ids = _rrf(dense_ns, fts_ns, trgm_ns)

            best_dense_score = None
            if dense_ids:
                # DENSE_DOCS orders DESC by score already; re-fetch is unnecessary,
                # but the score itself (not just the id) is what item 4 needs, so
                # a second small query keeps run_legs()'s return shape uniform.
                row = conn.execute(
                    DENSE_DOCS,
                    {"q": str(vector), "corpus": args.corpus, "model": embedder.model, "k": 1},
                ).fetchone()
                best_dense_score = float(row[1]) if row else None

            per_item[qid] = {
                "tags": it.get("tags", []),
                "expected_ids": list(expected_ids),
                "missing_expected_urls": missing_urls,
                "expected_source_classes": sorted({docs[d][1] for d in expected_ids}),
                "dense_hit": hit(dense_ids, expected_ids),
                "fts_hit": hit(fts_ids, expected_ids),
                "trgm_hit": hit(trgm_ids, expected_ids),
                "fused_hit": hit(fused_ids, expected_ids, dense_scores=dense_scores),
                "fused_no_stub_hit": hit(
                    fused_no_stub_ids, expected_ids, dense_scores=dense_ns_scores
                ),
                "best_dense_score": best_dense_score,
            }

        oos_best: dict[str, float] = {}
        for it in out_of_scope:
            qid = it["id"]
            vector = oos_vectors[qid]
            row = conn.execute(
                DENSE_DOCS,
                {"q": str(vector), "corpus": args.corpus, "model": embedder.model, "k": 1},
            ).fetchone()
            oos_best[qid] = float(row[1]) if row else 0.0

    # ---- Item 1: dense vs each lexical leg vs fused, all + informal subset ----
    informal_ids = [qid for qid, r in per_item.items() if "informal" in r["tags"]]

    def rate(key: str, ids: list[str]) -> tuple[int, int]:
        return sum(per_item[i][key] for i in ids), len(ids)

    print("=== Item 1: dense vs lexical legs vs fused (recall@5, doc-level) ===")
    for label, key in [
        ("dense", "dense_hit"),
        ("fts", "fts_hit"),
        ("trgm", "trgm_hit"),
        ("fused", "fused_hit"),
    ]:
        all_n, all_d = rate(key, list(per_item))
        inf_n, inf_d = rate(key, informal_ids)
        print(f"{label:>6}: all {pct(all_n, all_d)}  informal {pct(inf_n, inf_d)}")
    print(
        "right-section: NOT COMPUTED -- evals/golden.jsonl carries no field that "
        "identifies a document's 'section' relative to a question, and no "
        "committed code derives one. Not reconstructed; see report."
    )

    # ---- Item 2: stubs in vs out, product-page subset ----
    product_ids = [qid for qid, r in per_item.items() if "product" in r["expected_source_classes"]]
    print("\n=== Item 2: stubs in vs out (fused recall@5) ===")
    with_n, with_d = rate("fused_hit", list(per_item))
    without_n, without_d = rate("fused_no_stub_hit", list(per_item))
    p_with_n, p_with_d = rate("fused_hit", product_ids)
    p_without_n, p_without_d = rate("fused_no_stub_hit", product_ids)
    print(f"all rows:      with stubs {pct(with_n, with_d)}  without {pct(without_n, without_d)}")
    print(
        f"product subset (n={p_with_d}): with stubs {pct(p_with_n, p_with_d)}  "
        f"without {pct(p_without_n, p_without_d)}"
    )

    # ---- Item 4: retrieval floor ----
    lowest_answerable = min(
        r["best_dense_score"] for r in per_item.values() if r["best_dense_score"] is not None
    )
    lowest_id = min(
        (qid for qid, r in per_item.items() if r["best_dense_score"] is not None),
        key=lambda qid: per_item[qid]["best_dense_score"],
    )
    highest_oos = max(oos_best.values())
    highest_oos_id = max(oos_best, key=lambda k: oos_best[k])
    print("\n=== Item 4: retrieval floor ===")
    print(f"lowest answerable best-score: {lowest_answerable:.3f} ({lowest_id})")
    print(
        f"highest out-of-scope best-score: {highest_oos:.3f} ({highest_oos_id}) "
        f"over n={len(oos_best)}"
    )

    if args.out:
        pathlib.Path(args.out).write_text(
            json.dumps(
                {
                    "corpus": args.corpus,
                    "embedding_model": embedder.model,
                    "per_item": per_item,
                    "oos_best": oos_best,
                },
                indent=2,
                default=str,  # document ids come back from psycopg as uuid.UUID
            ),
            encoding="utf-8",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
