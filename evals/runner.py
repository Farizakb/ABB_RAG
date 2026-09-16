from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
from dataclasses import dataclass, field
from typing import Any


def _percentile(values: list[int], p: float) -> float:
    """Linear-interpolation percentile (numpy's default 'linear' method), so
    `p=0.5` agrees with `statistics.median` and the runner needs no numpy
    dependency for a handful of latency numbers."""
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    if lo == hi:
        return float(s[lo])
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def score_item(
    item: dict[str, Any],
    sources: list[Any],
    answer: str,
    grounded: bool,
    refused: bool,
    facts: list[Any],
    refusal_class: str | None = None,
    class_members: list[str] | None = None,
    index_exists: bool = False,
    timings: dict[str, int] | None = None,
) -> dict[str, Any]:
    urls = {getattr(s, "url", s.get("url") if isinstance(s, dict) else "") for s in sources}
    expected = set(item.get("expected_source_urls", []))
    must_refuse = bool(item.get("must_refuse"))

    refusal_correct = True
    if must_refuse:
        refusal_correct = refused
        if item.get("refusal_class"):
            refusal_correct = refused and refusal_class == item["refusal_class"]

    # Task 42, ruling 6: a small_talk item passes iff it was answered (not
    # refused) and carried no sources -- it is neither grounded nor a
    # refusal, so it gets its own pass/fail shape instead of borrowing
    # must_refuse's or an answerable item's.
    small_talk_ok = True
    if item.get("type") == "small_talk":
        small_talk_ok = (not refused) and not urls

    include_ok = all(s in answer for s in item.get("must_include_facts", []))
    exclude_ok = all(s not in answer for s in item.get("must_not_include", []))

    numeric_ok = True
    if fa := item.get("fact_assert"):

        def get_num(f: Any) -> float | None:
            val = f.get("value_num") if isinstance(f, dict) else getattr(f, "value_num", None)
            return float(val) if val is not None else None

        numeric_ok = any(
            (f.get("attribute") if isinstance(f, dict) else f.attribute) == fa["attribute"]
            and get_num(f) == fa["value_num"]
            for f in facts
        ) and str(int(fa["value_num"])) in answer.replace(" ", "").replace(" ", "")

    # SPEC §8.1. Which assertion applies is decided by §5.5's day-two measurement,
    # read from the corpus by the caller rather than configured here, so an item
    # can never silently assert the shape the build did not take.
    enumeration_ok: bool | None = None
    if ea := item.get("enumeration_assert"):
        if index_exists:
            enumeration_ok = all(m in answer for m in (class_members or []))
        else:
            no_index = ea.get("if_no_index", {})
            listings = {getattr(s, "listing_url", None) for s in sources}
            lowered = answer.lower()
            enumeration_ok = no_index.get("must_carry_listing_url") in listings and all(
                w.lower() not in lowered for w in no_index.get("must_not_include", [])
            )

    wrong = (
        (must_refuse and not refused)
        or not exclude_ok
        or not numeric_ok
        or enumeration_ok is False
        or not small_talk_ok
    )

    timings = timings or {}
    return {
        "id": item.get("id"),
        "type": item.get("type"),
        "retrieval_hit": bool(expected & urls) if expected else None,
        "citation_present": bool(sources) if grounded else None,
        "refusal_correct": refusal_correct,
        "include_ok": include_ok,
        "exclude_ok": exclude_ok,
        "numeric_ok": numeric_ok,
        "enumeration_ok": enumeration_ok,
        "small_talk_ok": small_talk_ok if item.get("type") == "small_talk" else None,
        "wrong_answer": wrong,
        "grounded": grounded,
        "retrieval_ms": timings.get("retrieval_ms"),
        "generation_ms": timings.get("generation_ms"),
    }


ENUM_CONTEXT_SQL = """
SELECT d.source_class, d.title, d.section_path, d.url
FROM rag.documents d JOIN rag.corpora r ON r.id = d.corpus_id
WHERE r.content_hash = %s
"""


def enumeration_context(corpus_id: str, item: dict[str, Any]) -> tuple[list[str], bool]:
    """Read which §5.5 branch the build actually took, and the class's real members.

    `index_exists` is per class, not global: a synthetic campaign index can exist
    while ABB's own `/ferdi/kreditler` page won for products. An index document is
    anchored to the listing page it mirrors, which is the same URL the item names
    in `if_no_index.must_carry_listing_url`, so that URL identifies the class.

    One query per enumeration item — there are two in the set, so caching it would
    be more code than it saves."""
    ea = item.get("enumeration_assert") or {}
    listing = (ea.get("if_no_index") or {}).get("must_carry_listing_url", "")
    from app.db import get_conn

    with get_conn() as conn:
        rows = conn.execute(ENUM_CONTEXT_SQL, (corpus_id,)).fetchall()

    want_class, want_path = ea.get("source_class"), ea.get("section_path")
    members = [
        title
        for source_class, title, section_path, _ in rows
        if (want_class and source_class == want_class)
        or (
            want_path
            and source_class == "product"
            and list(section_path or [])[: len(want_path)] == want_path
        )
    ]
    index_exists = any(
        source_class == "index" and url.rstrip("/") == listing.rstrip("/")
        for source_class, _, _, url in rows
    )
    return members, index_exists


@dataclass
class Report:
    rows: list[dict[str, Any]] = field(default_factory=list)

    def _rate(self, key: str, rows: list[dict[str, Any]] | None = None) -> float:
        rows = self.rows if rows is None else rows
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else 0.0

    def _latency(self, key: str) -> dict[str, float]:
        vals = [r[key] for r in self.rows if r.get(key) is not None]
        return {
            "median": round(_percentile(vals, 0.5), 1),
            "p95": round(_percentile(vals, 0.95), 1),
            "max": float(max(vals)) if vals else 0.0,
        }

    def exit_code(self) -> int:
        return 1 if any(r.get("wrong_answer") for r in self.rows) else 0

    def _latency_budget_row(self, label: str, lat: dict[str, float], threshold: float) -> str:
        """Both statistics, each with its own verdict, so a forgiving median
        can never be published as the sole result directly above p95 data
        that fails it -- SPEC §20 treats that as a quietly averaged-away miss."""
        median_status = "PASS" if lat["median"] < threshold else "MISS"
        p95_status = "PASS" if lat["p95"] < threshold else "MISS"
        return (
            f"| {label} | {lat['median']} {median_status} | {lat['p95']} {p95_status} | "
            f"median {median_status} / p95 {p95_status} |"
        )

    def markdown(self, config: dict[str, Any]) -> str:
        try:
            sha = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
            ).stdout.strip()
        except FileNotFoundError:
            # No git binary on PATH -- the runtime images ship without one, and
            # the runner is executed inside `rag` when the host cannot reach the
            # pool. The sha is provenance, not a metric: losing it beats losing a
            # completed run's report to an exception raised after every paid call.
            sha = ""
        wrong = [r for r in self.rows if r.get("wrong_answer")]
        answerable = [r for r in self.rows if r.get("type") == "answerable"]
        refusal_set = [r for r in self.rows if r.get("type") in ("out_of_scope", "advisory")]
        wrong_refusal = [r for r in refusal_set if r.get("wrong_answer")]
        grounded_answerable = self._rate("grounded", answerable)
        wrong_refusal_rate = self._rate("wrong_answer", refusal_set)
        retrieval_lat = self._latency("retrieval_ms")
        generation_lat = self._latency("generation_ms")
        total_ms = [
            r["retrieval_ms"] + r["generation_ms"]
            for r in self.rows
            if r.get("retrieval_ms") is not None and r.get("generation_ms") is not None
        ]
        # Finding 1: the per-item sum, then percentiled -- not the sum of the
        # two stages' separate p95s, which would overstate a value no single
        # request actually saw.
        e2e_lat = {
            "median": round(_percentile(total_ms, 0.5), 1) if total_ms else 0.0,
            "p95": round(_percentile(total_ms, 0.95), 1) if total_ms else 0.0,
        }
        lines = [
            "# Eval report",
            "",
            f"**Headline (SPEC §8.2): wrong-answer rate on out_of_scope + advisory, "
            f"{len(wrong_refusal)}/{len(refusal_set)}.** Abstention beats guessing.",
            "",
            f"Wrong-answer rate, all {len(self.rows)} items: {len(wrong)}/{len(self.rows)}.",
            "",
            "| metric | value |",
            "|---|---|",
            f"| retrieval hit@5 | {self._rate('retrieval_hit')} |",
            f"| citation present | {self._rate('citation_present')} |",
            f"| refusal correct | {self._rate('refusal_correct')} |",
            f"| must_include | {self._rate('include_ok')} |",
            f"| must_not_include | {self._rate('exclude_ok')} |",
            f"| numeric agreement | {self._rate('numeric_ok')} |",
            f"| enumeration | {self._rate('enumeration_ok')} |",
            f"| small talk correct | {self._rate('small_talk_ok')} |",
            f"| grounded rate, answerable only (n={len(answerable)}) | {grounded_answerable} |",
            "",
            "## Latency (ms)",
            "",
            "| stage | median | p95 | max |",
            "|---|---|---|---|",
            f"| retrieval | {retrieval_lat['median']} | {retrieval_lat['p95']} | "
            f"{retrieval_lat['max']} |",
            f"| generation | {generation_lat['median']} | {generation_lat['p95']} | "
            f"{generation_lat['max']} |",
            "",
            "## Budgets (SPEC §8.4 Global Constraints)",
            "",
            "### Latency",
            "",
            "Both median and p95 are shown against the same threshold, each with its "
            "own verdict -- a passing median does not stand in for a failing p95.",
            "",
            "| budget | median | p95 | status |",
            "|---|---|---|---|",
            self._latency_budget_row("retrieval < 300ms", retrieval_lat, 300),
            self._latency_budget_row(
                "end-to-end < 3000ms (per-item retrieval_ms + generation_ms, "
                "median-of-sums / p95-of-sums)",
                e2e_lat,
                3000,
            ),
            "",
            "### Correctness",
            "",
            "| budget | measured | status |",
            "|---|---|---|",
            f"| grounded rate, answerable ≥ 0.9 (n={len(answerable)}) | {grounded_answerable} | "
            f"{'PASS' if grounded_answerable >= 0.9 else 'MISS'} |",
            f"| wrong-answer rate, out_of_scope+advisory = 0 (n={len(refusal_set)}) | "
            f"{wrong_refusal_rate} | {'PASS' if wrong_refusal_rate == 0 else 'MISS'} |",
            "",
            f"config: `{json.dumps(config)}`  git: `{sha}`",
            "",
            "## Failures",
            "",
        ]
        checks = (
            "retrieval_hit",
            "refusal_correct",
            "include_ok",
            "exclude_ok",
            "numeric_ok",
            "enumeration_ok",
            "small_talk_ok",
        )
        lines += [
            f"- `{r['id']}` ({r['type']}): " + ", ".join(k for k in checks if r.get(k) is False)
            for r in self.rows
            if any(r.get(k) is False for k in checks)
        ]
        return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default="evals/golden.jsonl")
    ap.add_argument("--corpus-id", default="")
    ap.add_argument("--out", default="evals/report.md")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument(
        "--rows-out",
        default="",
        help="Write the scored rows + config as JSON here after a real run -- "
        "provenance, and lets a later presentation-only change re-render the "
        "report with --from-rows instead of paying for another run.",
    )
    ap.add_argument(
        "--from-rows",
        default="",
        help="Render the report from a --rows-out JSON file instead of running "
        "the eval. Makes zero API calls and touches no other flag.",
    )
    args = ap.parse_args()

    if args.from_rows:
        # No app.embedder / app.generate import on this path -- rendering from
        # already-scored rows must not need psycopg or openai, let alone spend
        # money, or it would not be the escape hatch it is meant to be.
        data = json.loads(pathlib.Path(args.from_rows).read_text("utf-8"))
        report = Report(rows=data["rows"])
        pathlib.Path(args.out).write_text(report.markdown(data["config"]), encoding="utf-8")
        print(report.markdown(data["config"]))
        return report.exit_code()

    from app.embedder import FakeEmbedder, OpenAIEmbedder
    from app.generate import OpenAIClient, answer

    embedder = FakeEmbedder(dim=8) if args.mock else OpenAIEmbedder()
    client = _MockClient() if args.mock else OpenAIClient()

    items = [json.loads(line) for line in pathlib.Path(args.golden).read_text("utf-8").splitlines()]
    if args.limit:
        items = items[: args.limit]

    report = Report()
    for item in items:
        r = answer(args.corpus_id, item["question"], embedder, client)
        members: list[str] = []
        index_exists = False
        if item.get("enumeration_assert") and not args.mock:
            members, index_exists = enumeration_context(args.corpus_id, item)
        report.rows.append(
            score_item(
                item,
                r.sources,
                r.answer,
                r.grounded,
                r.refused,
                [f.model_dump() for f in r.facts_used],
                r.refusal_class,
                class_members=members,
                index_exists=index_exists,
                timings=r.timings_ms,
            )
        )

    config = {
        "model": getattr(client, "model", "mock"),
        "embedder": embedder.model,
        "items": len(items),
    }
    if args.rows_out:
        pathlib.Path(args.rows_out).write_text(
            json.dumps({"config": config, "rows": report.rows}, indent=2), encoding="utf-8"
        )
    pathlib.Path(args.out).write_text(report.markdown(config), encoding="utf-8")
    print(report.markdown(config))
    return report.exit_code()


class _MockClient:
    model = "mock"

    def complete(self, prompt: str) -> tuple[str, dict[str, int]]:
        return json.dumps({"answer": "mock", "citations": [1], "grounded": True}), {}


if __name__ == "__main__":
    raise SystemExit(main())
