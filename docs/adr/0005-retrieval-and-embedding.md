# ADR-005: Retrieval and embedding, decided by measurement

## Status

Settled. All five questions below were written down in `SPEC.md` §8.3 on day one,
with their decision rules, **before any number was visible** — that pre-commitment
is the point of this ADR. The corpus that ships is
`713ea0871c2f68f7662420bdd03d9d89fbc8e7b7fa610992a3100f1ddfbcf8ed` (243 documents,
560 chunks) — the artifact in `fixtures/corpus_sample.json`, the one `make demo`
ingests, and the one the committed `evals/report.md` was generated against.
Items 1, 2 and 4 are pure retrieval questions with no generation step, so they were
re-measured directly on this corpus via `scripts/ablate_retrieval.py`, which
imports `retrieval.py`'s own `DENSE_DOCS`, `FTS_DOCS`, `TRGM_DOCS`, `_rrf`,
`_tsquery`, `_fold` and `RANK_DEPTH` rather than re-implementing them, so the
comparison cannot silently diverge from what ships. Item 3 is a qualitative
finding restated from Task 23, observed on an earlier corpus (`90e08090…`, 280
documents) and named as such rather than re-verified here. Item 5 (the embedder
bake-off) is also carried forward from `90e08090…`, for a stated reason: see Item
5. Every "Measured" cell below names the corpus it came from.

## Context

Five retrieval questions were pre-committed, each with the rule that would decide
it. Measurements use `recall@5` on `evals/golden.jsonl` (43 answerable rows, of
which 20 are "informal" — chat Azerbaijani with missing diacritics and typos) and,
where it existed at the time, on `evals/heldout.jsonl` (14 rows written by the
project owner, labelled from corpus text, never used to tune retrieval).

## Decision

| # | Question | Rule | Measured | Shipped |
|---|---|---|---|---|
| 1 | dense vs fused | ship dense unless fused wins by >2 pts | corpus `713ea087…`: dense 26/43 (60%), FTS 24/43 (56%), trgm 23/43 (53%), fused **30/43 (70%)**; informal (20 rows) 25% / 30% / 25% / **45%**; held-out 36% / 43% / 57% (corpus `90e08090…`, not re-measured); "right-section" not reproducible from committed data | **fused** (+10 pts) |
| 2 | stubs in corpus | out if product hit@5 drops at all | corpus `713ea087…`: with stubs 30/43 (70%), product subset 30/39 (77%); without stubs 32/43 (74%), product subset **32/39 (82%)** — excluding stubs does **not** drop hit@5 here, it raises it | **stubs stay in** (unreviewed reversal — see Item 2) |
| 3 | branch pointer | only if bank-facts fails | without a pointer, branch and ATM questions were answered from `/android-privacypolicy`; with one, they resolve to `/filiallar` and `/atmler` — observed live on corpus `90e08090…` (Task 23 Item 3), not re-verified on `713ea087…` | **two pointers** |
| 4 | retrieval floor | below lowest answerable best-score | corpus `713ea087…`: lowest answerable 0.307 (a41), highest out-of-scope **0.611** (a15) — the classes still overlap across nearly the whole range | **floor stays 0.0** |
| 5 | embedder | winner on hit@5 over the golden queries | corpus `90e08090…`, not re-measured on `713ea087…`: `3-small` dense-only 28/43 vs `3-large@1536` 26/43; **fused 30/43 vs 30/43** | **3-small** |

### Item 1 — the lexical channel earns its place

Re-measured on the shipping corpus (`713ea0871c2f68f7662420bdd03d9d89fbc8e7b7fa610992a3100f1ddfbcf8ed`,
243 documents / 560 chunks) with `scripts/ablate_retrieval.py`, which imports
`retrieval.py`'s `DENSE_DOCS`, `FTS_DOCS`, `TRGM_DOCS`, `_rrf`, `_tsquery`, `_fold`
and `RANK_DEPTH` and runs each leg's own query, fused and unfused, over the 43 rows
of `evals/golden.jsonl` that carry `expected_source_urls`. Dense, FTS and trigram
score close together on their own (26/43, 24/43, 23/43) yet miss different rows —
the same condition that motivated fusion in the first place — and Reciprocal Rank
Fusion over the three legs reaches **30/43 (70%)**, a 10-point gain over dense
alone, comfortably above the pre-committed 2-point bar. The gain concentrates on
informal questions (25%/30%/25% alone vs **45%** fused): `slm men nece kredit ala
bilerem` (`a26`), `en cox ne qeder kredit vere bilirsiz` (`a28`) and `faiz neceden
basliyir` (`a32`) are exactly the typo-heavy, diacritic-dropped queries the lexical
legs were added for.

This fused figure — 30/43, 0.698 — agrees exactly with the committed
`evals/report.md`'s retrieval hit@5 (also 0.698, from the full generation pipeline's
real run, not this isolated-legs ablation). Two independent measurements landing on
the same number is the cross-check a trustworthy pipeline would produce; it is not
assumed, it is checked, and it holds.

The held-out figure (36%/43%/57%) is carried forward from corpus `90e08090…` and
was not re-run: Task 36 re-measures the 43-row golden set only, `evals/heldout.jsonl`
is out of its scope. The "right-section" figure the ADR previously quoted (84%)
could not be reproduced: `evals/golden.jsonl` carries no field naming a document's
section relative to a question, and no committed code computes one. Rather than
invent a definition to fill that cell, it is dropped from the re-measured row; the
older, differently-corpused number is discussed in Consequences, labelled as such.

### Item 2 — stub pages, and a result that no longer holds on this corpus

The corpus holds 81 `source_class = 'stub'` documents (down from 109 on the retired
`90e08090…` corpus — fewer documents overall, same category). The pre-committed
rule was: exclude stubs only if doing so does not drop product-page hit@5. On
`90e08090…` it dropped (the full sweep is entry 5 of `docs/error-analysis.md`), so
stubs shipped in. Re-measured on `713ea087…` with the same cut — every
`source_class = 'stub'` document removed from all three legs before fusion — the
result is the opposite: fused hit@5 rises from **30/43 (70%) to 32/43 (74%)**, and
the product-page subset (39 of the 43 rows whose expected answer is a `product`
document) rises from **30/39 (77%) to 32/39 (82%)**. Three rows that missed with
stubs in the pool now hit once stubs are removed (`a26`, `a28`, `a32` — the same
three Item 1 named, now helped further by one fewer distractor class), and one row
that hit now misses (`a29`, `krediti banka getmeden ala bilerem?`, whose second
labelled answer, `/asan-kredit-veren-banklar`, is itself a stub and is removed along
with the rest of the class). It is the same tension the original measurement found
— crowding-out vs. deleted real answers — with the crowding effect outweighing the
lost-answer effect this time instead of the reverse.

**This is a reversal of the pre-committed rule's verdict, and it is reported rather
than acted on.** The rule says out if hit@5 drops; on this corpus it does not drop,
it improves, so the rule's own logic points at excluding stubs — a conclusion this
task does not implement. Acting on it means changing
`services/rag/app/retrieval.py`'s SQL, which Task 36 is explicitly forbidden from
touching, and re-running the full eval (including generation) to confirm the effect
survives past document-level recall into grounded answers, which this read-only
ablation cannot show. The shipped configuration is unchanged — stubs stay in —
because that is what the running system does today, not because the rule still
endorses it on this corpus. This belongs in a follow-up task with its own
measurement and its own `docs/error-analysis.md` entry, not a silent table edit.

### Item 3 — a pointer was needed, and says nothing a page does not

The §5.5 bank-facts document did not produce a plausible grounded answer for
branch and ATM questions; retrieval reached the Android privacy policy instead,
which mentions locations. The two pointer documents state only that the page exists
and what it lists — never an address, an opening hour or a product fact, because
those go stale between scrapes. They ship in commit `6d12270` alongside the FX rate
strip, and the README names both. This was observed live on corpus `90e08090…`
(Task 23 Item 3); the pointer documents are produced by the scraper's
synthetic-document step, not by anything corpus-specific, so the same behavior is
expected on `713ea087…`, but this task restates the finding rather than re-running
the live probe to confirm it.

### Item 4 — no floor can separate the classes, so there is no floor

Re-measured on `713ea087…` with the same `scripts/ablate_retrieval.py`. The rule
was to set `retrieval_floor` below the lowest answerable best-score. That is now
**0.307** (`a41`, `kartima pul nece yatira bilerem?`), but the highest out-of-scope
best-score is **0.611** (`a15`, an out-of-scope branch-hours question) — the
distributions still overlap across nearly their whole range, the same shape as the
retired measurement (0.311 vs 0.609 on `90e08090…`). Any floor low enough to keep
every answerable question also passes almost every out-of-scope one, and any floor
high enough to catch out-of-scope questions silently refuses real ones.
`retrieval_floor` stays `0.0` in `services/rag/app/config.py`, and refusals are
decided by the grounded answer path, not by a similarity threshold. The number
lives in the eval report, not in a code comment.

### Item 5 — the expensive embedder is a tie where it matters

`text-embedding-3-large` at 1,536 dimensions (chosen so the `vector(1536)` column
and its indexes would not change) was measured *through fusion*, which is what
ships, not on the dense leg alone: 30/43 against 30/43, swapping two rows for two
others. It wins the dense-only comparison (28 vs 26) and loses the only comparison
that describes the shipped system. Per the pre-committed rule — winner on hit@5 —
a tie goes to `text-embedding-3-small`, which is cheaper and needs no re-ingest.
This comparison is carried forward from corpus `90e08090…` and was **not**
re-run on the shipping corpus `713ea087…`: doing so means embedding all 560 chunks
under a second `embedding_model` value, which the schema supports (Invariant 8 —
every vector carries the model that produced it, and two embedding models never
share one index) but which would write a second index into the live, already-seeded
database — out of scope for a read-only reconciliation task. The tie stands as the
best available evidence; a shipping-corpus number for this row specifically would
need a dedicated re-ingest task, not an ablation.

## Consequences

- The losing options are deleted rather than flagged off. Re-adding one requires a
  new measurement and an entry in `docs/error-analysis.md`. Item 2 is a partial
  exception: measured, not re-implemented — see Item 2 above.
- Retrieval ships at **70% all / 45% informal** on the golden set, measured on the
  shipping corpus `713ea0871c2f68f7662420bdd03d9d89fbc8e7b7fa610992a3100f1ddfbcf8ed`.
  This agrees exactly with the committed `evals/report.md`'s retrieval hit@5 (0.698,
  i.e. 30/43) — two independently-run measurements, same number. The held-out set
  sits at 57% (corpus `90e08090…`, not re-measured this task) — still well above
  informal-alone performance, though it can no longer be cited as confirming the
  *current* golden figure is not overfit without re-running it on this corpus. The
  "right-section" figure this ADR previously quoted (84%) is not reproducible from
  committed data — no field in `evals/golden.jsonl` and no committed code define
  it — and has been dropped rather than restated as if unchanged.
- Item 2's re-measurement reverses the pre-committed rule's verdict on this corpus:
  fused hit@5 rises, not drops, when stub pages are excluded (30/43 → 32/43,
  product subset 30/39 → 32/39). The shipped system is unchanged — stubs stay in
  `services/rag/app/retrieval.py`, which this task does not modify — so this is
  recorded as an open finding for a follow-up task, not resolved here.
- The remaining misses are intent gaps, not ranking noise: the question and the
  page that answers it share almost no vocabulary (`kartimi itirmisem indi ne
  edim?` against a page that says "kartın bloklanması və aktiv edilməsi"). Fusion
  weighting, query rewriting and a larger embedder were all measured and all
  rejected, so closing these needs a different class of change.

## Rejected

- **Choosing any of the five by taste**, and keeping hybrid retrieval because it
  sounds sophisticated. Item 1 was one measurement away from deleting it.
- **Per-class fusion weights** (down-weighting stubs): +1 row on the golden set and
  nothing on any other cut — inside the noise, and fitted to the set it was tuned on.
- **LLM query rewriting** into formal Azerbaijani: 27/43 replacing the query and
  29/43 multi-query, against 30/43 shipped, plus 1.6s median added latency. The
  rewrites were correct Azerbaijani and still missed, which is what proved the gap
  is not query form.
- **A cross-encoder reranker.** The one remaining lever with a plausible shot at the
  intent gaps, dropped on cost: it adds a model call to every question for a gain
  this project cannot justify at its budget. Recorded here so the next person knows
  it was a priced decision, not an oversight.
- **A hand-written synonym map** for the intent gaps. It would raise the eval score
  without improving the language coverage the eval is supposed to stand for.
