# packages/scraper/abb_scraper/cli.py
from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import typer
from abb_scraper.corpus import build_corpus
from abb_scraper.fetcher import Fetcher
from abb_scraper.sitemap import fetch_sitemap, select_urls

app = typer.Typer(add_completion=False)
HOST = "https://abb-bank.az"


@app.command()
def scrape(max_pages: int = 400, out: Path = Path("data"), raw: Path = Path("data/raw")) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    fetcher = Fetcher(raw_dir=raw)
    fetcher.load_robots(HOST)

    with httpx.Client(headers={"User-Agent": fetcher.user_agent}) as client:
        entries = fetch_sitemap(client, HOST)
    urls, tally = select_urls(entries, date.today())
    typer.echo(f"sitemap={len(entries)} selected={len(urls)} excluded={tally}")

    results = list(fetcher.fetch_all(urls[:max_pages]))
    corpus, dropped = build_corpus(results, date.today())

    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = out / f"corpus_{stamp}.json"
    path.write_text(corpus.model_dump_json(indent=2, exclude_none=False), encoding="utf-8")

    typer.echo(f"\nkept={len(corpus.documents)} dropped={len(dropped)} -> {path}")
    typer.echo(
        f"artifact chars={corpus.stats['chars']} "
        f"est. localStorage quota={corpus.stats['chars'] * 2 / 1e6:.2f} MB (UTF-16)"
    )
    for d in dropped:
        typer.echo(f"  DROP {d.reason:28} {d.char_count:6} {d.url}")


if __name__ == "__main__":
    app()
