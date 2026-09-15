# ABB Assistant

## Hand-authored pointers

SPEC §5.5's constraint: a hand-authored pointer document may state that a page
exists and what it lists, and must never state a product fact. This section
names every one, as required.

| URL | Asserts | Reason |
|---|---|---|
| `https://abb-bank.az/filiallar` | ABB's branch and ATM network is shown on a map on this page and in the ABB mobile app's "Xidmət şəbəkəsi (filial, şöbə, bankomat)" section, which lists addresses, hours, and ATM points. No address or hour is stated here. | The branch/ATM list is a client-side map widget -- addresses never reach the fetched HTML, so no amount of retrieval tuning can recover them. Measured: `/filiallar` is in `data/raw`, contains exactly one occurrence of "ünvan," and zero street addresses. |
| `https://abb-bank.az/atmler` | ABB's ATM locations are shown on the same map, on the filiallar page and in the mobile app. No address is stated here. Merged into the real scraped `/atmler` document (a usage FAQ: deposit methods, limits, commissions) rather than published as a second, competing document at the same URL. | Same cause: no ATM location ever reaches the fetched HTML. The scraped page at this URL answers usage questions only, never "where." |

Both URLs returned HTTP 200 at scrape time and are present in `data/raw`
(invariant 12: no URL shown to a customer may be one we did not actually
fetch). Task 23 Item 3 measured, live, that the §5.5 bank-facts document does
not produce a plausible grounded answer to a branch or ATM location question
-- the assistant either refused, citing unrelated pages, or answered citing
the Android privacy policy -- which is what triggered these two pointers.
