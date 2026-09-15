# packages/scraper/tests/test_extract_gate.py
from pathlib import Path

from abb_scraper.extract import (
    MIN_CHARS,
    PageText,
    apply_gates,
    content_blocks,
    dedupe_blocks,
    extract_page,
    faq_blocks,
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
    """SPEC §5.3 rule 5, re-measured over the full 550-page raw cache 2026-09-15.

    Two populations, and the gate must fall between them:
        empty shells (zero content blocks)  190 .. 206   (18 pages)
        genuine short pages                 285 .. 389   (28 pages)

    The previous 400 was above the TOP of the genuine population, not between
    them, so it dropped all 28 -- including the 18 root stubs whose whole
    substance is a customer question in the title and its answer in the meta
    description. Ruling P57's constraint (never rise past a real page) is what
    these bounds encode; 400 violated it against the full cache even though it
    held against the smaller day-three sample it was calibrated on.
    """
    assert MIN_CHARS == 250
    assert MIN_CHARS > 206  # above every measured empty shell, so all 18 still drop
    assert MIN_CHARS <= 285  # at or below the smallest genuine page, so none is lost


def test_empty_shell_at_the_measured_baseline_is_dropped() -> None:
    kept, dropped = apply_gates([("https://abb-bank.az/empty", page("x" * 206))])
    assert kept == []
    assert dropped[0].reason == "under-min-chars" and dropped[0].char_count == 206


def test_smallest_genuine_page_in_the_measured_population_survives() -> None:
    """The counterpart to the shell test: 285 chars is the smallest real page in
    the full-cache measurement and must clear the gate."""
    kept, _ = apply_gates([("https://abb-bank.az/real", page("x" * 285))])
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
    standing check across all fixtures in fixtures/raw/ (stub-empty.html
    included) that `page.body` always equals the real joined block text --
    independently re-derived here straight from content_blocks + faq_blocks +
    dedupe_blocks (the same path extract_page itself takes to build `body`),
    rather than trusting extract_page's own value against itself.

    Count raised 11 -> 13 by Task 12: `listing-kampaniyalar.html` (0 bytes --
    the real page 404'd, RECON.md's dated day-two heading) and
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
