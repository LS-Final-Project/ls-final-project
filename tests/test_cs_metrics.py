"""고정 합성 정답과 기존 호출 경계로 최초 주분류 집계를 검증한다."""

import json
from dataclasses import replace

import psycopg
import pytest

from backend.engine.cs.classification_call import run_classification_attempt
from backend.shared.ai import Generation, Usage
from eval.cs_metrics import (
    NO_PREDICTION,
    NO_SECONDARY,
    NO_SUBTYPE,
    summarize_category_first_attempts,
    summarize_return_subtype_first_attempts,
    summarize_secondary_first_attempts,
    summarize_urgency_first_attempts,
)
from eval.cs_strata import summarize_first_attempts_by_group


def synthetic_attempt(
    category="RETURN",
    *,
    error=None,
    raw=None,
    number=1,
    held=False,
    urgency="NONE",
    secondary=None,
    return_subtype=None,
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
        return_subtype: 합성 반품 하위유형 또는 null.

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
                    "return_subtype": return_subtype,
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


@pytest.mark.parametrize(
    "metric,gold",
    [
        ("category", {"a": "RETURN", "b": "RETURN", "c": "MANUAL", "d": "MANUAL"}),
        ("urgency", {"a": "SAFETY", "b": "SITE_DOWN", "c": "SAFETY", "d": "NONE"}),
        ("secondary", {"a": None, "b": "MANUAL", "c": None, "d": "MANUAL"}),
    ],
)
def test_strata_preserve_all_targets_and_existing_metric_contracts(metric, gold):
    """세 지표의 그룹별 분모·실패가 전체 집계와 일치함을 확인한다.

    Args:
        metric: 기존 지표 이름.
        gold: 지표별 합성 고정 정답.

    Returns:
        None: 메타데이터 누락·미실행도 각 축의 한 그룹에 정확히 한 번 포함된다.
    """
    attempts = {
        "a": synthetic_attempt(urgency="SAFETY"),
        "b": synthetic_attempt(category="MANUAL", secondary="QUOTE"),
        "c": synthetic_attempt(error=TimeoutError()),
    }
    metadata = {
        "a": {"channel": "PHONE", "body_language": "ko"},
        "b": {"channel": "EMAIL", "body_language": "en"},
        "d": {"channel": "PHONE", "body_language": None},
    }
    report = summarize_first_attempts_by_group(gold, attempts, metadata, metric=metric)
    direct = {
        "category": summarize_category_first_attempts,
        "urgency": summarize_urgency_first_attempts,
        "secondary": summarize_secondary_first_attempts,
    }[metric](gold, attempts)
    assert report["overall"] == direct
    assert report["overall"]["accuracy"] == 0.25
    assert report["missing_metadata"] == {"channel": 1, "body_language": 2}
    for values in report["groups"].values():
        assert sum(r["total"] for r in values.values()) == 4
        assert sum(r["correct"] for r in values.values()) == 1
        ids = [c["case_id"] for r in values.values() for c in r["cases"]]
        assert sorted(ids) == sorted(gold)
        for outcome, count in direct["outcomes"].items():
            assert sum(r["outcomes"][outcome] for r in values.values()) == count
    phone = report["groups"]["phone"]
    assert phone["PHONE"]["total"] == 2
    assert phone["NON_PHONE"]["total"] == 1
    assert phone["UNKNOWN"]["outcomes"]["TIMEOUT"] == 1
    assert phone["PHONE"]["outcomes"]["NOT_STARTED"] == 1
    assert phone["PHONE"]["accuracy"] == 0.5
    assert not report["quality_gate_evaluated"]


@pytest.mark.parametrize(
    "metadata",
    [
        {"extra": {}},
        {"a": None},
        {"a": {"body": "PRIVATE"}},
        {"a": {"channel": "UNKNOWN"}},
        {"a": {"channel": ""}},
        {"a": {"body_language": "private@example.test"}},
        {"a": {"body_language": "en\n"}},
        {"a": {"body_language": 1}},
    ],
)
def test_strata_reject_invalid_metadata_without_echoing_values(metadata):
    """형식 오류를 누락으로 숨기거나 개인정보 메타데이터를 출력하지 않는다.

    Args:
        metadata: 잘못된 합성 메타데이터.

    Returns:
        None: 안전한 오류로 전체 집계를 거부한다.
    """
    with pytest.raises(ValueError, match="^CS_STRATA_INVALID_INPUT$"):
        summarize_first_attempts_by_group({"a": "RETURN"}, {}, metadata, metric="category")


def test_strata_validate_before_filtering_and_handle_empty_targets():
    """그룹 필터가 대상 밖 시도나 재시도를 숨기지 않는다.

    Args:
        없음.

    Returns:
        None: 전체 입력 검사와 비어 있는 대상의 null 점수를 유지한다.
    """
    for attempts in ({"extra": synthetic_attempt()}, {"a": synthetic_attempt(number=2)}):
        with pytest.raises(ValueError, match="^CS_METRICS_INVALID_INPUT$"):
            summarize_first_attempts_by_group({"a": "RETURN"}, attempts, {}, metric="category")
    with pytest.raises(ValueError, match="^CS_METRICS_INVALID_INPUT$"):
        summarize_first_attempts_by_group({"a": None}, {}, {}, metric="category")
    with pytest.raises(ValueError, match="^CS_STRATA_INVALID_INPUT$"):
        summarize_first_attempts_by_group({}, {}, {}, metric="other")
    report = summarize_first_attempts_by_group({}, {}, {}, metric="urgency")
    assert report["overall"]["accuracy"] is None
    assert report["groups"] == {"channel": {}, "body_language": {}, "phone": {}}
    assert report["missing_metadata"] == {"channel": 0, "body_language": 0}


@pytest.mark.parametrize("metric", ["category", "urgency", "secondary"])
def test_final_quality_keeps_attempt_numbers_and_missing_denominator(metric):
    """최종 시도의 고정 분모·위험 누락·null 실패를 손계산과 대조한다.

    Args:
        metric: 검증할 세 분류 지표.

    Returns:
        None: 최초 함수를 우회하지 않고 최종 번호와 미확정을 보존함을 확인한다.
    """
    from eval.cs_metrics import summarize_final_attempts

    gold = dict.fromkeys(("a", "b", "c", "d"), "RETURN" if metric == "category" else "SAFETY")
    if metric == "secondary":
        gold = {"a": None, "b": "DEFECT", "c": None, "d": "DEFECT"}
    attempts = {
        "a": synthetic_attempt(number=3, urgency="SAFETY"),
        "b": synthetic_attempt("INSTALL", number=2, urgency="SITE_DOWN", secondary="WARRANTY"),
        "c": synthetic_attempt(number=4, error=TimeoutError()),
    }
    result = summarize_final_attempts(gold, attempts, metric=metric)
    assert result["selection"] == "is_final" and "attempt_number" not in result
    assert result["version"] == f"cs-{metric}-final-attempt-v1"
    assert result["total"] == 4 and result["correct"] == 1 and result["accuracy"] == 0.25
    assert result["outcomes"]["NOT_FINAL"] == result["outcomes"]["TIMEOUT"] == 1
    assert "NOT_STARTED" not in result["outcomes"]
    assert [case["attempt_number"] for case in result["cases"]] == [3, 2, 4, None]
    if metric == "category":
        assert result["macro_f1"] == pytest.approx(0.4 / 10)
        assert result["per_class"]["RETURN"]["fn"] == 3
    elif metric == "urgency":
        assert result["macro_f1"] == pytest.approx(0.4 / 6)
        assert result["critical"]["missed"] == 2
        assert result["critical"]["safety_as_site_down"] == 1
        assert result["non_none"]["recall"] == 0.5
    else:
        assert "macro_f1" not in result
        assert result["composite"]["support"] == 2 and result["composite"]["macro_f1"] == 0
        assert result["absent"] == {"support": 2, "invented": 0}
        assert result["cases"][2]["correct"] is False
    first_function = {
        "category": summarize_category_first_attempts,
        "urgency": summarize_urgency_first_attempts,
        "secondary": summarize_secondary_first_attempts,
    }[metric]
    with pytest.raises(ValueError, match="CS_METRICS_INVALID_INPUT"):
        first_function(gold, attempts)


@pytest.mark.parametrize("number", [0, -1, True, 2.5])
def test_final_quality_rejects_invalid_attempt_number(number):
    """최종 입력에서도 양의 정수 시도 번호만 허용한다.

    Args:
        number: 부정확한 시도 번호.

    Returns:
        None: 최초/최종 경계의 입력 검증을 확인한다.
    """
    from eval.cs_metrics import summarize_final_attempts

    attempt = replace(synthetic_attempt(), attempt_number=number)
    with pytest.raises(ValueError, match="CS_METRICS_INVALID_INPUT"):
        summarize_final_attempts({"a": "RETURN"}, {"a": attempt}, metric="category")


@pytest.mark.parametrize("metric", ["category", "urgency", "secondary"])
def test_final_quality_empty_has_no_score(metric):
    """최종 평가 대상0을 합격 점수로 만들지 않는다.

    Args:
        metric: 비교 지표.

    Returns:
        None: 빈 대상의 null 점수와 빈 사례 목록을 확인한다.
    """
    from eval.cs_metrics import summarize_final_attempts

    result = summarize_final_attempts({}, {}, metric=metric)
    assert result["accuracy"] is None and result["cases"] == []
    assert (
        result["composite"]["macro_f1"] if metric == "secondary" else result["macro_f1"]
    ) is None


def test_return_subtype_null_and_failures_are_separate_false_negatives():
    """유효 null·실패·미실행을 서로 다른 열의 FN으로 손계산과 비교한다.

    Args:
        없음.

    Returns:
        None: 2클래스 macro-F1과 null 예측 건수가 손계산과 같다.
    """
    gold = dict(zip("abcdef", ["DEFECT", "DEFECT", "DEFECT", "NON_DEFECT", "NON_DEFECT", "DEFECT"]))
    attempts = {
        "a": synthetic_attempt(return_subtype="DEFECT"),
        "b": synthetic_attempt(return_subtype="NON_DEFECT"),
        "c": synthetic_attempt(),
        "d": synthetic_attempt(return_subtype="NON_DEFECT"),
        "e": synthetic_attempt(error=TimeoutError()),
    }
    report = summarize_return_subtype_first_attempts(gold, attempts)
    assert report["version"] == "cs-return-subtype-first-attempt-v1"
    assert report["labels"] == ["DEFECT", "NON_DEFECT"]
    assert report["total"] == 6 and report["correct"] == 2
    # DEFECT: tp1 fp0 fn3 → 2/5, NON_DEFECT: tp1 fp1 fn1 → 1/2
    assert report["macro_f1"] == pytest.approx((2 / 5 + 1 / 2) / 2)
    assert report["null_predictions"] == 1
    assert report["confusion_matrix"]["DEFECT"][NO_SUBTYPE] == 1
    assert report["confusion_matrix"]["DEFECT"][NO_PREDICTION] == 1
    assert report["confusion_matrix"]["NON_DEFECT"][NO_PREDICTION] == 1
    assert report["outcomes"]["NOT_STARTED"] == 1 and report["outcomes"]["TIMEOUT"] == 1
    cases = {r["case_id"]: r for r in report["cases"]}
    assert cases["c"]["prediction"] is None and cases["c"]["outcome"] == "VALID"
    assert not cases["c"]["correct"]
    assert not report["quality_gate_evaluated"]


@pytest.mark.parametrize(
    "gold,attempts",
    [
        ({"a": None}, {}),
        ({"a": NO_SUBTYPE}, {}),
        ({"a": "RETURN"}, {}),
        ({"a": "DEFECT"}, {"a": synthetic_attempt(number=2)}),
        ({"a": "DEFECT"}, {"extra": synthetic_attempt()}),
    ],
)
def test_return_subtype_rejects_null_gold_retry_or_extra_target(gold, attempts):
    """미확인 null·내부 표식 정답과 최초 시도 경계 위반을 거부한다.

    Args:
        gold: 잘못된 합성 정답 또는 정상 정답.
        attempts: 최초 시도 경계를 위반한 입력.

    Returns:
        None: 부분 집계 없이 안전한 오류로 거부한다.
    """
    with pytest.raises(ValueError, match="^CS_METRICS_INVALID_INPUT$"):
        summarize_return_subtype_first_attempts(gold, attempts)


def test_return_subtype_empty_target_has_no_score():
    """대상이 없으면 점수와 합격 판정을 만들지 않는다.

    Args:
        없음.

    Returns:
        None: accuracy·macro-F1이 null이다.
    """
    report = summarize_return_subtype_first_attempts({}, {})
    assert report["accuracy"] is None and report["macro_f1"] is None


def test_return_subtype_strata_and_final_reuse_first_attempt_contract():
    """반품 하위유형도 언어·채널 집계와 최종 시도 집계가 같은 계산식을 쓴다.

    Args:
        없음.

    Returns:
        None: 그룹 분모·최종 판본·시도 번호·null 예측 건수를 확인한다.
    """
    from eval.cs_metrics import summarize_final_attempts

    gold = {"a": "DEFECT", "b": "NON_DEFECT", "c": "DEFECT"}
    first = {
        "a": synthetic_attempt(return_subtype="DEFECT"),
        "b": synthetic_attempt(),
        "c": synthetic_attempt(error=TimeoutError()),
    }
    metadata = {"a": {"channel": "PHONE", "body_language": "ko"}}
    report = summarize_first_attempts_by_group(gold, first, metadata, metric="return_subtype")
    assert report["overall"] == summarize_return_subtype_first_attempts(gold, first)
    assert report["groups"]["phone"]["PHONE"]["correct"] == 1
    assert report["groups"]["phone"]["UNKNOWN"]["total"] == 2
    final = summarize_final_attempts(
        gold,
        {
            "a": synthetic_attempt(return_subtype="DEFECT", number=2),
            "b": synthetic_attempt(number=3),
        },
        metric="return_subtype",
    )
    assert final["version"] == "cs-return-subtype-final-attempt-v1"
    assert final["selection"] == "is_final" and final["correct"] == 1
    assert final["null_predictions"] == 1 and final["outcomes"]["NOT_FINAL"] == 1
    assert [case["attempt_number"] for case in final["cases"]] == [2, 3, None]
    assert final["cases"][1]["prediction"] is None


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"id,return_subtype\na,DEFECT\n",
        b"ticket_id,return_subtype\na,DEFECT\na,NON_DEFECT\n",
        b"ticket_id,return_subtype\na,\n",
        b"ticket_id,return_subtype\na,UNKNOWN\n",
        b"ticket_id,return_subtype\n,DEFECT\n",
        b"ticket_id,return_subtype\na,DEFECT,extra\n",
        b"\xff\xfe",
    ],
)
def test_return_subtype_gold_file_rejects_malformed_rows(data):
    """저장 전 정답 파일의 헤더·중복·빈 값·허용값 밖·인코딩 오류를 거부한다.

    Args:
        data: 잘못된 합성 정답 파일 바이트.

    Returns:
        None: 원문 없는 오류 코드로 거부한다.
    """
    from eval.cs_report import _parse_return_subtype_gold

    with pytest.raises(ValueError, match="^CS_QUALITY_GOLD_INVALID$"):
        _parse_return_subtype_gold(data)
    assert _parse_return_subtype_gold(
        "﻿ticket_id,return_subtype\r\na,DEFECT\r\nb,NON_DEFECT\r\n".encode()
    ) == {"a": "DEFECT", "b": "NON_DEFECT"}


def store_cli_args(gold_file, *extra):
    """저장 명령의 필수 인자를 만든다.

    Args:
        gold_file: 합성 정답 파일 경로.
        extra: 추가 인자.

    Returns:
        list[str]: main에 넘길 인자.
    """
    from uuid import UUID

    from eval.cs_report import STORE_COMMAND

    return [
        STORE_COMMAND,
        "--run-id",
        str(UUID(int=1)),
        "--gold-file",
        str(gold_file),
        "--gold-sha256",
        "0" * 64,
        "--scoring-version",
        "syn-v1",
        *extra,
    ]


@pytest.fixture
def store_cli(monkeypatch, tmp_path):
    """DB 없이 저장 명령을 실행할 합성 환경과 정답 파일을 준비한다.

    Args:
        monkeypatch: 환경 변수·연결 대체.
        tmp_path: 비밀로 취급할 정답 파일 위치.

    Returns:
        tuple: cs_report 모듈과 정답 파일 경로.
    """
    from contextlib import nullcontext

    from eval import cs_report

    gold = tmp_path / "secret-gold.csv"
    gold.write_bytes(b"ticket_id,return_subtype\n")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("EVALUATION_REPORT_DATABASE_URL", "postgresql://u:secret-pw@h/db")
    monkeypatch.setattr(cs_report.psycopg, "connect", lambda dsn: nullcontext(object()))
    return cs_report, gold


def test_store_cli_passes_bytes_and_prints_one_json_line(store_cli, monkeypatch, capsys):
    """정답 파일을 바이트로 넘기고 성공 결과만 JSON 한 줄로 출력한다.

    Args:
        store_cli: 합성 실행 환경.
        monkeypatch: 저장 함수 대체.
        capsys: 출력 확인.

    Returns:
        None: 인자 전달·기본 gold_version·경로 비출력을 확인한다.
    """
    cs_report, gold = store_cli
    calls = []

    def fake_store(conn, run_id, gold_csv, **kwargs):
        """전달된 인자를 기록한다.

        Args:
            conn: 대체 연결.
            run_id: 실행 ID.
            gold_csv: 정답 바이트.
            kwargs: 판본 인자.

        Returns:
            dict: 합성 저장 결과.
        """
        calls.append((run_id, gold_csv, kwargs))
        return {"run_id": str(run_id), "scoring_version": "syn-v1", "stored_rows": 5}

    monkeypatch.setattr(cs_report, "store_synthetic_return_subtype_metrics", fake_store)
    assert cs_report.main(store_cli_args(gold)) == 0
    ((run_id, gold_csv, kwargs),) = calls
    assert gold_csv == gold.read_bytes()
    assert kwargs == {"gold_sha256": "0" * 64, "gold_version": None, "scoring_version": "syn-v1"}
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "run_id": str(run_id),
        "scoring_version": "syn-v1",
        "stored_rows": 5,
    }
    assert captured.out.count("\n") == 1 and captured.err == ""
    assert str(gold) not in captured.out
    cs_report.main(store_cli_args(gold, "--gold-version", "v2"))
    assert calls[-1][2]["gold_version"] == "v2"


@pytest.mark.parametrize(
    "raised,expected",
    [
        (ValueError("CS_QUALITY_RUN_NOT_TERMINAL"), "CS_QUALITY_RUN_NOT_TERMINAL"),
        (ValueError("CS_STORED_METRICS_INVALID_INPUT"), "CS_STORED_METRICS_INVALID_INPUT"),
        (ValueError("CS_METRICS_INVALID_INPUT"), "CS_METRICS_INVALID_INPUT"),
        (ValueError("bad row in secret-gold.csv"), "CS_QUALITY_STORE_FAILED"),
        (ValueError("CS_QUALITY_X secret"), "CS_QUALITY_STORE_FAILED"),
        (psycopg.OperationalError("password=secret-pw"), "CS_QUALITY_STORE_FAILED"),
        (RuntimeError("secret"), "CS_QUALITY_STORE_FAILED"),
    ],
)
def test_store_cli_maps_errors_to_codes_only(store_cli, monkeypatch, capsys, raised, expected):
    """알려진 코드만 그대로 전달하고 나머지 오류는 원문 없이 일반 코드로 바꾼다.

    Args:
        store_cli: 합성 실행 환경.
        monkeypatch: 저장 함수 대체.
        capsys: 출력 확인.
        raised: 저장 함수가 낼 오류.
        expected: stderr에 기대하는 코드.

    Returns:
        None: exit1·코드 한 줄·경로/비밀/스택 비출력을 확인한다.
    """
    cs_report, gold = store_cli

    def fail(*args, **kwargs):
        """지정 오류를 낸다.

        Args:
            args: 무시.
            kwargs: 무시.

        Returns:
            None: 항상 예외.
        """
        raise raised

    monkeypatch.setattr(cs_report, "store_synthetic_return_subtype_metrics", fail)
    assert cs_report.main(store_cli_args(gold)) == 1
    captured = capsys.readouterr()
    assert captured.err == expected + "\n" and captured.out == ""
    assert "secret" not in captured.err and "Traceback" not in captured.err


def test_store_cli_rejects_configuration_unreadable_file_and_missing_args(
    store_cli, monkeypatch, capsys
):
    """설정·파일 읽기 실패는 코드만, 인자 누락은 argparse 오류로 끝난다.

    Args:
        store_cli: 합성 실행 환경.
        monkeypatch: 환경 변수 변경.
        capsys: 출력 확인.

    Returns:
        None: 각 실패의 exit 코드와 경로 비출력을 확인한다.
    """
    cs_report, gold = store_cli
    missing = gold.with_name("missing-secret.csv")
    assert cs_report.main(store_cli_args(missing)) == 1
    assert capsys.readouterr().err == "CS_QUALITY_GOLD_UNREADABLE\n"
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert cs_report.main(store_cli_args(gold)) == 1
    assert capsys.readouterr().err == "CS_REPORT_LOCAL_CONFIGURATION_REQUIRED\n"
    with pytest.raises(SystemExit) as exit_info:
        cs_report.main(store_cli_args(gold)[:-2])
    assert exit_info.value.code == 2


def info_attempt(items=None, *, outcome="VALID", number=1):
    """provided_info 예측만 다른 합성 최초 시도를 만든다.

    Args:
        items: 예측 항목 코드 목록. 유효 출력이 아니면 무시한다.
        outcome: 시도 결과.
        number: 시도 번호.

    Returns:
        ClassificationAttempt: 기존 분류 출력 형식의 시도.
    """
    from backend.engine.cs.classification import ClassificationOutput
    from backend.engine.cs.classification_call import ClassificationAttempt

    prediction = (
        ClassificationOutput(
            category_code="TECH_SPEC",
            secondary_category=None,
            urgency_flag="NONE",
            provided_info=list(items or []),
            return_subtype=None,
        )
        if outcome == "VALID"
        else None
    )
    return ClassificationAttempt(attempt_number=number, outcome=outcome, prediction=prediction)


def test_provided_info_hand_calculated_scopes_are_separate():
    """FULL과 PRECISION_ONLY를 따로 손계산하고 실패를 빈 예측으로 센다.

    Args:
        없음.

    Returns:
        None: FULL P/R/F1·완전 일치, PRECISION_ONLY precision만, 합산 값 없음을 확인한다.
    """
    from eval.cs_metrics import summarize_provided_info_first_attempts

    gold = {
        "a": (frozenset({"A", "B"}), "FULL"),
        "b": (frozenset({"A"}), "FULL"),
        "c": (frozenset(), "FULL"),
        "d": (frozenset({"B"}), "FULL"),
        "e": (frozenset(), "FULL"),
        "f": (frozenset({"A", "D"}), "PRECISION_ONLY"),
        "g": (frozenset({"A"}), "PRECISION_ONLY"),
    }
    report = summarize_provided_info_first_attempts(
        gold,
        {
            "a": info_attempt(["A", "C"]),
            "b": info_attempt(["A"]),
            "c": info_attempt([]),
            "d": info_attempt(outcome="TIMEOUT"),
            "f": info_attempt(["A", "E"]),
            "g": info_attempt(outcome="SCHEMA_VIOLATION"),
        },
    )
    full, partial = report["scopes"]["FULL"], report["scopes"]["PRECISION_ONLY"]
    assert report["total"] == 7 and report["quality_gate_evaluated"] is False
    assert (full["tp"], full["fp"], full["fn"]) == (2, 1, 2)
    assert full["precision"] == pytest.approx(2 / 3)
    assert full["recall"] == pytest.approx(0.5)
    assert full["f1"] == pytest.approx(4 / 7)
    # 실패한 e는 정답이 []여도 완전 일치가 아니다.
    assert (full["exact_match"], full["total"], full["valid_count"]) == (2, 5, 3)
    assert full["exact_match_rate"] == pytest.approx(0.4)
    assert full["outcomes"]["VALID"] == 3
    assert full["outcomes"]["TIMEOUT"] == full["outcomes"]["NOT_STARTED"] == 1
    assert (partial["tp"], partial["fp"], partial["total"], partial["valid_count"]) == (1, 1, 2, 1)
    assert partial["precision"] == pytest.approx(0.5)
    assert not {"fn", "recall", "f1", "exact_match", "exact_match_rate"} & set(partial)
    assert set(report["scopes"]) == {"FULL", "PRECISION_ONLY"}
    cases = {case["case_id"]: case for case in report["cases"]}
    assert cases["d"]["prediction"] is None and cases["d"]["fn"] == 1
    assert cases["e"]["exact"] is False and cases["c"]["exact"] is True


@pytest.mark.parametrize(
    "gold,attempts,expected",
    [
        # 유효 예측 항목이 없으면 precision 분모 0
        ({"a": (frozenset({"A"}), "FULL")}, {"a": info_attempt(outcome="HELD")}, (None, 0.0, 0.0)),
        # 정답 항목이 없으면 recall 분모 0
        ({"a": (frozenset(), "FULL")}, {"a": info_attempt(["X"])}, (0.0, None, 0.0)),
        # 둘 다 0이면 F1도 null, 완전 일치는 1건
        ({"a": (frozenset(), "FULL")}, {"a": info_attempt([])}, (None, None, None)),
    ],
)
def test_provided_info_zero_denominators_are_null(gold, attempts, expected):
    """0 분모 precision·recall과 둘 다 null인 F1을 0으로 바꾸지 않는다.

    Args:
        gold: 합성 FULL 정답.
        attempts: 합성 최초 시도.
        expected: 기대 (precision, recall, f1).

    Returns:
        None: null 규칙과 비어 있는 PRECISION_ONLY 범위의 null precision을 확인한다.
    """
    from eval.cs_metrics import summarize_provided_info_first_attempts

    report = summarize_provided_info_first_attempts(gold, attempts)
    full = report["scopes"]["FULL"]
    assert (full["precision"], full["recall"], full["f1"]) == expected
    assert report["scopes"]["PRECISION_ONLY"] == {
        "total": 0,
        "valid_count": 0,
        "tp": 0,
        "fp": 0,
        "precision": None,
        "outcomes": dict.fromkeys(sorted(full["outcomes"]), 0),
    }
    empty = summarize_provided_info_first_attempts({}, {})["scopes"]["FULL"]
    assert empty["exact_match_rate"] is None and empty["f1"] is None


@pytest.mark.parametrize(
    "gold,attempts",
    [
        ({"a": (frozenset({"A"}), "FULL")}, {"a": info_attempt(["A"], number=2)}),
        ({"a": (frozenset({"A"}), "FULL")}, {"b": info_attempt(["A"])}),
        ({"a": (frozenset({"A"}), "PARTIAL")}, {}),
        ({"a": ({"A"}, "FULL")}, {}),
        ({"a": (frozenset({""}), "FULL")}, {}),
        ({"": (frozenset({"A"}), "FULL")}, {}),
    ],
)
def test_provided_info_rejects_retry_extra_target_or_malformed_gold(gold, attempts):
    """재시도·대상 밖 시도·잘못된 범위나 항목 형식의 정답을 거부한다.

    Args:
        gold: 잘못된 합성 정답 또는 정상 정답.
        attempts: 잘못된 시도 또는 빈 시도.

    Returns:
        None: 원문 없는 오류 코드로 거부한다.
    """
    from eval.cs_metrics import summarize_provided_info_first_attempts

    with pytest.raises(ValueError, match="^CS_METRICS_INVALID_INPUT$"):
        summarize_provided_info_first_attempts(gold, attempts)


def test_provided_info_strata_reuse_overall_contract():
    """층화 집계가 provided_info 전체 결과를 직접 집계와 같게 낸다.

    Args:
        없음.

    Returns:
        None: 전체 결과 일치와 채널별 범위 분리를 확인한다.
    """
    from eval.cs_metrics import summarize_provided_info_first_attempts

    gold = {"a": (frozenset({"A"}), "FULL"), "b": (frozenset({"B"}), "PRECISION_ONLY")}
    attempts = {"a": info_attempt(["A"]), "b": info_attempt(["C"])}
    metadata = {"a": {"channel": "EMAIL"}, "b": {"channel": "CHAT"}}
    report = summarize_first_attempts_by_group(gold, attempts, metadata, metric="provided_info")
    assert report["overall"] == summarize_provided_info_first_attempts(gold, attempts)
    chat = report["groups"]["channel"]["CHAT"]["scopes"]
    assert chat["FULL"]["total"] == 0 and chat["PRECISION_ONLY"]["fp"] == 1


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"ticket_id,provided_info\na,[]\n",
        b"ticket_id,provided_info,scoring_scope\na,,FULL\n",
        b"ticket_id,provided_info,scoring_scope\na,A;;B,FULL\n",
        b"ticket_id,provided_info,scoring_scope\na, A,FULL\n",
        b"ticket_id,provided_info,scoring_scope\na,A;A,FULL\n",
        b"ticket_id,provided_info,scoring_scope\na,[];A,FULL\n",
        b"ticket_id,provided_info,scoring_scope\na,A,PARTIAL\n",
        b"ticket_id,provided_info,scoring_scope\na,A,FULL\na,B,FULL\n",
        b"ticket_id,provided_info,scoring_scope\n,A,FULL\n",
        b"ticket_id,provided_info,scoring_scope\na,A\n",
        b"\xff\xfe",
    ],
)
def test_provided_info_gold_file_rejects_malformed_rows(data):
    """문서에 기록된 '[]'·';' 표기 밖의 provided_info 정답 파일을 거부한다.

    Args:
        data: 잘못된 합성 정답 파일 바이트.

    Returns:
        None: 원문 없는 오류 코드로 거부하고 정상 표기는 집합으로 읽는다.
    """
    from eval.cs_report import _parse_provided_info_gold

    columns = ("ticket_id", "provided_info", "scoring_scope")
    with pytest.raises(ValueError, match="^CS_QUALITY_GOLD_INVALID$"):
        _parse_provided_info_gold(data, columns)
    assert _parse_provided_info_gold(
        "﻿ticket_id,provided_info,scoring_scope\r\na,[],FULL\r\nb,B;A,PRECISION_ONLY\r\n".encode(),
        columns,
    ) == {"a": (frozenset(), "FULL"), "b": (frozenset({"A", "B"}), "PRECISION_ONLY")}
