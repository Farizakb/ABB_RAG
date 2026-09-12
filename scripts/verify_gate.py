# scripts/verify_gate.py
"""SPEC.md §2 verification gate. Run once, on day one, before any parser exists.

Writes fixtures/raw/*.html and prints a report to paste into RECON.md.

Deviations from the original brief, per task-1 decisions:
- Captures all 8 fixtures named in the task brief's Interfaces section
  (adds kampaniya-active and stub-empty to the brief's 6-entry PAGES dict).
- V-2 is wrapped so a missing/invalid OPENAI_API_KEY prints PENDING instead
  of raising, so the whole script stays safely re-runnable.
- V-4 (localStorage quota) is a human browser step; this script only prints
  the JS snippet to paste into a console.
- robots.txt is checked here in Python (identifying UA, one request) instead
  of via a separate curl step.
"""
from __future__ import annotations

import datetime
import os
import pathlib
import re
import sys
import time

import httpx

UA = "ABB-Assistant-CaseStudy/1.0 (+farizakb090@gmail.com)"
HOST = "https://abb-bank.az"
RAW = pathlib.Path("fixtures/raw")

# The 6 fixed-path fixtures from the brief's PAGES dict.
FIXED_PAGES = {
    "nagd-kredit": "/ferdi/kreditler/nagd-kredit",
    "biznes-kicik-orta": "/biznes/kicik-ve-orta-biznes",
    "biznes-korporativ": "/biznes/korporativ",
    "biznes-mikro": "/biznes/mikro-biznes",
    "haqqimizda": "/haqqimizda",
    "homepage": "/",
}
# Decision: stub-empty is the client-rendered shell RECON §7 flagged.
STUB_EMPTY_PATH = "/filiallar"
# Interface-listing order for the printed report (not functionally required,
# just matches fixtures/raw/*.html filenames as named in the task brief).
REPORT_ORDER = [
    "nagd-kredit",
    "biznes-kicik-orta",
    "biznes-korporativ",
    "biznes-mikro",
    "kampaniya-active",
    "haqqimizda",
    "homepage",
    "stub-empty",
]

DATE_RANGE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})")
MAX_CAMPAIGN_PROBES = 5  # politeness bound: don't chase every campaign URL


def fetch(client: httpx.Client, path: str, host: str = HOST) -> httpx.Response:
    time.sleep(1.0)
    return client.get(host + path, follow_redirects=True)


def check_robots(client: httpx.Client) -> list[str]:
    """Step 2, done in Python per decision #4: fetch robots.txt, report
    Crawl-delay presence and Disallow paths for User-agent: *."""
    r = fetch(client, "/robots.txt")
    text = r.text
    lines = [ln.strip() for ln in text.splitlines()]

    # Walk to the "User-agent: *" block and collect its Disallow lines,
    # stopping at the next User-agent block or EOF.
    star_disallows: list[str] = []
    in_star_block = False
    for ln in lines:
        if re.match(r"(?i)^user-agent:\s*\*\s*$", ln):
            in_star_block = True
            continue
        if re.match(r"(?i)^user-agent:", ln):
            in_star_block = False
            continue
        if in_star_block:
            m = re.match(r"(?i)^disallow:\s*(.*)$", ln)
            if m and m.group(1).strip():
                star_disallows.append(m.group(1).strip())

    has_crawl_delay = any(re.match(r"(?i)^crawl-delay:", ln) for ln in lines)

    out = [
        f"robots.txt: status={r.status_code} Crawl-delay={'YES — OVERRIDES 1 req/s, RECOMPUTE ESTIMATES' if has_crawl_delay else 'none'} "
        f"disallow-for-*={star_disallows}",
    ]
    if has_crawl_delay:
        out.append(
            "!!! robots.txt now declares a Crawl-delay. This overrides the "
            "project's 1 req/s default and every scrape-time estimate "
            "downstream must be recomputed. !!!"
        )
    return out


def pick_kampaniya_active(
    client: httpx.Client, camp_sorted: list[str]
) -> tuple[str, str, list[str]]:
    """Decision #1: pick the kampaniyalar/** URL with the most recent sitemap
    lastmod, confirm its body carries a DD.MM.YYYY - DD.MM.YYYY range whose
    end date is in the future. Falls back to the most recent one if none of
    the probed candidates qualify.

    Returns (path, html, report_lines). `path` is host-relative.
    """
    today = datetime.date.today()
    out: list[str] = []
    fallback_path: str | None = None
    fallback_html: str | None = None

    for i, url in enumerate(camp_sorted[:MAX_CAMPAIGN_PROBES]):
        path = url if url.startswith("/") else url[len(HOST):]
        r = fetch(client, path)
        html = r.text
        if fallback_path is None:
            fallback_path = path
            fallback_html = html

        m = DATE_RANGE_RE.search(html)
        if m:
            try:
                end = datetime.datetime.strptime(m.group(2), "%d.%m.%Y").date()
            except ValueError:
                end = None
            if end and end > today:
                out.append(
                    f"V-5 kampaniya-active: picked {path} (probe {i + 1}/"
                    f"{min(MAX_CAMPAIGN_PROBES, len(camp_sorted))}), range "
                    f"{m.group(1)}-{m.group(2)}, end date is in the future."
                )
                return path, html, out

    out.append(
        f"V-5 kampaniya-active: none of the top "
        f"{min(MAX_CAMPAIGN_PROBES, len(camp_sorted))} most-recent-lastmod "
        f"campaign URLs carried a still-future date range. Saved the most "
        f"recent one anyway: {fallback_path}"
    )
    assert fallback_path is not None and fallback_html is not None
    return fallback_path, fallback_html, out


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    out: list[str] = []

    with httpx.Client(headers={"User-Agent": UA}, timeout=30.0) as client:
        out.append("--- Step 2 (in Python): robots.txt ---")
        out.extend(check_robots(client))

        # V-5 + V-6: sitemap counts (fetched early so kampaniya-active can be chosen)
        sm = fetch(client, "/sitemap.xml").text
        urls = re.findall(r"<loc>([^<]+)</loc>", sm)
        lastmods = dict(
            zip(urls, re.findall(r"<lastmod>([^<]+)</lastmod>", sm), strict=False)
        )
        az = [u for u in urls if "/en/" not in u and "/ru/" not in u]
        camp = [u for u in az if "/kampaniyalar/" in u]
        camp_sorted = sorted(camp, key=lambda u: lastmods.get(u, ""), reverse=True)
        recent = [u for u in camp if lastmods.get(u, "")[:4] in {"2026", "2025"}]

        out.append("\n--- V-5 / V-6: sitemap ---")
        out.append(f"V-5: campaigns total={len(camp)} lastmod-within-12mo={len(recent)}")
        out.append(f"V-6: sitemap total={len(urls)} az={len(az)}")

        # Decision #1: resolve kampaniya-active path before the fixture loop.
        kamp_path, kamp_html, kamp_report = pick_kampaniya_active(client, camp_sorted)
        out.extend(kamp_report)

        # Build the final 8-entry fixture set and fetch the rest (fixed pages
        # + stub-empty). kampaniya-active is already fetched; reuse it.
        already_fetched: dict[str, tuple[httpx.Response | None, str]] = {
            "kampaniya-active": (None, kamp_html)
        }
        all_pages = dict(FIXED_PAGES)
        all_pages["stub-empty"] = STUB_EMPTY_PATH

        out.append("\n--- V-1 / V-7: fixture shape signals ---")
        out.append(
            "| fixture | status | html-len | breadcrumb-links | terminus | ldjson |"
        )
        rows: dict[str, str] = {}
        for name in REPORT_ORDER:
            if name in already_fetched:
                _, html = already_fetched[name]
                r = None
            else:
                path = all_pages[name]
                r = fetch(client, path)
                html = r.text
            (RAW / f"{name}.html").write_text(html, encoding="utf-8")
            status = r.status_code if r is not None else "n/a (reused)"
            rows[name] = (
                f"| {name} | {status} | {len(html)} | "
                f"breadcrumb-links={len(re.findall(r'<a[^>]+href=\"/[^\"]*\"', html))} | "
                f"terminus={'yes' if 'Səhifəni dəyərləndirin' in html else 'NO'} | "
                f"ldjson={'yes' if 'BankOrCreditUnion' in html else 'NO'} |"
            )
        for name in REPORT_ORDER:
            out.append(rows[name])

        # V-7 detail: does the homepage ld+json BankOrCreditUnion block carry
        # address and phone?
        homepage_html = RAW.joinpath("homepage.html").read_text(encoding="utf-8")
        ldjson_blocks = re.findall(
            r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>',
            homepage_html,
            flags=re.DOTALL,
        )
        bank_block = next((b for b in ldjson_blocks if "BankOrCreditUnion" in b), None)
        if bank_block:
            has_address = '"address"' in bank_block
            has_phone = '"telephone"' in bank_block or '"phone"' in bank_block
            out.append(
                f"\nV-7: BankOrCreditUnion ld+json found on homepage. "
                f"address-key={'yes' if has_address else 'NO'} "
                f"telephone-key={'yes' if has_phone else 'NO'}"
            )
        else:
            out.append("\nV-7: NO BankOrCreditUnion ld+json block found on homepage.")

        # V-3: CDN tariff PDFs
        out.append("\n--- V-3: CDN probe ---")
        time.sleep(1.0)
        r = client.get("https://cdn.abb-bank.az/", timeout=15.0)
        out.append(f"V-3: cdn root status={r.status_code} len={len(r.text)}")

    # V-2: models the key can reach. Decision #2: no key on this machine ->
    # print PENDING and keep going. Also catches any runtime failure so the
    # script stays safely re-runnable once a key exists.
    out.append("\n--- V-2: model availability ---")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        out.append("V-2: PENDING — no OPENAI_API_KEY")
    else:
        try:
            from openai import OpenAI

            ids = sorted(m.id for m in OpenAI().models.list())
            out.append(
                f"V-2: {len(ids)} models. Chat-capable sample: "
                f"{[i for i in ids if 'gpt' in i][:12]}"
            )
            out.append(f"V-2: embedding models: {[i for i in ids if 'embedding' in i]}")
        except Exception as e:  # noqa: BLE001 - deliberately broad, this is a probe
            out.append(f"V-2: PENDING — OPENAI_API_KEY present but call failed: {e!r}")

    # V-4: human browser step, per decision #3.
    out.append("\n--- V-4: localStorage quota (human step) ---")
    out.append("V-4: PENDING — human step. Paste into a browser console on any page:")
    out.append(
        "const s = \"x\".repeat(975_000);\n"
        "try { localStorage.setItem(\"probe\", s); "
        "console.log(\"stored\", (JSON.stringify(localStorage).length * 2) / 1e6, "
        "\"MB of quota\"); }\n"
        "catch (e) { console.log(\"QUOTA EXCEEDED at\", s.length, \"chars\"); }\n"
        "finally { localStorage.removeItem(\"probe\"); }"
    )

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
