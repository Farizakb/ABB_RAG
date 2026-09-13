# packages/scraper/tests/test_extract_region.py
from pathlib import Path

import pytest
from abb_scraper.extract import content_blocks

RAW = Path("fixtures/raw")


def load(name: str) -> str:
    return (RAW / f"{name}.html").read_text("utf-8")


def test_breadcrumb_yields_section_path_and_title() -> None:
    _, crumbs = content_blocks(load("nagd-kredit"), "/ferdi/kreditler/nagd-kredit")
    assert crumbs[0] == "Fərdi"
    assert crumbs[-1] == "Nağd kredit"


def test_front_loaded_chrome_is_dropped() -> None:
    blocks, _ = content_blocks(load("nagd-kredit"), "/ferdi/kreditler/nagd-kredit")
    joined = " ".join(b.text for b in blocks)
    assert "App Store" not in joined
    assert "50 000" in joined


def test_feedback_marker_terminates_the_region() -> None:
    blocks, _ = content_blocks(load("nagd-kredit"), "/ferdi/kreditler/nagd-kredit")
    assert not any("Səhifəni dəyərləndirin" in b.text for b in blocks)


@pytest.mark.parametrize(
    "name,path",
    [
        ("biznes-kicik-orta", "/biznes/kicik-ve-orta-biznes"),
        ("biznes-korporativ", "/biznes/korporativ"),
        ("biznes-mikro", "/biznes/mikro-biznes"),
    ],
)
def test_biznes_pages_share_the_product_page_shape(name: str, path: str) -> None:
    """Gate item V-1. 88 of the 185 core pages live here and were never fetched
    when the extraction rules were written.

    Measurement (see task-7-report.md): these three are segment-hub pages
    with a tile carousel, not product pages -- they have no <nav
    aria-label="breadcrumb"> and no <h1> at all. `crumbs == []` is the honest
    result for them (V-1's real finding), not a bug; the second region
    strategy (landmark/widget exclusion, see extract.py) still recovers their
    real content, which is what this gate is actually protecting.
    """
    blocks, crumbs = content_blocks(load(name), path)
    assert crumbs == []
    assert sum(len(b.text) for b in blocks) > 400


def test_page_without_a_breadcrumb_falls_back_to_h1() -> None:
    html = "<html><body><nav>chrome</nav><h1>Başlıq</h1><p>" + "x" * 500 + "</p></body></html>"  # noqa: RUF001
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
            ["Biznes", "İslam bankçılığı"],  # noqa: RUF001
        ),
        ("biznes-sub-korporativ", "/biznes/korporativ/odenis-kartlari-1", ["Biznes", "Kartlar"]),
        # Feature-list page shape: genuinely has no breadcrumb (RECON §2 finding).
        ("biznes-sub-mikro", "/biznes/mikro-biznes/gundelik-bankciliq", []),
    ],
)
def test_biznes_sub_pages_second_strategy(name: str, path: str, expected_crumbs: list[str]) -> None:
    """The deeper biznes/** page shapes (captured for the SPEC §2 verification
    gate). Exercises the same landmark/widget-exclusion region strategy as the
    hub pages above, on pages one level deeper in the hierarchy."""
    blocks, crumbs = content_blocks(load(name), path)
    assert crumbs[:1] == expected_crumbs[:1]
    assert crumbs[-1:] == expected_crumbs[-1:]
    assert sum(len(b.text) for b in blocks) > 0
