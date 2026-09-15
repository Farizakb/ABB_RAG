# ruff: noqa: RUF001
from evals.runner import Report, score_item


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
    """SPEC §8.1 branch one. Truncation to the retrieval window fails the case
    rather than passing it — that is the whole reason the index exists."""
    args: dict[str, bool | list[str]] = dict(
        grounded=True, refused=False, facts=[], class_members=["A", "B", "C"], index_exists=True
    )
    assert score_item(ENUM_ITEM, [], "A, B və C", **args)["enumeration_ok"]  # type: ignore[arg-type]
    assert not score_item(ENUM_ITEM, [], "A və B", **args)["enumeration_ok"]  # type: ignore[arg-type]


def test_enumeration_without_an_index_asserts_the_link_and_forbids_a_total_claim() -> None:
    """SPEC §8.1 branch two. ABB's listing page won the §5.5 measurement, so we
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
