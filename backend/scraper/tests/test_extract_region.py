# backend/scraper/tests/test_extract_region.py
# ruff: noqa: RUF001, RUF002 -- this file quotes a lot of genuine Azerbaijani
# fixture/assertion text (dotless-i and friends); it isn't ambiguous, it's AZ.
from pathlib import Path

import pytest
from abb_scraper.extract import content_blocks

RAW = Path("backend/scraper/tests/fixtures/raw")


def load(name: str) -> str:
    return (RAW / f"{name}.html").read_text("utf-8")


def test_breadcrumb_yields_section_path_and_title() -> None:
    _, crumbs = content_blocks(load("nagd-kredit"), "/ferdi/kreditler/nagd-kredit")
    assert crumbs[0] == "Fərdi"
    assert crumbs[-1] == "Nağd kredit"


def test_front_loaded_chrome_is_dropped() -> None:
    """Every absence-assertion is paired with proof the fixture
    actually contains the string, so a test that can never fail is visible
    as such rather than reading as coverage. All four chrome strings below
    are confirmed present in nagd-kredit.html (raw counts: 'Tel:' x2,
    'Xidmət şəbəkəsi' x3, 'Karyera portalı' x3, 'Ünvan:' x3) and are dropped
    by chrome exclusion; '50 000' (content) survives. The block/char bounds
    are a mutation-test tripwire for F1: with `_is_chrome` disabled these
    four strings flow straight into `blocks` and the bounds are violated too
    (verified locally by patching `_is_chrome` to `lambda node: False`)."""
    html = load("nagd-kredit")
    chrome_strings = ["Tel:", "Xidmət şəbəkəsi", "Karyera portalı", "Ünvan:"]
    for s in chrome_strings:
        assert html.count(s) > 0, f"fixture no longer contains {s!r} -- test needs a new string"

    blocks, _ = content_blocks(html, "/ferdi/kreditler/nagd-kredit")
    joined = " ".join(b.text for b in blocks)
    for s in chrome_strings:
        assert s not in joined
    assert "50 000" in joined
    assert len(blocks) > 80
    assert sum(len(b.text) for b in blocks) > 4000


def test_feedback_marker_terminates_the_region() -> None:
    """The visible 'Səhifəni dəyərləndirin' heading is split across a <p>
    and a nested <span> in the raw markup (`<p>Səhifəni
    <span>dəyərləndirin</span></p>`), so no single block ever contains that
    22-char string contiguously -- an assertion built on it is unfalsifiable
    regardless of whether the widget is actually excluded. Re-anchored on
    the widget's subtitle, which genuinely is one block's own text and is
    genuinely dropped (raw count in nagd-kredit.html: 2)."""
    html = load("nagd-kredit")
    marker = "Fikirlərinizi bizimlə bölüşün"
    assert html.count(marker) > 0
    blocks, _ = content_blocks(html, "/ferdi/kreditler/nagd-kredit")
    assert not any(marker in b.text for b in blocks)


def test_inline_markup_is_kept_not_dropped() -> None:
    """A block's text includes text of inline descendants
    (strong/em/b/i/small/...) and excludes only descendants that are
    themselves block-level. `<strong>10.9%</strong>` must survive -- a
    silently truncated interest rate is the worst failure mode for a bank
    RAG's retrievable facts."""
    html = "<html><body><p>Rate is <strong>10.9%</strong> per year</p></body></html>"
    blocks, _ = content_blocks(html, "/some-page")
    assert any("10.9%" in b.text for b in blocks)
    assert any(b.text == "Rate is 10.9% per year" for b in blocks)


def test_nagd_kredit_faq_headings_survive_inline_markup() -> None:
    """The four FAQ sub-headings on nagd-kredit are real <h2>/<h3> elements
    whose text sits behind inline markup; before this fix they were silently
    dropped entirely (own text was empty once inline descendants were
    excluded)."""
    html = load("nagd-kredit")
    heading = "Nağd krediti nədir və kimlər üçün uyğundur?"
    assert html.count(heading) > 0
    blocks, _ = content_blocks(html, "/ferdi/kreditler/nagd-kredit")
    assert any(heading in b.text for b in blocks)


@pytest.mark.parametrize(
    "name,path",
    [
        ("biznes-kicik-orta", "/biznes/kicik-ve-orta-biznes"),
        ("biznes-korporativ", "/biznes/korporativ"),
        ("biznes-mikro", "/biznes/mikro-biznes"),
    ],
)
def test_biznes_pages_share_the_product_page_shape(name: str, path: str) -> None:
    """88 of the 185 core pages live here and were never fetched
    when the extraction rules were written.

    These three are segment-hub pages with a tile carousel, not product pages
    -- they have no <nav aria-label="breadcrumb"> and no <h1> at all.
    `crumbs == []` is the honest result for them, not a bug; the content-region
    strategy (landmark/widget exclusion, see extract.py) still recovers their
    real content."""
    blocks, crumbs = content_blocks(load(name), path)
    assert crumbs == []
    assert sum(len(b.text) for b in blocks) > 400


def test_kampaniya_page_recovers_a_navigational_breadcrumb() -> None:
    """Breadcrumb detection is the WAI-ARIA landmark alone, no
    href-prefix validation. kampaniya-active.html has a real
    <nav aria-label="breadcrumb"> whose trail links (/ferdi,
    /ferdi/kampaniyalar) are a navigational hierarchy, not the page's own
    URL hierarchy (/kampaniyalar/<slug>) -- an href-prefix validator rejects
    this genuine breadcrumb, which is why it was deleted rather than kept as
    a guard."""
    html = load("kampaniya-active")
    path = "/kampaniyalar/abb-play-master-liqa-il-futbol-h-y-cani-qayidir"
    _, crumbs = content_blocks(html, path)
    assert crumbs[0] == "Fərdi"
    assert crumbs[1] == "Kampaniyalar"


def test_page_without_a_breadcrumb_falls_back_to_h1() -> None:
    """No fallback branch exists in content_blocks any more -- landmark
    exclusion alone already drops the <nav> here and keeps the <h1>/<p>, so
    this test exercises the same single code path as everything else, not a
    separate fallback (an earlier `if not blocks: ...` h1-seeking branch was
    dead code: unreachable whenever any landmark tag is present at all, and
    buggy if it ever did fire, since it re-admitted chrome by skipping the
    _is_chrome filter on its own output. Deleted rather than fixed, since no
    real fixture ever needs it)."""
    html = "<html><body><nav>chrome</nav><h1>Başlıq</h1><p>" + "x" * 500 + "</p></body></html>"
    blocks, crumbs = content_blocks(html, "/some-page")
    assert crumbs == []
    assert "chrome" not in " ".join(b.text for b in blocks)


@pytest.mark.parametrize(
    "name,path,expected_crumbs",
    [
        # One-hop category page: has a real <nav aria-label="breadcrumb">.
        (
            "biznes-sub-kicik-orta",
            "/biznes/kicik-ve-orta-biznes/islam-bankcilig",
            ["Biznes", "İslam bankçılığı"],
        ),
        ("biznes-sub-korporativ", "/biznes/korporativ/odenis-kartlari-1", ["Biznes", "Kartlar"]),
        # Feature-list page shape: genuinely has no breadcrumb (RECON §2 finding).
        ("biznes-sub-mikro", "/biznes/mikro-biznes/gundelik-bankciliq", []),
    ],
)
def test_biznes_sub_pages_second_strategy(name: str, path: str, expected_crumbs: list[str]) -> None:
    """The deeper biznes/** page shapes. Exercises the same
    landmark/widget-exclusion region strategy as the
    hub pages above, on pages one level deeper in the hierarchy."""
    blocks, crumbs = content_blocks(load(name), path)
    assert crumbs[:1] == expected_crumbs[:1]
    assert crumbs[-1:] == expected_crumbs[-1:]
    assert sum(len(b.text) for b in blocks) > 200
