# packages/scraper/abb_scraper/sitemap.py
from __future__ import annotations

import re
from collections import Counter
from datetime import date, timedelta
from typing import NamedTuple
from urllib.parse import urlparse

import httpx
from selectolax.parser import HTMLParser


class SitemapEntry(NamedTuple):
    loc: str
    lastmod: date | None


INCLUDE_PREFIXES = ("/ferdi", "/biznes", "/haqqimizda", "/investorlarla-elaqe")
# /kampaniyalar is not listed here: it is handled by its own branch above (with the
# lastmod cutoff), which always `continue`s first, so an entry here would be dead config.
EXCLUDE = [
    ("tenders", re.compile(r"^/haqqimizda/satinalmalar(/|$)")),
    ("news", re.compile(r"^/xeberler(/|$)")),
    ("csr", re.compile(r"^/korporativ-sosial-mesuliyyet(/|$)")),
    ("press", re.compile(r"^/press-relizler(/|$)")),
]
LOCALE = re.compile(r"^/(en|ru)(/|$)")


def _under_prefix(path: str, prefix: str) -> bool:
    """Boundary-safe prefix match: `path` is `prefix` itself or one of its subpaths,
    never a sibling slug that merely starts with the same characters (e.g.
    `/haqqimizda-tarixi` must not match `/haqqimizda`)."""
    return path == prefix or path.startswith(prefix + "/")


def fetch_sitemap(client: httpx.Client, host: str = "https://abb-bank.az") -> list[SitemapEntry]:
    xml = client.get(f"{host}/sitemap.xml", timeout=60.0).text
    tree = HTMLParser(xml)
    out: list[SitemapEntry] = []
    for node in tree.css("url"):
        loc = node.css_first("loc")
        if loc is None:
            continue
        lm = node.css_first("lastmod")
        parsed: date | None = None
        if lm is not None and lm.text():
            parsed = date.fromisoformat(lm.text().strip()[:10])
        out.append(SitemapEntry(loc.text().strip(), parsed))
    return out


def select_urls(entries: list[SitemapEntry], today: date) -> tuple[list[str], dict[str, int]]:
    """Apply SPEC §5.2. Returns the fetch list and a per-reason exclusion tally."""
    cutoff = today - timedelta(days=365)
    keep: list[str] = []
    tally: Counter[str] = Counter()

    for loc, lastmod in entries:
        path = urlparse(loc).path or "/"
        if LOCALE.match(path):
            tally["locale"] += 1
            continue
        matched = next((name for name, rx in EXCLUDE if rx.match(path)), None)
        if matched:
            tally[matched] += 1
            continue
        if path.startswith("/kampaniyalar/"):
            # Two-stage filter: lastmod avoids fetching most expired campaigns at all;
            # the body date range in campaigns.py decides the rest. SPEC §5.4.
            if lastmod is None or lastmod < cutoff:
                tally["campaign_stale"] += 1
                continue
            keep.append(loc)
            continue
        if any(_under_prefix(path, prefix) for prefix in INCLUDE_PREFIXES):
            keep.append(loc)
            continue
        if path.count("/") == 1 and len(path) > 1:
            keep.append(loc)  # root single-segment: the §5.3 gate decides, not a curated list
            continue
        tally["other"] += 1

    return keep, dict(tally)
