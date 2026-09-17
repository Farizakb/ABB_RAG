# backend/scraper/tests/test_corpus.py
# ruff: noqa: RUF001, RUF002 -- genuine Azerbaijani text, not ambiguous-
# character typos; see campaigns.py / facts.py for the same convention.
import json
from datetime import date

from abb_scraper.corpus import build_corpus
from abb_scraper.extract import content_blocks, dedupe_blocks
from abb_scraper.facts import extract_facts
from abb_scraper.fetcher import FetchResult
from shared.contracts import Corpus, Document

TODAY = date(2026, 9, 14)


def r(path: str, html: str) -> FetchResult:
    url = "https://abb-bank.az" + path
    return FetchResult(url, url, 200, html, False)


def page(title: str, body_chars: int = 900) -> str:
    return (
        f"<html><head><title>{title}</title>"
        f'<meta name="description" content="{title} haqqında">'
        f"</head><body><h1>{title}</h1><p>{'x' * body_chars}</p></body></html>"
    )


# `pointer_documents` unconditionally adds these two
# hand-authored pointers to every corpus (ABB's branch/ATM
# situation does not depend on which pages a given scrape happened to fetch).
# Tests below that assert "nothing reaches the corpus" from these tiny,
# single-page fixtures must look past the pointers to
# the pages they are actually pinning the drop behaviour of.
POINTER_URLS = {"https://abb-bank.az/filiallar", "https://abb-bank.az/atmler"}


def non_pointer_docs(corpus: Corpus) -> list[Document]:
    return [d for d in corpus.documents if d.url not in POINTER_URLS]


# ------------------------------------------------------------- brief's own tests


def test_expired_campaign_never_reaches_the_corpus() -> None:
    results = [r("/kampaniyalar/old", page("Köhnə").replace("</p>", "01.12.2021 - 10.01.2022</p>"))]
    corpus, dropped = build_corpus(results, TODAY)
    assert non_pointer_docs(corpus) == []
    assert dropped[0].reason == "campaign-expired"


def test_active_campaign_is_kept_with_its_validity_dates() -> None:
    results = [r("/kampaniyalar/new", page("Yeni").replace("</p>", "01.09.2026 - 31.10.2026</p>"))]
    corpus, _ = build_corpus(results, TODAY)
    assert corpus.documents[0].valid_to == date(2026, 10, 31)


def test_corpus_id_is_stable_across_two_identical_runs() -> None:
    results = [r("/ferdi/kreditler/a", page("Nağd kredit"))]
    assert build_corpus(results, TODAY)[0].corpus_id == build_corpus(results, TODAY)[0].corpus_id


def test_stats_report_fetched_kept_and_dropped() -> None:
    results = [r("/ferdi/a", page("Real")), r("/empty", page("Boş", body_chars=10))]
    corpus, dropped = build_corpus(results, TODAY)
    assert corpus.stats["pages_fetched"] == 2
    assert corpus.stats["pages_kept"] == len(corpus.documents)
    assert corpus.stats["pages_dropped"] == len(dropped)


def test_index_documents_are_appended_after_the_gates_have_run() -> None:
    """Measured ABB's own listing pages and decided to build a
    campaign-only index; see synthetic.py's `index_documents`."""
    results = [r("/kampaniyalar/new", page("Yeni").replace("</p>", "01.09.2026 - 31.10.2026</p>"))]
    corpus, _ = build_corpus(results, TODAY)
    assert any(d.source_class == "index" for d in corpus.documents)


# --------------------------------------------------- open item 1: unknown campaigns


def test_unknown_campaign_with_no_date_range_is_dropped_and_reported() -> None:
    """A campaign page carrying no parseable date range
    classifies `unknown` (campaigns.classify), never `active`. It must be
    withheld from the corpus and the drop visibly reported, not silently
    omitted -- the same DropRecord mechanism as `campaign-expired`."""
    html = page("Naməlum kampaniya")  # no dd.mm.yyyy range anywhere in the body
    results = [r("/kampaniyalar/no-dates", html)]
    corpus, dropped = build_corpus(results, TODAY)
    assert non_pointer_docs(corpus) == []
    assert dropped[0].reason == "campaign-unknown"


def test_future_dated_campaign_is_dropped_as_unknown_not_active() -> None:
    """The other unknown case: a campaign whose validity window has not
    started yet must not be shown as active. Start date 2026-10-01 is after
    TODAY (2026-09-14)."""
    html = page("Gələcək kampaniya").replace("</p>", "01.10.2026 - 31.10.2026</p>")
    results = [r("/kampaniyalar/future", html)]
    corpus, dropped = build_corpus(results, TODAY)
    assert non_pointer_docs(corpus) == []
    assert dropped[0].reason == "campaign-unknown"
    assert dropped[0].url.endswith("/kampaniyalar/future")


def test_unknown_campaign_drop_is_counted_in_corpus_stats() -> None:
    """The count half of "dropped and reported": an unknown campaign must show
    up in `stats["pages_dropped"]`, not just in the `dropped` list a caller
    might ignore."""
    html = page("Naməlum kampaniya")
    results = [r("/kampaniyalar/no-dates", html)]
    corpus, dropped = build_corpus(results, TODAY)
    assert corpus.stats["pages_dropped"] == len(dropped) == 1


# ------------------------------------------------------- open item 2: fact dedupe


def test_facts_are_extracted_for_product_pages() -> None:
    """A naive implementation calling `extract_facts([], title, url) if sc != "product"
    else []` is both an inverted condition and an empty block list --
    zero facts ever reach the corpus. This pins the fix: a real stat block on
    a product page must produce a governed Fact row, and its figure must be
    retrievable in `Document.text` via `reassemble`."""
    html = (
        "<html><head><title>Nağd kredit</title>"
        '<meta name="description" content="Nağd kredit haqqında">'
        "</head><body><h1>Nağd kredit</h1>"
        "<p>50 000 AZN-dək</p><div>Məbləğ</div>"
        f"<p>{'x' * 400}</p></body></html>"
    )
    results = [r("/ferdi/kreditler/nagd", html)]
    corpus, _ = build_corpus(results, TODAY)
    doc = corpus.documents[0]
    assert any(f.attribute == "max_amount" and f.value_num == 50000.0 for f in doc.facts)
    assert "50 000 AZN-dək" in doc.text


def test_facts_are_not_extracted_outside_product_pages() -> None:
    """The other half of the gate: a non-product page carrying the
    exact same stat-block shape must not produce facts -- extract_facts is
    only ever called for source_class == "product"."""
    html = (
        "<html><head><title>Haqqımızda</title>"
        '<meta name="description" content="Haqqımızda haqqında">'
        "</head><body><h1>Haqqımızda</h1>"
        "<p>50 000 AZN-dək</p><div>Məbləğ</div>"
        f"<p>{'x' * 400}</p></body></html>"
    )
    results = [r("/haqqimizda", html)]
    corpus, _ = build_corpus(results, TODAY)
    doc = corpus.documents[0]
    assert doc.source_class == "corporate"
    assert doc.facts == []


def test_cross_block_duplicate_facts_collapse_to_one_row() -> None:
    """A repeated stat block pair on one page (a
    responsive mobile/desktop duplicate of the same DOM, observed on
    nagd-kredit.html and biznes-sub-kicik-orta.html) produces more than one
    raw `Fact` row. Proven below that two raw rows really survive
    `build_corpus`'s own block-level dedupe (`dedupe_blocks`) -- so the
    collapse asserted afterwards genuinely exercises fact-level dedupe, not
    block-level dedupe doing the work incidentally. The value block differs
    by a trailing period and the label block by a trailing colon, so
    `dedupe_blocks` (which folds case/whitespace only) keeps all four
    blocks; `facts._label_key` strips the trailing colon, so both labels
    still resolve to the governed `max_amount` attribute. `build_corpus`'s
    own fact-level dedupe, keyed on the natural key
    (attribute/value_num/value_text/unit/currency) rather than
    `raw_fragment`, must be what collapses the two rows into one."""
    path = "/ferdi/kreditler/test-product"
    body = (
        "<p>50 000 AZN-dək</p><div>Məbləğ</div>"
        "<p>50 000 AZN-dək.</p><div>Məbləğ:</div>"
        f"<p>{'x' * 400}</p>"
    )
    html = (
        "<html><head><title>Test Kredit</title>"
        '<meta name="description" content="Test kredit haqqında">'
        f"</head><body><h1>Test Kredit</h1>{body}</body></html>"
    )

    # Proof: two raw Fact rows survive block-level dedup, so the collapse
    # asserted below is fact-level dedupe's own work, not dedupe_blocks's.
    blocks, _ = content_blocks(html, path)
    deduped_blocks, _ = dedupe_blocks(blocks)
    assert len(deduped_blocks) == len(blocks)  # nothing collapsed at the block level
    raw_facts = [
        f for f in extract_facts(deduped_blocks, "Test Kredit", "x") if f.attribute == "max_amount"
    ]
    assert len(raw_facts) == 2  # proof: two raw rows exist before fact-level dedupe
    assert raw_facts[0].raw_fragment != raw_facts[1].raw_fragment

    results = [r(path, html)]
    corpus, _ = build_corpus(results, TODAY)
    doc = corpus.documents[0]
    max_amount_facts = [f for f in doc.facts if f.attribute == "max_amount"]
    assert len(max_amount_facts) == 1
    assert max_amount_facts[0].value_num == 50000.0


# ------------------------------------------------------------------- gate wiring


def test_under_min_char_pages_are_dropped_via_build_corpus() -> None:
    results = [r("/empty", page("Boş", body_chars=10))]
    corpus, dropped = build_corpus(results, TODAY)
    assert non_pointer_docs(corpus) == []
    assert dropped[0].reason == "under-min-chars"


def test_cross_document_duplicate_bodies_are_dropped_via_build_corpus() -> None:
    html = page("Eyni məzmun")
    results = [r("/ferdi/a", html), r("/ferdi/b", html)]
    corpus, dropped = build_corpus(results, TODAY)
    assert len(non_pointer_docs(corpus)) == 1
    assert dropped[0].reason == "cross-document-duplicate"


def test_non_200_results_are_dropped_with_their_status() -> None:
    results = [FetchResult("https://abb-bank.az/gone", "https://abb-bank.az/gone", 404, "", False)]
    corpus, dropped = build_corpus(results, TODAY)
    assert non_pointer_docs(corpus) == []
    assert dropped[0].reason == "status-404"


# ------------------------------------------------------------------- volatile


VOLATILE_TITLE = (
    "Valyuta Məzənnəsi | Canlı Valyuta konvertoru ABB — nağd və nağdsız alış-satış qiymətləri"
)


def test_volatile_page_is_stored_as_a_pointer_not_full_body() -> None:
    """Pointer mode: title and description only, real body discarded
    (exchange rates change daily and would go stale in the corpus).

    The gate now sizes the POINTER, not the pre-truncation page: `strip_chrome`
    rebuilds `char_count` from the pieces that survive, so a volatile page is
    quality-gated on what actually reaches the corpus. The real page clears it
    comfortably -- its own title and description are 202 chars before its FAQ is
    added -- so the fixture carries a realistically long title rather than a
    two-word one.
    """
    html = page(VOLATILE_TITLE, body_chars=900)
    results = [r("/ferdi/valyuta-mezenneleri", html)]
    corpus, _ = build_corpus(results, TODAY)
    doc = corpus.documents[0]
    assert doc.source_class == "volatile"
    assert "x" * 900 not in doc.text


def test_no_two_documents_ever_share_a_url() -> None:
    """The synthetic campaign index is published at /ferdi/kampaniyalar, the URL
    of the real hub page. That hub is a 320-char client-rendered shell: under the
    old 400 gate it was dropped and the two never met, but at the re-measured 250
    it clears the gate, so both would be emitted under one URL. Retrieval keys
    chunk text by URL, so the duplicate would silently shadow one of them."""
    results = [
        r("/kampaniyalar/aktiv", page("Aktiv").replace("</p>", "01.09.2026 - 31.12.2026</p>")),
        r("/ferdi/kampaniyalar", page("Kampaniyalar", body_chars=260)),
    ]
    corpus, _ = build_corpus(results, TODAY)
    urls = [d.url for d in corpus.documents]
    assert len(urls) == len(set(urls)), f"duplicate url in corpus: {urls}"

    hub = [d for d in corpus.documents if d.url.endswith("/ferdi/kampaniyalar")]
    assert len(hub) == 1 and hub[0].source_class == "index", "the enumerating index must win"
    assert "aktiv" in hub[0].text.lower()


# ------------------------------------------------------ rate table stripping


def test_rate_table_numbers_are_stripped_but_prose_survives() -> None:
    """Live-probed 2026-09-15: the assistant quoted a four-day-stale USD rate
    as fact from a page whose table is stamped `Son yenilənmə: 11.09.2026`.
    The same rate widget ABB embeds on /ferdi/valyuta-mezenneleri also appears
    on ordinary pages -- measured: /ferdi, and the two live-mezenne-converter
    stubs. Detected by shape (Alış + Satış + a 4-decimal number), not by URL,
    and only the bare numeric line is dropped -- the page's own prose must
    still reach the corpus."""
    html = (
        "<html><head><title>ABB Fərdi</title>"
        '<meta name="description" content="Fərdi məhsullar haqqında məlumat">'
        "</head><body><h1>ABB Fərdi</h1>"
        "<p>Alış</p><p>Satış</p><p>1.7020</p>"
        f"<p>{'x' * 400}</p></body></html>"
    )
    corpus, _ = build_corpus([r("/ferdi", html)], TODAY)
    doc = corpus.documents[0]
    assert "1.7020" not in doc.text
    assert "x" * 400 in doc.text


def test_alis_satis_without_a_rate_number_is_left_untouched() -> None:
    """The false positive `has_rate_table`'s conjunction exists to exclude:
    /ferdi/investisiya legitimately discusses buying and selling securities
    (Alış/Satış) without ever carrying a live rate figure. `Alış`/`Satış`
    alone matched 4 docs corpus-wide; neither half of the detector is usable
    alone."""
    html = (
        "<html><head><title>İnvestisiya</title>"
        '<meta name="description" content="İnvestisiya məhsulları haqqında">'
        "</head><body><h1>İnvestisiya</h1>"
        "<p>Alış qiyməti barədə məlumat</p><p>Satış qiyməti barədə məlumat</p>"
        f"<p>{'y' * 400}</p></body></html>"
    )
    corpus, _ = build_corpus([r("/ferdi/investisiya", html)], TODAY)
    doc = corpus.documents[0]
    assert "Alış qiyməti barədə məlumat" in doc.text
    assert "Satış qiyməti barədə məlumat" in doc.text


def test_a_bare_decimal_number_without_alis_satis_is_left_untouched() -> None:
    """The other false positive the conjunction excludes: a 4-decimal number
    alone matched 17 docs corpus-wide, including the miles cards' 0.6667
    conversion ratio and bare dates like 09.2026 -- neither carries a rate
    table and neither may be touched."""
    html = (
        "<html><head><title>Miles kartı</title>"
        '<meta name="description" content="Miles kartı haqqında məlumat">'
        "</head><body><h1>Miles kartı</h1>"
        "<p>0.6667</p>"
        f"<p>{'z' * 400}</p></body></html>"
    )
    corpus, _ = build_corpus([r("/ferdi/miles-karti", html)], TODAY)
    doc = corpus.documents[0]
    assert "0.6667" in doc.text


def test_rate_table_page_keeps_its_product_card_prose_not_pointer_mode() -> None:
    """/ferdi is a real landing page (Nağd kredit, Tam Visa, Biznes kartı,
    DigiTravel product cards, measured 4,023 extracted chars) that happens to
    embed the rate widget -- stripping the numbers must not collapse it into
    pointer mode the way /ferdi/valyuta-mezenneleri's own volatile branch
    does; that would destroy genuine landing-page content."""
    html = (
        "<html><head><title>ABB Fərdi</title>"
        '<meta name="description" content="Fərdi məhsullar haqqında məlumat">'
        "</head><body><h1>ABB Fərdi</h1>"
        "<p>Alış</p><p>Satış</p><p>1.9334</p>"
        "<p>Nağd kredit əldə edin</p>"
        f"<p>{'w' * 400}</p></body></html>"
    )
    corpus, _ = build_corpus([r("/ferdi", html)], TODAY)
    doc = corpus.documents[0]
    assert doc.source_class != "volatile"
    assert "Nağd kredit əldə edin" in doc.text
    assert "1.9334" not in doc.text


def _flight_faq(slug: str, question: str, answer: str) -> str:
    """One accordion item in the Next.js flight stream, the way ABB emits it:
    a `self.__next_f.push` row carrying a `\\u003c`-escaped React Query record."""
    obj = json.dumps({"trigger": question, "content": f"<p>{answer}</p>"}, separators=(",", ":"))
    row = (
        f'"state":{{"data":{{"data":[{{"id":1,"slug":"{slug}","url":"ferdi/{slug}"'
        f',"sections":[{obj.replace("<", chr(92) + "u003c")}]}}]}}}}'
    )
    return f"<script>self.__next_f.push([1,{json.dumps(row)}])</script>"


def test_volatile_pointer_keeps_its_faq_while_discarding_the_rate_body() -> None:
    """The rate table goes stale and is discarded; the FAQ does not.

    /ferdi/valyuta-mezenneleri carries 21 Q&A pairs that answer things
    independent of the rate — whether there is a commission, how to read the
    live rate. Pointer mode was discarding all 21 with the table, leaving the
    page's only retrievable text its own meta description (measured 2026-09-15:
    21 pairs on the page, 0 reaching the corpus).
    """
    question = "Valyuta köçürməsinə komissiya tutulurmu?"
    answer = "Xeyr, ABB mobile vasitəsilə edilən ilk köçürmə komissiyasızdır."
    html = page(VOLATILE_TITLE, body_chars=900).replace(
        "</body>", _flight_faq("valyuta-mezenneleri", question, answer) + "</body>"
    )
    corpus, _ = build_corpus([r("/ferdi/valyuta-mezenneleri", html)], TODAY)

    doc = corpus.documents[0]
    assert doc.source_class == "volatile"
    assert "x" * 900 not in doc.text, "the rate body must still be discarded"
    assert question in doc.text and answer in doc.text, "the FAQ must survive pointer mode"
