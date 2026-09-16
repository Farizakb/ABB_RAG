# services/rag/tests/test_smalltalk.py
# ruff: noqa: RUF001 -- genuine Azerbaijani query/answer text, same convention
# as services/rag/conftest.py and services/rag/tests/test_refusal.py.
"""Task 42: greetings and identity questions get a friendly reply, not the
937 refusal boilerplate. See .superpowers/sdd/2026-09-12-abb-assistant/
task-42-brief.md rulings 1-4."""

from __future__ import annotations

import json
from typing import Any

from app.embedder import FakeEmbedder
from app.generate import answer


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
    # P94/test convention: "kredit" is the query already proven (test_generate.py,
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
