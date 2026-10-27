"""C/S 최초 분류 시도의 주분류 집계. 파일·DB·모델에 접근하지 않는다."""

from collections import Counter
from collections.abc import Mapping
from typing import get_args

from backend.engine.cs.classification import Category
from backend.engine.cs.classification_call import ClassificationAttempt

CATEGORY_LABELS: tuple[str, ...] = get_args(Category)
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
    if any(
        not isinstance(key, str) or not key or label not in CATEGORY_LABELS
        for key, label in gold.items()
    ) or not set(attempts) <= set(gold):
        raise ValueError("CS_METRICS_INVALID_INPUT")
    matrix = {
        label: dict.fromkeys((*CATEGORY_LABELS, NO_PREDICTION), 0) for label in CATEGORY_LABELS
    }
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
                predicted = attempt.prediction.category_code
                if predicted not in CATEGORY_LABELS:
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
    per_class = {}
    for label in CATEGORY_LABELS:
        tp = matrix[label][label]
        support = sum(matrix[label].values())
        fp = sum(matrix[other][label] for other in CATEGORY_LABELS if other != label)
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
    total = len(gold)
    correct = sum(matrix[label][label] for label in CATEGORY_LABELS)
    return {
        "version": "cs-category-first-attempt-v1",
        "attempt_number": 1,
        "quality_gate_evaluated": False,
        "labels": list(CATEGORY_LABELS),
        "total": total,
        "correct": correct,
        "accuracy": correct / total if total else None,
        "macro_f1": sum(row["f1"] for row in per_class.values()) / len(CATEGORY_LABELS)
        if total
        else None,
        "outcomes": {key: outcomes[key] for key in sorted(OUTCOMES | {"NOT_STARTED"})},
        "confusion_matrix": matrix,
        "per_class": per_class,
        "cases": cases,
    }
