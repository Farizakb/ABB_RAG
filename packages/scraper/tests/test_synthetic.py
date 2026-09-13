# packages/scraper/tests/test_synthetic.py
# ruff: noqa: RUF001 -- genuine Azerbaijani fixture/test text.
from datetime import date
from pathlib import Path

from abb_scraper.synthetic import bank_facts_document, index_documents, listing_enumerates
from contracts.models import Document, SourceClass


def doc(
    url: str,
    title: str,
    sc: SourceClass,
    path: list[str] | None = None,
    vt: date | None = None,
) -> Document:
    return Document(
        url=url,
        title=title,
        section_path=path or [],
        source_class=sc,
        text="x" * 500,
        content_hash="sha256:" + title,
        valid_to=vt,
    )


# ---------------------------------------------------------------- unconditional


def test_bank_facts_document_comes_from_ldjson_not_from_prose() -> None:
    html = Path("fixtures/raw/homepage.html").read_text("utf-8")
    d = bank_facts_document(html)
    assert d is not None and d.source_class == "corporate"
    assert "937" in d.text or "+994" in d.text


def test_listing_enumerates_measures_the_named_fraction_of_a_classes_members() -> None:
    """The Step 1 measurement, pinned as a test so the number in RECON's
    dated day-two heading and docs/adr/0006-*.md can be re-derived rather
    than taken on trust."""
    members = ["Kampaniya A", "Kampaniya B", "Kampaniya C"]
    full = "Kampaniyalar\nKampaniya A\nKampaniya B\nKampaniya C"
    shell = "Kampaniyalar"
    assert listing_enumerates(full, members) == 1.0
    assert listing_enumerates(shell, members) == 0.0
    assert round(listing_enumerates("Kampaniya A\nKampaniya B", members), 2) == 0.67
    assert listing_enumerates(full, []) == 0.0  # no members: nothing to enumerate


# ------------------------------------------------------------------ conditional
#
# Step 1 (RECON.md dated day-two heading; docs/adr/0006-*.md) measured the two
# named classes differently, not the same outcome twice: `/ferdi/kreditler`
# names 6/6 of its real body product cards in extracted text (1.00 >= 0.60,
# ABB already enumerates -- no product index), while `/kampaniyalar` returned
# HTTP 404 on direct fetch (no listing page exists to defer to -- campaign
# index built). So only the campaign half of Step 3's conditional block below
# is written; `test_product_index_is_built_per_section_from_the_breadcrumb`
# is dropped along with the product branch of `index_documents` per SPEC
# §8.3's rule (the losing option is deleted, not kept behind a condition).


def test_campaign_index_lists_every_active_campaign_with_its_url() -> None:
    docs = [
        doc("https://abb-bank.az/kampaniyalar/a", "A", "campaign", vt=date(2026, 10, 31)),
        doc("https://abb-bank.az/kampaniyalar/b", "B", "campaign", vt=date(2026, 12, 1)),
    ]
    idx = [d for d in index_documents(docs) if "kampaniyalar" in d.url]
    assert len(idx) == 1
    for d in docs:
        assert d.title in idx[0].text and d.url in idx[0].text


def test_campaign_index_render_order_is_stable_across_input_order() -> None:
    """Invariant 13. The order exists so the generated document is diffable across
    ingests. It is not a recency ranking -- nothing in the corpus means "latest",
    and `valid_to` ascending answers "expiring soonest", a different question."""
    a = doc("https://abb-bank.az/kampaniyalar/late", "Late", "campaign", vt=date(2026, 12, 1))
    b = doc("https://abb-bank.az/kampaniyalar/soon", "Soon", "campaign", vt=date(2026, 10, 1))
    first = next(d for d in index_documents([a, b]) if "kampaniyalar" in d.url)
    second = next(d for d in index_documents([b, a]) if "kampaniyalar" in d.url)
    assert first.text == second.text
    assert first.content_hash == second.content_hash


def test_no_index_document_claims_recency() -> None:
    """Invariant 13, enforced at the source. If the words are never written into the
    chunk, the model cannot retrieve them and echo them back as ABB's own framing.

    Completeness is *not* banned here: a synthetic index is built from every member
    of its class, so "bütün məhsullar" is true. Completeness language is forbidden
    only in the other §5.5 branch, where ABB's page won and we hold no index -- that
    is asserted in the eval set (Task 18), not here."""
    docs = [
        doc("https://abb-bank.az/kampaniyalar/a", "A", "campaign", vt=date(2026, 10, 31)),
        doc("https://abb-bank.az/ferdi/kreditler/b", "B", "product", ["Fərdi", "Kreditlər"]),
    ]
    banned = ("ən son", "son kampaniya", "ən yeni", "latest", "most recent", "newest", "sıralan")
    for d in index_documents(docs):
        haystack = f"{d.title}\n{d.text}".lower()
        assert not any(w in haystack for w in banned), d.title
        # Unnumbered rows: "1." implies a rank we cannot justify.
        assert not any(line.lstrip().startswith(("1.", "2.")) for line in d.text.splitlines())


def test_index_is_anchored_to_a_real_listing_page_so_the_citation_resolves() -> None:
    docs = [doc("https://abb-bank.az/kampaniyalar/a", "A", "campaign", vt=date(2026, 10, 31))]
    idx = next(d for d in index_documents(docs) if d.source_class == "index")
    assert idx.url == "https://abb-bank.az/kampaniyalar"


def test_index_documents_are_never_built_from_other_index_documents() -> None:
    docs = [doc("https://abb-bank.az/kampaniyalar/a", "A", "campaign", vt=date(2026, 10, 31))]
    once = index_documents(docs)
    assert index_documents(docs + once) == once


def test_no_synthetic_index_when_abbs_own_listing_page_already_enumerates() -> None:
    """SPEC §5.5 day-two pre-commitment. ABB runs this query server-side; if their
    page enumerates, theirs wins on ordering, maintenance, and a URL that stays
    correct -- and the answer can then never disagree with §11.2's redirect."""
    members = [
        doc("https://abb-bank.az/kampaniyalar/a", "Kampaniya A", "campaign", vt=date(2026, 10, 31)),
        doc("https://abb-bank.az/kampaniyalar/b", "Kampaniya B", "campaign", vt=date(2026, 12, 1)),
    ]
    listing = Document(
        url="https://abb-bank.az/kampaniyalar",
        title="Kampaniyalar",
        section_path=[],
        source_class="stub",
        text="Kampaniyalar\nKampaniya A\nKampaniya B\n" + "x" * 500,
        content_hash="sha256:listing",
    )
    assert index_documents([*members, listing]) == []


def test_synthetic_index_is_still_built_when_the_listing_page_is_a_client_rendered_shell() -> None:
    members = [
        doc("https://abb-bank.az/kampaniyalar/a", "Kampaniya A", "campaign", vt=date(2026, 10, 31))
    ]
    shell = Document(
        url="https://abb-bank.az/kampaniyalar",
        title="Kampaniyalar",
        section_path=[],
        source_class="stub",
        text="Kampaniyalar\n" + "x" * 500,
        content_hash="sha256:shell",
    )
    assert len(index_documents([*members, shell])) == 1
