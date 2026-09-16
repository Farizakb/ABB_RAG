# services/rag/tests/test_refusal.py
# ruff: noqa: RUF001 -- genuine Azerbaijani query/assertion text, same
# convention as services/rag/conftest.py and services/rag/tests/test_chunking.py.
from __future__ import annotations

import json
from typing import Any

import pytest
from app.embedder import FakeEmbedder
from app.generate import answer


class Client:
    def __init__(self, grounded: bool) -> None:
        self.grounded = grounded

    def complete(self, prompt: str) -> tuple[str, dict[str, int]]:
        return (
            json.dumps(
                {
                    "answer": "cavab",
                    "citations": [1] if self.grounded else [],
                    "grounded": self.grounded,
                }
            ),
            {"prompt_tokens": 1, "completion_tokens": 1},
        )


@pytest.mark.parametrize(
    "question",
    [
        "Mənim maaşım 1200 manatdır, nə qədər kredit götürə bilərəm?",
        "Mənə hansı kredit daha uyğundur?",
        "Will I be approved for a mortgage?",
        "Mene hansi kredit daha uygundur?",
        "Maasim 1200 manatdir, ne qeder kredit goture bilerem?",
        "Kreditə tesdiq alacağammı?",
    ],
)
def test_advisory_questions_refuse_and_route(seeded_corpus: str, db: Any, question: str) -> None:
    r = answer(seeded_corpus, question, FakeEmbedder(dim=8), Client(grounded=False))
    assert r.refused and r.refusal_class == "advisory"
    assert "937" in r.answer


def test_advisory_refusal_still_shows_what_was_retrieved(seeded_corpus: str, db: Any) -> None:
    r = answer(seeded_corpus, "Mənə hansı kredit uyğundur?", FakeEmbedder(dim=8), Client(False))
    assert r.sources, "a refusal must be inspectable, not a dead end"


def test_out_of_scope_refusal_uses_the_general_copy_not_the_advisory_copy(
    seeded_corpus: str, db: Any
) -> None:
    r = answer(seeded_corpus, "Hava sabah necə olacaq?", FakeEmbedder(dim=8), Client(False))
    assert r.refusal_class == "out_of_scope" and "uyğunluq" not in r.answer


def test_refusal_copy_never_apologises_and_never_speculates(seeded_corpus: str, db: Any) -> None:
    r = answer(seeded_corpus, "Hava necədir?", FakeEmbedder(dim=8), Client(False))
    lowered = r.answer.lower()
    assert "üzr istəyirəm" not in lowered and "bəlkə" not in lowered
    # P96: a positive assertion -- the two substring-absence checks above also
    # hold vacuously if r.answer were emptied, so pin that the refusal copy is
    # non-empty and actually routes the customer to the call centre.
    assert r.answer.strip()
    assert "937" in r.answer


def test_a_published_criterion_may_still_be_stated_with_a_citation(
    seeded_corpus: str, db: Any
) -> None:
    """The boundary is between stating published terms and assessing a person
    against them. 'What are the requirements' is informational."""
    r = answer(
        seeded_corpus,
        "Nağd kredit üçün tələblər nələrdir?",
        FakeEmbedder(dim=8),
        Client(grounded=True),
    )
    assert r.grounded and not r.refused


def test_advisory_question_with_no_retrieved_sources_still_refuses_as_advisory(
    seeded_corpus: str, db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P94: `answer()` used to early-return `out_of_scope` before ever consulting
    `_looks_advisory` when retrieval came back empty, so which refusal copy the
    customer saw -- and whether they were routed to 937 -- depended on whether a
    vector search happened to clear the floor. Force the empty-retrieval path the
    same way Task 19's floor test does (monkeypatch the floor above the maximum
    possible cosine score), not by inventing a second mechanism."""
    monkeypatch.setattr("app.retrieval.settings.retrieval_floor", 2.0)
    r = answer(
        seeded_corpus, "Will I be approved for a mortgage?", FakeEmbedder(dim=8), Client(False)
    )
    assert r.sources == []
    assert r.refused and r.refusal_class == "advisory"
    assert "937" in r.answer
