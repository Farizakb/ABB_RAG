# backend/scraper/tests/test_extract_gate.py
# ruff: noqa: RUF001 -- the chrome fixtures quote genuine Azerbaijani site copy
# (dotless-i and friends); same convention as test_nextdata.py.
from pathlib import Path

from abb_scraper.extract import (
    CHROME_MIN_CHARS,
    MIN_CHARS,
    PageText,
    apply_gates,
    content_blocks,
    dedupe_blocks,
    extract_page,
    faq_blocks,
    strip_chrome,
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


def test_gate_sits_in_the_measured_gap_between_shells_and_real_pages() -> None:
    """Measured on CHROME-STRIPPED text over the full 550-page
    cache.

    The gate mis-calibrated twice because it was sizing text that still held
    site-wide chrome: an empty shell's 190 chars were entirely a generic title
    and description. With `strip_chrome` running first the populations separate
    completely instead of narrowly:

        empty shells (chrome and nothing else)     0        (17 pages)
        genuine short pages                      124 .. up  (531 pages)

    So the bound below is not a compromise between two overlapping tails; there
    is open space, and 100 sits in it.
    """
    assert MIN_CHARS == 100
    assert MIN_CHARS > 0  # a fully stripped shell must still be dropped
    assert MIN_CHARS <= 124  # /haqqimizda/siyasetlerimiz, the smallest real page


def test_fully_stripped_shell_is_dropped() -> None:
    kept, dropped = apply_gates([("https://abb-bank.az/empty", page(""))])
    assert kept == []
    assert dropped[0].reason == "under-min-chars" and dropped[0].char_count == 0


def test_smallest_genuine_page_in_the_measured_population_survives() -> None:
    """The counterpart to the shell test: 124 chars is the smallest real page in
    the full-cache measurement and must clear the gate."""
    kept, _ = apply_gates([("https://abb-bank.az/real", page("x" * 124))])
    assert len(kept) == 1


def test_root_stubs_sharing_a_boilerplate_body_are_not_deduped_into_one() -> None:
    """The regression the 2026-09-15 re-measurement exposed. ABB serves the same
    117-char "ABB mobile" CTA as the body of 18 root stubs whose real content is
    their title+meta. Keying identity on the body collapsed all 18 into the first
    and silently deleted 17 distinct customer questions, so identity keys on the
    full text whenever the body does not outweigh title+meta."""
    boiler = "c" * 117
    a = page("Kredit borcumu necə onlayn ödəyə bilərəm? " + "a" * 200 + "\n" + boiler, body=boiler)
    b = page(
        "Kommunal ödənişləri onlayn necə etmək olar? " + "b" * 200 + "\n" + boiler, body=boiler
    )
    kept, dropped = apply_gates(
        [("https://abb-bank.az/kredit", a), ("https://abb-bank.az/komm", b)]
    )
    assert [u for u, _ in kept] == ["https://abb-bank.az/kredit", "https://abb-bank.az/komm"]
    assert dropped == []


def _pg(head: tuple[str, ...], blocks: tuple[str, ...]) -> PageText:
    text = "\n".join(head + blocks)
    return PageText(
        text=text,
        crumbs=[],
        dup_collapsed=0,
        char_count=len(text),
        body="\n".join(blocks),
        head=head,
        block_texts=blocks,
    )


def test_strip_chrome_removes_a_site_wide_block_and_keeps_page_specific_text() -> None:
    """The CTA recurs on every page; each page's own answer recurs on one."""
    cta = "ABB mobile yükləmək üçün QR kodu skan edin."
    pages = [
        (f"https://abb-bank.az/p{i}", _pg((f"Sual {i}?", f"Cavab {i}."), (cta,))) for i in range(40)
    ]
    out, removed = strip_chrome(pages)
    assert removed == 40, "the CTA should be stripped from every page, once each"
    for i, (_, p) in enumerate(out):
        assert cta not in p.text
        assert f"Sual {i}?" in p.text and f"Cavab {i}." in p.text
        assert p.char_count == len(p.text)


def test_strip_chrome_collapses_a_pure_chrome_shell_to_nothing() -> None:
    """An empty shell is a generic title and description and no blocks. Once the
    generic pair is recognised as chrome the shell has no content left at all --
    which is what lets MIN_CHARS sit at 100 instead of straddling 190."""
    generic = ("ABB - Müasir, Faydalı, Universal", "ABB bank sektoru üzrə regionun ən iri bankı.")
    pages = [(f"https://abb-bank.az/shell{i}", _pg(generic, ())) for i in range(40)]
    pages.append(("https://abb-bank.az/real", _pg(("Nağd kredit",), ("Şərtlər burada.",))))
    out, _ = strip_chrome(pages)
    by_url = dict(out)
    assert by_url["https://abb-bank.az/shell0"].char_count == 0
    assert by_url["https://abb-bank.az/real"].text == "Nağd kredit\nŞərtlər burada."


def test_strip_chrome_keeps_short_high_frequency_table_labels_a05_a31_regression() -> None:
    """The a05/a31 regression: `Müddət` (6 chars) is the term-row label on
    every product table and recurs on far more than 15% of pages, so the
    frequency gate alone used to strip it and orphan the value beside it --
    measured damage on evals/golden.jsonl: a05 "What is the maximum term for
    a cash loan?" fell from rank 6 to rank 14, and a31 "kredit max nece aya
    olur" dropped out of the top 20 entirely. A 6-char label cannot dominate
    an embedding, so CHROME_MIN_CHARS protects it even though it clears the
    document-frequency threshold."""
    pages = [
        (f"https://abb-bank.az/p{i}", _pg((f"Sual {i}?",), ("Müddət", f"{i} ayadək")))
        for i in range(40)
    ]
    out, _ = strip_chrome(pages)
    for _, p in out:
        assert "Müddət" in p.text


def test_chrome_min_chars_sits_in_the_measured_gap_between_labels_and_chrome() -> None:
    """Measured 2026-09-15: table labels run 6, 7, 10, 10, 11, 12, 13 chars
    (the longest is the "open an account" CTA and two others up to 13);
    genuine chrome runs 32, 34, 43, 62, 72, 157 chars (shortest is the
    generic shell title at 32). 25 sits in the open space between 13 and 32,
    same style as test_gate_sits_in_the_measured_gap_between_shells_and_real_pages."""
    assert CHROME_MIN_CHARS == 25


def test_strip_chrome_does_nothing_in_a_corpus_too_small_to_judge() -> None:
    """CHROME_MIN_DOCS is the floor: three pages sharing a line is not evidence
    of site-wide chrome, and stripping it would delete real content."""
    shared = "Eyni sətir"
    pages = [(f"https://abb-bank.az/p{i}", _pg(("Başlıq",), (shared,))) for i in range(3)]
    out, removed = strip_chrome(pages)
    assert removed == 0
    assert all(shared in p.text for _, p in out)


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
    """`apply_gates`'s `if body and key in seen_bodies:`
    guard exists precisely so an empty body never collides via hash("").
    Two unrelated bodyless root stubs -- title+meta alone
    clearing the gate, zero content blocks -- must both survive;
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
    """`apply_gates` originally recovered the
    body positionally with `page.text.split("\\n", 2)[-1]`, which is only
    correct when both a title and a meta description were prepended --
    extract_page's `if p` skips empty parts when joining `text`, so a
    bodyless page (fixtures/raw/stub-empty.html: non-empty title/meta, zero
    content blocks) shifted the split and silently recovered the meta
    description as if it were the body. Root stubs must be recoverable this
    way, so hashing their description as a "body" would
    wrongly collapse two unrelated bodyless stubs that happen to share a
    generic description.

    The fix: `PageText.body` is now set explicitly by extract_page from the
    same joined body string, before `if p` filtering can drop it. This is a
    standing check across all fixtures in fixtures/raw/ (stub-empty.html
    included) that `page.body` always equals the real joined block text --
    independently re-derived here straight from content_blocks + faq_blocks +
    dedupe_blocks (the same path extract_page itself takes to build `body`),
    rather than trusting extract_page's own value against itself.

    Count raised 11 -> 13: `listing-kampaniyalar.html` (0 bytes --
    the real page 404'd) and
    `listing-ferdi-kreditler.html` were added to the fixture set, and both
    still need to clear this same positional-recovery check."""
    fixtures = sorted(RAW.glob("*.html"))
    assert len(fixtures) == 13, f"expected 13 fixtures, found {len(fixtures)}"

    for path in fixtures:
        html = path.read_text("utf-8")
        url = f"https://abb-bank.az/{path.stem}"
        pg = extract_page(html, url=url)

        # Independently re-derive the real body straight from content_blocks
        # + dedupe_blocks (the same path extract_page itself takes to build
        # `body`, called separately here) rather than trusting pg.body
        # against itself.
        blocks, _ = content_blocks(html, path.stem)
        blocks = blocks + faq_blocks(html, blocks, path.stem)
        kept, _ = dedupe_blocks(blocks)
        actual_body = "\n".join(b.text for b in kept)

        assert pg.body == actual_body, f"{path.name}: page.body diverges from actual body"
