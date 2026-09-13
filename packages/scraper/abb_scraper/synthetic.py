# packages/scraper/abb_scraper/synthetic.py
# ruff: noqa: RUF001 -- genuine Azerbaijani text/dashes, not ambiguous-
# character typos; see campaigns.py / facts.py for the same convention.
from __future__ import annotations

import hashlib
import json
from datetime import date

from contracts.models import Document
from selectolax.parser import HTMLParser

HOST = "https://abb-bank.az"
FAR_FUTURE = date(9999, 12, 31)


def _hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def _bank_node(data: object) -> dict[str, object] | None:
    """The `BankOrCreditUnion` node inside one parsed ld+json blob, wherever
    it sits.

    Not always the top-level object: ABB's real homepage (`fixtures/raw/
    homepage.html`) ships it nested inside a JSON-LD `@graph` array alongside
    `WebSite`/`WebPage`/`BreadcrumbList` sibling nodes, not as a bare object
    or a bare list of one. A version of this function that only checked
    `data[0] if isinstance(data, list) else data` -- the shape task-12's own
    brief assumed -- never finds the real node and always returns `None`
    against the committed fixture.
    """
    if isinstance(data, dict) and isinstance(data.get("@graph"), list):
        candidates: list[object] = data["@graph"]
    elif isinstance(data, list):
        candidates = data
    else:
        candidates = [data]
    for item in candidates:
        if isinstance(item, dict) and "BankOrCreditUnion" in str(item.get("@type", "")):
            return item
    return None


def bank_facts_document(homepage_html: str) -> Document | None:
    """SPEC §5.5. One `BankOrCreditUnion` ld+json block on the homepage;
    take it once. RECON V-7 confirmed the block carries both `address` and
    `telephone` data -- on the real page, phone numbers sit in a
    `contactPoint` list (a direct call centre number and the short code
    "937"), not a top-level `telephone` key, so both are read from there.
    """
    for node in HTMLParser(homepage_html).css('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.text())
        except json.JSONDecodeError:
            continue
        blob = _bank_node(data)
        if blob is None:
            continue
        address = blob.get("address")
        addr: dict[str, object] = address if isinstance(address, dict) else {}
        contact_points = blob.get("contactPoint")
        phones = [
            str(cp.get("telephone", "")).strip()
            for cp in (contact_points if isinstance(contact_points, list) else [])
            if isinstance(cp, dict) and cp.get("telephone")
        ]
        lines = [
            f"{blob.get('name', 'ABB')} — rəsmi əlaqə məlumatları.",
            f"Ünvan: {addr.get('streetAddress', '')} {addr.get('addressLocality', '')}".strip(),
            f"Telefon: {', '.join(phones)}",
            f"Sayt: {blob.get('url', HOST)}",
        ]
        text = "\n".join(line for line in lines if line.rsplit(":", 1)[-1].strip())
        return Document(
            url=HOST + "/",
            title="ABB əlaqə məlumatları",
            section_path=["Haqqımızda"],
            source_class="corporate",
            text=text,
            content_hash=_hash(text),
        )
    return None


def _index_doc(title: str, url: str, rows: list[str], section_path: list[str]) -> Document:
    text = f"{title}\n" + "\n".join(rows)
    return Document(
        url=url,
        title=title,
        section_path=section_path,
        source_class="index",
        text=text,
        content_hash=_hash(text),
    )


ENUMERATES_THRESHOLD = 0.6  # a real listing page names most of its children


def listing_enumerates(listing_text: str, member_titles: list[str]) -> float:
    """The fraction of a class's members named in its listing page's *extracted*
    text -- the Step 1 measurement behind SPEC §5.5's day-two decision.

    Extracted text, not HTML, because only extracted text is retrievable: a page
    can render its children as links and still lose them to the §5.3 chrome
    stripper. No members means nothing to enumerate, which is 0.0, not 1.0."""
    if not member_titles:
        return 0.0
    named = sum(1 for t in member_titles if t and t in listing_text)
    return named / len(member_titles)


def _abb_already_enumerates(
    listing_url: str, members: list[Document], docs: list[Document]
) -> bool:
    """SPEC §5.5. ABB runs the enumeration query server-side. If their page
    already names its children, build nothing -- theirs has better ordering,
    stays current, and is where §11.2's footer link points, so the two can
    never disagree.

    A runtime guard, kept even after Step 1's one-off measurement decided
    whether to write this module's index at all: this decides per ingest, so
    a corpus scraped after ABB redesigns a listing page does not silently
    grow a competing index.
    """
    listing = next((d for d in docs if d.url.rstrip("/") == listing_url.rstrip("/")), None)
    if listing is None:
        return False
    return listing_enumerates(listing.text, [m.title for m in members]) >= ENUMERATES_THRESHOLD


def index_documents(docs: list[Document]) -> list[Document]:
    """One index, campaigns only (SPEC §5.5's day-two pre-commitment).

    Step 1 fetched both of §5.5's named listing pages directly (`RECON.md`'s
    dated day-two measurement; `docs/adr/0006-*.md`) and measured two
    different outcomes, not the same one twice:

    - `/ferdi/kreditler` returned real content (2,083 extracted chars, well
      over the §5.3 400-char gate) naming 6 of its 6 body product cards
      (the 7th child link found in the raw HTML, "İpoteka", sits inside
      `<footer id="footer">` -- confirmed structurally, a genuine nav link,
      not a §5.3 chrome-stripper bug) -- 1.00 >= 0.60, so ABB already
      enumerates its own credit products and no product index is built.
      The losing branch is deleted per SPEC §8.3's rule, not kept behind a
      condition.
    - `/kampaniyalar` returned **HTTP 404** on direct fetch -- it does not
      exist on the live site right now, so it cannot enumerate anything and
      cannot clear the §5.3 gate either. The campaign index is therefore
      built. `_abb_already_enumerates` is still called on every ingest
      (not skipped just because Step 1's one-off probe 404'd): if ABB ships
      a working `/kampaniyalar` listing later, a corpus scraped after that
      redesign stops growing this index automatically, without a code
      change here.

    Top-k similarity returns k things; an enumeration question asks for all
    of them.
    """
    source = [d for d in docs if d.source_class != "index"]

    # Sorted for a stable, diffable document across ingests -- never as a
    # ranking. Invariant 13: no field here means "latest", so the rows
    # assert no order.
    campaigns = sorted(
        (d for d in source if d.source_class == "campaign"),
        key=lambda d: (d.valid_to or FAR_FUTURE, d.title),
    )
    if campaigns and _abb_already_enumerates(f"{HOST}/kampaniyalar", campaigns, source):
        campaigns = []
    if not campaigns:
        return []

    rows = [
        f"- {d.title}" + (f" ({d.valid_from} – {d.valid_to})" if d.valid_to else "") + f" — {d.url}"
        for d in campaigns
    ]
    return [
        _index_doc("ABB-nin aktiv kampaniyaları", f"{HOST}/kampaniyalar", rows, ["Kampaniyalar"])
    ]
