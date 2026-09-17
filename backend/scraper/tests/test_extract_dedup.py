# backend/scraper/tests/test_extract_dedup.py
# ruff: noqa: RUF001 -- this file quotes genuine Azerbaijani fixture/assertion
# text (dotless-i and friends); it isn't ambiguous, it's AZ.
from pathlib import Path

from abb_scraper.extract import Block, content_blocks, dedupe_blocks, extract_page


def test_repeated_block_kept_once() -> None:
    blocks = [Block("Onlayn kredit sifariş edin", "p")] * 7 + [Block("unique", "p")]
    kept, collapsed = dedupe_blocks(blocks)
    assert [b.text for b in kept] == ["Onlayn kredit sifariş edin", "unique"]
    assert collapsed == 6


def test_dedup_is_whitespace_and_case_insensitive() -> None:
    # "Nağd  Kredit" (double space) vs "nağd kredit" must collapse
    # to one -- the key must fold both case AND whitespace, not just case.
    _, collapsed = dedupe_blocks([Block("Nağd  Kredit", "p"), Block("nağd kredit", "p")])
    assert collapsed == 1


def test_real_page_duplication_is_around_39_percent() -> None:
    html = Path("fixtures/raw/nagd-kredit.html").read_text("utf-8")
    blocks, _ = content_blocks(html, "/ferdi/kreditler/nagd-kredit")
    kept, _ = dedupe_blocks(blocks)
    before = sum(len(b.text) for b in blocks)
    after = sum(len(b.text) for b in kept)
    assert 0.25 < (before - after) / before < 0.55


def test_title_and_meta_are_prepended() -> None:
    page = extract_page(
        "<html><body><h1>x</h1><p>body text</p></body></html>",
        url="https://abb-bank.az/some-page",
        title="Nağd kredit",
        meta="Nağd kredit haqqında",
    )
    assert page.text.startswith("Nağd kredit\nNağd kredit haqqında\n")
