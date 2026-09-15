# packages/scraper/tests/test_corpus.py
# ruff: noqa: RUF001 -- genuine Azerbaijani text, not ambiguous-
# character typos; see campaigns.py / facts.py for the same convention.
import json
from datetime import date

from abb_scraper.corpus import build_corpus
from abb_scraper.extract import content_blocks, dedupe_blocks
from abb_scraper.facts import extract_facts
from abb_scraper.fetcher import FetchResult

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


# ------------------------------------------------------------- brief's own tests


def test_expired_campaign_never_reaches_the_corpus() -> None:
    results = [r("/kampaniyalar/old", page("Köhnə").replace("</p>", "01.12.2021 - 10.01.2022</p>"))]
    corpus, dropped = build_corpus(results, TODAY)
    assert corpus.documents == []
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
    """Task 12 Step 2 measured ABB's own listing pages and decided to build a
    campaign-only index; see synthetic.py's `index_documents`."""
    results = [r("/kampaniyalar/new", page("Yeni").replace("</p>", "01.09.2026 - 31.10.2026</p>"))]
    corpus, _ = build_corpus(results, TODAY)
    assert any(d.source_class == "index" for d in corpus.documents)


# --------------------------------------------------- open item 1: unknown campaigns


def test_unknown_campaign_with_no_date_range_is_dropped_and_reported() -> None:
    """Controller ruling P47: a campaign page carrying no parseable date range
    classifies `unknown` (campaigns.classify), never `active`. It must be
    withheld from the corpus and the drop visibly reported, not silently
    omitted -- the same DropRecord mechanism as `campaign-expired`."""
    html = page("Naməlum kampaniya")  # no dd.mm.yyyy range anywhere in the body
    results = [r("/kampaniyalar/no-dates", html)]
    corpus, dropped = build_corpus(results, TODAY)
    assert corpus.documents == []
    assert dropped[0].reason == "campaign-unknown"


def test_future_dated_campaign_is_dropped_as_unknown_not_active() -> None:
    """P47's other unknown case: a campaign whose validity window has not
    started yet must not be shown as active. Start date 2026-10-01 is after
    TODAY (2026-09-14)."""
    html = page("Gələcək kampaniya").replace("</p>", "01.10.2026 - 31.10.2026</p>")
    results = [r("/kampaniyalar/future", html)]
    corpus, dropped = build_corpus(results, TODAY)
    assert corpus.documents == []
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
    """Controller ruling P9 (load-bearing): the brief's own reference
    implementation calls `extract_facts([], title, url) if sc != "product"
    else []`, which is both an inverted condition and an empty block list --
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
    """The other half of ruling P9's gate: a non-product page carrying the
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
    """Task 11's finding: a repeated stat block pair on one page (a
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
    assert corpus.documents == []
    assert dropped[0].reason == "under-min-chars"


def test_cross_document_duplicate_bodies_are_dropped_via_build_corpus() -> None:
    html = page("Eyni məzmun")
    results = [r("/ferdi/a", html), r("/ferdi/b", html)]
    corpus, dropped = build_corpus(results, TODAY)
    assert len(corpus.documents) == 1
    assert dropped[0].reason == "cross-document-duplicate"


def test_non_200_results_are_dropped_with_their_status() -> None:
    results = [FetchResult("https://abb-bank.az/gone", "https://abb-bank.az/gone", 404, "", False)]
    corpus, dropped = build_corpus(results, TODAY)
    assert corpus.documents == []
    assert dropped[0].reason == "status-404"


# ------------------------------------------------------------------- volatile


VOLATILE_TITLE = (
    "Valyuta Məzənnəsi | Canlı Valyuta konvertoru ABB — nağd və nağdsız alış-satış qiymətləri"
)


def test_volatile_page_is_stored_as_a_pointer_not_full_body() -> None:
    """SPEC §5.4 pointer mode: title and description only, real body discarded
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
    """The rate table goes stale and is discarded (SPEC §5.4); the FAQ does not.

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
