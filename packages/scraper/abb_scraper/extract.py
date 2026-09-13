# packages/scraper/abb_scraper/extract.py
from __future__ import annotations

import re
from typing import NamedTuple
from urllib.parse import urlparse

from selectolax.parser import HTMLParser, Node

# Kept for documentation / matching the visible page copy: the feedback
# widget is excluded by its container id (FEEDBACK_WIDGET_ID below), not by
# scanning for this text -- see _is_chrome for why.
TERMINUS = "Səhifəni dəyərləndirin"
BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "li", "td", "th", "dd", "dt", "span", "div", "a"}
LANDMARK_TAGS = {"nav", "header", "footer"}
FEEDBACK_WIDGET_ID = "rate-this-page"
CF_EMAIL = re.compile(r"\[email\s*protected\]", re.I)


class Block(NamedTuple):
    text: str
    tag: str


def _clean(s: str) -> str:
    return CF_EMAIL.sub("", re.sub(r"\s+", " ", s)).strip()


def _is_breadcrumb(node: Node, url_path: str) -> bool:
    """A breadcrumb is 2-8 same-host links whose paths are successive
    prefixes of the current path. Structural, so a Tailwind redeploy cannot
    break it. The upper bound matters: without it a footer sitemap or a nav
    mega-menu can coincidentally contain 2 prefix-matching links buried among
    dozens of unrelated ones and get mistaken for a breadcrumb trail, which a
    real breadcrumb (short by definition) never does."""
    hrefs = [a.attributes.get("href") or "" for a in node.css("a")]
    paths = [
        urlparse(h).path.rstrip("/") for h in hrefs if h.startswith(("/", "https://abb-bank.az"))
    ]
    paths = [p for p in paths if p]
    if len(paths) < 2 or len(paths) > 8:
        return False
    target = url_path.rstrip("/")
    prefixes = [p for p in paths if target.startswith(p)]
    return len(prefixes) >= 2 and prefixes == sorted(prefixes, key=len)


def _all_blocks(tree: HTMLParser) -> list[tuple[Node, Block]]:
    """All BLOCK_TAGS elements with non-empty own text, in document order.

    Uses `tree.root.traverse()` rather than `tree.css(",".join(BLOCK_TAGS))`:
    selectolax's multi-tag CSS selector returns matches grouped by selector
    (all <p>s, then all <div>s, ...), not in document order, which silently
    breaks any "content runs from X to Y" range computation. `node.text(deep=
    False)` (own text only, not nested tags' text, which get their own pair
    when the selector matches them separately) is used instead of the more
    common `tag is None`-for-text-nodes check: selectolax 0.4.11 text
    pseudo-nodes report tag == "-text" (truthy), not None.
    """
    out: list[tuple[Node, Block]] = []
    root = tree.root
    if root is None:
        return out
    for node in root.traverse(include_text=False):
        if node.tag in BLOCK_TAGS:
            own = _clean(node.text(deep=False))
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


def _find_breadcrumb(tree: HTMLParser, url_path: str) -> list[str]:
    """Locate the breadcrumb trail via the WAI-ARIA breadcrumb-nav landmark
    convention (`<nav aria-label="breadcrumb">`) -- a standard accessibility
    attribute, not a Tailwind class, so a redeploy cannot break it. The
    href-prefix rule (`_is_breadcrumb`) still validates the candidate: on
    ABB's site, the site-wide "Biznes" nav dropdown happens to contain 2+
    links that are path-prefixes of a /biznes/... page too, so structure
    alone (any container with qualifying links) is ambiguous; requiring both
    the landmark *and* the structural shape avoids that false positive.
    Pages that render with neither (the `biznes-kicik-orta`/`korporativ`/
    `mikro` segment-hub fixtures) genuinely have no breadcrumb: `[]` is the
    honest answer there, not a bug.
    """
    root = tree.root
    if root is None:
        return []
    for node in root.traverse(include_text=False):
        if node.tag != "nav":
            continue
        if "breadcrumb" not in (node.attributes.get("aria-label") or "").lower():
            continue
        if _is_breadcrumb(node, url_path):
            return _crumbs_from_holder(node)
    return []


def content_blocks(html: str, url_path: str) -> tuple[list[Block], list[str]]:
    """SPEC §5.3 rule 1: the content region, minus nav/header/footer chrome
    and the feedback widget, plus the breadcrumb trail (which may be empty --
    see _find_breadcrumb)."""
    tree = HTMLParser(html)
    for tag in ("script", "style", "noscript", "svg"):
        for node in tree.css(tag):
            node.decompose()

    crumbs = _find_breadcrumb(tree, url_path)
    pairs = _all_blocks(tree)

    # This landmark/widget exclusion is the sole content-region strategy: it
    # naturally covers both breadcrumb-anchored pages (ABB wraps the
    # breadcrumb itself in <nav aria-label="breadcrumb">, so it is dropped
    # along with the rest of chrome) and pages with no breadcrumb at all,
    # such as the biznes/** segment-hub pages (V-1: 88 of 185 core pages live
    # under biznes/**, and roughly a third of the sampled ones have no
    # breadcrumb and no <h1> -- a hub of promo tiles instead).
    blocks = [b for node, b in pairs if not _is_chrome(node)]

    # Fallback for markup with no landmark tags at all (see the
    # no-breadcrumb unit test below): start at the first h1 instead of
    # returning nothing. Not observed on any real fixture.
    if not blocks:
        for i, (_, b) in enumerate(pairs):
            if b.tag == "h1":
                blocks = [b for _, b in pairs[i:]]
                break

    return blocks, crumbs
