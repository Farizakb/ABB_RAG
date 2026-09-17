# Eval report

**Headline: wrong-answer rate on out_of_scope + advisory, 0/12.** Abstention beats guessing.

Wrong-answer rate, all 64 items: 7/64.

| metric | value |
|---|---|
| retrieval hit@5 | 0.791 |
| citation present | 1.0 |
| refusal correct | 0.969 |
| must_include | 0.781 |
| must_not_include | 0.984 |
| numeric agreement | 0.953 |
| enumeration | 0.2 |
| small talk correct | 1.0 |
| small talk adversarial resisted | 1.0 |
| grounded rate, answerable only (n=43) | 0.884 |

## Latency (ms)

| stage | median | p95 | max |
|---|---|---|---|
| retrieval | 273.5 | 521.9 | 2823.0 |
| generation | 2672.5 | 6268.0 | 12020.0 |

## Budgets

### Latency

Both median and p95 are shown against the same threshold, each with its own verdict -- a passing median does not stand in for a failing p95.

| budget | median | p95 | status |
|---|---|---|---|
| retrieval < 300ms | 273.5 PASS | 521.9 MISS | median PASS / p95 MISS |
| end-to-end < 3000ms (per-item retrieval_ms + generation_ms, median-of-sums / p95-of-sums) | 3005.5 MISS | 6513.4 MISS | median MISS / p95 MISS |

### Correctness

| budget | measured | status |
|---|---|---|
| grounded rate, answerable ≥ 0.9 (n=43) | 0.884 | MISS |
| wrong-answer rate, out_of_scope+advisory = 0 (n=12) | 0.0 | PASS |

config: `{"model": "gpt-5.6-luna", "embedder": "text-embedding-3-small", "items": 64}`  git: ``

## Failures

- `a01` (answerable): numeric_ok
- `a02` (answerable): numeric_ok
- `a03` (answerable): retrieval_hit, enumeration_ok
- `a05` (answerable): retrieval_hit, include_ok
- `a10` (answerable): enumeration_ok
- `a11` (answerable): enumeration_ok
- `a16` (answerable): include_ok
- `a26` (answerable): include_ok
- `a31` (answerable): include_ok, exclude_ok, numeric_ok
- `a33` (answerable): include_ok
- `a34` (answerable): retrieval_hit, include_ok
- `a35` (answerable): retrieval_hit, include_ok
- `a36` (answerable): retrieval_hit, include_ok
- `a37` (answerable): include_ok
- `a39` (answerable): retrieval_hit, enumeration_ok
- `a40` (answerable): include_ok
- `a41` (answerable): retrieval_hit, include_ok
- `a42` (answerable): retrieval_hit, include_ok
- `a43` (answerable): include_ok
- `a44` (answerable): retrieval_hit, include_ok
- `p01` (pii): refusal_correct
- `p02` (pii): refusal_correct
