"""메일 업무 유형 분류 결과 채점(KAN-142). 파일·DB·모델에 접근하지 않는다.

결과가 None이면 실패·미실행이며 분모에 남기고 NO_PREDICTION 열로 센다. 합격 판정과 메일 원문은
결과에 넣지 않는다(TriageResult에는 원문이 없다).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, get_args

from backend.engine.mail_triage.triage import LinkSuggestion, MailType, TriageResult
from eval.cs_metrics import NO_PREDICTION, _per_class_metrics

MAIL_TYPES: tuple[str, ...] = get_args(MailType)
EVIDENCE_SOURCES = frozenset({"subject", "body", "reply_header"})
MAX_EVIDENCE = 10


@dataclass(frozen=True)
class MailGold:
    """메일 하나의 잠긴 정답. human_needs_review가 None이면 사람이 검토 필요를 판정하지 않았다."""

    mail_type: MailType
    link_suggestion: LinkSuggestion | None
    human_needs_review: bool | None


def _rate(part: int, whole: int) -> float | None:
    """비율. 분모가 0이면 None이다.

    Args:
        part: 분자.
        whole: 분모.

    Returns:
        float | None: part / whole.
    """
    return part / whole if whole else None


def _valid_evidence(result: TriageResult) -> bool:
    """근거 목록이 형식 규칙(최대 10개, 허용 출처, 본문·제목은 start<end, 헤더는 구간 없음)에 맞는지 본다.

    Args:
        result: 분류 결과.

    Returns:
        bool: 맞으면 True. 구간 길이 상한은 입력이 없어 보지 않는다.
    """
    if len(result.evidence) > MAX_EVIDENCE:
        return False
    for item in result.evidence:
        if item.source not in EVIDENCE_SOURCES:
            return False
        if item.source == "reply_header":
            if item.start is not None or item.end is not None:
                return False
        elif item.start is None or item.end is None or not 0 <= item.start < item.end:
            return False
    return True


def summarize_triage(
    gold: Mapping[str, MailGold], results: Mapping[str, TriageResult | None]
) -> dict:
    """메일 유형 5클래스 지표·연결 추천 일치·근거 유효율·검토 필요 과잉/누락을 집계한다.

    Args:
        gold: 메일별 잠긴 정답.
        results: 메일별 분류 결과. None은 실패·미실행이다. ID 집합이 gold와 같아야 한다.

    Returns:
        dict: 혼동행렬(NO_PREDICTION 열 포함)·클래스별 P/R/F1·macro-F1, 연결 추천 정확 일치율(전체 분모와
            유형이 맞은 사례 분모), 근거 유효율(결과 있는 사례 분모), 검토 필요 비율과 사람 판정 대비 과잉
            보류(모델 True·사람 False)·누락(모델 False·사람 True) 수, 메일별 결과. 잘못된 입력은
            EVAL_INVALID_INPUT ValueError다.
    """
    if (
        set(gold) != set(results)
        or any(
            not isinstance(item, MailGold)
            or item.mail_type not in MAIL_TYPES
            or (item.human_needs_review is not None and type(item.human_needs_review) is not bool)
            for item in gold.values()
        )
        # 검증을 거치지 않은 결과(model_construct)도 들어올 수 있으므로 유형·검토 필요 값을 다시 본다.
        or any(
            r is not None
            and (
                not isinstance(r, TriageResult)
                or r.mail_type not in MAIL_TYPES
                or type(r.needs_review) is not bool
            )
            for r in results.values()
        )
    ):
        raise ValueError("EVAL_INVALID_INPUT")
    matrix = {label: dict.fromkeys((*MAIL_TYPES, NO_PREDICTION), 0) for label in MAIL_TYPES}
    type_correct = link_all = link_typed = evidence_ok = flagged = 0
    review = {"compared": 0, "over_hold": 0, "missed": 0}
    per_mail: list[dict[str, Any]] = []
    for mail_id, expected in gold.items():
        result = results[mail_id]
        predicted = NO_PREDICTION if result is None else result.mail_type
        matrix[expected.mail_type][predicted] += 1
        correct = predicted == expected.mail_type
        link = result is not None and result.link_suggestion == expected.link_suggestion
        type_correct += correct
        link_all += link
        link_typed += correct and link
        if result is not None:
            evidence_ok += _valid_evidence(result)
            flagged += result.needs_review
            if expected.human_needs_review is not None:
                review["compared"] += 1
                review["over_hold"] += result.needs_review and not expected.human_needs_review
                review["missed"] += expected.human_needs_review and not result.needs_review
        per_mail.append(
            {
                "mail_id": mail_id,
                "gold": expected.mail_type,
                "prediction": None if result is None else predicted,
                "correct": correct,
                "link_correct": link,
                "needs_review": None if result is None else result.needs_review,
            }
        )
    per_class = _per_class_metrics(matrix, MAIL_TYPES)
    total = len(gold)
    answered = sum(result is not None for result in results.values())
    return {
        "version": "mail-triage-metrics-v1",
        "quality_gate_evaluated": False,
        "labels": list(MAIL_TYPES),
        "total": total,
        "failed": total - answered,
        "accuracy": _rate(type_correct, total),
        "macro_f1": sum(row["f1"] for row in per_class.values()) / len(MAIL_TYPES)
        if total
        else None,
        "confusion_matrix": matrix,
        "per_class": per_class,
        "link_suggestion": {
            "exact_rate": _rate(link_all, total),
            "exact_rate_when_type_correct": _rate(link_typed, type_correct),
        },
        "evidence_valid_rate": _rate(evidence_ok, answered),
        "needs_review": {"rate": _rate(flagged, answered), **review},
        "per_mail": per_mail,
    }
