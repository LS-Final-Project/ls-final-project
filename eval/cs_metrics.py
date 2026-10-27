"""C/S 최초·최종 분류 시도의 분리된 지표 집계. 파일·DB·모델에 접근하지 않는다."""

from collections import Counter
from collections.abc import Mapping
from typing import Literal, cast, get_args

from backend.engine.cs.classification import Category, Urgency
from backend.engine.cs.classification_call import ClassificationAttempt

CATEGORY_LABELS: tuple[str, ...] = get_args(Category)
URGENCY_LABELS: tuple[str, ...] = get_args(Urgency)
CRITICAL_LABELS = ("SAFETY", "SITE_DOWN")
NO_SECONDARY = "NO_SECONDARY"
RETURN_SUBTYPE_LABELS = ("DEFECT", "NON_DEFECT")
NO_SUBTYPE = "NO_SUBTYPE"
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
    return _summarize_attempts(
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
    return _summarize_urgency(gold, attempts)


def _summarize_urgency(
    gold: Mapping[str, str], attempts: Mapping[str, ClassificationAttempt], *, final: bool = False
) -> dict:
    """긴급도 계산식을 공유하되 시도 선택 의미를 구분한다.

    Args:
        gold: 고정 대상 정답.
        attempts: 호출자가 선택한 대상별 시도.
        final: 최종 선택 여부. 최초 경로는 번호1만 허용한다.

    Returns:
        dict: 시도 선택에 맞는 독립 지표와 판본.
    """
    report = _summarize_attempts(
        gold,
        attempts,
        URGENCY_LABELS,
        "urgency_flag",
        "cs-urgency-final-attempt-v1" if final else "cs-urgency-first-attempt-v1",
        final=final,
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
    return _summarize_secondary(gold, attempts)


def _summarize_secondary(
    gold: Mapping[str, str | None],
    attempts: Mapping[str, ClassificationAttempt],
    *,
    final: bool = False,
) -> dict:
    """보조 분류 계산식을 공유하되 시도 선택 의미를 구분한다.

    Args:
        gold: 고정 대상 정답.
        attempts: 호출자가 선택한 대상별 시도.
        final: 최종 선택 여부. 최초 경로는 번호1만 허용한다.

    Returns:
        dict: 시도 선택에 맞는 독립 지표와 판본.
    """
    if any(value is not None and value not in CATEGORY_LABELS for value in gold.values()):
        raise ValueError("CS_METRICS_INVALID_INPUT")
    report = _summarize_attempts(
        {key: NO_SECONDARY if value is None else value for key, value in gold.items()},
        attempts,
        (*CATEGORY_LABELS, NO_SECONDARY),
        "secondary_category",
        "cs-secondary-final-attempt-v1" if final else "cs-secondary-first-attempt-v1",
        final=final,
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


def summarize_return_subtype_first_attempts(
    gold: Mapping[str, str], attempts: Mapping[str, ClassificationAttempt]
) -> dict:
    """잠금된 반품 대상의 하위유형 최초 시도를 집계한다.

    Args:
        gold: 반품 대상 ID와 DEFECT/NON_DEFECT 정답. 판본·hash 인수는 호출자 책임이다.
        attempts: 대상별 최초 시도. 없는 시도도 전체 분모에 남긴다.

    Returns:
        dict: 2클래스 P/R/F1·macro-F1과 유효 응답의 null 예측 건수. 유효 null은
            NO_SUBTYPE, 실패·보류·미실행은 NO_PREDICTION 열로 구분하고 둘 다 FN이다.
    """
    return _summarize_return_subtype(gold, attempts)


def _summarize_return_subtype(
    gold: Mapping[str, str],
    attempts: Mapping[str, ClassificationAttempt],
    *,
    final: bool = False,
) -> dict:
    """반품 하위유형 계산식을 공유하되 시도 선택 의미를 구분한다.

    Args:
        gold: 잠금 반품 대상 정답.
        attempts: 호출자가 선택한 대상별 시도.
        final: 최종 선택 여부. 최초 경로는 번호1만 허용한다.

    Returns:
        dict: null 예측 건수를 더한 2클래스 집계.
    """
    report = _summarize_attempts(
        gold,
        attempts,
        RETURN_SUBTYPE_LABELS,
        "return_subtype",
        "cs-return-subtype-final-attempt-v1" if final else "cs-return-subtype-first-attempt-v1",
        final=final,
    )
    report["null_predictions"] = sum(row[NO_SUBTYPE] for row in report["confusion_matrix"].values())
    for case in report["cases"]:
        if case["prediction"] == NO_SUBTYPE:
            case["prediction"] = None
    return report


def summarize_final_attempts(
    gold: Mapping[str, str | None],
    attempts: Mapping[str, ClassificationAttempt],
    *,
    metric: Literal["category", "urgency", "secondary", "return_subtype"],
) -> dict:
    """사전에 선택한 최종 시도를 번호 변경 없이 별도 품질 지표로 계산한다.

    Args:
        gold: 잠금 대상별 해당 지표 정답.
        attempts: 호출자가 is_final 및 저장 일관성을 검증한 최종 시도.
        metric: 집계 지표. 누락 시도는 NOT_FINAL로 전체 분모에 남긴다.

    Returns:
        dict: 최종 전용 판본·선택 의미·사례별 실제 시도 번호를 가진 지표.
            이 순수 함수는 DB의 최종 표시나 권한을 확인하지 않는다.
    """
    if metric == "secondary":
        return _summarize_secondary(gold, attempts, final=True)
    typed = cast(Mapping[str, str], gold)
    if metric == "urgency":
        return _summarize_urgency(typed, attempts, final=True)
    if metric == "category":
        return _summarize_attempts(
            typed,
            attempts,
            CATEGORY_LABELS,
            "category_code",
            "cs-category-final-attempt-v1",
            final=True,
        )
    if metric == "return_subtype":
        return _summarize_return_subtype(typed, attempts, final=True)
    raise ValueError("CS_METRICS_INVALID_INPUT")


def _summarize_attempts(
    gold: Mapping[str, str],
    attempts: Mapping[str, ClassificationAttempt],
    labels: tuple[str, ...],
    field: Literal["category_code", "urgency_flag", "secondary_category", "return_subtype"],
    version: str,
    *,
    final: bool = False,
) -> dict:
    """선택한 시도의 검사와 동일한 분모 계산을 공유한다.

    Args:
        gold: 잠금 대상과 해당 필드의 정답.
        attempts: 대상별로 선택한 최초 또는 최종 시도.
        labels: 해당 필드의 고정 클래스 목록.
        field: 기존 분류 출력에서 집계할 필드.
        version: 반환 집계 계약 판본.
        final: 최종 선택 여부. 누락은 NOT_FINAL이며 실제 시도 번호를 보존한다.

    Returns:
        dict: 고정 클래스와 실패를 보존한 단일 분류 집계.
    """
    if any(
        not isinstance(key, str) or not key or label not in labels for key, label in gold.items()
    ) or not set(attempts) <= set(gold):
        raise ValueError("CS_METRICS_INVALID_INPUT")
    missing_outcome = "NOT_FINAL" if final else "NOT_STARTED"
    # 반품 하위유형의 유효 null은 실패와 다른 열에 두되 정답으로 인정하지 않는다.
    predictable = (*labels, NO_SUBTYPE) if field == "return_subtype" else labels
    matrix = {label: dict.fromkeys((*predictable, NO_PREDICTION), 0) for label in labels}
    outcomes: Counter[str] = Counter()
    cases = []
    for case_id, expected in gold.items():
        attempt = attempts.get(case_id)
        predicted = NO_PREDICTION
        outcome = missing_outcome
        if case_id in attempts:
            if (
                not isinstance(attempt, ClassificationAttempt)
                or type(attempt.attempt_number) is not int
                or attempt.attempt_number < 1
                or (not final and attempt.attempt_number != 1)
                or attempt.outcome not in OUTCOMES
                or (attempt.outcome == "VALID") != (attempt.prediction is not None)
            ):
                raise ValueError("CS_METRICS_INVALID_INPUT")
            outcome = attempt.outcome
            if attempt.prediction is not None:
                predicted = getattr(attempt.prediction, field)
                if field == "secondary_category" and predicted is None:
                    predicted = NO_SECONDARY
                if field == "return_subtype" and predicted is None:
                    predicted = NO_SUBTYPE
                if predicted not in predictable:
                    raise ValueError("CS_METRICS_INVALID_INPUT")
        matrix[expected][predicted] += 1
        outcomes[outcome] += 1
        cases.append(
            {
                "case_id": case_id,
                **(
                    {"attempt_number": attempt.attempt_number if attempt else None} if final else {}
                ),
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
        **({"selection": "is_final"} if final else {"attempt_number": 1}),
        "quality_gate_evaluated": False,
        "labels": list(labels),
        "total": total,
        "correct": correct,
        "accuracy": correct / total if total else None,
        "macro_f1": sum(row["f1"] for row in per_class.values()) / len(labels) if total else None,
        "outcomes": {key: outcomes[key] for key in sorted(OUTCOMES | {missing_outcome})},
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
