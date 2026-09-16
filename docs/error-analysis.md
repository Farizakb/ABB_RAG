# Error analysis

Each entry: failure observed → hypothesis → change → before and after, with the
numbers that decided it. Retrieval numbers are `recall@5` on `evals/golden.jsonl`
(43 answerable rows; "informal" is the 20 rows written in chat Azerbaijani with
missing diacritics and typos) and, from entry 4 onward, on `evals/heldout.jsonl`
(14 answerable rows, written by the project owner, never used to tune retrieval).
A before/after pair is only comparable when both sides were measured on the same
labels and the same corpus; where that is not true, this log says so.

---

## 1. Informal Azerbaijani questions missed the page that answers them

**Observed.** With dense retrieval alone, the 20 informal/typo rows scored 8/20
(40%) while the whole set scored 28/43 (65%). Questions like `en cox ne qeder
kredit vere bilirsiz` returned unrelated card pages.

**Hypothesis.** `text-embedding-3-small` is trained mostly on well-formed text.
Azerbaijani written in chat drops `ə ı ö ü ç ş ğ` and misspells product names, so
the query vector lands far from the product page — but the surviving letters still
overlap the page lexically.

**Change.** A lexical channel beside the dense one: Postgres FTS (`simple`
configuration, `:*` prefix matching) and `pg_trgm` `word_similarity`, both over a
diacritic-folded generated column (`əıöüçşğ` → `eioucsg`), fused with the dense
ranking by Reciprocal Rank Fusion (`RRF_K=60`, `RANK_DEPTH=100`).

**Before → after** (same labels, same corpus `90e08090`; the lexical leg is shown on
its own too, because that is what explains the gain):

| | dense only | lexical only | fused |
|---|---|---|---|
| all | 28/43 (65%) | 28/43 (65%) | 34/43 (79%) |
| informal | 8/20 (40%) | 7/20 (35%) | 13/20 (65%) |
| right section | 31/43 (72%) | 31/43 (72%) | 36/43 (84%) |
| held-out | 5/14 (36%) | 6/14 (43%) | 8/14 (57%) |

Dense and lexical score identically on their own yet miss different rows, which is
the condition under which fusion pays: it gains 14 points over either, far above the
pre-committed 2-point bar, so the lexical channel ships. See
[ADR-0005](adr/0005-retrieval-and-embedding.md).

---

## 2. Live exchange rates leaked into answers

**Observed.** Answers quoted specific FX figures scraped from
`/ferdi/valyuta-mezenneleri` and two sibling pages. A rate scraped once and served
later is wrong the moment the rate moves, and the answer carried no way to know.

**Hypothesis.** The rate pages are volatile tables; their *numbers* are the problem,
not the pages. Branch and ATM pages have the same property for opening hours.

**Change.** `has_rate_table` (requires `Alış`, `Satış` and a bare `^\d+\.\d{4}$`
line) plus `strip_rate_numbers` in the scraper, and pointer documents for
`/filiallar` and `/atmler` that send the reader to the live page instead of
answering from a snapshot. Commit `6d12270`.

**Before → after.** Three pages carried live rate figures → 0 figures in the rebuilt
corpus (280 documents; the three pages shrank by 42 characters each, `/atmler` grew
by 272). A live probe of the running stack confirmed the assistant now points at the
rates page rather than quoting a number, and the earlier wrong citation to the
Android privacy policy for branch questions is gone.

---

## 3. The analytics top-sources chart was always empty

**Observed.** `GET /api/v1/analytics/summary` returned an empty `TOP_SOURCES` list
however many interactions existed.

**Hypothesis.** The aggregation joins on fields inside the persisted retrieval JSON;
if the write path stores a different shape, the join matches nothing and fails
silently — an empty chart looks like "no data yet" rather than a bug.

**Change.** Persist the four fields the aggregation reads (`n`, `url`, `score`,
`source_class`) from the prompt sources at write time. Commit `51d08e7`.

**Before → after.** 0 rows returned for any window → the chart populates from the
live corpus; 23 chat tests pass, including one that asserts the aggregation over a
seeded interaction.

---

## 4. Recall was understated because a question could have only one right page

**Observed.** Reading the misses one by one showed that for roughly half of them the
top 5 *did* contain a page answering the question — `kredit max nece aya olur` was
scored a miss although `/onlayn-kredit` ("onlayn kredit müddəti 3 aydan 60 ayadəkdir")
was returned. The measurement, not the retriever, was wrong.

**Hypothesis.** A bank site answers one question on several pages. A single expected
URL per row therefore measures "did we find *my* favourite page", not "did we find an
answer" — and tuning against it would optimise the wrong thing.

**Change.** Multi-URL labels, added only where the page carries every
`must_include_fact`, carries none of `must_not_include`, and reads as an answer
(evidence in `.superpowers/sdd/2026-09-12-abb-assistant/relabel-proposal.md`): a29,
a31, a33, a45. Two facts that had matched in unrelated contexts were tightened at the
same time: a05 `60` → `60 ay` (bare `60` matched "60 günədək güzəşt"), a42
`komissiya` → `komissiyasız nağdlaşdırma` (bare `komissiya` matched transfer fees).
Four rows whose extra pages did *not* survive that rule (a03, a05, a39, a42) kept
their single label. No retrieval code changed. Commit `b7b4443`.

**Before → after** (same corpus, same retriever):

| | before | after |
|---|---|---|
| all | 30/43 (70%) | 34/43 (79%) |
| informal | 9/20 (45%) | 13/20 (65%) |
| right section | 35/43 (81%) | 36/43 (84%) |

**Guard against fooling ourselves.** Relabelling one's own test set can manufacture
any number. So a held-out set of 15 questions written by the project owner was
labelled from corpus *text* probes — never from retrieval output — and is never used
to tune anything: `evals/heldout.jsonl`, 8/14 (57%), right section 11/14 (79%). That
it lands near the golden informal figure (65%) is the evidence that the relabelled
golden set is not overfit. A runner check asserts every labelled page really contains
its own facts, so a bad label shows up as a label error rather than as a miss.

---

## 5. Noise pages were blamed for the remaining misses — the measurement said no

**Observed.** In 3 of the 6 held-out misses a thin SEO stub page (`/pulsuz-debet-kart`)
occupied a top-5 slot, and the misses clustered on operational pages
(`cash-by-code`, `karta-medaxil`, `melumat-merkezi`) that never surfaced.

**Hypothesis.** The corpus's 109 depth-1 stub pages crowd out the page that answers,
so excluding them should raise recall.

**Change.** None — the rule was measured before it was written. Candidate exclusions
were applied to the fused ranking and scored on both sets:

| rule | golden all | golden informal | held-out |
|---|---|---|---|
| ship nothing (current) | 34/43 (79%) | 13/20 (65%) | 8/14 (57%) |
| legal/privacy pages only | 34/43 (79%) | 13/20 (65%) | 8/14 (57%) |
| + stubs under 1,500 chars | 34/43 (79%) | 13/20 (65%) | 8/14 (57%) |
| + stubs under 3,000 chars | 33/43 (77%) | 12/20 (60%) | 8/14 (57%) |
| + all depth-1 stubs | 32/43 (74%) | 11/20 (55%) | 9/14 (64%) |

**Result.** The premise was wrong. Stub pages are often the *correct* answer —
`/asan-kredit-veren-banklar` and `/onlayn-kredit` are both stubs and both are labelled
answers — so any rule strong enough to stop the crowding also deletes real answers.
The last row helps the held-out set and hurts the golden set, which fails the
pre-committed bar of helping both. Nothing shipped; the corpus keeps its stubs.

The misses that remain are intent gaps, not ranking noise: `kartimi itirmisem indi ne
edim?` shares almost no vocabulary with the page that answers it (the information
centre page, which lists "kartın bloklanması və aktiv edilməsi"). Closing them needs
either a hand-written synonym map — which would fit the eval set rather than the
language — or a cross-encoder reranker, which was dropped on cost. Both are recorded
as rejected options in [ADR-0005](adr/0005-retrieval-and-embedding.md), not as work
left undone.
