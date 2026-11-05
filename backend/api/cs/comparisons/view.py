"""DB 행에서 조건 비교 응답을 만드는 순수 함수. DB나 요청 없이 테스트한다."""

from typing import Any
from uuid import UUID

from backend.api.cs.comparisons.schemas import (
    CONDITION_LABELS,
    CauseItem,
    ComparisonEvidence,
    ConditionComparison,
    ConditionItem,
    DocumentSignal,
    JudgementCounts,
    Side,
)

Row = dict[str, Any]
HIDDEN = "HIDDEN_BY_SCOPE"


def hide(
    items: list[Row], evidence: list[Row], visible_tickets: set[UUID] | None
) -> tuple[list[Row], list[Row], int]:
    """조회 범위 밖 문의에서 온 근거와 그 근거에서 파생된 값을 숨긴다.

    판정과 finding은 유지한다. 숨긴 근거가 기여한 항목의 요약과, 숨긴 근거에서 온 기준·대상
    값은 null과 HIDDEN_BY_SCOPE가 된다(자료에 없던 값이어도 숨김이 우선한다). 문서 근거처럼
    문의가 없는 근거는 숨기지 않는다.

    Args:
        items: 항목 행(kind·code·판정·값·근거 ID 배열).
        evidence: 근거 행(evidence_id·ticket_id 등).
        visible_tickets: 사용자가 볼 수 있는 문의 ID. None이면 전체를 본다.

    Returns:
        tuple[list[Row], list[Row], int]: 숨김을 적용한 항목 행(hidden_evidence_count 포함),
        남은 근거 행, 숨긴 근거 건수.
    """
    hidden = {
        e["evidence_id"]
        for e in evidence
        if visible_tickets is not None
        and e["ticket_id"] is not None
        and e["ticket_id"] not in visible_tickets
    }
    shown = []
    for row in items:
        item = dict(row)
        mine = hidden.intersection(item["evidence_ids"])
        item["evidence_ids"] = [e for e in item["evidence_ids"] if e not in hidden]
        item["hidden_evidence_count"] = len(mine)
        if mine and item["kind"] != "DOCUMENT_SIGNAL":
            item["masked_summary"], item["summary_missing_reason"] = None, HIDDEN
        for side in ("baseline", "target"):
            if hidden.intersection(item[side + "_evidence_ids"]):
                item[side + "_value"], item[side + "_missing_reason"] = None, HIDDEN
        shown.append(item)
    return shown, [e for e in evidence if e["evidence_id"] not in hidden], len(hidden)


def judged(item: Row) -> Row:
    """조건 항목과 원인이 함께 쓰는 판정 필드를 고른다.

    Args:
        item: 숨김을 적용한 항목 행.

    Returns:
        Row: Judged 모델의 필드.
    """
    names = (
        "judgement",
        "unconfirmed_reason",
        "masked_summary",
        "summary_missing_reason",
        "provenance_kind",
        "decided_by_name",
        "decided_at",
        "evidence_ids",
        "hidden_evidence_count",
    )
    return {name: item[name] for name in names}


def build(
    comparison: Row,
    version: Row,
    request: Row | None,
    items: list[Row],
    evidence: list[Row],
    hidden_count: int,
) -> ConditionComparison:
    """숨김을 적용한 행으로 응답 한 판본을 만든다.

    Args:
        comparison: 비교 결과 행(id·묶음 키·latest_version).
        version: 판본 행.
        request: 판정 기준 요청서 행. 없으면 None.
        items: hide()를 거친 항목 행.
        evidence: hide()를 거친 근거 행.
        hidden_count: 숨긴 근거 건수.

    Returns:
        ConditionComparison: 판정 집계·계산된 condition_status·최신 여부를 채운 응답.
    """
    conditions = [
        ConditionItem(
            item_code=i["code"],
            item_label=CONDITION_LABELS[i["code"]],
            finding=i["finding"],
            baseline=Side(
                masked_value=i["baseline_value"], missing_reason=i["baseline_missing_reason"]
            ),
            target=Side(masked_value=i["target_value"], missing_reason=i["target_missing_reason"]),
            **judged(i),
        )
        for i in items
        if i["kind"] == "CONDITION"
    ]
    judgements = [c.judgement for c in conditions]
    return ConditionComparison(
        comparison_id=comparison["id"],
        product_code=comparison["product_code"],
        fault_id=comparison["fault_id"],
        improvement_request_id=version["improvement_request_id"],
        request_version=version["request_version"],
        version=version["version"],
        latest_version=comparison["latest_version"],
        is_latest=version["version"] == comparison["latest_version"],
        data_origin=version["data_origin"],
        derivation_kind=version["derivation_kind"],
        known_at=version["known_at"],
        # 조건 항목이 1개 이상이고 모두 CONFIRMED일 때만 CONFIRMED다.
        computed_condition_status=(
            "CONFIRMED" if judgements and set(judgements) == {"CONFIRMED"} else "UNKNOWN"
        ),
        condition_summary=request["condition_summary"] if request else None,
        fw_fixed_version=request["fw_fixed_version"] if request else None,
        fw_released_on=request["fw_released_on"] if request else None,
        summary=JudgementCounts(
            confirmed=judgements.count("CONFIRMED"),
            suspected=judgements.count("SUSPECTED"),
            unconfirmed=judgements.count("UNCONFIRMED"),
        ),
        conditions=conditions,
        causes=[
            CauseItem(cause_type=i["code"], **judged(i)) for i in items if i["kind"] == "CAUSE"
        ],
        document_signals=[
            DocumentSignal(
                doc_signal=i["code"],
                provenance_kind=i["provenance_kind"],
                evidence_ids=i["evidence_ids"],
                hidden_evidence_count=i["hidden_evidence_count"],
            )
            for i in items
            if i["kind"] == "DOCUMENT_SIGNAL"
        ],
        evidence=[ComparisonEvidence.model_validate(e) for e in evidence],
        hidden_evidence_count=hidden_count,
    )
