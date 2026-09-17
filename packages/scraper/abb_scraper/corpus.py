# packages/scraper/abb_scraper/corpus.py
from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from urllib.parse import urlparse

from abb_scraper.campaigns import CampaignStatus, classify
from abb_scraper.extract import (
    Block,
    DropRecord,
    PageText,
    apply_gates,
    content_blocks,
    dedupe_blocks,
    extract_page,
    faq_blocks,
    has_rate_table,
    strip_chrome,
    strip_rate_numbers,
)
from abb_scraper.facts import extract_facts, reassemble
from abb_scraper.fetcher import FetchResult
from abb_scraper.synthetic import bank_facts_document, index_documents, pointer_documents
from contracts.models import Corpus, Document, Fact

VOLATILE_PATHS = ("/ferdi/valyuta-mezenneleri",)


def _source_class(path: str) -> str:
    if path.startswith(VOLATILE_PATHS):
        return "volatile"
    if path.startswith("/kampaniyalar/"):
        return "campaign"
    if path.startswith(("/ferdi/", "/biznes/")):
        return "product"
    if path.startswith(("/haqqimizda", "/investorlarla-elaqe")):
        return "corporate"
    return "stub"


def _fact_key(f: Fact) -> tuple[str, float | None, str | None, str | None, str | None]:
    return (f.attribute, f.value_num, f.value_text, f.unit, f.currency)


def _dedupe_facts(facts: list[Fact]) -> list[Fact]:
    """The same stat block pair (value, label) can repeat
    verbatim elsewhere on one page -- a responsive mobile/desktop duplicate of
    the same DOM subtree, observed on nagd-kredit.html and
    biznes-sub-kicik-orta.html -- and `extract_facts` does not dedupe its own
    input, so two identical `Fact` rows reach the corpus for the same page.

    Dedupe on the natural key: the governed value fields (`attribute`,
    `value_num`, `value_text`, `unit`, `currency`), not `raw_fragment` or
    `source_url`, which may legitimately differ in formatting for what is
    still the same fact. First occurrence wins; order is otherwise preserved.
    """
    seen: set[tuple[str, float | None, str | None, str | None, str | None]] = set()
    out: list[Fact] = []
    for f in facts:
        key = _fact_key(f)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out


def build_corpus(results: list[FetchResult], today: date) -> tuple[Corpus, list[DropRecord]]:
    pages: list[tuple[str, PageText]] = []
    dropped: list[DropRecord] = []
    class_by_url: dict[str, str] = {}
    status_by_url: dict[str, CampaignStatus] = {}
    blocks_by_url: dict[str, list[Block]] = {}
    homepage_html = ""

    for res in results:
        if res.status != 200 or not res.html:
            dropped.append(DropRecord(res.url, f"status-{res.status}", 0))
            continue
        path = urlparse(res.url).path or "/"
        if path == "/":
            homepage_html = res.html
        sc = _source_class(path)
        page = extract_page(res.html, res.url)

        if has_rate_table(page.text):
            # A live FX rate table, stripped wherever the widget is embedded --
            # not just on /ferdi/valyuta-mezenneleri's own
            # volatile page below. Measured: two more pages carry
            # the identical widget and reach the corpus as ordinary "stub"
            # pages, one of them (/ferdi) a real landing page whose
            # product-card prose must survive -- so only the bare numbers are
            # dropped and the rest of the page is kept, unlike the volatile
            # branch's pointer mode which discards the whole body. Runs
            # before source-class branching so it reaches every class; for
            # the volatile page itself this is a no-op, since the branch
            # below replaces these blocks outright regardless.
            stripped = strip_rate_numbers(list(page.block_texts))
            body = "\n".join(stripped)
            text = "\n".join(page.head + tuple(stripped))
            page = page._replace(
                text=text, body=body, char_count=len(text), block_texts=tuple(stripped)
            )

        if sc == "volatile":
            # Pointer mode: title and description only, rate body discarded --
            # a rate table is stale the moment it is embedded.
            #
            # The FAQ is not a rate table and is kept. /ferdi/valyuta-mezenneleri
            # carries 21 Q&A pairs answering things that do not move with the
            # rate -- whether there is a commission, how to see the live rate,
            # which currencies are traded -- and pointer mode was discarding all
            # 21 along with the table, leaving the page's only retrievable text
            # its own meta description (measured: 21 pairs, 0 in the
            # corpus). `[]` as the DOM blocks is deliberate: the body they would
            # have been deduped against is exactly what pointer mode throws away.
            faq = tuple(b.text for b in faq_blocks(res.html, [], path))
            pointer = "\n".join(page.head + faq)
            page = page._replace(
                text=pointer,
                body="\n".join(faq),
                char_count=len(pointer),
                block_texts=faq,
            )

        if sc == "product":
            # Pull the real block list and dedupe it the same
            # way extract_page's own body is deduped, so a page whose stat block
            # is rendered twice (responsive duplicate DOM) does not double-count
            # here before `_dedupe_facts` even runs below.
            raw_blocks, _ = content_blocks(res.html, path)
            blocks_by_url[res.url], _ = dedupe_blocks(raw_blocks)

        if sc == "campaign":
            status = classify(page.text, today)
            if status.status != "active":
                # A campaign that classifies "unknown"
                # (no date range, or a start date still in the future) must be
                # withheld, not shown as active -- and withheld visibly, via a
                # DropRecord, never by silent omission. Same mechanism covers
                # "expired".
                dropped.append(DropRecord(res.url, f"campaign-{status.status}", page.char_count))
                continue
            status_by_url[res.url] = status

        pages.append((res.url, page))
        class_by_url[res.url] = sc

    # Site-wide marketing rendered inside the content region, removed before the
    # gate judges size: an empty shell is title+description chrome and nothing
    # else, so stripping collapses it to 0 chars and it can no longer be
    # confused with a short but real page.
    pages, _chrome_removed = strip_chrome(pages)

    kept, gate_drops = apply_gates(pages)
    dropped.extend(gate_drops)

    docs: list[Document] = []
    for url, page in kept:
        sc = class_by_url[url]
        title = page.crumbs[-1] if page.crumbs else page.text.split("\n", 1)[0]
        status = status_by_url.get(url)
        facts = (
            _dedupe_facts(extract_facts(blocks_by_url.get(url, []), title, url))
            if sc == "product"
            else []
        )
        text = page.text
        if facts:
            text = f"{reassemble(facts, title)}\n{text}"
        docs.append(
            Document(
                url=url,
                canonical_url=url,
                title=title,
                section_path=page.crumbs[:-1],
                source_class=sc,
                text=text,
                facts=facts,
                status=status.status if status else None,
                valid_from=status.valid_from if status else None,
                valid_to=status.valid_to if status else None,
                content_hash="sha256:" + hashlib.sha256(text.encode()).hexdigest(),
                fetched_at=datetime.now(UTC),
            )
        )

    if homepage_html and (bank := bank_facts_document(homepage_html)):
        docs.append(bank)
    # Measured against ABB's own listing pages: /ferdi/kreditler already
    # enumerates its products (1.00 >= 0.60, no product index), /ferdi/kampaniyalar
    # does not (0.00 < 0.60, campaign index built). Only the campaign branch of
    # index_documents exists; this call is unconditional per that measured verdict.
    # A synthetic index is published at the URL of the real hub page it
    # enumerates. That hub (/ferdi/kampaniyalar, a 320-char client-rendered
    # shell) would otherwise be dropped by the size gate, so the two never
    # collided -- but the gate is now low enough that the shell clears it,
    # which would emit two documents under one URL. Retrieval keys chunk text by URL
    # (retrieval.source_texts), so a duplicate silently shadows one of them.
    # The index wins: it carries the enumeration the shell lacks, and the
    # shell's own text is just the heading the index already restates.
    indexes = index_documents(docs)
    shadowed = {d.url for d in indexes}
    docs = [d for d in docs if d.url not in shadowed] + indexes

    # Hand-authored pointers for branch/ATM location
    # questions, which the scrape cannot answer (client-side map widget, no
    # addresses ever reach the HTML). `pointer_documents` already resolves the
    # /atmler URL collision -- it returns that page's own Document with the
    # pointer text appended when it is present in `docs`, so shadowing here
    # replaces rather than duplicates it, the same mechanism used for the
    # campaign index above.
    pointers = pointer_documents(docs)
    pointer_urls = {d.url for d in pointers}
    docs = [d for d in docs if d.url not in pointer_urls] + pointers

    corpus = Corpus(
        scraped_at=datetime.now(UTC),
        stats={
            "pages_fetched": len(results),
            "pages_kept": len(docs),
            "pages_dropped": len(dropped),
            "chars": sum(len(d.text) for d in docs),
        },
        documents=docs,
    )
    return corpus, dropped
