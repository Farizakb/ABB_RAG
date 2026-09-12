# packages/scraper/tests/test_fetcher.py
from pathlib import Path

import httpx
import pytest
from abb_scraper.fetcher import Fetcher

ROBOTS = "User-agent: *\nDisallow:\nDisallow: /cgi-bin/\n"


@pytest.fixture
def fetcher(tmp_path: Path) -> Fetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS)
        if request.url.path == "/dead":
            return httpx.Response(404, text="")
        return httpx.Response(200, text=f"<html>{request.url.path}</html>")

    f = Fetcher(raw_dir=tmp_path, delay=0.0)
    f.client = httpx.Client(transport=httpx.MockTransport(handler))
    f.load_robots("https://abb-bank.az")
    return f


def test_obeys_robots_disallow(fetcher: Fetcher) -> None:
    assert fetcher.allowed("https://abb-bank.az/ferdi/x") is True
    assert fetcher.allowed("https://abb-bank.az/cgi-bin/x") is False


def test_persists_raw_html_so_reparsing_never_means_recrawling(
    fetcher: Fetcher, tmp_path: Path
) -> None:
    list(fetcher.fetch_all(["https://abb-bank.az/ferdi/a"]))
    assert len(list(tmp_path.glob("*.html"))) == 1


def test_second_run_serves_from_disk_without_a_request(fetcher: Fetcher) -> None:
    list(fetcher.fetch_all(["https://abb-bank.az/ferdi/a"]))
    again = list(fetcher.fetch_all(["https://abb-bank.az/ferdi/a"]))
    assert again[0].from_cache is True


def test_404_is_normal_and_logged_not_raised(fetcher: Fetcher) -> None:
    results = list(fetcher.fetch_all(["https://abb-bank.az/dead"]))
    assert results[0].status == 404 and results[0].html == ""


def test_user_agent_identifies_and_carries_a_contact() -> None:
    f = Fetcher(raw_dir=None)
    assert "ABB-Assistant" in f.user_agent and "@" in f.user_agent


def test_allowed_raises_before_load_robots_instead_of_silently_allowing() -> None:
    f = Fetcher(raw_dir=None)
    with pytest.raises(RuntimeError, match="load_robots"):
        f.allowed("https://abb-bank.az/cgi-bin/x")
