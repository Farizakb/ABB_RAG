# backend/scraper/tests/test_campaigns.py
# ruff: noqa: RUF001, RUF002 -- this file quotes genuine Azerbaijani fixture text.
from datetime import date
from pathlib import Path

from abb_scraper.campaigns import classify, parse_range
from abb_scraper.extract import content_blocks, extract_page

TODAY = date(2026, 9, 14)
RAW = Path("backend/scraper/tests/fixtures/raw")


def load(name: str) -> str:
    return (RAW / f"{name}.html").read_text("utf-8")


def test_parses_the_fixed_dd_mm_yyyy_range() -> None:
    assert parse_range("Qeyd* 01.12.2021 - 10.01.2022 Aktiv deyil") == (
        date(2021, 12, 1),
        date(2022, 1, 10),
    )


def test_expired_when_end_date_precedes_today() -> None:
    c = classify("01.12.2021 - 10.01.2022", TODAY)
    assert c.status == "expired"


def test_active_when_end_date_is_in_the_future() -> None:
    c = classify("01.09.2026 - 31.10.2026", TODAY)
    assert c.status == "active" and c.valid_to == date(2026, 10, 31)


def test_active_when_valid_to_is_exactly_today() -> None:
    """Fix round 1 finding: `end >= today` had no test pinning its boundary --
    mutating it to `end > today` left the full suite green. An offer whose
    last valid day is today (2026-09-14, the same injected TODAY used
    throughout this file, not `date.today()`) must still read as active: it
    is still valid on its last day, and flipping it to expired a day early
    is exactly the silent-drop risk invariant 9 exists to prevent."""
    c = classify("01.09.2026 - 14.09.2026", TODAY)
    assert c.status == "active" and c.valid_to == TODAY


def test_unknown_when_start_date_is_in_the_future() -> None:
    """A campaign that hasn't started yet is not
    active -- telling a customer an offer is available when it isn't is a
    factual error about a financial product -- and not expired either, so
    it classifies unknown. Ingest drops unknown and reports the count, so
    an upcoming campaign is withheld rather than misrepresented, visibly
    rather than silently."""
    c = classify("01.10.2026 - 31.10.2026", TODAY)
    assert c.status == "unknown"


def test_active_when_start_date_is_exactly_today() -> None:
    """Boundary for the future-start guard: `start > today`, not `start >= today` --
    a campaign launching today is active on its own launch day, not
    withheld as unknown. Pairs with test_active_when_valid_to_is_exactly_today
    as the other end of the same window."""
    c = classify("14.09.2026 - 31.10.2026", TODAY)
    assert c.status == "active"


def test_expiry_marker_on_a_future_dated_campaign_still_wins() -> None:
    """Marker precedence survives the future-start addition: the marker branch
    runs before the future-start check, so an explicit expiry marker on a
    page whose date range is entirely in the future still reports expired,
    not unknown."""
    c = classify("01.10.2026 - 31.10.2026 Aktiv deyil", TODAY)
    assert c.status == "expired"


def test_explicit_expiry_marker_confirms_but_does_not_replace_the_date_check() -> None:
    """Active pages carry no positive marker, so the date is the decision and the
    marker is corroboration only."""
    assert classify("Kampaniya artıq bitmişdir", TODAY).status == "expired"
    assert classify("01.09.2026 - 31.10.2026 Aktiv deyil", TODAY).status == "expired"


def test_no_date_and_no_marker_is_unknown_not_active() -> None:
    assert classify("Kredit kampaniyası", TODAY).status == "unknown"


def test_kampaniya_active_resolves_the_campaigns_own_range_not_a_phase() -> None:
    """kampaniya-active.html carries five distinct dd.mm.yyyy
    ranges once comment-node fragmentation is bridged by extraction, not
    four: a campaign-validity badge (calendar icon + "09.09.2026 -
    05.06.2027", rendered in a <p class="...text-content-tertiary"> right
    under the page title) plus four internal "Mərhələ" (phase) sub-ranges
    narrated later in the prose body (09.09.2026-29.10.2026,
    30.10.2026-28.12.2026, 29.12.2026-01.03.2027, 02.03.2027-05.06.2027).

    The badge is the campaign's own validity window -- it is literally the
    union of the four phase ranges (spans phase 1's start to the final
    phase's end) and is displayed as the page's own date-range metadata,
    structurally separate from the narrative body. It also happens to be
    first in document order, ahead of every phase range, so first-match-wins
    recovers it correctly here: the phase ranges are internal narrative
    detail, not competing candidates for the campaign's validity window.

    This is a standing check against the real fixture (not a hand-built
    string) so a change to extract.py's block ordering that silently
    reordered the badge behind the prose would turn this test red.
    """
    html = load("kampaniya-active")
    path = "/kampaniyalar/abb-play-master-liqa-il-futbol-h-y-cani-qayidir"

    blocks, _ = content_blocks(html, path)
    body = "\n".join(b.text for b in blocks)

    # Proof the fixture really carries all five ranges the finding relies on,
    # so "picks the right one" is a real discrimination and not vacuous.
    phase_ranges = [
        "09.09.2026 – 29.10.2026",
        "30.10.2026 – 28.12.2026",
        "29.12.2026 – 01.03.2027",
        "02.03.2027 – 05.06.2027",
    ]
    for phase in phase_ranges:
        assert phase in body, f"fixture no longer contains phase range {phase!r}"
    assert "09.09.2026 - 05.06.2027" in body

    assert parse_range(body) == (date(2026, 9, 9), date(2027, 6, 5))

    page = extract_page(html, url=f"https://abb-bank.az{path}")
    assert parse_range(page.text) == (date(2026, 9, 9), date(2027, 6, 5))

    inside_the_window = date(2026, 11, 1)
    c = classify(page.text, inside_the_window)
    assert c.status == "active"
    assert c.valid_from == date(2026, 9, 9)
    assert c.valid_to == date(2027, 6, 5)


def test_kampaniya_active_expiry_marker_is_not_attested_in_extracted_text() -> None:
    """Neither expiry marker reaches extracted text on this
    fixture: "Kampaniya artıq bitmişdir" is absent from the raw HTML too (so
    it has no presence proof and is not asserted here), and "Aktiv deyil"
    is present in the raw HTML only as the JSON i18n fragment
    '"inactive":"Aktiv deyil"' inside a <script> blob, which content_blocks
    strips before block extraction ever runs.

    The absence assertion below is paired with proof the raw fixture really
    contains the string: without that proof, an absence assertion
    against a string the fixture never had would pass regardless of whether
    the code does anything."""
    html = load("kampaniya-active")
    # The literal raw bytes carry an escaped JSON string (a Next.js RSC
    # payload embedded as a JS string literal), backslashes and all.
    assert '\\"inactive\\":\\"Aktiv deyil\\"' in html  # proof of presence, raw HTML

    page = extract_page(html, url="https://abb-bank.az/kampaniyalar/abb-play")
    assert "Aktiv deyil" not in page.text  # absence, extracted text

    # The campaign is genuinely still active (classify agrees), which is the
    # scenario invariant 9 exists to protect: no marker means the date
    # arithmetic alone must make the call, and it must get it right.
    assert classify(page.text, date(2026, 11, 1)).status == "active"
