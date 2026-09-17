# ADR-006: Governed values and derived links

## Status

Partially recorded. This ADR covers several related decisions named together in
`SPEC.md` §19 (why numbers do not live only in vectors, why there is no numeric
router, why a synthetic index is built only where ABB has not already built
one, and why every user-facing URL is derived from a retrieved document rather
than written by the model). The sub-decision below — the synthetic-index one —
is settled, by measurement, as of Task 12. The others in that list are settled
by earlier or later tasks and are not restated here to avoid claiming a
measurement this task did not make.

## Context

`SPEC.md` §5.5 describes two kinds of synthetic document assembled at ingest
from already-scraped field values: a bank-facts document (one
`BankOrCreditUnion` ld+json block, taken once from the homepage) and one index
document per "enumerable" class (active campaigns; products, one per
`ferdi`/`biznes` sub-section) — because top-*k* dense retrieval cannot answer
an enumeration question ("hansı kreditləriniz var") correctly: similarity
search returns *k* items in no meaningful order, and the model would present
that arbitrary subset as the complete answer.

§5.5 also pre-commits a rule, in writing, before any index-generation code:
**if ABB's own listing page already enumerates its children in retrievable
text, no synthetic index is built for that class.** ABB runs that query
server-side; using their page keeps ordering and maintenance on their side,
and — the decisive reason — makes the synthetic answer incapable of
disagreeing with `§11.2`'s footer link, which points at that same page.

Task 12 measured this directly rather than assuming `RECON`'s prediction held.
Per controller ruling P49, the no-network rule was lifted narrowly to fetch
exactly the two URLs §5.5 names — `https://abb-bank.az/kampaniyalar` and
`https://abb-bank.az/ferdi/kreditler` — once each, through the existing
`Fetcher` (robots.txt honoured, 1 req/s + jitter). Both responses are
committed as `backend/scraper/tests/fixtures/raw/listing-kampaniyalar.html` and
`backend/scraper/tests/fixtures/raw/listing-ferdi-kreditler.html`, so the measurement is
reproducible offline and is never re-fetched. Full detail, including the
structural check that ruled out a §5.3 chrome-stripper bug as an alternative
explanation, is in `RECON.md`'s dated "Task 12" day-two heading.

**Measured, not assumed:**

| Class | Listing URL | Result | Extracted chars | Enumerates |
|---|---|---|---|---|
| product | `/ferdi/kreditler` | HTTP 200 | 2,083 (> 400-char §5.3 gate) | 6/6 real product-card titles survive extraction = **1.00 >= 0.60** |
| campaign | `/kampaniyalar` | **HTTP 404** | n/a — page does not exist | 0 children, cannot clear the gate = **0.00 < 0.60** |

The product page names every one of its six real body-content product cards
(each a title block immediately followed by a value-then-label stat block,
the same shape as `nagd-kredit.html`) in text that survives `extract_page`. A
seventh same-prefix link found in raw HTML, `/ferdi/kreditler/ipoteka-krediti`
("İpoteka"), was checked structurally rather than assumed lost to a stripper
bug: its ancestor chain terminates in `footer#footer`, i.e. it is a genuine
site-wide footer sitemap link, not a body enumeration this listing failed to
render.

The campaign listing, by contrast, does not exist at the expected URL: a
direct fetch returns HTTP 404. `kampaniya-active.html`'s
own breadcrumb (already on disk, no extra fetch) links to
`/ferdi/kampaniyalar`, not `/kampaniyalar` — weak corroborating evidence that
ABB's real campaigns hub, if one exists, lives at a different path. Per the
controller ruling's two-URL budget, that alternate path was not fetched to
confirm it; this is an open question, not a resolved one (see Consequences).

**Fix round 1 (controller ruling P50).** The coordinator hypothesized the 404
was a missing-trailing-slash artifact and authorized one more fetch,
`https://abb-bank.az/kampaniyalar/`. Result: still HTTP 404 — the server
redirects the trailing-slash form onto the bare one, which is the same 404 as
before (`backend/scraper/tests/fixtures/raw/listing-kampaniyalar.html`, byte-identical, re-saved
under the same name per the ruling). Re-measured through
`extract_page`/`listing_enumerates` rather than a raw-HTML grep: still
**0.00 < 0.60**. The coordinator's supporting evidence (a claimed grep of
`data/sitemap.xml` finding the trailing-slash form 13 times and no
`/ferdi/kampaniyalar` at all) does not reproduce against the same cached
file: re-grepped locally (no network) and found **zero** `<loc>` entries for
any bare `/kampaniyalar` or `/kampaniyalar/` parent under any locale, 247
`kampaniyalar/<slug>` children (matching `RECON.md` §3's independently
measured count exactly), and `/ferdi/kampaniyalar` present once per locale
(3 matches) — corroborating fix round 0's breadcrumb-based finding, not
refuting it. Exact grep commands and counts are in `RECON.md`'s "Task 12 fix
round 1" heading. This discrepancy is recorded, not adjudicated, here.

**Fix round 2 (controller ruling P51) — resolved.** The coordinator re-ran
their sitemap check with an exact-match query and confirmed fix round 1's
re-grep was correct: over all 7,042 `<loc>` entries, `/kampaniyalar` (bare)
appears 0 times, `/ferdi/kampaniyalar` appears 1 time, and 247 entries start
with `/kampaniyalar/` (children only — the hub itself is absent). Their
fix-round-1 "13 occurrences" figure was a regex artifact: a character class
that stopped at digits/uppercase truncated slug children like
`/kampaniyalar/abb-play-2026` down to `/kampaniyalar/` and miscounted them
as hub hits. This 247-children/0-hub asymmetry is exactly why the wrong URL
looked plausible, and is a useful general lesson (a section can be heavily
populated in a sitemap with no index page of its own).

Authorized fetch of `https://abb-bank.az/ferdi/kampaniyalar`: **HTTP 200**,
593,870 bytes, saved as `backend/scraper/tests/fixtures/raw/listing-ferdi-kampaniyalar.html` and
committed. `listing-kampaniyalar.html` (the two prior 404
bodies) deleted — a committed 404 is a trap for the next reader once
superseded. Re-measured through `extract_page`/`content_blocks`: the page is
real but a **client-rendered shell** — 5 content blocks total (`h1
"Kampaniyalar"`, `p "Ən son kampaniyalar"`, three generic ABB-mobile-app
promo blocks), **320 extracted chars, under the §5.3 400-char gate**, and
zero `/kampaniyalar/<slug>` child links anywhere in its raw HTML. So
`listing_enumerates == 0.00 < 0.60` — the campaign index is kept, and this
time on two independent grounds (no enumeration, and the page would be
gate-dropped regardless).

The campaign index's anchor is now `https://abb-bank.az/ferdi/kampaniyalar`
— a real, HTTP-200 URL. The citation-resolution risk carried since fix round
0 is resolved: the previous two rounds' rejection of the bare and
trailing-slash spellings (both confirmed 404) was correct, not overcautious.

## Decision

Per §5.5's table (one class at/above the 0.60 threshold, one class below —
in fact unreachable): `index_documents` is written **for the campaign class
only**. The product branch (a `section_path`-keyed index, one per
`ferdi`/`biznes` sub-section) was never written at all — the losing option is
deleted before being written, per `SPEC.md` §8.3's rule, not left behind a
flag or a commented-out block. `bank_facts_document` and `listing_enumerates`
are unconditional and both exist regardless of this measurement;
`listing_enumerates` is the measurement's own evidence function, re-checked on
every ingest via `_abb_already_enumerates` (not a one-off gate), so a corpus
scraped after ABB ships a working product-enumeration or campaign-listing page
does not silently grow or keep a competing index.

Implementation: `backend/scraper/abb_scraper/synthetic.py`. Tests:
`backend/scraper/tests/test_synthetic.py` — 2 unconditional tests plus 7 of
8 planned conditional tests (the product-section test is dropped along
with the product branch it would have exercised).

## Consequences

- The campaign index's citation is anchored to `https://abb-bank.az/ferdi/kampaniyalar`
  (fix round 2; SPEC §5.5's originally-named `/kampaniyalar` was confirmed
  absent from the site — two direct 404s, and 0 of 7,042 sitemap `<loc>`
  entries, even though 247 of its own children are present there), pinned
  verbatim by `test_index_is_anchored_to_a_real_listing_page_so_the_citation_resolves`.
  **This URL is confirmed HTTP 200 by direct fetch** — the citation-
  resolution risk carried since fix round 0 is resolved. The page itself is
  a client-rendered shell content-wise (320 extracted chars, under the §5.3
  400-char gate) — a separate, already-handled concern (the gate exists
  precisely for pages like this), not a citation-resolution defect: a real
  visitor following the footer link lands on a real page, even though our
  scraper (no JS execution) cannot recover its campaign list from that same
  fetch.
- No product index exists. A product enumeration question ("hansı
  kreditləriniz var") is answered by retrieval finding `/ferdi/kreditler`
  itself as an ordinary chunk — which is the intended outcome, not a gap:
  ABB's own page already answers that question, with better ordering and
  maintenance than a synthetic copy could offer.
- `_abb_already_enumerates` is a runtime guard, not a one-time flag: if a
  future scrape (Task 13 onward) finds `/ferdi/kreditler` no longer names its
  products, or finds `/ferdi/kampaniyalar` server-rendering its campaign list
  (it is currently a client-rendered shell), `index_documents` changes its
  output automatically on the next ingest, without a code change.
