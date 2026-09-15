# packages/scraper/abb_scraper/extract.py
from __future__ import annotations

import hashlib
import re
from typing import NamedTuple
from urllib.parse import urlparse

from abb_scraper.nextdata import faq_pairs
from selectolax.parser import HTMLParser, Node

BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "li", "td", "th", "dd", "dt", "span", "div", "a"}
LANDMARK_TAGS = {"nav", "header", "footer"}
FEEDBACK_WIDGET_ID = "rate-this-page"
CF_EMAIL = re.compile(r"\[email\s*protected\]", re.I)


class Block(NamedTuple):
    text: str
    tag: str


def _clean(s: str) -> str:
    return CF_EMAIL.sub("", re.sub(r"\s+", " ", s)).strip()


def _own_text(node: Node) -> str:
    """`node`'s own sentence: text of `node` plus text of any inline
    descendant (strong/em/b/i/small/span-as-inline-emphasis/...), but NOT
    text belonging to a nested BLOCK_TAGS element -- that element gets its
    own Block when the tree walk reaches it separately, so double-counting
    it here would duplicate content.

    Deliberately not `node.text(deep=False)` (own text only, no descendants
    at all): that also throws away `<strong>10.9%</strong>`-style inline
    markup, silently deleting figures from sentences that still read as
    grammatically complete without them -- the worst failure mode for a
    RAG's retrievable facts. The walk recurses into non-block descendants
    and stops (without recursing further) at any BLOCK_TAGS boundary.
    """
    parts: list[str] = []
    for child in node.iter(include_text=True):
        if child.tag == "-text":
            parts.append(child.text())
        elif child.tag not in BLOCK_TAGS:
            parts.append(_own_text(child))
    return "".join(parts)


def _all_blocks(tree: HTMLParser) -> list[tuple[Node, Block]]:
    """All BLOCK_TAGS elements with non-empty own text (see `_own_text`), in
    document order.

    Uses `tree.root.traverse()` rather than `tree.css(",".join(BLOCK_TAGS))`:
    selectolax's multi-tag CSS selector returns matches grouped by selector
    (all <p>s, then all <div>s, ...), not in document order, which silently
    breaks any "content runs from X to Y" range computation.
    """
    out: list[tuple[Node, Block]] = []
    root = tree.root
    if root is None:
        return out
    for node in root.traverse(include_text=False):
        if node.tag in BLOCK_TAGS:
            own = _clean(_own_text(node))
            if own:
                out.append((node, Block(own, node.tag)))
    return out


def _is_chrome(node: Node) -> bool:
    """True if `node` sits inside a semantic chrome landmark (nav/header/
    footer -- HTML5 structure, not Tailwind classes, so a redeploy of styling
    cannot break it) or inside the site-wide "rate this page" feedback
    widget (id="rate-this-page" on every fixture that has one).

    The widget is excluded by container id rather than by truncating the
    document at the first occurrence of its text (SPEC's original mental
    model): on ABB's Next.js pages the widget's markup is emitted near the
    TOP of the raw HTML, right after </header> and ahead of several React
    Suspense placeholders, even though it renders at the bottom of the page.
    A "content runs from breadcrumb to feedback marker" scan over raw source
    order is therefore not just fragile, it is wrong on every real fixture --
    the marker sits before the content in source order, not after it.
    """
    cur: Node | None = node
    while cur is not None:
        if cur.tag in LANDMARK_TAGS:
            return True
        if cur.attributes.get("id") == FEEDBACK_WIDGET_ID:
            return True
        cur = cur.parent
    return False


def _crumbs_from_holder(holder: Node) -> list[str]:
    crumbs = [_clean(x.text()) for x in holder.css("a") if _clean(x.text())]
    current = holder.css_first("[aria-current]")
    if current is not None:
        label = _clean(current.text())
        if label and (not crumbs or crumbs[-1] != label):
            crumbs.append(label)
    return crumbs


def _find_breadcrumb(tree: HTMLParser) -> list[str]:
    """Locate the breadcrumb trail via the WAI-ARIA breadcrumb-nav landmark
    convention alone (`<nav aria-label="breadcrumb">`) -- a standard
    accessibility attribute, not a Tailwind class, so a redeploy cannot
    break it.

    An earlier version additionally required the trail's links to be
    successive href path-prefixes of `url_path`, to guard against a nav
    element being mislabeled. That validator was removed: `kampaniya-active
    .html` has a genuine `aria-label="breadcrumb"` nav whose trail is a
    *navigational* hierarchy (`/ferdi` -> `/ferdi/kampaniyalar`), not the
    page's own URL hierarchy (`/kampaniyalar/<slug>`), so the href-prefix
    rule rejected a real breadcrumb there. Checked against all 11 fixtures:
    dropping the validator changes output on exactly one of them (recovers
    kampaniya-active's breadcrumb) and nothing else. The site-wide "Biznes"
    nav dropdown, which does coincidentally satisfy the href-prefix rule on
    a /biznes/... page, was never actually a risk for *this* function: it
    carries no `aria-label="breadcrumb"`, so the landmark check alone
    already excludes it before the validator would ever run.
    """
    root = tree.root
    if root is None:
        return []
    for node in root.traverse(include_text=False):
        if node.tag != "nav":
            continue
        if "breadcrumb" not in (node.attributes.get("aria-label") or "").lower():
            continue
        return _crumbs_from_holder(node)
    return []


def content_blocks(html: str, url_path: str) -> tuple[list[Block], list[str]]:
    """The content region, minus nav/header/footer chrome and the feedback
    widget, plus the breadcrumb trail (which may legitimately be empty --
    see `_find_breadcrumb`). Amends SPEC.md §5.3 rule 1, which described a
    "breadcrumb to feedback marker" position-based scan that does not work
    on ABB's actual markup; see SPEC.md for the superseded original text and
    RECON.md for the fixture-by-fixture evidence.

    `url_path` is part of the required interface for this task (consumed by
    later tasks) but is currently unused: breadcrumb detection no longer
    validates against it (see `_find_breadcrumb`), and chrome exclusion
    never depended on it. Kept for interface stability.
    """
    tree = HTMLParser(html)
    for tag in ("script", "style", "noscript", "svg"):
        for node in tree.css(tag):
            node.decompose()

    crumbs = _find_breadcrumb(tree)
    pairs = _all_blocks(tree)

    # The sole content-region strategy: it naturally covers both
    # breadcrumb-anchored pages (ABB wraps the breadcrumb itself in
    # <nav aria-label="breadcrumb">, so it is dropped along with the rest of
    # chrome) and pages with no breadcrumb at all, such as the biznes/**
    # segment-hub pages (V-1: 88 of 185 core pages live under biznes/**, and
    # roughly a third of the sampled ones have no breadcrumb and no <h1> --
    # a hub of promo tiles instead).
    blocks = [b for node, b in pairs if not _is_chrome(node)]

    return blocks, crumbs


FAQ_TAG = "faq"


def faq_blocks(html: str, dom_blocks: list[Block], url_path: str) -> list[Block]:
    """The page's own accordion Q&A, recovered from the Next.js flight payload
    (see `nextdata.faq_pairs`), as one block per item.

    `url_path` is what scopes the payload to THIS page's CMS record: the
    payload also carries every ancestor route's sections, so an unscoped scan
    hands a child page the FAQ its parent renders.

    Question and answer share a single block on purpose: `chunk_document`
    splits on line boundaries, so a question emitted as its own block can be
    packed into the previous chunk and stranded from its answer.

    The skip fires only when BOTH the question and the FULL answer are
    already present in the DOM text -- not the answer alone, and not a
    prefix of it. A question-keyed skip discarded 76 pairs (25,903 chars)
    across 19 pages: many pages server-render the accordion's question
    triggers but never their answers (task-14c-fix2-brief.md, measured
    2026-09-14 on the 550-file raw cache). Re-keying on the answer's first 60
    characters fixed that but broke 3 different pages the same way: a DOM
    teaser that shares the answer's opening clause matched the prefix while
    the full answer was never rendered at all, so the same class of silent
    deletion recurred (task-14c-fix3-brief.md, same measurement date).
    Corpus-wide, requiring the full question AND the full answer drops zero
    pairs today -- no ABB page currently server-renders a complete accordion
    item -- and that is correct; the guard stays because a page that does
    render one must not be duplicated. `_clean` collapses whitespace on both
    sides of the comparison: the flight text and the DOM flattening differ in
    whitespace, and that difference is the only reason a prefix was ever
    reached for.
    """
    present = _clean("\n".join(b.text for b in dom_blocks))
    return [
        Block(f"{q} {a}", FAQ_TAG)
        for q, a in faq_pairs(html, url_path)
        if not (_clean(q) in present and _clean(a) in present)
    ]


class PageText(NamedTuple):
    text: str
    crumbs: list[str]
    dup_collapsed: int
    char_count: int
    body: str


def dedupe_blocks(blocks: list[Block]) -> tuple[list[Block], int]:
    """SPEC §5.3 rule 2. Hash each block, keep the first, count the rest.

    The key folds both case AND whitespace (`re.sub(r"\\s+", " ", ...)`), not
    case alone: real pages repeat the same block with differing inline
    whitespace after a template re-render (e.g. "Nağd  Kredit" vs "nağd
    kredit"), and under-collapsing here costs more than over-collapsing --
    rule 2 is the single largest ingestion win (SPEC §5.3, RECON §5).
    """
    seen: set[str] = set()
    kept: list[Block] = []
    collapsed = 0
    for b in blocks:
        norm = re.sub(r"\s+", " ", b.text.lower()).strip()
        key = hashlib.sha256(norm.encode()).hexdigest()
        if key in seen:
            collapsed += 1
            continue
        seen.add(key)
        kept.append(b)
    return kept, collapsed


def page_meta(html: str) -> tuple[str, str]:
    """The `<title>` and `<meta name="description">` content, cleaned."""
    tree = HTMLParser(html)
    t = tree.css_first("title")
    m = tree.css_first('meta[name="description"]')
    title = _clean(t.text()) if t else ""
    meta = _clean(m.attributes.get("content") or "") if m else ""
    return title, meta


MIN_CHARS = 250  # re-measured corpus-wide over the full 550-page raw cache
# (2026-09-15), replacing the 400 calibrated on the day-three 243-document crawl.
# 400 was separating the wrong two populations. The gate counts `char_count` =
# len(title + meta + body), and ABB's "root stub" pages (SPEC §5.3 rule 3) carry
# their whole substance in title+meta -- the question in the title, the answer in
# the description -- above a 117-character boilerplate body that is the same ABB
# mobile CTA on all 18 of them. At 400 every one of those was dropped:
# /kredit-borcumu-nece-onlayn-odeye-bilerem, /kommunal-odenisleri-onlayn-nece-etmek-olar,
# /ipoteka-odenisimi-nece-ede-bilerem and 15 siblings -- 18 of the highest-intent
# customer questions in the corpus.
# The two populations actually separate cleanly on char_count:
#   empty shells (zero content blocks):      190 .. 206   (18 pages)
#   genuine short pages:                     285 .. 389   (28 pages)
# 250 sits in the gap with a 44-char margin above the largest shell and a 35-char
# margin below the smallest genuine page. Ruling P57's constraint -- never let the
# gate rise past a real page -- is preserved and tightened, not relaxed.


class DropRecord(NamedTuple):
    url: str
    reason: str
    char_count: int


def apply_gates(
    pages: list[tuple[str, PageText]],
) -> tuple[list[tuple[str, PageText]], list[DropRecord]]:
    """SPEC §5.3 rules 4 and 5.

    Rule 4 identifies a page by whichever half actually carries its substance.

    Hashing title+description would silently delete every page sharing the
    generic site title, so the body is the right key for an ordinary page. But
    the body is the WRONG key for a root stub (rule 3), whose content is the
    question in its title and the answer in its description, above a boilerplate
    body. ABB serves the identical 117-character "ABB mobile" CTA as the body of
    18 such stubs -- /kredit-borcumu-nece-onlayn-odeye-bilerem,
    /ipoteka-odenisimi-nece-ede-bilerem and 16 siblings. Keying those on the body
    collapsed all 18 into whichever one happened to be crawled first and silently
    deleted the other 17, each a distinct high-intent customer question
    (measured 2026-09-15 over the full 550-page raw cache).

    So: key on the body when the body outweighs title+meta, otherwise key on the
    full text. `head` is derived by subtraction rather than re-parsing, because
    `text` is exactly title+meta+body joined by extract_page. An ordinary page
    keeps body-keying and the cross-document-duplicate count is unchanged at 31;
    a root stub is keyed on the text that distinguishes it. Two genuinely
    identical pages still collapse under either branch.
    """
    kept: list[tuple[str, PageText]] = []
    dropped: list[DropRecord] = []
    seen_bodies: set[str] = set()

    for url, page in pages:
        head = len(page.text) - len(page.body)
        ident = page.body if len(page.body) > head else page.text
        key = hashlib.sha256(re.sub(r"\s+", " ", ident.lower()).encode()).hexdigest()
        if ident.strip() and key in seen_bodies:
            dropped.append(DropRecord(url, "cross-document-duplicate", page.char_count))
            continue
        if page.char_count < MIN_CHARS:
            dropped.append(DropRecord(url, "under-min-chars", page.char_count))
            continue
        seen_bodies.add(key)
        kept.append((url, page))

    return kept, dropped


def extract_page(html: str, url: str, title: str = "", meta: str = "") -> PageText:
    """Rules 1-3 composed. Rule 3 recovers the root stubs, whose whole content
    is a question in the title and a one-sentence answer in the description.

    `title`/`meta` are accepted as parameters (rather than always parsed from
    `html`) so a caller that already has them from a sitemap/index page need
    not re-parse; when either is omitted, it falls back to `page_meta(html)`.
    """
    path = urlparse(url).path or "/"
    blocks, crumbs = content_blocks(html, path)
    blocks = blocks + faq_blocks(html, blocks, path)
    kept, collapsed = dedupe_blocks(blocks)
    if not title or not meta:
        fallback_title, fallback_meta = page_meta(html)
        title, meta = title or fallback_title, meta or fallback_meta
    body = "\n".join(b.text for b in kept)
    text = "\n".join(p for p in (title, meta, body) if p)
    return PageText(
        text=text, crumbs=crumbs, dup_collapsed=collapsed, char_count=len(text), body=body
    )
