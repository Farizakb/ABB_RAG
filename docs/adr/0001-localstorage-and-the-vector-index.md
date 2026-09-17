# ADR-001: localStorage and the vector index

## Status

Settled, and confirmed live. The apparent conflict — one requirement calls for the extracted data
to live in the browser's `localStorage`; another calls for it formatted into a vector database —
is resolved by treating them as two different writes of two different things, not one artifact
doing two jobs. The measurement this ADR rests on was taken live against the running stack on 
2026-09-16, against the shipping corpus (`90e08090…`, 280 documents).

## Context

`localStorage` and Postgres/`pgvector` are not competing answers to "where does the data live" —
they hold different things. `localStorage` holds the corpus the user uploaded, verbatim, so a
reload can re-process without hunting for the file again. Postgres holds the *derived* index: the
chunks, embeddings and governed facts built from that corpus at ingest. Both requirements hold at
once because they describe two different writes: the browser write happens first, untouched by
the server; the database write happens second, when the user clicks "Process dataset."

The real open question was not *whether* to write the full corpus to `localStorage`, but whether
it would *fit* — and the answer depends on which unit you measure in. A file's size on disk is
UTF-8 bytes. Browsers account `localStorage` quota in **UTF-16 code units**, because that is
JavaScript's native string representation — `JSON.stringify(localStorage).length * 2` is the
actual cost, not `stat`'s byte count. For ASCII-light, diacritic-heavy Azerbaijani text, that is
close to double the on-disk figure. Quoting the disk size and calling it the quota cost would have
been quietly wrong by roughly a factor of two.

## Decision

Write the **full corpus verbatim** to `localStorage`, under `abb.corpus`, plus a small
`abb.corpus.manifest` key (corpus id, hash, page count, ingest status) that drives the "already
processed, go to chat" fast path on reload — and measure the real cost in the unit the browser
actually charges before claiming it fits.

Measured live (`http://localhost:8080`, corpus `90e08090…` loaded fresh through the Data
screen's file picker):

| Source | Figure |
|---|---|
| Data screen's own "localStorage used" readout | 1.73 MB (UTF-16) |
| Browser console, `JSON.stringify(localStorage).length * 2` | **1,729,290 bytes** |
| Corpus body alone, `len(json.dumps(corpus, ensure_ascii=False)) * 2` | 1,699,724 bytes (1.70 MB) |
| Percentage of a 5 MB (5,242,880-byte) budget | **33.0%** |

The two measurements — the Data screen's own readout and an independent console cross-check —
agree exactly, and the delta between the corpus body alone and the whole store (29,566 bytes) is
consistent with the manifest key and `localStorage`'s own JSON-escaping overhead, not a
discrepancy to explain away.

**On quota exceeded:** catch the write failure, drop the corpus body, keep the manifest, show a
one-line notice. An upload must never fail outright over a browser storage limit.

**Fallback, not taken:** if the measured figure had landed above roughly 4 MB consumed — close
enough to the 5 MB ceiling that a second, larger corpus or a future embedder change could tip it
over — the design would fall back to manifest-plus-bounded-preview instead of the full body. It
did not fire: 1.73 MB is far below that trip point, with headroom to spare.

## Consequences

- A reviewer who reloads the page can re-process the same corpus without re-selecting the file,
  because the full body survived the reload in `localStorage`.
- The measured figure (33% of budget) leaves real headroom for a larger corpus before the
  fallback would need to exist in code rather than only in this ADR.
- The number in this ADR is a **lower bound in one sense and an exact figure in another**: it is
  the exact cost of this corpus, on this browser, today; it is not a claim about every possible
  corpus this app could ever be given. A corpus several times this size would need this ADR
  revisited, not assumed to still fit.
- Because the measurement was taken **live**, against the running stack, rather than estimated
  from the file on disk, the number in the README and here is not a projection — it is what a
  reviewer's own browser will show if they repeat the same steps.

## Rejected

- **Manifest-plus-preview, decided before measuring.** The cautious-sounding option — never write
  the full corpus, just a manifest and a bounded preview, because "5 MB is not a lot" — was
  available and deliberately not taken until the real number existed. Choosing it
  pre-emptively would have satisfied the localStorage requirement's letter while quietly
  under-delivering its spirit: the requirement says "store it," not "store a summary of it."
- **Quoting the file's on-disk byte size as if it were the quota cost.** This was the actual trap:
  a corpus that is "1.1 MB on disk" and a corpus that costs "1.73 MB of `localStorage` quota" are
  the same file, measured in the wrong and the right unit respectively. Reporting the disk figure
  in the README would have been defensible-sounding and wrong by roughly 2×, and wrong in the
  direction that hides risk rather than reveals it.
