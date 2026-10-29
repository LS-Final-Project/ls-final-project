"""KAN-148 리포트 수치 단계 대조. 합성 값만 쓰고 DB·파일에 접근하지 않는다."""

from decimal import Decimal

import pytest

from eval.report_reconcile import (
    PARTIAL_NOTE,
    StageValues,
    csv_totals,
    reconcile,
    screen_payload_values,
)

RAW = StageValues("RAW", {"macro_f1": 0.8125, "critical_miss_count": 1, "hit_at_5": None}, 40)


def stages(**values):
    """RAW와 같은 값의 DB·SCREEN·CSV 단계를 만들고 일부를 바꾼다.

    Args:
        values: 단계 이름 → 바꿀 값 dict.

    Returns:
        list[StageValues]: 네 단계.
    """
    result = [RAW]
    for stage in ("DB", "SCREEN", "CSV"):
        changed = dict(RAW.values) | values.get(stage, {})
        result.append(StageValues(stage, changed, RAW.case_count))
    return result


def run(items, **kwargs):
    """완료 실행으로 대조한다.

    Args:
        items: 단계 값.
        kwargs: 바꿀 인자.

    Returns:
        dict: 대조 결과.
    """
    return reconcile(
        items, **({"run_status": "COMPLETED", "targets": 40, "evaluated": 40} | kwargs)
    )


def test_all_stages_match_except_missing_value():
    """네 단계가 같으면 MATCH이고, 모든 단계가 None인 지표는 MISSING이며 MATCH로 올리지 않는다.

    Args:
        없음.

    Returns:
        None: 상태·요약·일관성을 확인한다.
    """
    result = run(stages())
    assert result["metrics"]["macro_f1"]["status"] == "MATCH"
    assert result["metrics"]["macro_f1"]["values"] == dict.fromkeys(
        ("RAW", "DB", "SCREEN", "CSV"), 0.8125
    )
    assert result["metrics"]["hit_at_5"] == {
        "status": "MISSING",
        "values": dict.fromkeys(("RAW", "DB", "SCREEN", "CSV")),
        "diff_stages": ["RAW", "DB", "SCREEN", "CSV"],
    }
    assert result["case_count"]["status"] == "MATCH"
    assert result["summary"] == {"MATCH": 2, "MISMATCH": 0, "MISSING": 1, "ZERO_FOR_MISSING": 0}
    assert result["consistent"] is True and result["partial"] is False and "note" not in result


def test_mismatch_and_tolerance():
    """한 단계가 다르면 그 단계를 MISMATCH로 내고, 허용 절대 차 안은 MATCH다.

    Args:
        없음.

    Returns:
        None: 다른 단계 목록과 일관성을 확인한다.
    """
    result = run(stages(SCREEN={"macro_f1": 0.81}))
    assert result["metrics"]["macro_f1"]["status"] == "MISMATCH"
    assert result["metrics"]["macro_f1"]["diff_stages"] == ["SCREEN"]
    assert result["consistent"] is False
    assert (
        run(stages(SCREEN={"macro_f1": 0.81}), tolerance=0.01)["metrics"]["macro_f1"]["status"]
        == "MATCH"
    )


def test_missing_stage_and_zero_for_missing():
    """None 단계는 MISSING, 0과 None이 함께 있으면 누락을 0으로 채운 것으로 의심해 따로 표시한다.

    Args:
        없음.

    Returns:
        None: 상태·단계 목록·일관성을 확인한다.
    """
    lost = run(stages(CSV={"macro_f1": None}))
    missing = lost["metrics"]["macro_f1"]
    assert missing["status"] == "MISSING" and missing["diff_stages"] == ["CSV"]
    # 앞 단계에 있던 값이 CSV에서 사라졌으므로 일관하지 않다(전 단계 None인 hit_at_5와 다름).
    assert lost["consistent"] is False
    zero = run(stages(DB={"hit_at_5": 0}))
    assert zero["metrics"]["hit_at_5"]["status"] == "ZERO_FOR_MISSING"
    assert zero["metrics"]["hit_at_5"]["diff_stages"] == ["RAW", "SCREEN", "CSV"]
    assert zero["consistent"] is False
    # 한 단계에 key가 아예 없어도 None으로 본다.
    absent = run([RAW, StageValues("DB", {"macro_f1": 0.8125}, 40)])["metrics"][
        "critical_miss_count"
    ]
    assert absent["status"] == "MISSING" and absent["diff_stages"] == ["DB"]


def test_case_count_mismatch_breaks_consistency():
    """사례 수도 같은 규칙으로 대조한다.

    Args:
        없음.

    Returns:
        None: 사례 수 MISMATCH와 일관성을 확인한다.
    """
    result = run([RAW, StageValues("DB", dict(RAW.values), 39)])
    assert result["case_count"]["status"] == "MISMATCH" and result["consistent"] is False


@pytest.mark.parametrize("status", ["ABORTED", "PARTIAL"])
def test_partial_run_is_marked_and_not_consistent(status):
    """중단·부분 완료는 부분 결과로 표시하고 값이 맞아도 consistent가 아니다.

    Args:
        status: 실행 상태.

    Returns:
        None: partial·coverage·note를 확인한다.
    """
    result = run(stages(), run_status=status, evaluated=30)
    assert result["partial"] is True and result["note"] == PARTIAL_NOTE
    assert result["coverage"] == {
        "targets": 40,
        "evaluated": 30,
        "incomplete_targets": 10,
        "ratio": 0.75,
    }
    assert result["consistent"] is False
    assert run(stages(), targets=0, evaluated=0)["coverage"]["ratio"] is None


def test_completed_status_with_unevaluated_targets_is_partial():
    """상태가 COMPLETED여도 평가를 마치지 못한 대상이 있으면 부분 결과다.

    Args:
        없음.

    Returns:
        None: partial·note·consistent를 확인한다.
    """
    result = run(stages(), evaluated=39)
    assert result["partial"] is True and result["note"] == PARTIAL_NOTE
    assert result["coverage"]["incomplete_targets"] == 1 and result["consistent"] is False


@pytest.mark.parametrize(
    "items,kwargs",
    [
        (stages(), {"evaluated": 41}),
        (stages() + [RAW], {}),
        (stages()[1:], {}),
        (stages(DB={"macro_f1": float("nan")}), {}),
        (stages(DB={"macro_f1": True}), {}),
        (stages(), {"tolerance": -0.1}),
        (stages(), {"run_status": "DONE"}),
    ],
    ids=[
        "evaluated-over-targets",
        "duplicate-stage",
        "no-raw",
        "nan",
        "bool",
        "negative-tolerance",
        "bad-status",
    ],
)
def test_invalid_input_is_rejected(items, kwargs):
    """잘못된 대상 수·단계·값·허용 차·상태를 거부한다.

    Args:
        items: 단계 값.
        kwargs: 바꿀 인자.

    Returns:
        None: EVAL_INVALID_INPUT을 확인한다.
    """
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        run(items, **kwargs)


def test_csv_totals_keep_missing_apart_and_sum_exactly():
    """빈 칸은 누락으로 세고 0으로 더하지 않으며, Decimal로 정확히 합한다.

    Args:
        없음.

    Returns:
        None: 보고·누락 수와 합계를 확인한다.
    """
    rows = [
        {"cost_usd": "0.1", "tokens": "10"},
        {"cost_usd": "0.2", "tokens": ""},
        {"cost_usd": " 0.0 ", "tokens": None},
        {"cost_usd": "0.3"},
    ]
    totals = csv_totals(rows, ("cost_usd", "tokens"))
    assert totals["cost_usd"] == {
        "reported_count": 4,
        "missing_count": 0,
        "known_sum": Decimal("0.6"),
        "total": Decimal("0.6"),
    }
    assert totals["tokens"] == {
        "reported_count": 1,
        "missing_count": 3,
        "known_sum": Decimal("10"),
        "total": None,
    }
    assert csv_totals([{"x": ""}], ("x",))["x"]["known_sum"] is None
    for bad in ("abc", "NaN", "1,000"):
        with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
            csv_totals([{"x": bad}], ("x",))


def test_screen_payload_missing_path_is_none():
    """화면 DTO에서 경로가 없으면 0이 아니라 None이고, 숫자가 아닌 값은 거부한다.

    Args:
        없음.

    Returns:
        None: 꺼낸 값과 오류를 확인한다.
    """
    payload = {"summary": {"category": {"macro_f1": 0.8125}, "urgency": None}}
    values = screen_payload_values(
        payload,
        {
            "macro_f1": ("summary", "category", "macro_f1"),
            "urgency_macro_f1": ("summary", "urgency", "macro_f1"),
            "hit_at_5": ("search", "hit_at_5"),
        },
    )
    assert values == {"macro_f1": 0.8125, "urgency_macro_f1": None, "hit_at_5": None}
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        screen_payload_values({"a": "0.8"}, {"a": ("a",)})


def test_csv_totals_accept_plain_numbers_only():
    """부호·숫자·소수점·지수만 허용하고 Decimal이 받는 밑줄 숫자('1_000')는 거부한다.

    Args:
        없음.

    Returns:
        None: 허용 형식 합계와 밑줄 거부를 확인한다.
    """
    totals = csv_totals([{"x": "-0.5"}, {"x": "1e3"}, {"x": "+.5"}], ("x",))
    assert totals["x"]["total"] == Decimal("1000.0")
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        csv_totals([{"x": "1_000"}], ("x",))
