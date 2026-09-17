# backend/scraper/abb_scraper/fetcher.py
from __future__ import annotations

import hashlib
import logging
import random
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

import httpx
from protego import Protego

log = logging.getLogger(__name__)


class FetchResult(NamedTuple):
    url: str
    final_url: str
    status: int
    html: str
    from_cache: bool


class Fetcher:
    """One connection, one request per second with jitter, robots.txt obeyed.

    Raw HTML is persisted so re-parsing never means re-crawling.
    """

    user_agent = "ABB-Assistant-CaseStudy/1.0 (+farizakb090@gmail.com)"

    def __init__(self, raw_dir: Path | None, delay: float = 1.0) -> None:
        self.raw_dir = raw_dir
        self.delay = delay
        self.client = httpx.Client(
            headers={"User-Agent": self.user_agent},
            timeout=30.0,
            limits=httpx.Limits(max_connections=1),
        )
        self._robots: Protego | None = None
        if raw_dir is not None:
            raw_dir.mkdir(parents=True, exist_ok=True)

    def load_robots(self, host: str) -> None:
        self._robots = Protego.parse(self.client.get(f"{host}/robots.txt").text)

    def allowed(self, url: str) -> bool:
        if self._robots is None:
            raise RuntimeError("load_robots() must be called before allowed()")
        return self._robots.can_fetch(url, self.user_agent)

    def _path_for(self, url: str) -> Path | None:
        if self.raw_dir is None:
            return None
        return self.raw_dir / f"{hashlib.sha256(url.encode()).hexdigest()[:16]}.html"

    def fetch_all(self, urls: list[str]) -> Iterator[FetchResult]:
        for url in urls:
            if not self.allowed(url):
                log.info("skip %s reason=robots", url)
                continue

            cached = self._path_for(url)
            if cached is not None and cached.exists():
                yield FetchResult(url, url, 200, cached.read_text("utf-8"), True)
                continue

            time.sleep(self.delay + random.uniform(0, self.delay * 0.3))
            try:
                r = self.client.get(url, follow_redirects=True)
            except httpx.HTTPError as exc:
                log.info("skip %s reason=transport %s", url, exc)
                continue

            if r.status_code != 200:
                log.info("skip %s reason=status-%s", url, r.status_code)
                yield FetchResult(url, str(r.url), r.status_code, "", False)
                continue

            if cached is not None:
                cached.write_text(r.text, encoding="utf-8")
            yield FetchResult(url, str(r.url), 200, r.text, False)
