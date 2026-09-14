# services/rag/tests/test_retrieval.py
from __future__ import annotations

from typing import Any

import pytest
from app.embedder import FakeEmbedder
from app.retrieval import retrieve


def test_returns_top_k_prompt_sources_from_top_k_candidates(seeded_corpus: str, db: Any) -> None:
    result = retrieve(seeded_corpus, "nağd kredit məbləği", FakeEmbedder(dim=8))
    # P81: the brief's original assertions (`<= 5`, `<= 20`) hold vacuously for an
    # empty result, so a broken retrieve() that always returns [] would still pass.
    # Pin the lower bound too, and that sources can never outnumber candidates.
    assert result.sources
    assert result.candidates
    assert len(result.sources) <= len(result.candidates)
    assert len(result.sources) <= 5
    assert len(result.candidates) <= 20


def test_sources_are_numbered_from_one_in_retrieval_order(seeded_corpus: str, db: Any) -> None:
    result = retrieve(seeded_corpus, "kredit", FakeEmbedder(dim=8))
    assert result.sources  # P81: [] == [] would pass this ordering check vacuously
    assert [s.n for s in result.sources] == list(range(1, len(result.sources) + 1))
    assert result.sources == sorted(result.sources, key=lambda s: -s.score)


def test_governed_facts_travel_with_their_source(seeded_corpus: str, db: Any) -> None:
    result = retrieve(seeded_corpus, "maksimum məbləğ", FakeEmbedder(dim=8))
    # any() over an empty list is already False, so this is non-vacuous as written.
    assert any(s.facts for s in result.sources)


def test_retrieval_is_scoped_to_one_corpus_and_one_embedding_model(
    seeded_corpus: str, db: Any
) -> None:
    result = retrieve(seeded_corpus, "kredit", FakeEmbedder(dim=8, model="model-b"))
    assert result.sources == []  # invariant 8: never mix vector spaces


def test_floor_filters_candidates_below_threshold(
    seeded_corpus: str, db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    query = "tamamilə əlaqəsiz sual"
    embedder = FakeEmbedder(dim=8)
    # P83: pin against the opposite first. A floor of 2.0 is above the maximum
    # possible cosine score (1.0), so `sources == []` below would also pass if
    # retrieve() were broken and always returned []. Proving the SAME query
    # returns real results at the real (measured, see task-19-report.md's P82
    # section) floor shows the emptiness that follows is the floor's doing.
    assert retrieve(seeded_corpus, query, embedder).sources != []
    monkeypatch.setattr("app.retrieval.settings.retrieval_floor", 2.0)
    assert retrieve(seeded_corpus, query, embedder).sources == []


def test_retrieval_meets_the_300ms_budget(seeded_corpus: str, db: Any) -> None:
    assert retrieve(seeded_corpus, "kredit", FakeEmbedder(dim=8)).took_ms < 300


def test_every_source_carries_a_listing_url_derived_from_its_own_url(
    seeded_corpus: str, db: Any
) -> None:
    """Invariant 12. The footer target in SPEC §11.2 is computed here, from a
    document that was actually retrieved -- never written by the model."""
    sources = retrieve(seeded_corpus, "kredit", FakeEmbedder(dim=8)).sources
    assert sources  # P81: an empty list makes the loop below assert nothing
    for s in sources:
        assert s.listing_url and s.url.startswith(s.listing_url)


def test_a_top_level_page_is_its_own_listing_rather_than_the_bare_host() -> None:
    from app.retrieval import listing_url_for

    known = {"https://abb-bank.az/kampaniyalar", "https://abb-bank.az/filiallar"}
    assert (
        listing_url_for("https://abb-bank.az/kampaniyalar/yay", known)
        == "https://abb-bank.az/kampaniyalar"
    )
    assert (
        listing_url_for("https://abb-bank.az/filiallar", known) == "https://abb-bank.az/filiallar"
    )
    assert listing_url_for("https://abb-bank.az/", known) == "https://abb-bank.az/"


def test_a_trim_that_is_not_in_the_corpus_falls_back_to_the_page_itself() -> None:
    """Invariant 12, second half. Trimming a segment produces a *plausible* URL,
    and a plausible URL that 404s on the bank's own domain is worse than no link.
    Prefix-correctness is not existence, so the trim must be proven, not assumed."""
    from app.retrieval import listing_url_for

    deep = "https://abb-bank.az/haqqimizda/satinalmalar/tender-2026-11"
    assert listing_url_for(deep, {"https://abb-bank.az/haqqimizda"}) == deep
    assert (
        listing_url_for(deep, {"https://abb-bank.az/haqqimizda/satinalmalar"})
        == "https://abb-bank.az/haqqimizda/satinalmalar"
    )


def test_a_trailing_slash_in_the_corpus_still_counts_as_existing() -> None:
    from app.retrieval import listing_url_for

    assert (
        listing_url_for(
            "https://abb-bank.az/kampaniyalar/yay", {"https://abb-bank.az/kampaniyalar/"}
        )
        == "https://abb-bank.az/kampaniyalar"
    )
