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

---

## 6. ADR-0005's numbers had drifted from the corpus that ships

**Observed.** Row 1 of ADR-0005's decision table claimed fused retrieval scored
34/43 (79%) on the shipped configuration; row 5, describing that same fused
configuration, claimed 30/43. Both rows describe one system on one corpus — they
cannot both be true.

**Hypothesis.** The ADR's Status section said every number was re-run on corpus
`90e08090` (280 documents). The artifact that actually ships is
`fixtures/corpus_sample.json` — corpus
`713ea0871c2f68f7662420bdd03d9d89fbc8e7b7fa610992a3100f1ddfbcf8ed`, 243 documents,
560 chunks — a different, later corpus that `make demo` and the committed
`evals/report.md` both use. The ADR was last measured before that corpus existed
and was never re-checked against it; the 34/43-vs-30/43 split is exactly what
appears if some rows were updated against the shipping corpus (30/43 matches
`evals/report.md`'s committed hit@5 of 0.698 exactly) while the table and
Consequences prose elsewhere were not.

**Change.** `scripts/ablate_retrieval.py`, which imports `retrieval.py`'s own
`DENSE_DOCS`, `FTS_DOCS`, `TRGM_DOCS`, `_rrf`, `_tsquery`, `_fold` and
`RANK_DEPTH` (never modifying that module) and re-runs items 1, 2 and 4 — the
retrieval-only questions, no generation involved — as isolated per-leg SQL
against the corpus that ships, over the 43 `evals/golden.jsonl` rows carrying
`expected_source_urls`. Item 3 is restated with its original corpus named; item 5
(the embedder bake-off) is carried forward unchanged, with a sentence saying why
it was not re-run (re-running it means writing a second `embedding_model` index
into the live database — Invariant 8). ADR-0005 rewritten in place so every
"Measured" cell names the corpus it came from.

**Before → after** (fused hit@5, the ADR's headline number):

| | before (claimed, corpus `90e08090`, internally contradictory) | after (measured, corpus `713ea087…`) |
|---|---|---|
| fused, all | 34/43 (79%) [row 1] vs 30/43 (70%) [row 5] | 30/43 (70%) |
| informal | 65% | 45% |
| right-section | 84% | not reproducible from committed data |
| held-out | 57% | 57% (corpus `90e08090…`, not re-measured — out of this task's scope) |

The corrected 30/43 (0.698) is not a new number invented for this entry — it
matches the committed `evals/report.md`'s retrieval hit@5 exactly, which is the
cross-check that makes it trustworthy rather than merely convenient.

One further, unplanned finding came out of re-running item 2 (stub exclusion) on
the shipping corpus: the pre-committed rule ("out if product hit@5 drops at all")
now points the other way. Excluding stub pages *raises* fused hit@5 here (30/43 →
32/43, product subset 30/39 → 32/39) instead of lowering it, the opposite of what
the retired corpus showed. That reversal is recorded in ADR-0005 Item 2 as an open
finding, not acted on here: implementing it means editing `retrieval.py`, which
this task did not touch.

---

## 7. Entry 6's correction was itself measured against the artifact about to be retired

**Observed.** Hashing every corpus artifact on disk
(`corpus_id = sha256(json.dumps(sorted(d.content_hash for d in documents)))`,
`backend/shared/contracts/models.py:61-62`) against `rag.corpora` found three
files, not two. `fixtures/corpus_sample.json` (243 docs, `713ea0871c2f…`) and
`data/corpus_20260913T163802Z.json` (also 243 docs, same id — a duplicate
artifact) both matched a corpus ingested at `2026-09-15 15:35:10Z`.
`data/corpus_20260915T151437Z.json` (280 docs, `90e08090d305…`) matched a
*different*, already-`ready` corpus ingested twenty minutes earlier, at
`15:15:33Z`. The 243-document fixture was not stale relative to a corpus that
predates it — it was made the fixture *after* a larger corpus was already live.
That makes it a regression, not a stale copy, and it was found by hashing
artifacts against the database, not by reading `README.md` or `docs/adr/0005`,
both of which had named the missing pointer documents by URL the whole time.

**Hypothesis.** Entry 6 corrected ADR-0005's numbers to 30/43, "measured on the
shipping corpus `713ea087…`" — every sentence in it was true when it was written.
Its re-measurement was necessarily performed against whichever artifact
`fixtures/corpus_sample.json` was at the time, which was `713ea087…`. What entry 6
could not know is that `713ea087…` being the fixture was not settled fact: ruling
P155 had proven the 30/43 figure was correctly measured against the artifact that
then shipped, and consistent with the committed report — but ruling P164, that a
larger, already-`ready` corpus had been ingested *before* `713ea087…` even
existed, had not yet been established. Proving P164 is what reverses entry 6's
conclusion. Entry 6's own method was not wrong; the artifact it measured was about
to be retired.

**Change.** `fixtures/corpus_sample.json` replaced with
`data/corpus_20260915T151437Z.json` (280 documents, 736 chunks,
`corpus_id = 90e08090d30552fc939ea2c78e8c6248a7aa87af1970906b2dcdd0e78898fde9`).
`scripts/ablate_retrieval.py` re-run against `90e08090…`; `evals/report.md`
regenerated against it. No re-ingest was needed — `90e08090…` was already `ready`
in `rag.corpora`, confirmed by a no-op run of `scripts/ingest_fixture.py` that
returned the corpus id immediately with no embedding calls logged.

**Before → after**, both corpora named:

| | `713ea087…` (243 docs, entry 6's figure) | `90e08090…` (280 docs, shipping now) |
|---|---|---|
| fused, all | 30/43 (70%) | 34/43 (79%) |
| informal | 45% | 65% |
| product subset, with stubs | 30/39 (77%) | 33/39 (85%) |
| pointer documents present | neither `/filiallar` nor `/atmler` | both, confirmed in `rag.documents` |

Entry 6 above is left exactly as written. It was correct given what P155 alone
had established, and a log that edits its own history to look consistent after
the fact is worth nothing.

**The user-visible cost.** The two pointer documents are what Task 23 Item 3
added specifically to stop branch and ATM questions being answered from
`/android-privacypolicy`. The shipped fixture had neither. A reviewer asking
where their nearest branch is, against the corpus that was about to ship, would
have received exactly the `/android-privacypolicy` answer Task 23 existed to
eliminate — the regression this entry corrects was not cosmetic, it was the
specific failure mode ADR-0005 Item 3 exists to prevent.
