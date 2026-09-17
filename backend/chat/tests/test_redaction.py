# ruff: noqa: RUF001 -- genuine Azerbaijani fixture/assertion text (dotless-i
# and friends), same convention as backend/rag/conftest.py and
# backend/rag/tests/test_chunking.py.
from __future__ import annotations

from chat.redaction import redact


def test_card_shaped_digit_runs_are_masked() -> None:
    assert "4169" not in redact("kart nömrəm 4169 7388 1234 5678")
    assert "[redacted:card]" in redact("kart nömrəm 4169738812345678")


def test_azerbaijani_phone_numbers_are_masked() -> None:
    assert "[redacted:phone]" in redact("əlaqə: +994 50 123 45 67")


def test_national_id_pattern_is_masked() -> None:
    assert "[redacted:id]" in redact("FİN kodum 5AB1C2D")


def test_national_id_pattern_is_masked_lowercase() -> None:
    assert "[redacted:id]" in redact("FIN kodum 5ab1c2d")


def test_national_id_pattern_is_masked_mixed_case() -> None:
    assert "[redacted:id]" in redact("FIN kodum 5Ab1c2D")


def test_ordinary_product_figures_are_never_masked() -> None:
    """A false positive here silently corrupts the analytics table."""
    kept = redact("50 000 AZN məbləğində 10.9% faizlə 60 aylıq kredit")
    assert "50 000" in kept and "10.9" in kept and "60" in kept


def test_all_letter_word_is_not_masked_when_a_digit_appears_later() -> None:
    """Lookaheads must be scoped to the 7-char token, not the whole string."""
    kept = redact("SALAMLA 50 AZN")
    assert "SALAMLA" in kept


def test_plain_word_next_to_a_number_is_not_masked() -> None:
    """A 7-letter word (no digits of its own) beside a number is not a FIN."""
    kept = redact("KREDITI 60 aylıq")
    assert "KREDITI" in kept


def test_redaction_is_idempotent() -> None:
    once = redact("kart 4169738812345678")
    assert redact(once) == once
