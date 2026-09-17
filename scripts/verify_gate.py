# scripts/verify_gate.py
"""Verification gate. Run once, before any parser exists.

Writes fixtures/raw/*.html and prints a report to paste into RECON.md.

Deviations from the original brief, per task-1 decisions:
- Captures all 8 fixtures named in the task brief's Interfaces section
  (adds kampaniya-active and stub-empty to the brief's 6-entry PAGES dict).
- V-4 (localStorage quota) is a human browser step; this script only prints
  the JS snippet to paste into a console.
- robots.txt is checked here in Python (identifying UA, one request) instead
  of via a separate curl step.

Fix round 1:
- F1: fetches one real sub-page per biznes segment (one hop below each hub,
  discovered from the hub fixture's own hrefs already on disk) and reports
  the two signals that decide the extraction shape: a bullet-separated breadcrumb
  and a rendered value-then-label stat block, both checked in visible text
  (script/style/tags stripped) rather than raw HTML, so JSON-payload false
  positives don't count.
- F2: V-5's "within 12 months" check is now a true rolling 365-day window
  (`lastmod >= today - 365d`), not a coarse calendar-year-prefix match.
- F3: loads OPENAI_API_KEY from .env into the process environment (value
  never printed/logged) and runs the real V-2 model/embedding/structured-
  output probe.
- F4: greps the already-captured fixtures for cdn.abb-bank.az PDF links and
  probes any found instead of the previous inconclusive CDN root request.

Fix round 2:
- R1: every sleep is now jittered (`1.0 + random.uniform(0, 0.3)`), matching
  the "one request per second with jitter" policy and the shape the real
  Fetcher will use.
- R2: robots.txt is now enforced, not just parsed and printed. `fetch()` is
  gated by a `urllib.robotparser.RobotFileParser` built from the fetched
  robots.txt; a disallowed URL is skipped (no request made) with a printed
  line, rather than fetched anyway or silently dropped. This matters because
  `pick_kampaniya_active()` and `first_sub_page_path()` derive URLs
  dynamically. (The CDN PDF probe targets a different host, cdn.abb-bank.az,
  so it is not gated by abb-bank.az's robots.txt.)
- R3: dependencies are now pinned in requirements-dev.txt at the repo root.
- R4: the CDN PDF probe uses client.stream() and reads only the first chunk,
  so a server that ignores the Range header still only costs ~2 KB, not the
  whole file.

The whole script stays idempotent: fixtures already on disk are not
re-fetched, so re-running it only pays for what's actually missing.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import random
import re
import sys
import time
import urllib.robotparser

import httpx

UA = "ABB-Assistant-CaseStudy/1.0 (+farizakb090@gmail.com)"
HOST = "https://abb-bank.az"
RAW = pathlib.Path("fixtures/raw")
ENV_FILE = pathlib.Path(".env")

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

# F1: one hop below each biznes hub. (fixture name) -> (hub fixture name, hub base path)
SUB_PAGES = {
    "biznes-sub-kicik-orta": ("biznes-kicik-orta", "/biznes/kicik-ve-orta-biznes"),
    "biznes-sub-korporativ": ("biznes-korporativ", "/biznes/korporativ"),
    "biznes-sub-mikro": ("biznes-mikro", "/biznes/mikro-biznes"),
}
STAT_LABELS = ("Məbləğ", "Müddət", "İllik faiz dərəcəsi")

DATE_RANGE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})")
MAX_CAMPAIGN_PROBES = 5  # politeness bound: don't chase every campaign URL
PDF_URL_RE = re.compile(r"https://cdn\.abb-bank\.az/[A-Za-z0-9_./%-]+\.pdf")


def _polite_sleep() -> None:
    """R1: one request per second, with jitter, everywhere the script sleeps."""
    time.sleep(1.0 + random.uniform(0, 0.3))


def load_dotenv_key(key: str, path: pathlib.Path = ENV_FILE) -> None:
    """F3: load one key from .env into the process environment. Never
    prints or logs the value. No-op if already set, or the file/key is
    absent."""
    if os.environ.get(key) or not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            os.environ[key] = v.strip().strip('"').strip("'")
            return


def fetch(
    client: httpx.Client,
    path: str,
    rp: urllib.robotparser.RobotFileParser,
    host: str = HOST,
) -> httpx.Response | None:
    """R1 + R2: jittered 1 req/s sleep, gated by robots.txt. Returns None
    (no request made) if `rp` disallows the URL for our UA."""
    url = host + path
    if not rp.can_fetch(UA, url):
        return None
    _polite_sleep()
    return client.get(url, follow_redirects=True)


def load_or_fetch(
    client: httpx.Client, name: str, path: str, rp: urllib.robotparser.RobotFileParser
) -> tuple[str | None, str]:
    """Idempotent fixture fetch: reuse fixtures/raw/<name>.html if it's
    already on disk instead of re-fetching, so re-running the gate doesn't
    re-hit pages it already has. Returns (None, status) if robots.txt
    disallows the path and nothing is on disk yet."""
    file = RAW / f"{name}.html"
    if file.exists():
        return file.read_text(encoding="utf-8"), "on-disk (skipped fetch)"
    r = fetch(client, path, rp)
    if r is None:
        return None, "SKIPPED — disallowed by robots.txt"
    file.write_text(r.text, encoding="utf-8")
    return r.text, str(r.status_code)


def visible_text(html: str) -> str:
    """Strip <script>/<style> blocks and tags for a plain-text view. Used
    only for boolean shape-signal checks (breadcrumb bullet, stat-block
    labels) so JSON-payload/i18n-dictionary hits don't count as content.
    Not extraction logic: no field parsing, just a text view for substring
    checks, same spirit as the existing terminus/ldjson checks."""
    no_script = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    no_style = re.sub(r"<style[^>]*>.*?</style>", "", no_script, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", no_style)
    return re.sub(r"\s+", " ", text)


def first_sub_page_path(hub_fixture_name: str, base_path: str) -> str | None:
    """F1: read the already-captured hub fixture off disk and return the
    first href one level deeper into the same segment (in document order).
    Plain URL discovery, not content extraction."""
    file = RAW / f"{hub_fixture_name}.html"
    if not file.exists():
        return None
    html = file.read_text(encoding="utf-8")
    matches: list[str] = re.findall(r"/biznes/[a-z0-9-]+/[a-z0-9-]+", html)
    for p in matches:
        if p.startswith(base_path + "/"):
            return p
    return None


def find_cdn_pdf_urls() -> list[str]:
    """F4: grep the already-captured fixtures for cdn.abb-bank.az PDF
    links. No new HTML fetches — reuses what the fixtures already contain."""
    urls: set[str] = set()
    for f in sorted(RAW.glob("*.html")):
        text = f.read_text(encoding="utf-8", errors="ignore")
        urls.update(PDF_URL_RE.findall(text))
    return sorted(urls)


def check_robots(
    client: httpx.Client,
) -> tuple[list[str], urllib.robotparser.RobotFileParser]:
    """Step 2, done in Python per decision #4: fetch robots.txt (the one
    request nothing else can be gated behind), report Crawl-delay presence
    and Disallow paths for the printed table, and build the
    RobotFileParser (R2) used to gate every later request in this run."""
    _polite_sleep()
    r = client.get(HOST + "/robots.txt", follow_redirects=True)
    text = r.text
    lines = [ln.strip() for ln in text.splitlines()]

    # Walk to the "User-agent: *" block and collect its Disallow lines,
    # stopping at the next User-agent block or EOF. (Reporting only — actual
    # enforcement is via RobotFileParser.can_fetch below.)
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

    crawl_delay_note = "YES — OVERRIDES 1 req/s, RECOMPUTE ESTIMATES" if has_crawl_delay else "none"
    out = [
        f"robots.txt: status={r.status_code} Crawl-delay={crawl_delay_note} "
        f"disallow-for-*={star_disallows}",
    ]
    if has_crawl_delay:
        out.append(
            "!!! robots.txt now declares a Crawl-delay. This overrides the "
            "project's 1 req/s default and every scrape-time estimate "
            "downstream must be recomputed. !!!"
        )

    rp = urllib.robotparser.RobotFileParser()
    rp.parse(text.splitlines())
    return out, rp


def pick_kampaniya_active(
    client: httpx.Client,
    camp_sorted: list[str],
    rp: urllib.robotparser.RobotFileParser,
) -> tuple[str | None, str | None, list[str]]:
    """Decision #1: pick the kampaniyalar/** URL with the most recent sitemap
    lastmod, confirm its body carries a DD.MM.YYYY - DD.MM.YYYY range whose
    end date is in the future. Falls back to the most recent one if none of
    the probed candidates qualify. A candidate disallowed by robots.txt is
    skipped (reported) and does not count against the probe budget.

    Returns (path, html, report_lines). `path` is host-relative. Both may be
    None if no candidate could be fetched at all.
    """
    today = datetime.date.today()
    out: list[str] = []
    fallback_path: str | None = None
    fallback_html: str | None = None
    probed = 0

    for url in camp_sorted:
        if probed >= MAX_CAMPAIGN_PROBES:
            break
        path = url if url.startswith("/") else url[len(HOST) :]
        r = fetch(client, path, rp)
        if r is None:
            out.append(f"V-5 kampaniya-active: skipping {path} — disallowed by robots.txt")
            continue
        probed += 1
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
                    f"V-5 kampaniya-active: picked {path} (probe {probed}/"
                    f"{MAX_CAMPAIGN_PROBES}), range "
                    f"{m.group(1)}-{m.group(2)}, end date is in the future."
                )
                return path, html, out

    if fallback_path is None:
        out.append(
            "V-5 kampaniya-active: no campaign URL could be fetched (empty "
            "candidate list, or every candidate disallowed by robots.txt)."
        )
        return None, None, out

    out.append(
        f"V-5 kampaniya-active: none of the {probed} probed most-recent-lastmod "
        f"campaign URLs carried a still-future date range. Saved the most "
        f"recent one anyway: {fallback_path}"
    )
    return fallback_path, fallback_html, out


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    load_dotenv_key("OPENAI_API_KEY")  # F3, value never touches `out`
    out: list[str] = []

    with httpx.Client(headers={"User-Agent": UA}, timeout=30.0) as client:
        out.append("--- Step 2 (in Python): robots.txt ---")
        robots_report, rp = check_robots(client)
        out.extend(robots_report)

        # V-5 + V-6: sitemap counts (always re-fetched — F2 needs a fresh
        # rolling window each run, and it's cheap: 1 request)
        sm_resp = fetch(client, "/sitemap.xml", rp)
        if sm_resp is None:
            out.append("\nV-5 / V-6: SKIPPED — /sitemap.xml disallowed by robots.txt")
            urls: list[str] = []
            lastmods: dict[str, str] = {}
            az: list[str] = []
            camp: list[str] = []
            camp_sorted: list[str] = []
        else:
            sm = sm_resp.text
            urls = re.findall(r"<loc>([^<]+)</loc>", sm)
            lastmods = dict(zip(urls, re.findall(r"<lastmod>([^<]+)</lastmod>", sm), strict=False))
            az = [u for u in urls if "/en/" not in u and "/ru/" not in u]
            camp = [u for u in az if "/kampaniyalar/" in u]
            camp_sorted = sorted(camp, key=lambda u: lastmods.get(u, ""), reverse=True)

        # F2: true rolling 365-day window, not a calendar-year-prefix check.
        today = datetime.date.today()
        cutoff = today - datetime.timedelta(days=365)

        def _lastmod_date(u: str) -> datetime.date | None:
            s = lastmods.get(u, "")
            if not s:
                return None
            try:
                return datetime.date.fromisoformat(s[:10])
            except ValueError:
                return None

        recent = []
        for u in camp:
            d = _lastmod_date(u)
            if d is not None and d >= cutoff:
                recent.append(u)

        out.append("\n--- V-5 / V-6: sitemap ---")
        out.append(
            f"V-5: campaigns total={len(camp)} "
            f"lastmod-within-365d={len(recent)} (cutoff={cutoff.isoformat()})"
        )
        out.append(f"V-6: sitemap total={len(urls)} az={len(az)}")

        # kampaniya-active: idempotent — skip the whole probe if the fixture
        # is already on disk from a prior run.
        kamp_file = RAW / "kampaniya-active.html"
        kamp_already_on_disk = kamp_file.exists()
        if kamp_already_on_disk:
            kamp_html: str | None = kamp_file.read_text(encoding="utf-8")
            out.append("V-5 kampaniya-active: fixture already on disk, skipped re-probe.")
        else:
            _, kamp_html, kamp_report = pick_kampaniya_active(client, camp_sorted, rp)
            out.extend(kamp_report)

        # Build the final 8-entry fixture set and fetch the rest (fixed pages
        # + stub-empty), idempotently. kampaniya-active is already resolved.
        all_pages = dict(FIXED_PAGES)
        all_pages["stub-empty"] = STUB_EMPTY_PATH

        out.append("\n--- V-1 / V-7: fixture shape signals ---")
        out.append("| fixture | status | html-len | breadcrumb-links | terminus | ldjson |")
        rows: dict[str, str] = {}
        for name in REPORT_ORDER:
            if name == "kampaniya-active":
                if kamp_already_on_disk:
                    html: str | None = kamp_html
                    status = "on-disk (skipped fetch)"
                elif kamp_html is not None:
                    html = kamp_html
                    kamp_file.write_text(html, encoding="utf-8")
                    status = "n/a (reused from probe)"
                else:
                    html = None
                    status = "SKIPPED — no campaign candidate could be fetched"
            else:
                html, status = load_or_fetch(client, name, all_pages[name], rp)

            if html is None:
                rows[name] = f"| {name} | {status} | - | - | - | - |"
                continue
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
            r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>",
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

        # F1: one real sub-page per biznes segment, one hop below each hub.
        out.append("\n--- F1 (V-1 follow-up): biznes sub-pages, one hop below each hub ---")
        out.append(
            "| fixture (path) | status | html-len | breadcrumb-bullet-in-visible-text | "
            "stat-block-in-visible-text | ldjson |"
        )
        for sub_name, (hub_name, base_path) in SUB_PAGES.items():
            sub_path = first_sub_page_path(hub_name, base_path)
            if sub_path is None:
                out.append(
                    f"| {sub_name} | SKIPPED — no sub-page href found under "
                    f"{base_path} in {hub_name}.html | | | | |"
                )
                continue
            html, status = load_or_fetch(client, sub_name, sub_path, rp)
            if html is None:
                out.append(f"| {sub_name} ({sub_path}) | {status} | - | - | - | - |")
                continue
            vis = visible_text(html)
            has_bullet = "•" in vis
            has_stat = any(lbl in vis for lbl in STAT_LABELS)
            out.append(
                f"| {sub_name} ({sub_path}) | {status} | {len(html)} | "
                f"breadcrumb-bullet={'yes' if has_bullet else 'NO'} | "
                f"stat-block={'yes' if has_stat else 'NO'} | "
                f"ldjson={'yes' if 'BankOrCreditUnion' in html else 'NO'} |"
            )

        # F4: CDN PDF probe sourced from the already-captured fixtures
        # (superseding round 1's inconclusive CDN-root request). This targets
        # cdn.abb-bank.az, a different host from the abb-bank.az robots.txt
        # `rp` above was built from, so it is not gated by `rp`.
        out.append("\n--- V-3 (F4 follow-up): CDN PDF probe from fixtures ---")
        pdf_urls = find_cdn_pdf_urls()
        if not pdf_urls:
            out.append("V-3: no cdn.abb-bank.az PDF links found in any fixture. Clean negative.")
        else:
            out.append(
                f"V-3: {len(pdf_urls)} distinct PDF URL(s) found across fixtures: {pdf_urls}"
            )
            for url in pdf_urls[:2]:
                _polite_sleep()
                # R4: stream and read only the first chunk, so a CDN that
                # ignores Range still costs ~2 KB, not the whole file.
                with client.stream(
                    "GET", url, headers={"Range": "bytes=0-2047"}, timeout=15.0
                ) as r:
                    ctype = r.headers.get("content-type", "?")
                    clen_header = r.headers.get("content-length", "?")
                    chunk = next(r.iter_bytes(chunk_size=2048), b"")
                out.append(
                    f"V-3: probe {url} -> status={r.status_code} "
                    f"content-type={ctype} content-length-header={clen_header} "
                    f"bytes-read={len(chunk)}"
                )

    # V-2 (F3): now answerable — OPENAI_API_KEY loaded from .env above.
    # Still wrapped so a bad/missing key degrades to PENDING instead of
    # crashing, keeping the script safely re-runnable.
    out.append("\n--- V-2: model availability ---")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        out.append("V-2: PENDING — no OPENAI_API_KEY")
    else:
        try:
            from openai import OpenAI

            oai = OpenAI()
            ids = sorted(m.id for m in oai.models.list())
            gpt_ids = [i for i in ids if "gpt" in i]
            embed_ids = [i for i in ids if "embedding" in i]

            out.append(f"V-2: {len(ids)} models. Chat-capable sample: {gpt_ids[:12]}")
            out.append(f"V-2: embedding models: {embed_ids}")

            luna = "gpt-5.6-luna"
            luna_available = luna in ids
            out.append(f"V-2: LLM_MODEL candidate '{luna}' available={luna_available}")

            mini_candidates = sorted(i for i in gpt_ids if "mini" in i)
            newest_mini = mini_candidates[-1] if mini_candidates else None
            out.append(f"V-2: newest available mini-tier chat model: {newest_mini}")

            embed_model = "text-embedding-3-small"
            if embed_model in embed_ids:
                eresp = oai.embeddings.create(
                    model=embed_model, input="ABB Bank verification gate probe"
                )
                dim = len(eresp.data[0].embedding)
                out.append(f"V-2: embedding model {embed_model} dimension={dim}")
            else:
                out.append(f"V-2: default embedding model {embed_model} NOT in available list")

            schema_model = (
                luna if luna_available else (newest_mini or (gpt_ids[-1] if gpt_ids else None))
            )
            if schema_model:
                try:
                    oai.responses.create(
                        model=schema_model,
                        input="Return the number 1.",
                        text={
                            "format": {
                                "type": "json_schema",
                                "name": "probe",
                                "schema": {
                                    "type": "object",
                                    "properties": {"n": {"type": "integer"}},
                                    "required": ["n"],
                                    "additionalProperties": False,
                                },
                                "strict": True,
                            }
                        },
                    )
                    out.append(
                        f"V-2: strict JSON schema on Responses API with {schema_model}: SUPPORTED"
                    )
                except Exception as e:  # deliberately broad, this is a probe
                    out.append(
                        f"V-2: strict JSON schema on Responses API with "
                        f"{schema_model}: UNSUPPORTED/FAILED — {e!r}"
                    )
            else:
                out.append("V-2: no chat model available to test structured output against")
        except Exception as e:  # deliberately broad, this is a probe
            out.append(f"V-2: PENDING — OPENAI_API_KEY present but call failed: {e!r}")

    # V-4: human browser step, per decision #3.
    out.append("\n--- V-4: localStorage quota (human step) ---")
    out.append("V-4: PENDING — human step. Paste into a browser console on any page:")
    out.append(
        'const s = "x".repeat(975_000);\n'
        'try { localStorage.setItem("probe", s); '
        'console.log("stored", (JSON.stringify(localStorage).length * 2) / 1e6, '
        '"MB of quota"); }\n'
        'catch (e) { console.log("QUOTA EXCEEDED at", s.length, "chars"); }\n'
        'finally { localStorage.removeItem("probe"); }'
    )

    # Final safety net: never let the API key value reach the printed report,
    # even indirectly through an exception message.
    key_val = os.environ.get("OPENAI_API_KEY", "")
    if key_val:
        out = [line.replace(key_val, "<REDACTED>") for line in out]

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
