"""C/S 최초 분류 시도의 분류 지표 집계. 파일·DB·모델에 접근하지 않는다."""

from collections import Counter
from collections.abc import Mapping
from typing import Literal, get_args

from backend.engine.cs.classification import Category, Urgency
from backend.engine.cs.classification_call import ClassificationAttempt

CATEGORY_LABELS: tuple[str, ...] = get_args(Category)
URGENCY_LABELS: tuple[str, ...] = get_args(Urgency)
CRITICAL_LABELS = ("SAFETY", "SITE_DOWN")
NO_SECONDARY = "NO_SECONDARY"
NO_PREDICTION = "NO_PREDICTION"
OUTCOMES = {"VALID", "SCHEMA_VIOLATION", "TRANSPORT_FAILURE", "TIMEOUT", "HELD"}


def summarize_category_first_attempts(
    gold: Mapping[str, str], attempts: Mapping[str, ClassificationAttempt]
) -> dict:
    """고정 대상 전체를 분모로 최초 시도의 주분류 결과를 집계한다.

    Args:
        gold: 사전 잠금 대상 ID와 주분류 정답. 실제 검수/사용 승인은 호출자 책임이다.
        attempts: 대상 ID별 최초 시도. 없는 ID는 미실행 실패로 분모에 남긴다.

    Returns:
        dict: 정오·혼동행렬·클래스별 P/R/F1과 고정 10클래스 macro-F1.
            불일치·재시도 입력은 원문 없는 ValueError로 거부한다. 합격 판정은 하지 않는다.
    """
    return _summarize_first_attempts(
        gold, attempts, CATEGORY_LABELS, "category_code", "cs-category-first-attempt-v1"
    )


def summarize_urgency_first_attempts(
    gold: Mapping[str, str], attempts: Mapping[str, ClassificationAttempt]
) -> dict:
    """최초 긴급도 예측의 위험 누락과 유형 혼동을 구분한다.

    Args:
        gold: 사전 잠금 대상 ID와 긴급도 정답. 검수/사용 승인은 호출자 책임이다.
        attempts: 대상 ID별 최초 시도. 미실행·실패도 분모에 남긴다.

    Returns:
        dict: 6클래스 집계와 비NONE 재현율, 유형별 CRITICAL 누락·상호 혼동.
    """
    report = _summarize_first_attempts(
        gold, attempts, URGENCY_LABELS, "urgency_flag", "cs-urgency-first-attempt-v1"
    )
    matrix = report["confusion_matrix"]
    non_none = tuple(label for label in URGENCY_LABELS if label != "NONE")
    support = sum(sum(matrix[label].values()) for label in non_none)
    detected = sum(matrix[label][predicted] for label in non_none for predicted in non_none)
    report["non_none"] = {
        "support": support,
        "detected": detected,
        "recall": detected / support if support else None,
    }
    by_gold = {
        label: {
            "support": sum(matrix[label].values()),
            "missed": sum(
                count
                for predicted, count in matrix[label].items()
                if predicted not in CRITICAL_LABELS
            ),
        }
        for label in CRITICAL_LABELS
    }
    report["critical"] = {
        "support": sum(item["support"] for item in by_gold.values()),
        "missed": sum(item["missed"] for item in by_gold.values()),
        "by_gold": by_gold,
        "safety_as_site_down": matrix["SAFETY"]["SITE_DOWN"],
        "site_down_as_safety": matrix["SITE_DOWN"]["SAFETY"],
    }
    for case in report["cases"]:
        case["critical_missed"] = (
            case["gold"] in CRITICAL_LABELS and case["prediction"] not in CRITICAL_LABELS
        )
    return report


def summarize_secondary_first_attempts(
    gold: Mapping[str, str | None], attempts: Mapping[str, ClassificationAttempt]
) -> dict:
    """확정된 보조 의도 없음과 실행 실패를 분리해 최초 시도를 집계한다.

    Args:
        gold: 잠금 대상의 보조 분류 정답. None은 검수된 의도 없음이며 미검수 표시가 아니다.
        attempts: 대상별 최초 시도. 없는 시도도 전체 분모에 남긴다.

    Returns:
        dict: null 포함 정확 일치율, 복합 의도 대상의 macro-F1, 없는 의도 생성 건수.
    """
    if any(value is not None and value not in CATEGORY_LABELS for value in gold.values()):
        raise ValueError("CS_METRICS_INVALID_INPUT")
    report = _summarize_first_attempts(
        {key: NO_SECONDARY if value is None else value for key, value in gold.items()},
        attempts,
        (*CATEGORY_LABELS, NO_SECONDARY),
        "secondary_category",
        "cs-secondary-first-attempt-v1",
    )
    # null이 많은 전체 집합의 macro-F1을 복합 의도 성능으로 보고하지 않는다.
    report.pop("macro_f1")
    matrix = report["confusion_matrix"]
    composite = _per_class_metrics(matrix, CATEGORY_LABELS)
    support = sum(row["support"] for row in composite.values())
    report["composite"] = {
        "support": support,
        "labels": list(CATEGORY_LABELS),
        "per_class": composite,
        "macro_f1": sum(row["f1"] for row in composite.values()) / len(CATEGORY_LABELS)
        if support
        else None,
    }
    report["absent"] = {
        "support": sum(matrix[NO_SECONDARY].values()),
        "invented": sum(matrix[NO_SECONDARY][label] for label in CATEGORY_LABELS),
    }
    for case in report["cases"]:
        case["invented_secondary"] = (
            case["gold"] == NO_SECONDARY and case["prediction"] in CATEGORY_LABELS
        )
        if case["gold"] == NO_SECONDARY:
            case["gold"] = None
        if case["prediction"] == NO_SECONDARY:
            case["prediction"] = None
    return report


def _summarize_first_attempts(
    gold: Mapping[str, str],
    attempts: Mapping[str, ClassificationAttempt],
    labels: tuple[str, ...],
    field: Literal["category_code", "urgency_flag", "secondary_category"],
    version: str,
) -> dict:
    """단일 분류 지표의 최초 시도 검사와 동일한 분모 계산을 공유한다.

    Args:
        gold: 잠금 대상과 해당 필드의 정답.
        attempts: 대상별 최초 시도.
        labels: 해당 필드의 고정 클래스 목록.
        field: 기존 분류 출력에서 집계할 필드.
        version: 반환 집계 계약 판본.

    Returns:
        dict: 고정 클래스와 실패를 보존한 단일 분류 집계.
    """
    if any(
        not isinstance(key, str) or not key or label not in labels for key, label in gold.items()
    ) or not set(attempts) <= set(gold):
        raise ValueError("CS_METRICS_INVALID_INPUT")
    matrix = {label: dict.fromkeys((*labels, NO_PREDICTION), 0) for label in labels}
    outcomes: Counter[str] = Counter()
    cases = []
    for case_id, expected in gold.items():
        attempt = attempts.get(case_id)
        predicted = NO_PREDICTION
        outcome = "NOT_STARTED"
        if case_id in attempts:
            if (
                not isinstance(attempt, ClassificationAttempt)
                or type(attempt.attempt_number) is not int
                or attempt.attempt_number != 1
                or attempt.outcome not in OUTCOMES
                or (attempt.outcome == "VALID") != (attempt.prediction is not None)
            ):
                raise ValueError("CS_METRICS_INVALID_INPUT")
            outcome = attempt.outcome
            if attempt.prediction is not None:
                predicted = getattr(attempt.prediction, field)
                if field == "secondary_category" and predicted is None:
                    predicted = NO_SECONDARY
                if predicted not in labels:
                    raise ValueError("CS_METRICS_INVALID_INPUT")
        matrix[expected][predicted] += 1
        outcomes[outcome] += 1
        cases.append(
            {
                "case_id": case_id,
                "gold": expected,
                "prediction": None if predicted == NO_PREDICTION else predicted,
                "outcome": outcome,
                "correct": expected == predicted,
            }
        )
    per_class = _per_class_metrics(matrix, labels)
    total = len(gold)
    correct = sum(matrix[label][label] for label in labels)
    return {
        "version": version,
        "attempt_number": 1,
        "quality_gate_evaluated": False,
        "labels": list(labels),
        "total": total,
        "correct": correct,
        "accuracy": correct / total if total else None,
        "macro_f1": sum(row["f1"] for row in per_class.values()) / len(labels) if total else None,
        "outcomes": {key: outcomes[key] for key in sorted(OUTCOMES | {"NOT_STARTED"})},
        "confusion_matrix": matrix,
        "per_class": per_class,
        "cases": cases,
    }


def _per_class_metrics(matrix: dict[str, dict[str, int]], labels: tuple[str, ...]) -> dict:
    """지정한 정답 행 집합 안에서 클래스별 지표를 계산한다.

    Args:
        matrix: 무예측·의도 없음 열을 포함한 혼동행렬.
        labels: 분모에 포함할 정답 행과 평균할 클래스 목록.

    Returns:
        dict: 실패와 null 예측도 해당 정답의 FN으로 포함한 지표.
    """
    per_class = {}
    for label in labels:
        tp = matrix[label][label]
        support = sum(matrix[label].values())
        fp = sum(matrix[other][label] for other in labels if other != label)
        fn = support - tp
        per_class[label] = {
            "support": support,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / support if support else 0.0,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0,
        }
    return per_class
