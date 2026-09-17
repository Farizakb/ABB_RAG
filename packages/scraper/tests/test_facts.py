# packages/scraper/tests/test_facts.py
# ruff: noqa: RUF001, RUF002 -- genuine Azerbaijani fixture/assertion text
# (dotless-i and friends); see campaigns.py for the same convention.
from pathlib import Path

from abb_scraper.extract import Block, content_blocks
from abb_scraper.facts import extract_facts, normalize_label, reassemble

RAW = Path("fixtures/raw")


def load(name: str) -> str:
    return (RAW / f"{name}.html").read_text("utf-8")


STAT = [
    Block("50 000 AZN-dək", "div"),
    Block("Məbləğ", "div"),
    Block("60 ayadək", "div"),
    Block("Müddət", "div"),
    Block("10.9%-dən", "div"),
    Block("İllik faiz dərəcəsi", "div"),
    Block("Zaminsiz", "div"),
    Block("Zamin tələb olunmur", "div"),
]


def test_value_then_label_order_is_parsed() -> None:
    facts = {f.attribute: f for f in extract_facts(STAT, "Nağd kredit", "https://abb-bank.az/x")}
    assert facts["max_amount"].value_num == 50000
    assert facts["max_amount"].unit == "AZN"
    assert facts["max_amount"].currency == "AZN"
    assert facts["term_months"].value_num == 60
    assert facts["apr_min"].value_num == 10.9
    assert facts["collateral"].value_text == "Zaminsiz"


def test_raw_fragment_is_preserved_for_verification() -> None:
    facts = extract_facts(STAT, "Nağd kredit", "https://abb-bank.az/x")
    assert any(f.raw_fragment == "50 000 AZN-dək" for f in facts)


def test_reassembled_sentence_carries_the_product_name_and_every_figure() -> None:
    facts = extract_facts(STAT, "Nağd kredit", "https://abb-bank.az/x")
    sentence = reassemble(facts, "Nağd kredit")
    assert sentence.startswith("Nağd kredit")
    for figure in ("50000", "60", "10.9"):
        assert figure in sentence.replace(" ", "")


def test_prose_paragraphs_are_not_mistaken_for_stat_blocks() -> None:
    prose = [Block("Nağd kredit sizə 50 000 AZN-dək vəsait əldə etməyə imkan verir.", "p")]
    assert extract_facts(prose, "Nağd kredit", "https://abb-bank.az/x") == []


def test_a_long_prose_paragraph_next_to_a_real_label_is_not_its_value() -> None:
    """The test above is vacuous: with a single block, `range(len(blocks) -
    1)` is empty regardless of MAX_FRAGMENT_CHARS, so it passes even if the
    length guard is deleted. This is the real exercise of that branch: an
    ordinary sentence (66 chars, well over MAX_FRAGMENT_CHARS=40) sitting
    right before a genuine label. Without the guard this would extract
    max_amount=50000 from the sentence, a fabricated-looking fact stitched
    from narrative prose rather than a stat block."""
    blocks = [
        Block("Nağd kredit sizə 50 000 AZN-dək vəsait əldə etməyə imkan verir.", "p"),
        Block("Məbləğ", "div"),
    ]
    assert extract_facts(blocks, "Nağd kredit", "https://abb-bank.az/x") == []


def test_unknown_labels_are_ignored_rather_than_guessed() -> None:
    blocks = [Block("7 gün", "div"), Block("Naməlum etiket", "div")]
    assert extract_facts(blocks, "X", "https://abb-bank.az/x") == []


def test_sentence_and_rows_never_disagree_on_a_figure() -> None:
    facts = extract_facts(STAT, "Nağd kredit", "https://abb-bank.az/x")
    sentence = reassemble(facts, "Nağd kredit")
    for f in facts:
        assert f.raw_fragment in sentence


# -- Azerbaijani casing -------------------------------------------------------


def test_normalize_label_folds_the_capital_dotted_i_to_plain_i() -> None:
    """`"İllik faiz dərəcəsi".lower()` alone does NOT produce
    `"illik faiz dərəcəsi"`: Python lowercases U+0130 (LATIN CAPITAL LETTER
    I WITH DOT ABOVE) to a *two*-codepoint sequence, `i` + U+0307 COMBINING
    DOT ABOVE, not the single ASCII `i` a plain-lowercase dict key expects.
    `casefold()` behaves identically. Proof the raw label really carries the
    two-codepoint form first, so the fold assertion below is a real fix and
    not a no-op on an already-plain string."""
    raw = "İllik faiz dərəcəsi"
    assert raw.lower() == "i̇llik faiz dərəcəsi"  # the broken form, unfixed
    assert normalize_label(raw) == "illik faiz dərəcəsi"


def test_normalize_label_leaves_other_azerbaijani_letters_intact() -> None:
    """The other half of the previous test: a general NFKD fold would also pass the İ
    case above but would silently decompose `ə ü ğ ç ş ı` into
    base-letter-plus-combining-mark sequences, corrupting every other
    Azerbaijani label. Stripping only U+0307 must leave these untouched."""
    raw = "Əlavə ödəniş üçün ğərar - çətin şərait ıraq"
    normalized = normalize_label(raw)
    for letter in "əüğçşı":
        assert letter in normalized
    # and no combining marks were introduced
    assert "̇" not in normalized
    assert len(normalized) == len(raw.lower())


def test_real_fixture_label_carries_trailing_punctuation_the_brief_missed() -> None:
    """nagd-kredit.html's actual collateral label block is
    `"Zamin tələb olunmur."`, period and all -- not the bare
    `"Zamin tələb olunmur"` the brief's hand-built STAT list used. An exact
    `LABELS.get(label.lower().strip())` lookup, run against this real block,
    misses it and silently drops the collateral fact. Proven against the
    real fixture via `content_blocks`, not a hand-typed string, per the
    re-derive-from-real-extraction requirement."""
    html = load("nagd-kredit")
    blocks, _ = content_blocks(html, "/ferdi/kreditler/nagd-kredit")
    label_blocks = [b for b in blocks if b.text.startswith("Zamin tələb")]
    assert label_blocks, "fixture no longer carries the collateral label block"
    assert label_blocks[0].text == "Zamin tələb olunmur."  # proof: trailing period, real markup

    url = "https://abb-bank.az/ferdi/kreditler/nagd-kredit"
    facts = extract_facts(blocks, "Nağd kredit", url)
    collateral = [f for f in facts if f.attribute == "collateral"]
    assert collateral and collateral[0].value_text == "Zaminsiz"
