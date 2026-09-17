# backend/shared/tests/test_models.py
import pytest
from contracts.models import Corpus, Document, Fact, QuestionResponse
from pydantic import ValidationError


def test_document_rejects_unknown_source_class() -> None:
    with pytest.raises(ValidationError):
        Document(
            url="https://abb-bank.az/x",
            title="x",
            section_path=[],
            source_class="news",
            text="x" * 500,
            content_hash="sha256:a",
        )


def test_corpus_id_is_stable_over_document_order() -> None:
    a = Document(
        url="https://abb-bank.az/a",
        title="a",
        section_path=[],
        source_class="product",
        text="a" * 500,
        content_hash="sha256:a",
    )
    b = Document(
        url="https://abb-bank.az/b",
        title="b",
        section_path=[],
        source_class="product",
        text="b" * 500,
        content_hash="sha256:b",
    )
    assert Corpus(documents=[a, b]).corpus_id == Corpus(documents=[b, a]).corpus_id


def test_answer_carrying_no_citation_must_be_refusal() -> None:
    with pytest.raises(ValidationError):
        QuestionResponse(
            interaction_id="1",
            answer="ABB offers 10.9%",
            sources=[],
            grounded=True,
            refused=False,
            timestamp="2026-09-14T00:00:00Z",
        )


def test_fact_requires_either_numeric_or_text_value() -> None:
    with pytest.raises(ValidationError):
        Fact(attribute="max_amount", raw_fragment="50 000 AZN-dək")
