# backend/scraper/abb_scraper/campaigns.py
# ruff: noqa: RUF001, RUF002 -- genuine Azerbaijani text/dashes (dotless-i, en
# dash), not ambiguous-character typos; see test_extract_region.py for the
# same convention.
from __future__ import annotations

import re
from datetime import date
from typing import Literal, NamedTuple

RANGE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})\s*[-–—]\s*(\d{2})\.(\d{2})\.(\d{4})")
EXPIRED_MARKERS = ("Kampaniya artıq bitmişdir", "Aktiv deyil")


class CampaignStatus(NamedTuple):
    status: Literal["active", "expired", "unknown"]
    valid_from: date | None
    valid_to: date | None


def parse_range(text: str) -> tuple[date | None, date | None]:
    """First `dd.mm.yyyy - dd.mm.yyyy` range in document order.

    First-match-wins is correct, not merely convenient: on
    kampaniya-active.html -- the one sampled fixture whose body narrates
    internal campaign "phase" sub-ranges in prose -- ABB renders the
    campaign's own validity window as a separate calendar-icon badge
    directly under the page title, ahead of the prose in document order.
    That badge's range (09.09.2026 - 05.06.2027, the union of the four
    phase ranges) is therefore the first match in extracted text, and the
    four phase ranges that follow it are correctly ignored.
    """
    m = RANGE.search(text)
    if m is None:
        return (None, None)
    d1, m1, y1, d2, m2, y2 = (int(g) for g in m.groups())
    return (date(y1, m1, d1), date(y2, m2, d2))


def classify(text: str, today: date) -> CampaignStatus:
    """Derived at scrape time, never curated.

    An expiry marker takes precedence over date arithmetic when present.
    As measured, neither marker is currently attested
    in extracted text on any of the 11 sampled fixtures: "Kampaniya artıq
    bitmişdir" occurs zero times anywhere, raw or extracted; "Aktiv deyil"
    occurs only as the JSON fragment '"inactive":"Aktiv deyil"' inside a
    <script> i18n blob, which content_blocks strips before block
    extraction, so it never reaches PageText.text either. The branch is
    kept regardless: an unattested guard is cheaper than the alternative if
    the site ever does render one of these markers into visible text --
    silently publishing an expired financial offer.

    A campaign whose `valid_from` is still in the future (strictly after
    `today`; a campaign that starts today is active, not unknown) also
    classifies `unknown`, not `active`.
    The status enum is deliberately fixed at active/expired/unknown -- a
    fourth "upcoming" value would ripple into the DB enum and the contracts
    package -- so `unknown` does double duty as "not yet decidable either
    way": a not-yet-started campaign is plainly not `active` (telling a
    customer an offer is available when it isn't is a factual error about a
    financial product) and just as plainly not `expired`. Because the later
    ingest step drops `unknown` and reports the count, an upcoming campaign
    is withheld rather than misrepresented, and its exclusion is visible in
    the drop report rather than silent. This check sits after the marker
    branch, not before it, so an explicit expiry marker on a future-dated
    page still wins and reports `expired` -- marker precedence is
    unaffected by this addition.
    """
    start, end = parse_range(text)
    if any(mark in text for mark in EXPIRED_MARKERS):
        return CampaignStatus("expired", start, end)
    if end is None:
        return CampaignStatus("unknown", start, end)
    if start is not None and start > today:
        return CampaignStatus("unknown", start, end)
    return CampaignStatus("active" if end >= today else "expired", start, end)
