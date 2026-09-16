# ADR-005: Retrieval and embedding, decided by measurement

## Status

Settled. All five questions below were written down in `SPEC.md` §8.3 on day one,
with their decision rules, **before any number was visible** — that pre-commitment
is the point of this ADR. Every measurement here was re-run on the current corpus
`90e08090` and the current labels, so the five rows are comparable with each other.

## Context

Five retrieval questions were pre-committed, each with the rule that would decide
it. Measurements use `recall@5` on `evals/golden.jsonl` (43 answerable rows, of
which 20 are "informal" — chat Azerbaijani with missing diacritics and typos) and,
where it existed at the time, on `evals/heldout.jsonl` (14 rows written by the
project owner, labelled from corpus text, never used to tune retrieval).

## Decision

| # | Question | Rule | Measured | Shipped |
|---|---|---|---|---|
| 1 | dense vs fused | ship dense unless fused wins by >2 pts | dense 28/43 (65%), lexical 28/43 (65%), fused **34/43 (79%)**; informal 40% / 35% / **65%**; held-out 36% / 43% / **57%** | **fused** (+14 pts) |
| 2 | stubs in corpus | out if product hit@5 drops at all | with stubs **34/43 (79%)**, informal 65%; without all depth-1 stubs 32/43 (74%), informal 55% | **stubs stay in** |
| 3 | branch pointer | only if bank-facts fails | without a pointer, branch and ATM questions were answered from `/android-privacypolicy`; with one, they resolve to `/filiallar` and `/atmler` | **two pointers** |
| 4 | retrieval floor | below lowest answerable best-score | lowest answerable 0.311 (a41), highest out-of-scope **0.609** (r06) — the classes overlap across the whole range | **floor stays 0.0** |
| 5 | embedder | winner on hit@5 over the golden queries | `3-small` dense-only 28/43 vs `3-large@1536` 26/43; **fused 30/43 vs 30/43** | **3-small** |

### Item 1 — the lexical channel earns its place

Dense and lexical retrieval score identically on their own (28/43) yet miss
different rows, which is exactly the condition under which fusion helps: Reciprocal
Rank Fusion over the three legs (dense, Postgres FTS, `pg_trgm` `word_similarity`,
all over a diacritic-folded column) reaches 34/43. The gain is largest where it was
predicted to be — informal questions go from 40%/35% alone to 65% fused — and it
reproduces on the held-out set, which had no part in choosing the design. Well
above the 2-point bar, so the `tsv` column, the GIN index and the trigram leg stay.

### Item 2 — stub pages stay, and the first plausible rule was wrong

The corpus holds 109 depth-1 "stub" pages (thin SEO landing pages; text length
p25/p50/p75 = 365/1,532/4,202 characters). They plainly occupy top-5 slots on
questions they do not answer, so excluding them looked obviously right. Measuring
it first showed the opposite: stubs are often *the* answer —
`/asan-kredit-veren-banklar` and `/onlayn-kredit` are both stubs and both are
labelled correct sources. Excluding thin stubs under 1,500 characters changes
nothing; anything stronger deletes real answers (77% at 3,000 chars, 74% for all
depth-1 stubs). The full sweep, including the one rule that helped the held-out set
while hurting the golden set, is entry 5 of `docs/error-analysis.md`.

### Item 3 — a pointer was needed, and says nothing a page does not

The §5.5 bank-facts document did not produce a plausible grounded answer for
branch and ATM questions; retrieval reached the Android privacy policy instead,
which mentions locations. The two pointer documents state only that the page exists
and what it lists — never an address, an opening hour or a product fact, because
those go stale between scrapes. They ship in commit `6d12270` alongside the FX rate
strip, and the README names both.

### Item 4 — no floor can separate the classes, so there is no floor

The rule was to set `retrieval_floor` below the lowest answerable best-score. That
is 0.311, but the highest out-of-scope best-score is 0.609: the distributions
overlap across nearly their whole range (out-of-scope rows span 0.272–0.609,
answerable rows 0.311–0.764). Any floor low enough to keep every answerable
question also passes almost every out-of-scope one, and any floor high enough to
catch out-of-scope questions silently refuses real ones. `retrieval_floor` stays
`0.0` in `services/rag/app/config.py`, and refusals are decided by the grounded
answer path, not by a similarity threshold. The number lives in the eval report,
not in a code comment.

### Item 5 — the expensive embedder is a tie where it matters

`text-embedding-3-large` at 1,536 dimensions (chosen so the `vector(1536)` column
and its indexes would not change) was measured *through fusion*, which is what
ships, not on the dense leg alone: 30/43 against 30/43, swapping two rows for two
others. It wins the dense-only comparison (28 vs 26) and loses the only comparison
that describes the shipped system. Per the pre-committed rule — winner on hit@5 —
a tie goes to `text-embedding-3-small`, which is cheaper and needs no re-ingest.

## Consequences

- The losing options are deleted rather than flagged off. Re-adding one requires a
  new measurement and an entry in `docs/error-analysis.md`.
- Retrieval ships at 79% all / 65% informal / 84% right-section on the golden set,
  with the held-out set at 57% confirming the golden set is not overfit.
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
