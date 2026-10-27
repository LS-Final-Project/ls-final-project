"""고정 합성 정답과 기존 호출 경계로 최초 주분류 집계를 검증한다."""

import json
from dataclasses import replace

import pytest

from backend.engine.cs.classification_call import run_classification_attempt
from backend.shared.ai import Generation, Usage
from eval.cs_metrics import (
    NO_PREDICTION,
    NO_SECONDARY,
    summarize_category_first_attempts,
    summarize_secondary_first_attempts,
    summarize_urgency_first_attempts,
)


def synthetic_attempt(
    category="RETURN", *, error=None, raw=None, number=1, held=False, urgency="NONE", secondary=None
):
    """실제 모델 없이 기존 호출 함수의 정상·실패 결과를 만든다.

    Args:
        category: 합성 예측 주분류.
        error: fake 제공자가 발생시킬 예외.
        raw: 스키마 실패 재현용 응답.
        number: 호출 시도 번호.
        held: 빈 본문으로 입력 보류를 재현할지 여부.
        urgency: 합성 예측 긴급도.
        secondary: 합성 보조 분류 또는 의도 없음.

    Returns:
        ClassificationAttempt: 기존 호출 경계가 만든 시도 기록.
    """

    def provider(_):
        """합성 응답 또는 예외를 반환한다.

        Args:
            _: 정제된 합성 입력.

        Returns:
            Generation: 합성 출력과 사용량.
        """
        if error:
            raise error
        text = (
            raw
            if raw is not None
            else json.dumps(
                {
                    "category_code": category,
                    "secondary_category": secondary,
                    "urgency_flag": urgency,
                    "provided_info": [],
                    "return_subtype": None,
                }
            )
        )
        return Generation(text, Usage(), "fake", [])

    return run_classification_attempt(
        {
            "channel": "EMAIL",
            "body_language": "en",
            "subject": None,
            "body": "" if held else "synthetic",
        },
        sensitive_values={},
        allowed_info_codes=frozenset(),
        provider=provider,
        attempt_number=number,
    )


def test_hand_calculated_confusion_and_fixed_ten_class_macro():
    """오분류·타임아웃·미실행을 포함한 손계산 결과와 비교한다.

    Args:
        없음.

    Returns:
        None: 5건 분모, 10클래스 평균, 사례 정오와 혼동행렬 보존을 확인한다.
    """
    gold = {"a": "RETURN", "b": "RETURN", "c": "RETURN", "d": "MANUAL", "e": "MANUAL"}
    report = summarize_category_first_attempts(
        gold,
        {
            "a": synthetic_attempt(),
            "b": synthetic_attempt("MANUAL"),
            "c": synthetic_attempt(error=TimeoutError("PRIVATE")),
            "d": synthetic_attempt("MANUAL"),
        },
    )
    assert report["total"] == 5 and report["correct"] == 2
    assert report["accuracy"] == pytest.approx(0.4)
    assert report["macro_f1"] == pytest.approx(0.1)
    assert len(report["labels"]) == 10
    assert report["per_class"]["RETURN"] == {
        "support": 3,
        "tp": 1,
        "fp": 0,
        "fn": 2,
        "precision": 1.0,
        "recall": 1 / 3,
        "f1": 0.5,
    }
    assert report["per_class"]["MANUAL"]["f1"] == 0.5
    assert report["per_class"]["QUOTE"]["support"] == 0
    assert report["per_class"]["QUOTE"]["f1"] == 0
    assert report["confusion_matrix"]["RETURN"]["MANUAL"] == 1
    assert report["confusion_matrix"]["RETURN"][NO_PREDICTION] == 1
    assert report["confusion_matrix"]["MANUAL"][NO_PREDICTION] == 1
    assert report["outcomes"]["NOT_STARTED"] == 1
    assert [r["correct"] for r in report["cases"]] == [True, False, False, True, False]
    assert not report["quality_gate_evaluated"]
    assert "PRIVATE" not in json.dumps(report)
    assert "raw_response" not in json.dumps(report)


@pytest.mark.parametrize(
    "kwargs,outcome",
    [
        ({"raw": "PRIVATE_BAD_JSON"}, "SCHEMA_VIOLATION"),
        ({"error": RuntimeError("PRIVATE")}, "TRANSPORT_FAILURE"),
        ({"held": True}, "HELD"),
    ],
)
def test_failures_are_false_negatives(kwargs, outcome):
    """출력 실패와 입력 보류를 정답 분모에서 제외하지 않는다.

    Args:
        kwargs: 합성 실패 설정.
        outcome: 기대 시도 상태.

    Returns:
        None: 실패를 무예측 열과 정답 클래스 FN으로 계산한다.
    """
    report = summarize_category_first_attempts({"a": "RETURN"}, {"a": synthetic_attempt(**kwargs)})
    assert report["total"] == 1 and report["accuracy"] == 0
    assert report["per_class"]["RETURN"]["fn"] == 1
    assert report["outcomes"][outcome] == 1
    assert report["cases"][0]["prediction"] is None


def test_retries_extra_ids_and_inconsistent_records_are_rejected():
    """재시도 성공이나 잘못된 대상을 최초 점수로 대체하지 못한다.

    Args:
        없음.

    Returns:
        None: 안전한 입력 오류로 집계를 거부한다.
    """
    good = synthetic_attempt()
    for gold, attempts in [
        ({"a": "RETURN"}, {"a": synthetic_attempt(number=2)}),
        ({"a": "RETURN"}, {"extra": good}),
        ({"a": "INVALID"}, {}),
        ({"a": "RETURN"}, {"a": replace(good, prediction=None)}),
        ({"a": "RETURN"}, {"a": replace(good, outcome="HELD")}),
        ({"a": "RETURN"}, {"a": None}),
    ]:
        with pytest.raises(ValueError, match="^CS_METRICS_INVALID_INPUT$"):
            summarize_category_first_attempts(gold, attempts)


def test_empty_target_has_no_quality_score():
    """평가 대상이 없으면 성공률이나 품질 점수를 만들지 않는다.

    Args:
        없음.

    Returns:
        None: 합계0·점수null·합격 판정 없음이다.
    """
    report = summarize_category_first_attempts({}, {})
    assert report["total"] == 0
    assert report["accuracy"] is None and report["macro_f1"] is None
    assert not report["quality_gate_evaluated"]


def test_urgency_confusion_is_distinct_from_critical_misses():
    """위험 유형 혼동·누락·비NONE 재현율을 손계산과 대조한다.

    Args:
        없음.

    Returns:
        None: 실패를 포함한 분모와 6클래스 평균을 확인한다.
    """
    gold = dict(
        zip(
            "abcdefgh",
            [
                "SAFETY",
                "SAFETY",
                "SITE_DOWN",
                "SITE_DOWN",
                "PROD_IMPACT",
                "NONE",
                "NONE",
                "COMMISSION",
            ],
        )
    )
    attempts = {
        "a": synthetic_attempt(urgency="SITE_DOWN"),
        "b": synthetic_attempt(urgency="NONE"),
        "c": synthetic_attempt(urgency="SAFETY"),
        "d": synthetic_attempt(error=TimeoutError()),
        "e": synthetic_attempt(urgency="DEADLINE"),
        "f": synthetic_attempt(),
        "g": synthetic_attempt(urgency="SAFETY"),
    }
    report = summarize_urgency_first_attempts(gold, attempts)
    assert report["total"] == 8
    assert report["accuracy"] == 1 / 8
    assert report["macro_f1"] == pytest.approx(1 / 12)
    assert len(report["labels"]) == 6
    assert report["non_none"] == {"support": 6, "detected": 3, "recall": 0.5}
    assert report["critical"] == {
        "support": 4,
        "missed": 2,
        "by_gold": {
            "SAFETY": {"support": 2, "missed": 1},
            "SITE_DOWN": {"support": 2, "missed": 1},
        },
        "safety_as_site_down": 1,
        "site_down_as_safety": 1,
    }
    assert [r["case_id"] for r in report["cases"] if r["critical_missed"]] == ["b", "d"]
    assert report["confusion_matrix"]["COMMISSION"][NO_PREDICTION] == 1
    assert report["per_class"]["SAFETY"]["fn"] == 2
    assert report["per_class"]["DEADLINE"]["support"] == 0
    assert not report["quality_gate_evaluated"]


@pytest.mark.parametrize("kwargs", [{"held": True}, {"raw": "BAD"}, {"error": RuntimeError()}])
def test_failed_critical_cases_remain_missed(kwargs):
    """각 실패 상태의 위험 사례를 무누락으로 집계하지 않는다.

    Args:
        kwargs: 기존 호출 경계에 전달할 합성 실패 설정.

    Returns:
        None: CRITICAL 누락 및 비NONE 재현율 실패를 확인한다.
    """
    report = summarize_urgency_first_attempts({"a": "SAFETY"}, {"a": synthetic_attempt(**kwargs)})
    assert report["critical"]["missed"] == 1
    assert report["non_none"]["recall"] == 0


def test_urgency_no_positive_target_is_not_perfect_recall():
    """비NONE 대상이 없는 경우 재현율을 100%로 만들지 않는다.

    Args:
        없음.

    Returns:
        None: 대상0과 미실행·기타 긴급도 예측의 경계를 확인한다.
    """
    for gold, attempts in [({}, {}), ({"a": "NONE"}, {"a": synthetic_attempt()})]:
        report = summarize_urgency_first_attempts(gold, attempts)
        assert report["non_none"] == {"support": 0, "detected": 0, "recall": None}
        assert report["critical"]["support"] == 0
        assert not report["quality_gate_evaluated"]
    report = summarize_urgency_first_attempts({"a": "SAFETY"}, {})
    assert report["critical"]["missed"] == 1
    report = summarize_urgency_first_attempts(
        {"a": "SAFETY"}, {"a": synthetic_attempt(urgency="PROD_IMPACT")}
    )
    assert report["critical"]["missed"] == 1
    assert report["non_none"]["recall"] == 1


@pytest.mark.parametrize(
    "gold,attempts",
    [
        ({"a": "RETURN"}, {}),
        ({"a": "SAFETY"}, {"a": synthetic_attempt(number=2)}),
        ({"a": "SAFETY"}, {"extra": synthetic_attempt()}),
    ],
)
def test_urgency_rejects_wrong_gold_retry_or_extra_target(gold, attempts):
    """긴급도에도 최초 시도 및 고정 대상 검사를 동일하게 적용한다.

    Args:
        gold: 합성 대상 정답.
        attempts: 잘못된 합성 시도 집합.

    Returns:
        None: 부분 집계 없이 안전한 오류로 거부한다.
    """
    with pytest.raises(ValueError, match="^CS_METRICS_INVALID_INPUT$"):
        summarize_urgency_first_attempts(gold, attempts)


def test_secondary_null_failure_and_composite_metrics_are_separate():
    """null 정답·실패·없는 의도 생성과 복합 의도 분모를 손계산으로 검증한다.

    Args:
        없음.

    Returns:
        None: null 예측만 정답인 경우와 실패가 서로 다른 행렬 칸에 남는다.
    """
    gold = dict(
        zip("abcdefghi", [None, None, None, "MANUAL", "MANUAL", "QUOTE", "QUOTE", None, "MANUAL"])
    )
    attempts = {
        "a": synthetic_attempt(),
        "b": synthetic_attempt(secondary="MANUAL"),
        "c": synthetic_attempt(error=TimeoutError()),
        "d": synthetic_attempt(secondary="MANUAL"),
        "e": synthetic_attempt(),
        "f": synthetic_attempt(secondary="MANUAL"),
        "g": synthetic_attempt(secondary="QUOTE"),
        "i": synthetic_attempt(held=True),
    }
    report = summarize_secondary_first_attempts(gold, attempts)
    assert report["total"] == 9 and report["correct"] == 3
    assert report["accuracy"] == 1 / 3
    assert "macro_f1" not in report
    assert report["composite"]["support"] == 5
    assert report["composite"]["macro_f1"] == pytest.approx((0.4 + 2 / 3) / 10)
    assert len(report["composite"]["labels"]) == 10
    assert report["composite"]["per_class"]["MANUAL"]["fp"] == 1
    assert report["per_class"]["MANUAL"]["fp"] == 2
    assert report["absent"] == {"support": 4, "invented": 1}
    assert report["confusion_matrix"][NO_SECONDARY][NO_SECONDARY] == 1
    assert report["confusion_matrix"][NO_SECONDARY][NO_PREDICTION] == 2
    cases = {r["case_id"]: r for r in report["cases"]}
    assert cases["a"]["gold"] is None and cases["a"]["prediction"] is None
    assert cases["a"]["correct"] is True and cases["a"]["outcome"] == "VALID"
    assert cases["c"]["prediction"] is None and cases["c"]["correct"] is False
    assert cases["h"]["outcome"] == "NOT_STARTED" and not cases["h"]["correct"]
    assert [r["case_id"] for r in report["cases"] if r["invented_secondary"]] == ["b"]
    assert not report["quality_gate_evaluated"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"raw": "BAD"},
        {"error": RuntimeError()},
        {"held": True},
    ],
)
def test_secondary_failed_response_is_not_null_match(kwargs):
    """실행 실패를 보조 의도 없음 정답으로 인정하지 않는다.

    Args:
        kwargs: 합성 실패 설정.

    Returns:
        None: 정답 null 사례에서도 실패는 오답이다.
    """
    report = summarize_secondary_first_attempts({"a": None}, {"a": synthetic_attempt(**kwargs)})
    assert report["accuracy"] == 0
    assert report["absent"]["invented"] == 0
    assert report["confusion_matrix"][NO_SECONDARY][NO_PREDICTION] == 1
    assert report["composite"]["macro_f1"] is None


def test_secondary_empty_or_absent_only_has_no_composite_score():
    """복합 의도 대상이 없으면 전체 정확도로 복합 성능을 주장하지 않는다.

    Args:
        없음.

    Returns:
        None: null만 맞힌 결과도 복합 macro-F1은 null이다.
    """
    empty = summarize_secondary_first_attempts({}, {})
    assert empty["accuracy"] is None and empty["composite"]["macro_f1"] is None
    report = summarize_secondary_first_attempts({"a": None}, {"a": synthetic_attempt()})
    assert report["accuracy"] == 1
    assert report["composite"]["support"] == 0
    assert report["composite"]["macro_f1"] is None


@pytest.mark.parametrize(
    "gold,attempts",
    [
        ({"a": ""}, {}),
        ({"a": "NO_SECONDARY"}, {}),
        ({"a": "UNKNOWN"}, {}),
        ({"a": None}, {"a": synthetic_attempt(number=2)}),
        ({"a": None}, {"extra": synthetic_attempt()}),
    ],
)
def test_secondary_invalid_gold_and_retry_are_rejected(gold, attempts):
    """미검수 빈 문자열이나 재시도를 확정 null 정답으로 바꾸지 않는다.

    Args:
        gold: 잘못된 합성 정답 또는 정상 null 정답.
        attempts: 최초 시도 경계를 위반한 입력.

    Returns:
        None: 안전한 오류로 전체 집계를 거부한다.
    """
    with pytest.raises(ValueError, match="^CS_METRICS_INVALID_INPUT$"):
        summarize_secondary_first_attempts(gold, attempts)
