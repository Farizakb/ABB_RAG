# backend/rag/tests/test_smalltalk.py
# ruff: noqa: RUF001, RUF002 -- genuine Azerbaijani query/answer text (incl.
# in docstrings quoting that text), same convention as backend/rag/conftest.py
# and backend/rag/tests/test_refusal.py.
"""Greetings and identity questions get a friendly reply, not the
937 refusal boilerplate."""

from __future__ import annotations

import json
from typing import Any

from rag.embedder import FakeEmbedder
from rag.generate import answer


class FakeClient:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def complete(self, prompt: str) -> tuple[str, dict[str, int]]:
        return self.payload, {"prompt_tokens": 10, "completion_tokens": 5}


def test_small_talk_passes_through_as_a_friendly_non_refusal(seeded_corpus: str, db: Any) -> None:
    payload = json.dumps(
        {
            "answer": "Salam! Mən ABB Bank-ın nəşr olunmuş məlumat köməkçisiyəm və "
            "bankın rəsmi dərc olunmuş məlumatları əsasında cavab verirəm. Kreditlər, "
            "kartlar və digər ABB xidmətləri barədə sual verə bilərsiniz.",
            "citations": [],
            "grounded": False,
            "intent": "small_talk",
        }
    )
    # "kredit" is the query already proven (test_generate.py,
    # test_refusal.py) to clear FakeEmbedder's deterministic retrieval floor, so
    # this exercises the generation branch rather than the pre-LLM empty-retrieval
    # refusal -- the intent classification itself is on `out.intent` from the
    # (fake) LLM output, not on the question text reaching this call.
    r = answer(seeded_corpus, "kredit", FakeEmbedder(dim=8), FakeClient(payload))
    assert r.refused is False
    assert r.grounded is False
    assert r.refusal_class is None
    assert r.sources == []
    assert r.citations == []
    assert "937" not in r.answer


def test_small_talk_that_leaks_a_number_falls_back_to_refusal(seeded_corpus: str, db: Any) -> None:
    """Ruling 4: a model that mislabels a real banking answer as small_talk to
    dodge the cite-or-refuse gate must still be caught."""
    payload = json.dumps(
        {
            "answer": "Nağd kredit üzrə maksimum məbləğ 50000 AZN-dir.",
            "citations": [],
            "grounded": False,
            "intent": "small_talk",
        }
    )
    r = answer(seeded_corpus, "kredit", FakeEmbedder(dim=8), FakeClient(payload))
    assert r.refused is True
    assert r.grounded is False


def test_small_talk_that_leaks_a_url_falls_back_to_refusal(seeded_corpus: str, db: Any) -> None:
    payload = json.dumps(
        {
            "answer": "Ətraflı: https://abb-bank.az/ferdi/kreditler",
            "citations": [],
            "grounded": False,
            "intent": "small_talk",
        }
    )
    r = answer(seeded_corpus, "kredit", FakeEmbedder(dim=8), FakeClient(payload))
    assert r.refused is True


def test_small_talk_naming_categories_still_passes(seeded_corpus: str, db: Any) -> None:
    """Fix round 1, F1: naming what it can help with is not itself a claim."""
    payload = json.dumps(
        {
            "answer": "Kartlar, kreditlər və depozitlər haqqında suallarınıza kömək edə bilərəm.",
            "citations": [],
            "grounded": False,
            "intent": "small_talk",
        }
    )
    r = answer(seeded_corpus, "kredit", FakeEmbedder(dim=8), FakeClient(payload))
    assert r.refused is False
    assert r.grounded is False


def test_small_talk_stating_a_fee_condition_falls_back_to_refusal(
    seeded_corpus: str, db: Any
) -> None:
    """Fix round 1, F1: "Kartın illik haqqı yoxdur" has no digit, %, currency
    mark, or URL -- only the lexicon guard catches it."""
    payload = json.dumps(
        {
            "answer": "Kartın illik haqqı yoxdur.",
            "citations": [],
            "grounded": False,
            "intent": "small_talk",
        }
    )
    r = answer(seeded_corpus, "kredit", FakeEmbedder(dim=8), FakeClient(payload))
    assert r.refused is True


def test_small_talk_stating_a_price_claim_falls_back_to_refusal(
    seeded_corpus: str, db: Any
) -> None:
    """Fix round 1, F1/F2: "kart pulsuzdur" ("the card is free") -- an
    Azerbaijani suffix attached directly to the stem, so the guard must match
    "pulsuz" as a substring, not a whole word."""
    payload = json.dumps(
        {
            "answer": "Kart pulsuzdur.",
            "citations": [],
            "grounded": False,
            "intent": "small_talk",
        }
    )
    r = answer(seeded_corpus, "kredit", FakeEmbedder(dim=8), FakeClient(payload))
    assert r.refused is True


def test_small_talk_english_price_claim_falls_back_to_refusal(seeded_corpus: str, db: Any) -> None:
    payload = json.dumps(
        {
            "answer": "Yes, the card is free.",
            "citations": [],
            "grounded": False,
            "intent": "small_talk",
        }
    )
    r = answer(seeded_corpus, "kredit", FakeEmbedder(dim=8), FakeClient(payload))
    assert r.refused is True


def test_small_talk_over_400_characters_falls_back_to_refusal(seeded_corpus: str, db: Any) -> None:
    """Fix round 1, F1: a real small-talk reply is short (prompt rule 2 caps
    it at 3 sentences); an over-length one is where an unlisted claim is most
    likely hiding, so length alone is its own signal -- no claim word needed
    to trigger this test."""
    payload = json.dumps(
        {
            "answer": "Salam. " * 80,  # far over 400 chars, no lexicon word at all
            "citations": [],
            "grounded": False,
            "intent": "small_talk",
        }
    )
    r = answer(seeded_corpus, "kredit", FakeEmbedder(dim=8), FakeClient(payload))
    assert r.refused is True


def test_small_talk_labelled_reply_confirming_a_free_card_is_refused(
    seeded_corpus: str, db: Any
) -> None:
    """Fix round 1, F2: the exact case named in the fix brief -- an
    LLM-labelled small_talk reply that answers a disguised bank question."""
    payload = json.dumps(
        {
            "answer": "Bəli, kart pulsuzdur.",
            "citations": [],
            "grounded": False,
            "intent": "small_talk",
        }
    )
    r = answer(seeded_corpus, "kredit", FakeEmbedder(dim=8), FakeClient(payload))
    assert r.refused is True


def test_haqqinda_is_not_mistaken_for_the_haqqi_fee_claim(seeded_corpus: str, db: Any) -> None:
    """The guard's "haqqı" alternative must not fire on "haqqında" ("about"),
    an unrelated postposition that happens to start with the same letters --
    otherwise nearly every small-talk reply naming a topic would false-refuse."""
    payload = json.dumps(
        {
            "answer": "ABB-nin kartları haqqında ümumi məlumat verə bilərəm.",
            "citations": [],
            "grounded": False,
            "intent": "small_talk",
        }
    )
    r = answer(seeded_corpus, "kredit", FakeEmbedder(dim=8), FakeClient(payload))
    assert r.refused is False


def test_bank_question_intent_is_unaffected_by_the_new_field(seeded_corpus: str, db: Any) -> None:
    """A grounded, cited answer that explicitly labels itself bank_question
    takes the exact same path as before the intent field existed."""
    payload = json.dumps(
        {
            "answer": "Maksimum məbləğ 20 000 AZN-dir.",
            "citations": [1],
            "grounded": True,
            "intent": "bank_question",
        }
    )
    r = answer(seeded_corpus, "maksimum məbləğ", FakeEmbedder(dim=8), FakeClient(payload))
    assert r.grounded is True
    assert r.refused is False
    assert r.citations == [1]


def test_claim_lexicon_matches_whole_english_words_and_az_ru_stems() -> None:
    """English entries are whole-word ("rate" must not fire on "generate",
    "fee" on "coffee"); az/ru entries are stems that keep their suffixes."""
    from rag.generate import _CLAIM_PATTERN

    for benign in (
        "I can help you with separate questions about cards.",
        "Happy to chat over a coffee-length question!",
        "I find banking topics interesting.",
    ):
        assert not _CLAIM_PATTERN.search(benign), benign
    for claim in ("The card is free.", "No fees apply.", "Kart pulsuzdur.", "Ставки низкие."):
        assert _CLAIM_PATTERN.search(claim), claim
