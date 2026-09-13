# packages/scraper/tests/test_extract_gate.py
from pathlib import Path

from abb_scraper.extract import (
    MIN_CHARS,
    PageText,
    apply_gates,
    content_blocks,
    dedupe_blocks,
    extract_page,
)

RAW = Path("fixtures/raw")


def page(text: str, body: str | None = None) -> PageText:
    return PageText(
        text=text,
        crumbs=[],
        dup_collapsed=0,
        char_count=len(text),
        body=text if body is None else body,
    )


def test_gate_is_400_not_150() -> None:
    """SPEC §5.3 rule 5. The measured empty shell is 190 chars, so any gate at or
    below it passes every empty page — which is why v1.0's 150 was inoperative."""
    assert MIN_CHARS == 400
    assert MIN_CHARS > 190  # must exceed the empty-shell baseline (stub-empty) to drop it
    assert MIN_CHARS <= 439  # must not exceed the lowest genuine page (biznes-sub-korporativ)


def test_empty_shell_at_the_measured_baseline_is_dropped() -> None:
    kept, dropped = apply_gates([("https://abb-bank.az/empty", page("x" * 273))])
    assert kept == []
    assert dropped[0].reason == "under-400-chars" and dropped[0].char_count == 273


def test_lowest_genuine_page_survives() -> None:
    kept, _ = apply_gates([("https://abb-bank.az/real", page("x" * 1349))])
    assert len(kept) == 1


def test_cross_document_duplicate_bodies_collapse_to_the_first() -> None:
    body = "y" * 900
    kept, dropped = apply_gates(
        [("https://abb-bank.az/a", page(body)), ("https://abb-bank.az/b", page(body))]
    )
    assert [u for u, _ in kept] == ["https://abb-bank.az/a"]
    assert dropped[0].reason == "cross-document-duplicate"


def test_two_bodyless_pages_are_not_deduped_against_each_other() -> None:
    """Ruling P41's crux: `apply_gates`'s `if body and key in seen_bodies:`
    guard exists precisely so an empty body never collides via hash("").
    Two unrelated bodyless root stubs (SPEC §5.3 rule 3) -- title+meta alone
    clearing the 400-char gate, zero content blocks -- must both survive;
    neither may be dropped as a cross-document-duplicate of the other."""
    kept, dropped = apply_gates(
        [
            ("https://abb-bank.az/stub-a", page("a" * 450, body="")),
            ("https://abb-bank.az/stub-b", page("b" * 450, body="")),
        ]
    )
    assert [u for u, _ in kept] == ["https://abb-bank.az/stub-a", "https://abb-bank.az/stub-b"]
    assert dropped == []


def test_every_drop_is_reported_with_its_char_count() -> None:
    _, dropped = apply_gates([("https://abb-bank.az/x", page("short"))])
    assert dropped[0].url and dropped[0].char_count == 5


def test_positional_body_recovery_matches_actual_body_on_every_fixture() -> None:
    """Ruling P41 on task 9's brief: `apply_gates` originally recovered the
    body positionally with `page.text.split("\\n", 2)[-1]`, which is only
    correct when both a title and a meta description were prepended --
    extract_page's `if p` skips empty parts when joining `text`, so a
    bodyless page (fixtures/raw/stub-empty.html: non-empty title/meta, zero
    content blocks) shifted the split and silently recovered the meta
    description as if it were the body. SPEC §5.3 rule 3 exists precisely to
    recover such root stubs, so hashing their description as a "body" would
    wrongly collapse two unrelated bodyless stubs that happen to share a
    generic description.

    The fix: `PageText.body` is now set explicitly by extract_page from the
    same joined body string, before `if p` filtering can drop it. This is a
    standing check across all 11 fixtures in fixtures/raw/ (stub-empty.html
    included) that `page.body` always equals the real joined block text --
    independently re-derived here straight from content_blocks +
    dedupe_blocks (the same path extract_page itself takes to build `body`),
    rather than trusting extract_page's own value against itself."""
    fixtures = sorted(RAW.glob("*.html"))
    assert len(fixtures) == 11, f"expected 11 fixtures, found {len(fixtures)}"

    for path in fixtures:
        html = path.read_text("utf-8")
        url = f"https://abb-bank.az/{path.stem}"
        pg = extract_page(html, url=url)

        # Independently re-derive the real body straight from content_blocks
        # + dedupe_blocks (the same path extract_page itself takes to build
        # `body`, called separately here) rather than trusting pg.body
        # against itself.
        blocks, _ = content_blocks(html, path.stem)
        kept, _ = dedupe_blocks(blocks)
        actual_body = "\n".join(b.text for b in kept)

        assert pg.body == actual_body, f"{path.name}: page.body diverges from actual body"
