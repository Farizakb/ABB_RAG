# Corpus scope

What "all textual content" excludes, and why, plus the two pages where the app states a fact
about page existence rather than about ABB's business.

## Excluded sections

"All textual content" means the full body text of a page, not that every URL on the domain is
knowledge. 1,179 procurement tender notices make that argument on their own.

| Section | URLs | Reason |
|---|---|---|
| `haqqimizda/satinalmalar/**` | 1,179 | Procurement tender notices. Pure noise |
| `xeberler/**` | 590 | News. Not product knowledge, and a source of stale figures |
| `kampaniyalar/**` older than 12 months | ~197 | Cannot be active; excluded before a request is made |
| `korporativ-sosial-mesuliyyet/**` | 69 | CSR posts, 31 of them literal duplicates of news articles |
| `press-relizler/**` | 9 | Press releases |
| `/en/**`, `/ru/**` | 4,619 | See below |

**Azerbaijani only, and the reason is retrieval, not cost.** English coverage was measured at 95%
with genuine (not machine) translation, so excluding it is a decision, not a limitation. Indexing
both locales would put near-duplicate content in two languages into one index: both get retrieved
for the same question, both consume the context budget, and cross-document dedup cannot catch
them because the text differs between locales. English and Russian questions are still handled —
the answer mirrors the question's language while citing the Azerbaijani source.

Scraped, kept: 556 URLs fetched at one request per second (about 9.3 minutes) after the sitemap
filter; the shipping corpus holds 280 documents / 736 chunks after the extraction gates.

## Hand-authored pointers

A hand-authored pointer document may state that a page exists and what it lists, and must never
state a product fact. Two exist, both required because the underlying data never reaches the
scraped HTML.

| URL | Asserts | Reason |
|---|---|---|
| `https://abb-bank.az/filiallar` | ABB's branch and ATM network is shown on a map on this page and in the ABB mobile app's "Xidmət şəbəkəsi (filial, şöbə, bankomat)" section, which lists addresses, hours, and ATM points. No address or hour is stated here. | The branch/ATM list is a client-side map widget — addresses never reach the fetched HTML, so no amount of retrieval tuning can recover them. |
| `https://abb-bank.az/atmler` | ABB's ATM locations are shown on the same map. No address is stated here. Merged into the real scraped `/atmler` document (a usage FAQ: deposit methods, limits, commissions) rather than published as a second, competing document at the same URL. | Same cause: no ATM location ever reaches the fetched HTML. The scraped page at this URL answers usage questions only, never "where." |

Both URLs returned HTTP 200 at scrape time and are present in `data/raw` — no URL shown to a
customer is one that was not actually fetched. Without these pointers, branch/ATM questions
resolved to an unrelated page (the Android privacy policy); with them, they resolve correctly.
