# packages/scraper/tests/test_sitemap.py
from datetime import date

from abb_scraper.sitemap import SitemapEntry, select_urls

H = "https://abb-bank.az"
TODAY = date(2026, 9, 14)


def e(path: str, y: int = 2026) -> SitemapEntry:
    return SitemapEntry(loc=H + path, lastmod=date(y, 1, 1))


def test_includes_core_product_sections() -> None:
    urls, _ = select_urls([e("/ferdi/kreditler/nagd-kredit"), e("/biznes/korporativ")], TODAY)
    assert len(urls) == 2


def test_excludes_tenders_news_csr_press() -> None:
    entries = [
        e("/haqqimizda/satinalmalar/x"),
        e("/xeberler/y"),
        e("/korporativ-sosial-mesuliyyet/z"),
        e("/press-relizler/w"),
    ]
    urls, tally = select_urls(entries, TODAY)
    assert urls == []
    assert tally == {"tenders": 1, "news": 1, "csr": 1, "press": 1}


def test_excludes_other_locales() -> None:
    urls, tally = select_urls([e("/en/ferdi/kreditler"), e("/ru/ferdi/kreditler")], TODAY)
    assert urls == []
    assert tally["locale"] == 2


def test_campaign_kept_only_when_lastmod_within_twelve_months() -> None:
    urls, tally = select_urls(
        [e("/kampaniyalar/fresh", 2026), e("/kampaniyalar/stale", 2021)], TODAY
    )
    assert urls == [H + "/kampaniyalar/fresh"]
    assert tally["campaign_stale"] == 1


def test_keeps_haqqimizda_but_not_its_tender_subtree() -> None:
    urls, _ = select_urls([e("/haqqimizda"), e("/haqqimizda/satinalmalar/a")], TODAY)
    assert urls == [H + "/haqqimizda"]


def test_root_single_segment_pages_are_fetched_and_the_gate_decides_later() -> None:
    urls, _ = select_urls(
        [e("/kommunal-odenisleri-onlayn-nece-etmek-olar"), e("/filiallar")], TODAY
    )
    assert len(urls) == 2


def test_campaign_with_no_lastmod_is_excluded() -> None:
    urls, tally = select_urls([SitemapEntry(loc=H + "/kampaniyalar/no-date", lastmod=None)], TODAY)
    assert urls == []
    assert tally["campaign_stale"] == 1


def test_sibling_slug_near_miss_is_not_admitted() -> None:
    urls, tally = select_urls([e("/haqqimizda-tarixi/x")], TODAY)
    assert urls == []
    assert tally["other"] == 1


def test_include_prefix_matches_bare_segment_and_subtree_but_not_a_sibling() -> None:
    urls, tally = select_urls([e("/ferdi"), e("/ferdi/kreditler"), e("/ferdi-xeberleri/y")], TODAY)
    assert urls == [H + "/ferdi", H + "/ferdi/kreditler"]
    assert tally["other"] == 1
