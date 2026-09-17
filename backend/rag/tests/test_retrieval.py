# backend/rag/tests/test_retrieval.py
# ruff: noqa: RUF001 -- genuine Azerbaijani fixture/assertion text (dotless-i
# and friends); see backend/scraper/tests/test_facts.py for the same convention.
from __future__ import annotations

from typing import Any

import pytest
from rag.config import settings
from rag.embedder import FakeEmbedder
from rag.ingest import ingest_corpus
from rag.retrieval import _rrf, _tsquery, retrieve
from shared.contracts import Corpus, Document


def test_returns_top_k_prompt_sources_from_top_k_candidates(seeded_corpus: str, db: Any) -> None:
    result = retrieve(seeded_corpus, "nağd kredit məbləği", FakeEmbedder(dim=8))
    # `<= 5`/`<= 20` alone hold vacuously for an empty result, so a broken
    # retrieve() that always returns [] would still pass. Pin the lower
    # bound too, and that sources can never outnumber candidates.
    assert result.sources
    assert result.candidates
    assert len(result.sources) <= len(result.candidates)
    assert len(result.sources) <= 5
    assert len(result.candidates) <= settings.top_k_candidates


def test_sources_are_numbered_from_one_in_retrieval_order(seeded_corpus: str, db: Any) -> None:
    result = retrieve(seeded_corpus, "kredit", FakeEmbedder(dim=8))
    assert result.sources  # [] == [] would pass this ordering check vacuously
    assert [s.n for s in result.sources] == list(range(1, len(result.sources) + 1))
    # NOT `result.sources == sorted(result.sources, key=lambda s: -s.score)` any
    # more. Under hybrid retrieval `Source.score` is only the dense cosine
    # component -- what `retrieval_floor` gates on and what analytics compares
    # across queries -- while display order is the fused RRF rank. The two
    # coincided under dense-only retrieval by construction; under fusion they
    # deliberately do not, because a document the lexical legs single out must
    # be able to outrank a dense-only near-duplicate. See
    # test_a_lexically_favoured_document_can_outrank_a_higher_dense_score below.


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
    # Pin against the opposite first. A floor of 2.0 is above the maximum
    # possible cosine score (1.0), so `sources == []` below would also pass if
    # retrieve() were broken and always returned []. Proving the SAME query
    # returns real results at the real, measured floor shows the emptiness
    # that follows is the floor's doing.
    assert retrieve(seeded_corpus, query, embedder).sources != []
    monkeypatch.setattr("rag.retrieval.settings.retrieval_floor", 2.0)
    assert retrieve(seeded_corpus, query, embedder).sources == []


def test_retrieval_meets_the_300ms_budget(seeded_corpus: str, db: Any) -> None:
    assert retrieve(seeded_corpus, "kredit", FakeEmbedder(dim=8)).took_ms < 300


def test_every_source_carries_a_listing_url_derived_from_its_own_url(
    seeded_corpus: str, db: Any
) -> None:
    """The footer target is computed here, from a
    document that was actually retrieved -- never written by the model."""
    sources = retrieve(seeded_corpus, "kredit", FakeEmbedder(dim=8)).sources
    assert sources  # an empty list makes the loop below assert nothing
    for s in sources:
        assert s.listing_url and s.url.startswith(s.listing_url)


def test_a_top_level_page_is_its_own_listing_rather_than_the_bare_host() -> None:
    from rag.retrieval import listing_url_for

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
    from rag.retrieval import listing_url_for

    deep = "https://abb-bank.az/haqqimizda/satinalmalar/tender-2026-11"
    assert listing_url_for(deep, {"https://abb-bank.az/haqqimizda"}) == deep
    assert (
        listing_url_for(deep, {"https://abb-bank.az/haqqimizda/satinalmalar"})
        == "https://abb-bank.az/haqqimizda/satinalmalar"
    )


def test_each_chunk_of_the_same_document_keeps_its_own_text(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression. Measured on the live corpus 6fcf823c... over the 45
    golden questions plus the known-failing valyuta question: 29 of 46 were
    affected by the old `source_texts()`, which keyed its lookup dict by URL
    -- so when several chunks of the same document ranked in the same top-k
    (routine: worst case 5 sources sharing 1 distinct text), every chunk but
    the last-fetched one had its text discarded before the prompt was built.

    Seeds THREE documents, one chunk each, so all three reach the prompt under
    `_best_per_document`, and asserts each source's text is its own -- not
    collapsed onto another's. (Before document dedup this test used one
    document with three chunks; dedup now keeps only a document's best chunk,
    so the collapse is provoked across documents instead. The property under
    test is unchanged: a source never carries another source's text.)
    """
    docs = [
        Document(
            url=f"https://abb-bank.az/test/multi-chunk-{i}",
            title=f"Çoxfəsilli sənəd {i}",
            section_path=["Test"],
            source_class="product",
            text=word * 700,
            content_hash=f"sha256:seed-multi-chunk-{i}",
        )
        for i, word in enumerate(("birinci ", "ikinci ", "üçüncü "))
    ]
    corpus_id = ingest_corpus(Corpus(documents=docs), FakeEmbedder(dim=8))
    # FakeEmbedder's cosine scores are hash-derived, not query-relevant, so a
    # chunk can legitimately score below 0. Floor to -2.0 (below the [-1, 1]
    # range) so all 3 chunks clear it regardless of sign -- same technique as
    # test_floor_filters_candidates_below_threshold -- isolating the collapse
    # bug under test from floor filtering.
    monkeypatch.setattr("rag.retrieval.settings.retrieval_floor", -2.0)

    result = retrieve(corpus_id, "kredit", FakeEmbedder(dim=8), k_candidates=10, k_prompt=3)

    assert len(result.sources) == 3
    assert set(result.texts) == {s.n for s in result.sources}
    distinct = {result.texts[s.n] for s in result.sources}
    assert len(distinct) == 3, f"expected 3 distinct texts, got {distinct}"


def test_prompt_gets_distinct_documents_not_several_chunks_of_one(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured on the live corpus: 29 of 46 golden questions filled the prompt
    with two or more chunks of a single document, spending prompt slots on a
    page already represented while the page that answers the question sat below
    the cut. Keeping each document's best chunk lifted recall@5 of the expected
    page from 51% to 58% over the 43 golden rows carrying an expected URL, and
    from 20% to 25% over the 20 informal/typo rows.

    One document of 3 chunks must therefore contribute exactly one source, and a
    second document must get a slot rather than being crowded out."""
    docs = [
        Document(
            url="https://abb-bank.az/test/long-page",
            title="Uzun səhifə",
            section_path=["Test"],
            source_class="product",
            text="\n".join(["birinci " * 700, "ikinci " * 700, "üçüncü " * 700]),
            content_hash="sha256:seed-long-page",
        ),
        Document(
            url="https://abb-bank.az/test/other-page",
            title="Başqa səhifə",
            section_path=["Test"],
            source_class="product",
            text="dördüncü " * 700,
            content_hash="sha256:seed-other-page",
        ),
    ]
    corpus_id = ingest_corpus(Corpus(documents=docs), FakeEmbedder(dim=8))
    monkeypatch.setattr("rag.retrieval.settings.retrieval_floor", -2.0)

    result = retrieve(corpus_id, "kredit", FakeEmbedder(dim=8), k_candidates=10, k_prompt=3)

    urls = [s.url for s in result.sources]
    assert len(urls) == len(set(urls)), f"a document appears twice: {urls}"
    assert set(urls) == {
        "https://abb-bank.az/test/long-page",
        "https://abb-bank.az/test/other-page",
    }


def test_texts_is_empty_when_no_source_clears_the_floor(
    seeded_corpus: str, db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("rag.retrieval.settings.retrieval_floor", 2.0)
    result = retrieve(seeded_corpus, "kredit", FakeEmbedder(dim=8))
    assert result.sources == [] and result.texts == {}


def test_a_trailing_slash_in_the_corpus_still_counts_as_existing() -> None:
    from rag.retrieval import listing_url_for

    assert (
        listing_url_for(
            "https://abb-bank.az/kampaniyalar/yay", {"https://abb-bank.az/kampaniyalar/"}
        )
        == "https://abb-bank.az/kampaniyalar"
    )


def test_a_lexical_only_match_can_reach_the_prompt(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of hybrid retrieval: a document the lexical legs single
    out reaches the prompt even though FakeEmbedder's hash-derived dense
    scores give it no relative advantage over the other document. If this
    passed with the lexical legs deleted, it would not be testing anything --
    RRF still sums a document's presence across the FTS and trigram rankings
    even when its dense rank is the worse of the two."""
    docs = [
        Document(
            url="https://abb-bank.az/test/lexical-match",
            title="Telefon bankçılığı",
            section_path=["Test"],
            source_class="product",
            text="Telefon bankçılığı xidməti mövcuddur.",
            content_hash="sha256:seed-lexical-match",
        ),
        Document(
            url="https://abb-bank.az/test/lexical-miss",
            title="Avtomobil sığortası",
            section_path=["Test"],
            source_class="product",
            text="Avtomobil sığortası təklif olunur.",
            content_hash="sha256:seed-lexical-miss",
        ),
    ]
    corpus_id = ingest_corpus(Corpus(documents=docs), FakeEmbedder(dim=8))
    monkeypatch.setattr("rag.retrieval.settings.retrieval_floor", -2.0)

    result = retrieve(corpus_id, "telefon", FakeEmbedder(dim=8), k_candidates=10, k_prompt=1)

    assert len(result.sources) == 1
    assert result.sources[0].url == "https://abb-bank.az/test/lexical-match"


def test_a_lexically_favoured_document_can_outrank_a_higher_dense_score(
    db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix round 1: the property the old re-sort-by-score destroyed. FakeEmbedder
    gives the car-insurance document a higher raw dense score (0.60) than the
    telephone-banking one (0.33) for the query "telefon" -- verified directly
    against FakeEmbedder(dim=8). Despite that, the telephone-banking document
    is the only one either lexical leg singles out, so fused rank must put it
    first: sources ship in fused order, not re-sorted by the dense component,
    which is exactly what lets a lexically-favoured document with a *lower*
    dense score outrank a dense-only near-duplicate with a higher one."""
    docs = [
        Document(
            url="https://abb-bank.az/test/lexical-match",
            title="Telefon bankçılığı",
            section_path=["Test"],
            source_class="product",
            text="Telefon bankçılığı xidməti mövcuddur.",
            content_hash="sha256:seed-lexical-match-2",
        ),
        Document(
            url="https://abb-bank.az/test/lexical-miss",
            title="Avtomobil sığortası",
            section_path=["Test"],
            source_class="product",
            text="Avtomobil sığortası təklif olunur.",
            content_hash="sha256:seed-lexical-miss-2",
        ),
    ]
    corpus_id = ingest_corpus(Corpus(documents=docs), FakeEmbedder(dim=8))
    monkeypatch.setattr("rag.retrieval.settings.retrieval_floor", -2.0)

    result = retrieve(corpus_id, "telefon", FakeEmbedder(dim=8), k_candidates=10, k_prompt=2)

    assert [s.url for s in result.sources] == [
        "https://abb-bank.az/test/lexical-match",
        "https://abb-bank.az/test/lexical-miss",
    ]
    # The property under test, stated directly: fused rank 1 has the LOWER
    # dense score. A re-sort by -score would put these in the opposite order.
    assert result.sources[0].score < result.sources[1].score


def test_a_query_of_only_stopwords_matches_nothing_lexically() -> None:
    """A tsquery that matched everything would rank the corpus at random."""
    assert _tsquery("ne var hansi") == "zzzznomatch"


def test_fusion_prefers_a_document_two_retrievers_agree_on() -> None:
    """Direct unit test of `_rrf`, no database needed: a document ranked 2nd
    by two lists must outrank a document ranked 1st by only a single list."""
    fused = _rrf(["solo-best", "agreed"], ["other-best", "agreed"])
    assert fused[0] == "agreed"
