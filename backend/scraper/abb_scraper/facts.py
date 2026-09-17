# backend/scraper/abb_scraper/facts.py
# ruff: noqa: RUF002 -- genuine Azerbaijani text, not ambiguous-character
# typos; see campaigns.py / test_extract_region.py for the same convention.
from __future__ import annotations

import re

from abb_scraper.extract import Block
from contracts.models import Fact

EXTRACTOR_VERSION = 1
MAX_FRAGMENT_CHARS = 40  # a stat block is a fragment, not a sentence

LABELS: dict[str, str] = {
    "məbləğ": "max_amount",
    "müddət": "term_months",
    "illik faiz dərəcəsi": "apr_min",
    "faiz dərəcəsi": "apr_min",
    "zamin tələb olunmur": "collateral",
    "zamin": "collateral",
}

UNITS: list[tuple[re.Pattern[str], str, str | None]] = [
    (re.compile(r"%"), "percent", None),
    (re.compile(r"AZN|₼", re.I), "AZN", "AZN"),
    (re.compile(r"\bay\w*", re.I), "months", None),
    (re.compile(r"\bil\w*", re.I), "years", None),
]

NUMBER = re.compile(r"(\d[\d\s.,]*)")


def normalize_label(s: str) -> str:
    """`s.lower()` alone is broken for Azerbaijani:
    `"İllik faiz dərəcəsi".lower()` does not produce `"illik faiz dərəcəsi"`
    -- Python lowercases U+0130 LATIN CAPITAL LETTER I WITH DOT ABOVE to a
    *two*-codepoint sequence, `i` + U+0307 COMBINING DOT ABOVE (`casefold()`
    behaves identically), so a label starting with `İ` never matches a
    plain-ASCII dict key. Stripping only U+0307 fixes exactly that, and
    nothing else: a general NFKD fold (`unicodedata.normalize("NFKD", ...)`)
    would also decompose `ə ü ğ ç ş ı`, corrupting every other Azerbaijani
    label this function must still match unchanged.
    """
    return s.lower().replace("̇", "").strip()


def _label_key(label_raw: str) -> str:
    """Lookup key for `LABELS`. Real markup carries trailing sentence
    punctuation that a hand-built stat list wouldn't include --
    nagd-kredit.html's actual block is `"Zamin tələb olunmur."`, period and
    all, not the bare `"Zamin tələb olunmur"`.
    Stripped here, separately from `normalize_label`, which fixes exactly
    one defect (AZ casing) and nothing more.
    """
    return normalize_label(label_raw).rstrip(".,;:!?")


def _parse_value(raw: str) -> tuple[float | None, str | None, str | None]:
    unit = currency = None
    for rx, u, c in UNITS:
        if rx.search(raw):
            unit, currency = u, c
            break
    m = NUMBER.search(raw)
    if m is None:
        return (None, unit, currency)
    cleaned = m.group(1).replace(" ", "").replace("\xa0", "").rstrip(".,")
    if cleaned.count(",") == 1 and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        return (float(cleaned), unit, currency)
    except ValueError:
        return (None, unit, currency)


def extract_facts(blocks: list[Block], product: str, url: str) -> list[Fact]:
    """Value-then-label stat blocks -> governed rows (measured on the live
    markup as value-then-label, not label-then-value).
    Rule-based only: a model that invents a number here produces a
    fabricated fact wearing a valid citation, which no downstream check can
    catch.
    """
    out: list[Fact] = []
    for i in range(len(blocks) - 1):
        value_raw, label_raw = blocks[i].text, blocks[i + 1].text
        if len(value_raw) > MAX_FRAGMENT_CHARS or len(label_raw) > MAX_FRAGMENT_CHARS:
            continue
        attribute = LABELS.get(_label_key(label_raw))
        if attribute is None:
            continue
        num, unit, currency = _parse_value(value_raw)
        if num is None and attribute != "collateral":
            continue
        out.append(
            Fact(
                attribute=attribute,
                value_num=num if attribute != "collateral" else None,
                value_text=value_raw if attribute == "collateral" else None,
                unit=unit,
                currency=currency,
                raw_fragment=value_raw,
                source_url=url,
            )
        )
    return out


def reassemble(facts: list[Fact], product: str) -> str:
    """One declarative sentence carrying the product name, so a question
    about the maximum amount retrieves a subject rather than a bag of
    numbers. Every `raw_fragment` appears verbatim in the sentence, so it
    and the fact rows can never disagree on a figure.
    """
    if not facts:
        return ""
    parts = [f"{f.attribute.replace('_', ' ')}: {f.raw_fragment}" for f in facts]
    return f"{product} — " + "; ".join(parts) + "."
