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


FILIALLAR_URL = f"{HOST}/filiallar"
ATMLER_URL = f"{HOST}/atmler"

FILIALLAR_TEXT = (
    "Filiallar və bankomatlar\n"
    "ABB-nin filial və bankomat şəbəkəsi bankın saytındakı filiallar səhifəsində "
    'və ABB mobile tətbiqinin "Xidmət şəbəkəsi (filial, şöbə, bankomat)" '
    "bölməsində xəritə üzərində göstərilir.\n"
    "Həmin səhifə filialların ünvanlarını, iş saatlarını və bankomatların "
    "yerləşdiyi nöqtələri sadalayır.\n"
    "Ünvanlar və iş saatları bu sənəddə saxlanılmır, çünki onlar xəritə üzərində "
    "canlı göstərilir. Ən yaxın filialı və ya bankomatı tapmaq üçün həmin "
    "səhifəyə və ya ABB mobile tətbiqinə baxın."
)

ATMLER_POINTER_TEXT = (
    "Bankomatların yerləşdiyi yerlər\n"
    "ABB bankomatlarının yerləşdiyi nöqtələr xəritə üzərində filiallar "
    "səhifəsində və ABB mobile tətbiqində göstərilir.\n"
    "Bankomatların ünvanları bu sənəddə saxlanılmır. Bankomatın harada olduğunu "
    "öyrənmək üçün həmin səhifəyə və ya tətbiqə baxın."
)


def pointer_documents(docs: list[Document]) -> list[Document]:
    """SPEC §5.5 day-two pre-commitment, triggered: Task 23 Item 3 measured that
    the §5.5 bank-facts document does not produce a plausible grounded answer to
    a branch or ATM question -- live probes either refuse (citing unrelated
    pages) or, worse, cite the Android privacy policy for "where is the nearest
    ATM". The branch/ATM list is a client-side map widget on abb-bank.az, so no
    address is ever present in the fetched HTML for retrieval to find (measured:
    /filiallar has exactly one occurrence of "ünvan" and zero street addresses).

    Both pointers state only that a page exists and what it lists, never a
    product fact (SPEC §5.5's constraint on any hand-authored pointer) -- no
    address, no hours, no phone number is invented here. Both URLs returned
    HTTP 200 at scrape time (both are in `data/raw`), so invariant 12 holds.

    `/filiallar` never survives the §5.3 gate (measured: one real body block,
    under MIN_CHARS), so its pointer is always a new Document. `/atmler` DOES
    survive -- it is kept as an ordinary "stub" page carrying a usage FAQ
    (deposit methods, limits, commissions) but no locations -- so a second
    Document at that URL would silently shadow one of them in retrieval, which
    chunks by URL. Handled explicitly: when `/atmler` is present in `docs`, its
    own Document is returned here with the pointer text appended (for
    `build_corpus` to shadow-replace the original with, the same mechanism
    `index_documents` uses for /ferdi/kampaniyalar) rather than emitted as a
    second, competing document.
    """
    pointers = [
        Document(
            url=FILIALLAR_URL,
            title="Filiallar və bankomatlar",
            section_path=["Fərdi"],
            source_class="index",
            text=FILIALLAR_TEXT,
            content_hash=_hash(FILIALLAR_TEXT),
        )
    ]

    existing_atmler = next((d for d in docs if d.url.rstrip("/") == ATMLER_URL), None)
    if existing_atmler is None:
        pointers.append(
            Document(
                url=ATMLER_URL,
                title="Bankomatların yerləşdiyi yerlər",
                section_path=["Fərdi"],
                source_class="index",
                text=ATMLER_POINTER_TEXT,
                content_hash=_hash(ATMLER_POINTER_TEXT),
            )
        )
    else:
        merged_text = f"{existing_atmler.text}\n{ATMLER_POINTER_TEXT}"
        pointers.append(
            existing_atmler.model_copy(
                update={"text": merged_text, "content_hash": _hash(merged_text)}
            )
        )

    return pointers


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
    - `/kampaniyalar` (bare) does not exist at all -- confirmed twice, two
      different exact URL strings, both HTTP 404 (fix rounds 0 and 1) -- and
      is absent from the sitemap entirely (0 of 7,042 `<loc>` entries),
      even though 247 of its own children (`/kampaniyalar/<slug>`) are
      present there. The real campaigns hub is `/ferdi/kampaniyalar`
      (fix round 2, controller ruling P51): HTTP 200, but a client-rendered
      shell -- `extract_page` recovers only 320 chars ("Kampaniyalar", "Ən
      son kampaniyalar", plus generic ABB-mobile-app boilerplate), under the
      §5.3 400-char gate, and zero same-prefix child links exist anywhere in
      its raw HTML. So the campaign index is built because ABB's real hub
      doesn't enumerate its children (0.00 < 0.60). (That shell was also
      under the §5.3 gate when it was 400; at the re-measured 250 it now
      clears the gate, so `build_corpus` drops the shell in favour of this
      index rather than emitting two documents under one URL.)
      `_abb_already_enumerates` is still called on every ingest (not skipped
      just because this one-off probe found a shell): if ABB ever ships a
      working `/ferdi/kampaniyalar` that server-renders its campaign list, a
      corpus scraped after that redesign stops growing this index
      automatically, without a code change here.

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
    if campaigns and _abb_already_enumerates(f"{HOST}/ferdi/kampaniyalar", campaigns, source):
        campaigns = []
    if not campaigns:
        return []

    rows = [
        f"- {d.title}" + (f" ({d.valid_from} – {d.valid_to})" if d.valid_to else "") + f" — {d.url}"
        for d in campaigns
    ]
    return [
        _index_doc(
            "ABB-nin aktiv kampaniyaları", f"{HOST}/ferdi/kampaniyalar", rows, ["Kampaniyalar"]
        )
    ]
