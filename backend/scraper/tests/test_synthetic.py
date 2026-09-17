# backend/scraper/tests/test_synthetic.py
# ruff: noqa: RUF001 -- genuine Azerbaijani fixture/test text.
import re
from datetime import date
from pathlib import Path

from abb_scraper.synthetic import (
    _abb_already_enumerates,
    bank_facts_document,
    index_documents,
    listing_enumerates,
    pointer_documents,
)
from shared.contracts import Document, SourceClass


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
    html = Path("backend/scraper/tests/fixtures/raw/homepage.html").read_text("utf-8")
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
# Measured (docs/adr/0006-*.md) the two named classes differently, not the
# same outcome twice: `/ferdi/kreditler` names 6/6 of its real body product
# cards in extracted text (1.00 >= 0.60, ABB already enumerates -- no
# product index), while `/kampaniyalar` returned HTTP 404 on direct fetch
# (no listing page exists to defer to -- campaign index built). So only the
# campaign half of the conditional block below is written;
# `test_product_index_is_built_per_section_from_the_breadcrumb` is dropped
# along with the product branch of `index_documents` (the losing option is
# deleted, not kept behind a condition).


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
    only in the other branch, where ABB's page won and we hold no index -- that
    is asserted in the eval set, not here."""
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
    """The bare `/kampaniyalar` is
    confirmed absent from the site (two direct 404s, and absent from the
    sitemap's 7,042 entries even though 247 of its own children are
    present). The real, HTTP-200 campaigns hub is `/ferdi/kampaniyalar`
    (`backend/scraper/tests/fixtures/raw/listing-ferdi-kampaniyalar.html`) -- a client-rendered
    shell content-wise, but a page that actually resolves, which is what
    this test pins."""
    docs = [doc("https://abb-bank.az/kampaniyalar/a", "A", "campaign", vt=date(2026, 10, 31))]
    idx = next(d for d in index_documents(docs) if d.source_class == "index")
    assert idx.url == "https://abb-bank.az/ferdi/kampaniyalar"


def test_index_documents_are_never_built_from_other_index_documents() -> None:
    docs = [doc("https://abb-bank.az/kampaniyalar/a", "A", "campaign", vt=date(2026, 10, 31))]
    once = index_documents(docs)
    assert index_documents(docs + once) == once


def test_no_synthetic_index_when_abbs_own_listing_page_already_enumerates() -> None:
    """ABB runs this query server-side; if their
    page enumerates, theirs wins on ordering, maintenance, and a URL that stays
    correct -- and the answer can then never disagree with the redirect."""
    members = [
        doc("https://abb-bank.az/kampaniyalar/a", "Kampaniya A", "campaign", vt=date(2026, 10, 31)),
        doc("https://abb-bank.az/kampaniyalar/b", "Kampaniya B", "campaign", vt=date(2026, 12, 1)),
    ]
    listing = Document(
        url="https://abb-bank.az/ferdi/kampaniyalar",
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
        url="https://abb-bank.az/ferdi/kampaniyalar",
        title="Kampaniyalar",
        section_path=[],
        source_class="stub",
        text="Kampaniyalar\n" + "x" * 500,
        content_hash="sha256:shell",
    )
    assert len(index_documents([*members, shell])) == 1


def test_exactly_at_the_060_threshold_counts_as_already_enumerates() -> None:
    """`ENUMERATES_THRESHOLD` (0.60) is the pre-committed
    rule deciding whether a synthetic index exists at all, not a readability
    heuristic (contrast `facts.MAX_FRAGMENT_CHARS`, left unpinned) --
    so its boundary is pinned here. A mutation of `_abb_already_enumerates`'s
    `>=` to `>` left the whole suite green before this test existed.

    5 members, 3 named in the listing text: a genuine 3/5 = 0.60, asserted via
    `listing_enumerates` itself (not hand-verified against the fixture) before
    being routed through `_abb_already_enumerates`, the function that actually
    owns this comparison."""
    members = [
        doc(f"https://abb-bank.az/kampaniyalar/{c}", f"Kampaniya {c}", "campaign") for c in "ABCDE"
    ]
    listing = Document(
        url="https://abb-bank.az/ferdi/kampaniyalar",
        title="Kampaniyalar",
        section_path=[],
        source_class="stub",
        text="Kampaniyalar\nKampaniya A\nKampaniya B\nKampaniya C\n" + "x" * 500,
        content_hash="sha256:boundary",
    )
    fraction = listing_enumerates(listing.text, [m.title for m in members])
    assert fraction == 0.6  # proof the fixture genuinely sits at the boundary
    assert (
        _abb_already_enumerates(
            "https://abb-bank.az/ferdi/kampaniyalar", members, [*members, listing]
        )
        is True
    )


# --------------------------------------------------------------- pointers
#
# Pre-commitment: "No pointer unless the bank-facts document
# fails to produce a plausible grounded answer." Measured, live: it
# fails for branch and ATM location questions -- either a refusal citing
# unrelated pages, or (worse) a grounded-looking answer citing the Android
# privacy policy. The branch/ATM list is a client-side map widget, so no
# address is ever present in the fetched HTML for retrieval to find.


def test_pointer_bodies_state_no_number_and_no_product_fact() -> None:
    """The constraint on any hand-authored pointer: it may state that
    a page exists and what it lists, and must never state a product fact.
    Checked with no scraped /atmler present, so both pointers are their own,
    unmerged text -- the standalone form the constraint is written about."""
    pointers = pointer_documents([])
    assert {d.url for d in pointers} == {
        "https://abb-bank.az/filiallar",
        "https://abb-bank.az/atmler",
    }
    for d in pointers:
        assert "%" not in d.text
        assert "AZN" not in d.text
        assert not re.search(r"\d{3,}", d.text), d.text


def test_pointer_documents_never_duplicates_a_scraped_url() -> None:
    """Retrieval chunks by URL (corpus.py), so two documents at the same URL
    would silently shadow one of them. `/atmler` is a real scraped usage-FAQ
    page (deposit methods, limits, commissions -- no locations); its pointer
    must not be emitted as a second, competing document at that URL."""
    atmler = doc(
        "https://abb-bank.az/atmler",
        "Bankomatlar haqqında suallar",
        "stub",
    )
    scraped = [atmler]
    pointers = pointer_documents(scraped)
    urls = [d.url for d in pointers]
    assert len(urls) == len(set(urls))

    combined = [d for d in scraped if d.url not in urls] + pointers
    combined_urls = [d.url for d in combined]
    assert len(combined_urls) == len(set(combined_urls)), combined_urls


def test_atmler_pointer_text_is_merged_into_the_scraped_atmler_document() -> None:
    """The pointer text is appended to the real page's own text rather than
    emitted twice -- both the existing FAQ content and the new pointer
    sentence must survive in the single document that reaches the corpus."""
    atmler = Document(
        url="https://abb-bank.az/atmler",
        title="Bankomatlar haqqında suallar",
        section_path=["Fərdi"],
        source_class="stub",
        text="Bankomatdan pul çıxarmaq üçün kartınızı daxil edin.",
        content_hash="sha256:atmler-faq",
    )
    pointers = pointer_documents([atmler])
    merged = next(d for d in pointers if d.url == "https://abb-bank.az/atmler")
    assert "Bankomatdan pul çıxarmaq üçün kartınızı daxil edin." in merged.text
    assert "Bankomatların ünvanları bu sənəddə saxlanılmır" in merged.text
