# ruff: noqa: RUF001
import json
import pathlib

import pytest

from evals.runner import Report, _percentile, main, score_item


def test_retrieval_hit_at_5_matches_on_expected_source_url() -> None:
    item = {"type": "answerable", "expected_source_urls": ["https://abb-bank.az/x"]}
    hit = score_item(
        item,
        sources=[type("S", (), {"url": "https://abb-bank.az/x"})()],
        answer="x",
        grounded=True,
        refused=False,
        facts=[],
    )
    assert hit["retrieval_hit"] is True


def test_must_not_include_failure_is_a_wrong_answer_not_a_soft_miss() -> None:
    item = {"type": "answerable", "expected_source_urls": [], "must_not_include": ["30 000"]}
    r = score_item(item, sources=[], answer="30 000 AZN", grounded=True, refused=False, facts=[])
    assert r["wrong_answer"] is True


def test_answering_an_out_of_scope_question_is_a_wrong_answer() -> None:
    item = {"type": "out_of_scope", "must_refuse": True}
    r = score_item(
        item, sources=[], answer="Kapital Bankda 12%", grounded=True, refused=False, facts=[]
    )
    assert r["refusal_correct"] is False and r["wrong_answer"] is True


def test_advisory_case_requires_the_advisory_class_specifically() -> None:
    item = {"type": "advisory", "must_refuse": True, "refusal_class": "advisory"}
    ok = score_item(
        item, [], "…937…", grounded=False, refused=True, facts=[], refusal_class="advisory"
    )
    bad = score_item(
        item, [], "…", grounded=False, refused=True, facts=[], refusal_class="out_of_scope"
    )
    assert ok["refusal_correct"] and not bad["refusal_correct"]


def test_numeric_case_asserts_against_the_governed_fact_row() -> None:
    item = {
        "type": "answerable",
        "tags": ["numeric"],
        "expected_source_urls": [],
        "fact_assert": {"attribute": "max_amount", "value_num": 50000},
    }
    good = score_item(
        item, [], "50 000 AZN", True, False, facts=[{"attribute": "max_amount", "value_num": 50000}]
    )
    bad = score_item(
        item, [], "40 000 AZN", True, False, facts=[{"attribute": "max_amount", "value_num": 40000}]
    )
    assert good["numeric_ok"] and not bad["numeric_ok"]


def test_report_exits_non_zero_when_wrong_answer_rate_is_above_zero() -> None:
    assert Report(rows=[{"wrong_answer": True}]).exit_code() == 1
    assert Report(rows=[{"wrong_answer": False}]).exit_code() == 0


ENUM_ITEM = {
    "id": "a03",
    "type": "answerable",
    "tags": ["enumeration"],
    "expected_source_urls": [],
    "enumeration_assert": {
        "source_class": "campaign",
        "status": "active",
        "if_no_index": {
            "must_carry_listing_url": "https://abb-bank.az/kampaniyalar",
            "must_not_include": ["hamısı", "ən son"],
        },
    },
}


def src(url: str, listing_url: str | None = None) -> object:
    return type("S", (), {"url": url, "listing_url": listing_url or url})()


def test_enumeration_with_a_synthetic_index_asserts_completeness() -> None:
    """Truncation to the retrieval window fails the case
    rather than passing it — that is the whole reason the index exists."""
    args: dict[str, bool | list[str]] = dict(
        grounded=True, refused=False, facts=[], class_members=["A", "B", "C"], index_exists=True
    )
    assert score_item(ENUM_ITEM, [], "A, B və C", **args)["enumeration_ok"]  # type: ignore[arg-type]
    assert not score_item(ENUM_ITEM, [], "A və B", **args)["enumeration_ok"]  # type: ignore[arg-type]


def test_enumeration_without_an_index_asserts_the_link_and_forbids_a_total_claim() -> None:
    """ABB's listing page won the measurement, so we
    hold no complete list — and an answer claiming one is a wrong answer even
    though every sentence in it is cited."""
    args: dict[str, bool | list[str]] = dict(
        grounded=True, refused=False, facts=[], class_members=["A", "B", "C"], index_exists=False
    )
    linked = [src("https://abb-bank.az/kampaniyalar/a", "https://abb-bank.az/kampaniyalar")]
    assert score_item(ENUM_ITEM, linked, "A və B var.", **args)["enumeration_ok"]  # type: ignore[arg-type]
    assert not score_item(ENUM_ITEM, linked, "Hamısı budur: A və B.", **args)["enumeration_ok"]  # type: ignore[arg-type]
    assert (
        not score_item(ENUM_ITEM, [src("https://abb-bank.az/ferdi")], "A və B var.", **args)[  # type: ignore[arg-type]
            "enumeration_ok"
        ]
    )


def test_percentile_linear_interpolation_over_a_known_list() -> None:
    values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    assert _percentile(values, 0.5) == 55.0
    assert round(_percentile(values, 0.95), 1) == 95.5
    assert _percentile(values, 0.0) == 10.0
    assert _percentile([], 0.5) == 0.0


def test_latency_table_reports_median_p95_max_in_markdown() -> None:
    rows = [
        {"type": "answerable", "grounded": True, "retrieval_ms": v, "generation_ms": v}
        for v in [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    ]
    md = Report(rows=rows).markdown({"model": "x", "embedder": "y", "items": 10})
    assert "| retrieval | 55.0 | 95.5 | 100.0 |" in md
    assert "| generation | 55.0 | 95.5 | 100.0 |" in md


def test_grounded_rate_on_answerable_subset_ignores_non_answerable_rows() -> None:
    """The budget's denominator is the answerable set only -- a
    correct refusal on an out-of-scope or advisory item is not grounded and
    must not drag the rate down, nor may it be counted as a free pass."""
    rows = [
        {"type": "answerable", "grounded": True},
        {"type": "answerable", "grounded": True},
        {"type": "answerable", "grounded": False},
        {"type": "out_of_scope", "grounded": False},
        {"type": "advisory", "grounded": False},
    ]
    md = Report(rows=rows).markdown({"model": "x", "embedder": "y", "items": 5})
    assert "grounded rate, answerable only (n=3) | 0.667" in md


def test_budget_table_shows_median_and_p95_with_separate_verdicts() -> None:
    """Fix round 1, Finding 1: a passing median must never be published as the
    sole verdict when p95 fails the same threshold. Nine fast items and one
    slow outlier keep the median comfortably under 300ms while p95 blows
    through it, so the table must show both numbers and both verdicts."""
    values = [100] * 9 + [5000]
    rows = [
        {"type": "answerable", "grounded": True, "retrieval_ms": v, "generation_ms": 0}
        for v in values
    ]
    md = Report(rows=rows).markdown({"model": "x", "embedder": "y", "items": len(rows)})
    assert "| retrieval < 300ms | 100.0 PASS | 2795.0 MISS | median PASS / p95 MISS |" in md


def test_end_to_end_budget_percentiles_the_per_row_sum_not_the_sum_of_percentiles() -> None:
    """Finding 1: e2e p95 must be percentile(retrieval_ms + generation_ms per
    row), not percentile(retrieval_ms) + percentile(generation_ms) -- the
    latter is a number no single request ever produced."""
    retrieval = [100] * 9 + [5000]
    generation = [200] * 9 + [100]
    rows = [
        {"type": "answerable", "grounded": True, "retrieval_ms": r, "generation_ms": g}
        for r, g in zip(retrieval, generation, strict=True)
    ]
    md = Report(rows=rows).markdown({"model": "x", "embedder": "y", "items": len(rows)})
    correct_p95 = round(
        _percentile([r + g for r, g in zip(retrieval, generation, strict=True)], 0.95), 1
    )
    wrong_p95 = round(_percentile(retrieval, 0.95), 1) + round(_percentile(generation, 0.95), 1)
    assert correct_p95 != wrong_p95, "fixture must distinguish the two approaches"
    assert f"{correct_p95} PASS" in md
    assert f"{wrong_p95} PASS" not in md and f"{wrong_p95} MISS" not in md


def test_headline_is_the_out_of_scope_and_advisory_wrong_answer_rate() -> None:
    """The headline is the out_of_scope+advisory wrong-answer
    rate, not the all-items rate -- both are real numbers and both are kept,
    but only one is labelled the headline."""
    rows = [
        {"type": "answerable", "wrong_answer": True},
        {"type": "answerable", "wrong_answer": False},
        {"type": "out_of_scope", "wrong_answer": False},
        {"type": "advisory", "wrong_answer": False},
    ]
    md = Report(rows=rows).markdown({"model": "x", "embedder": "y", "items": 4})
    assert "Headline: wrong-answer rate on out_of_scope + advisory, 0/2." in md
    assert "Wrong-answer rate, all 4 items: 1/4." in md


def test_from_rows_cli_renders_byte_identical_markdown_to_in_memory_rows(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of --rows-out/--from-rows is that a presentation-only
    change can be re-rendered for free -- so it must reproduce exactly what a
    live run would have written, not an approximation of it."""
    rows = [
        {
            "id": "a1",
            "type": "answerable",
            "grounded": True,
            "wrong_answer": False,
            "retrieval_ms": 120,
            "generation_ms": 900,
        }
    ]
    config = {"model": "gpt-5.6-luna", "embedder": "text-embedding-3-small", "items": 1}
    expected = Report(rows=rows).markdown(config)

    rows_path = tmp_path / "rows.json"
    rows_path.write_text(json.dumps({"config": config, "rows": rows}), encoding="utf-8")
    out_path = tmp_path / "report.md"

    monkeypatch.setattr(
        "sys.argv",
        ["runner.py", "--from-rows", str(rows_path), "--out", str(out_path)],
    )
    exit_code = main()

    assert exit_code == 0
    assert out_path.read_text(encoding="utf-8") == expected


# small_talk items pass iff refused=false and no sources
# were carried, and stay out of the grounded/citation/refusal-set metrics
# purely by virtue of their `type` not being "answerable"/"out_of_scope"/
# "advisory" (score_item's existing type-scoped logic already handles that).
def test_small_talk_item_passes_when_answered_with_no_sources() -> None:
    item = {"id": "st01", "type": "small_talk", "question": "sen kimsen?"}
    r = score_item(item, sources=[], answer="Salam!", grounded=False, refused=False, facts=[])
    assert r["small_talk_ok"] is True
    assert r["wrong_answer"] is False
    assert r["citation_present"] is None
    assert r["retrieval_hit"] is None


def test_small_talk_item_fails_when_refused() -> None:
    item = {"id": "st02", "type": "small_talk", "question": "Salam kimsen sen?"}
    r = score_item(
        item,
        sources=[],
        answer="937...",
        grounded=False,
        refused=True,
        facts=[],
        refusal_class="out_of_scope",
    )
    assert r["small_talk_ok"] is False
    assert r["wrong_answer"] is True


def test_small_talk_item_fails_when_it_carries_sources() -> None:
    item = {"id": "st03", "type": "small_talk", "question": "necesen?"}
    r = score_item(
        item,
        sources=[src("https://abb-bank.az/x")],
        answer="Salam!",
        grounded=False,
        refused=False,
        facts=[],
    )
    assert r["small_talk_ok"] is False
    assert r["wrong_answer"] is True


def test_small_talk_rows_are_excluded_from_grounded_answerable_metric() -> None:
    rows = [
        {"type": "answerable", "grounded": True},
        {"type": "small_talk", "grounded": False, "small_talk_ok": True, "wrong_answer": False},
    ]
    md = Report(rows=rows).markdown({"model": "x", "embedder": "y", "items": 2})
    assert "grounded rate, answerable only (n=1) | 1.0" in md


# Fix round 1, F2: an adversarial item passes iff the guard held -- either a
# refusal or a properly grounded, cited answer. Only the small-talk shape
# itself (answered, no sources, not refused) is a failure.
def test_adversarial_item_passes_on_refusal() -> None:
    item = {"id": "sta01", "type": "small_talk_adversarial"}
    r = score_item(item, sources=[], answer="937...", grounded=False, refused=True, facts=[])
    assert r["adversarial_ok"] is True
    assert r["wrong_answer"] is False


def test_adversarial_item_passes_on_a_grounded_cited_answer() -> None:
    item = {"id": "sta02", "type": "small_talk_adversarial"}
    r = score_item(
        item,
        sources=[src("https://abb-bank.az/x")],
        answer="Bəli, illik haqqı yoxdur. [1]",
        grounded=True,
        refused=False,
        facts=[],
    )
    assert r["adversarial_ok"] is True
    assert r["wrong_answer"] is False


def test_adversarial_item_fails_when_it_slips_through_as_small_talk() -> None:
    item = {"id": "sta01", "type": "small_talk_adversarial"}
    r = score_item(
        item, sources=[], answer="Bəli, kart pulsuzdur.", grounded=False, refused=False, facts=[]
    )
    assert r["adversarial_ok"] is False
    assert r["wrong_answer"] is True


def test_a_failed_enumeration_assertion_counts_as_a_wrong_answer() -> None:
    r = score_item(
        ENUM_ITEM,
        [],
        "Hamısı budur: A.",
        grounded=True,
        refused=False,
        facts=[],
        class_members=["A", "B"],
        index_exists=False,
    )
    assert r["wrong_answer"] is True
