"""KAN-145 반복 일치율·캐시·예산 집계. 합성 값만 쓰고 모델·DB·정답 파일에 접근하지 않는다."""

import hashlib
import json
from decimal import Decimal

import pytest

from eval.repeat_metrics import (
    CACHE_NOTE,
    summarize_budget,
    summarize_cache,
    summarize_repeat_agreement,
)


def label(category="WARRANTY", urgency="NONE", info=()):
    """분류 출력 dict 하나.

    Args:
        category: 주분류.
        urgency: 긴급도.
        info: 제공 정보 코드.

    Returns:
        dict: ClassificationOutput 형식.
    """
    return {
        "category_code": category,
        "secondary_category": None,
        "urgency_flag": urgency,
        "provided_info": list(info),
        "return_subtype": None,
    }


# a: 세 실행 모두 같음, b: 3회차 긴급도만 다름, c: 2회차 실패(None), d: 정보 코드 순서만 다름(집합 같음).
RUNS = [
    {"case-a": label(), "case-b": label(), "case-c": label(), "case-d": label(info=("X", "Y"))},
    {"case-a": label(), "case-b": label(), "case-c": None, "case-d": label(info=("Y", "X"))},
    {
        "case-a": label(),
        "case-b": label(urgency="SAFETY"),
        "case-c": label(),
        "case-d": label(info=("X", "Y")),
    },
]


def test_repeat_agreement_matches_hand_calculation():
    """필드별·전체 일치율과 결과 유무 불일치를 따로 센다.

    Args:
        없음.

    Returns:
        None: 손계산 값과 ID 비포함을 확인한다.
    """
    result = summarize_repeat_agreement(RUNS)
    assert (result["runs"], result["cases"]) == (3, 4)
    fields = result["fields"]
    assert fields["category_code"] == {"agree": 3, "rate": 0.75}
    assert fields["urgency_flag"] == {"agree": 2, "rate": 0.5}
    assert fields["provided_info"] == {"agree": 3, "rate": 0.75}
    assert result["all_fields"] == {"agree": 2, "rate": 0.5}
    assert (result["validity_mismatch"], result["all_null"], result["disagreements"]) == (1, 0, 2)
    assert result["disagreements_sha256"] == hashlib.sha256(b"case-b\ncase-c").hexdigest()
    assert "case-" not in json.dumps(result)
    both_null = summarize_repeat_agreement([{"x": None}, {"x": None}])
    assert both_null["all_null"] == 1 and both_null["validity_mismatch"] == 0


@pytest.mark.parametrize(
    "runs",
    [
        [RUNS[0]],
        [RUNS[0], {k: v for k, v in RUNS[1].items() if k != "case-a"}],
        [RUNS[0], RUNS[1] | {"case-a": label(category="UNKNOWN")}],
        [RUNS[0], RUNS[1] | {"case-a": label() | {"extra": 1}}],
    ],
    ids=["one-run", "id-mismatch", "bad-category", "extra-key"],
)
def test_repeat_agreement_rejects_invalid_input(runs):
    """실행 1개·ID 집합 불일치·출력 형식 위반을 거부한다.

    Args:
        runs: 잘못된 실행 목록.

    Returns:
        None: EVAL_INVALID_INPUT을 확인한다.
    """
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        summarize_repeat_agreement(runs)


def usage(cached_reported, cached_sum, input_sum, missing=0):
    """후보 행 usage 묶음 하나.

    Args:
        cached_reported: 캐시 보고 수.
        cached_sum: 캐시 합.
        input_sum: 입력 합.
        missing: 캐시 누락 수.

    Returns:
        dict: sum_report 형식 usage.
    """
    return {
        "input_tokens": {
            "reported_count": 4,
            "missing_count": 0,
            "known_sum": input_sum,
            "total": input_sum,
        },
        "cached_input_tokens": {
            "reported_count": cached_reported,
            "missing_count": missing,
            "known_sum": cached_sum,
            "total": None,
        },
    }


def test_cache_ratio_and_unreported_note():
    """캐시가 보고되면 입력 대비 비율을, 0건이면 구분 불가 note를 낸다.

    Args:
        없음.

    Returns:
        None: 실행별·합계 비율과 note를 확인한다.
    """
    reported = summarize_cache([usage(4, 200, 1000), usage(2, 100, 1000, missing=2)])
    assert reported["runs"][0]["cache_ratio"] == pytest.approx(0.2)
    assert reported["total"] == {
        "cached_reported": 6,
        "cached_missing": 2,
        "cached_known_sum": 300,
        "input_known_sum": 2000,
        "cache_ratio": pytest.approx(0.15),
        "complete": False,
    }
    assert "note" not in reported
    unreported = summarize_cache([usage(0, None, 1000, missing=4)])
    assert (
        unreported["total"]["cache_ratio"] is None
        and unreported["total"]["cached_known_sum"] is None
    )
    assert unreported["note"] == CACHE_NOTE
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        summarize_cache([{"input_tokens": {}}])


def test_budget_burn_rate_and_over_budget():
    """실행별·누적 비용과 토큰을 예산 대비 소진률로 내고, 비용 미보고 실행을 따로 센다.

    Args:
        없음.

    Returns:
        None: Decimal 합계·소진률·초과·누락 표시를 확인한다.
    """
    runs = [
        {"name": "run-1", "cost_usd": "0.40", "total_tokens": 6000, "calls": 50},
        {"name": "run-2", "cost_usd": Decimal("0.70"), "total_tokens": 7000, "calls": 50},
        {"name": "run-3", "cost_usd": None, "total_tokens": None, "calls": 50},
    ]
    result = summarize_budget(runs, budget_usd=Decimal("1.00"), budget_tokens=20000)
    cost = result["cost_usd"]
    assert cost["known_sum"] == Decimal("1.10") and isinstance(cost["known_sum"], Decimal)
    assert (cost["missing_runs"], cost["complete"]) == (1, False)
    assert cost["burn_rate"] == Decimal("1.10") and cost["over_budget"] is True
    tokens = result["tokens"]
    assert tokens["known_sum"] == 13000 and tokens["burn_rate"] == pytest.approx(0.65)
    assert tokens["over_budget"] is False and result["calls"] == 150
    no_budget = summarize_budget(runs, budget_usd=None, budget_tokens=None)
    assert (
        no_budget["cost_usd"]["burn_rate"] is None and no_budget["cost_usd"]["over_budget"] is None
    )
    for bad in (
        {"name": "x", "cost_usd": "-1", "total_tokens": 1, "calls": 1},
        {"name": "x", "calls": -1},
    ):
        with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
            summarize_budget([bad], budget_usd=None, budget_tokens=None)


def test_partial_cache_report_marks_ratio_as_lower_bound():
    """캐시 보고가 일부 빠진 실행의 비율은 complete=False(하한)로 표시한다.

    Args:
        없음.

    Returns:
        None: 실행별·합계 complete를 확인한다.
    """
    result = summarize_cache([usage(4, 200, 1000), usage(2, 100, 1000, missing=2)])
    assert [row["complete"] for row in result["runs"]] == [True, False]
    assert result["runs"][1]["cache_ratio"] == pytest.approx(0.1)
    assert result["total"]["complete"] is False
    assert summarize_cache([usage(4, 200, 1000)])["total"]["complete"] is True


@pytest.mark.parametrize(
    "call",
    [
        lambda: summarize_repeat_agreement([RUNS[0], None]),
        lambda: summarize_budget([{"name": "x", "calls": 1}], budget_usd=1.0, budget_tokens=None),
        lambda: summarize_cache([usage(4, -5, 1000)]),
    ],
    ids=["non-mapping-run", "float-budget", "negative-known-sum"],
)
def test_more_invalid_inputs_are_rejected(call):
    """매핑 아닌 실행·Decimal 아닌 예산·음수 토큰 합을 다른 예외가 아니라 입력 오류로 거부한다.

    Args:
        call: 잘못된 입력으로 부르는 함수.

    Returns:
        None: EVAL_INVALID_INPUT을 확인한다.
    """
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        call()
