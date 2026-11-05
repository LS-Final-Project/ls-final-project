"""판정 전이(계약 '상태 전이' 표), 근거 제외, 새 판본 조립. DB나 요청 없이 테스트한다."""

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from backend.api.cs.comparisons.schemas import CONDITION_LABELS, ItemWrite, JudgementWrite
from backend.shared.errors import ApplicationError

Row = dict[str, Any]
# (현재, 다음) → 그 전이를 쓸 수 있는 작성 주체. 표에 없는 전이는 409다.
# 처음 쓰는 원인은 UNCONFIRMED에서 시작한 것으로 본다. 생성 시 기본값은 initial_items()다.
ALLOWED: dict[tuple[str, str], frozenset[str]] = {
    ("UNCONFIRMED", "UNCONFIRMED"): frozenset({"human_input", "system_record"}),
    ("UNCONFIRMED", "SUSPECTED"): frozenset({"ai_draft", "human_input"}),
    ("SUSPECTED", "SUSPECTED"): frozenset({"ai_draft", "human_input"}),
    ("UNCONFIRMED", "CONFIRMED"): frozenset({"human_input"}),
    ("SUSPECTED", "CONFIRMED"): frozenset({"human_input"}),
    ("CONFIRMED", "CONFIRMED"): frozenset({"human_input"}),
    ("CONFIRMED", "SUSPECTED"): frozenset({"human_input"}),
    ("CONFIRMED", "UNCONFIRMED"): frozenset({"human_input"}),
    ("SUSPECTED", "UNCONFIRMED"): frozenset({"human_input", "system_record"}),
}


def unprocessable(code: str, message: str) -> ApplicationError:
    """판정 조건을 채우지 못한 쓰기의 오류를 만든다.

    Args:
        code: CS_ 접두 오류 코드.
        message: 원문·개인정보 없는 설명.

    Returns:
        ApplicationError: 422 오류.
    """
    return ApplicationError(422, code, message)


def initial_items(now: datetime) -> list[Row]:
    """비교를 만들 때의 기본 항목: 사용 조건 8개 모두 근거 없는 미확인.

    Args:
        now: 생성 시각.

    Returns:
        list[Row]: system_record가 만든 UNCONFIRMED 조건 항목 행.
    """
    return [
        {
            "kind": "CONDITION",
            "code": code,
            "judgement": "UNCONFIRMED",
            "finding": None,
            "unconfirmed_reason": "NO_EVIDENCE",
            "baseline_value": None,
            "baseline_missing_reason": "NOT_PROVIDED",
            "baseline_evidence_ids": [],
            "target_value": None,
            "target_missing_reason": "NOT_PROVIDED",
            "target_evidence_ids": [],
            "masked_summary": None,
            "summary_missing_reason": "NOT_PROVIDED",
            "evidence_ids": [],
            "provenance_kind": "system_record",
            "decided_by": None,
            "decided_by_name": None,
            "decided_at": now,
            "reason": None,
        }
        for code in CONDITION_LABELS
    ]


def key(item: Row) -> str:
    """항목의 감사·제외 이력 키를 만든다.

    Args:
        item: 항목 행.

    Returns:
        str: 'CONDITION:site_condition' 같은 키.
    """
    return f"{item['kind']}:{item['code']}"


def expected_reason(
    item: Row, evidence: dict[str, Row], removed: Collection[str], now: datetime
) -> str | None:
    """미확인 항목의 사유를 근거 상태에서 계산한다(계약 우선순위).

    NOT_YET_KNOWN > EVIDENCE_REMOVED > VALUE_NOT_PROVIDED > NO_EVIDENCE 순으로 처음 맞는 하나다.

    Args:
        item: 항목 행.
        evidence: 새 판본의 근거 ID → 근거 행.
        removed: 근거 제외 이력이 있는 항목 키.
        now: 판정 시각.

    Returns:
        str | None: 맞는 사유. 알려진 근거가 있고 값도 다 있으면 None(미확인일 이유가 없다).
    """
    cited = [evidence[e] for e in item["evidence_ids"]]
    if cited and all(e["known_at"] > now for e in cited):
        return "NOT_YET_KNOWN"
    if key(item) in removed:
        return "EVIDENCE_REMOVED"
    missing = "NOT_PROVIDED" in (item["baseline_missing_reason"], item["target_missing_reason"])
    if cited and missing:
        return "VALUE_NOT_PROVIDED"
    return None if cited else "NO_EVIDENCE"


def check_item(
    old: str,
    item: Row,
    provenance: str,
    evidence: dict[str, Row],
    now: datetime,
    removed: Collection[str] = (),
) -> None:
    """항목 하나의 전이가 계약 표와 조건을 지키는지 확인한다.

    Args:
        old: 현재 판정. 처음 쓰는 원인은 UNCONFIRMED.
        item: 새 항목 행.
        provenance: 작성 주체(ai_draft·human_input·system_record).
        evidence: 새 판본의 근거 ID → 근거 행.
        now: 판정 시각. 이보다 늦게 알려진 근거는 세지 않는다.
        removed: 근거 제외 이력이 있는 항목 키(EVIDENCE_REMOVED 판단).

    Returns:
        None: 표에 없는 전이·주체는 409 CS_INVALID_TRANSITION, 조건 미충족은 422.
    """
    new = item["judgement"]
    if provenance not in ALLOWED.get((old, new), frozenset()):
        raise ApplicationError(
            409, "CS_INVALID_TRANSITION", f"{provenance} may not change {old} to {new}."
        )
    known = [evidence[e] for e in item["evidence_ids"] if evidence[e]["known_at"] <= now]
    if new != "UNCONFIRMED":
        if "NOT_PROVIDED" in (item["baseline_missing_reason"], item["target_missing_reason"]):
            raise unprocessable(
                "CS_VALUE_NOT_PROVIDED", "A missing value is never judged the same or different."
            )
        if not known:
            raise unprocessable(
                "CS_EVIDENCE_REQUIRED",
                "Judging needs evidence already known; use UNCONFIRMED with NOT_YET_KNOWN.",
            )
    if new == "CONFIRMED" and not any(e["case_id"] is not None for e in known):
        raise unprocessable("CS_EVIDENCE_REQUIRED", "Confirming needs a known resolution case.")
    if provenance == "system_record" and old == "SUSPECTED" and known:
        raise ApplicationError(
            409, "CS_INVALID_TRANSITION", "The system lowers a judgement only without evidence."
        )
    if new == "UNCONFIRMED":
        expected = expected_reason(item, evidence, removed, now)
        if expected is None:
            raise unprocessable(
                "CS_REASON_MISMATCH",
                "Known evidence and both values are present, so it is not unconfirmed. "
                "SUSPECTED로 내리세요.",
            )
        if item["unconfirmed_reason"] != expected:
            raise unprocessable(
                "CS_REASON_MISMATCH", f"The evidence makes the unconfirmed reason {expected}."
            )
    lowered = (old == "CONFIRMED" and new != "CONFIRMED") or (old, new) == (
        "SUSPECTED",
        "UNCONFIRMED",
    )
    if provenance == "human_input" and (new == "CONFIRMED" or lowered) and not item["reason"]:
        raise unprocessable("CS_REASON_REQUIRED", "Confirming or lowering needs a reason.")


def check_sources(
    write: JudgementWrite, tickets: dict[UUID, Row], cases: dict[UUID, Row], group: Row
) -> None:
    """새 근거가 실재하고 같은 묶음(제품·고장)의 문의·사례인지 확인한다.

    Args:
        write: 쓰기 본문.
        tickets: 근거 문의 ID → 문의 행(product_code 등).
        cases: 근거 사례 ID → 사례 행(ticket_id·fault_id 등).
        group: 비교 결과 행(product_code·fault_id).

    Returns:
        None: 어기면 422 CS_EVIDENCE_INVALID.
    """
    for e in write.evidence:
        ticket = tickets.get(e.ticket_id)
        if ticket is None or ticket["product_code"] != group["product_code"]:
            raise unprocessable(
                "CS_EVIDENCE_INVALID", f"{e.evidence_id}: unknown or other product."
            )
        if e.case_id is None:
            continue
        case = cases.get(e.case_id)
        if (
            case is None
            or case["ticket_id"] != e.ticket_id
            or case["fault_id"] != group["fault_id"]
        ):
            raise unprocessable("CS_EVIDENCE_INVALID", f"{e.evidence_id}: not this fault's case.")


def item_row(write: ItemWrite, provenance: str, actor: UUID | None, now: datetime) -> Row:
    """쓰기 본문의 항목을 저장할 행으로 바꾼다.

    Args:
        write: 항목 쓰기.
        provenance: 작성 주체.
        actor: 판정한 사람. 사람이 아니면 None.
        now: 판정 시각.

    Returns:
        Row: condition_comparison_items 한 행. 표시명은 플랫폼 중계 단계에서 붙여 지금은 null.
    """
    row: Row = {
        "kind": write.kind,
        "code": write.code,
        "judgement": write.judgement,
        "finding": write.finding,
        "unconfirmed_reason": write.unconfirmed_reason,
        "masked_summary": write.masked_summary,
        "summary_missing_reason": write.summary_missing_reason,
        "evidence_ids": list(write.evidence_ids),
        "provenance_kind": provenance,
        "decided_by": actor,
        "decided_by_name": None,
        "decided_at": now,
        "reason": write.reason,
    }
    for name in ("baseline", "target"):
        side = getattr(write, name)
        row[name + "_value"] = side.masked_value if side else None
        row[name + "_missing_reason"] = side.missing_reason if side else None
        row[name + "_evidence_ids"] = list(side.evidence_ids) if side else []
    return row


def next_version(
    items: list[Row],
    evidence: list[Row],
    write: JudgementWrite,
    sources: dict[str, Row],
    provenance: str,
    actor: UUID | None,
    now: datetime,
    removals: Sequence[Row] = (),
) -> tuple[list[Row], list[Row], Row]:
    """이전 판본에 쓰기를 적용한 새 판본 전체를 만든다. 근거 제외는 remove_evidence()다.

    Args:
        items: 이전 판본 항목 행.
        evidence: 이전 판본 근거 행.
        write: 쓰기 본문.
        sources: 새 근거 ID → 원본 행(data_origin·derivation_kind·known_at).
        provenance: 작성 주체.
        actor: 판정한 사람.
        now: 판정 시각.
        removals: 제외 이력 행(evidence_id·item_keys). 제외된 ID는 다시 쓰지 못한다.

    Returns:
        tuple[list[Row], list[Row], Row]: 새 항목 행, 새 근거 행, 감사 기록의 changes.
    """
    kept = {e["evidence_id"] for e in evidence} | {r["evidence_id"] for r in removals}
    removed = {k for r in removals for k in r["item_keys"]}
    if kept & set(sources):
        raise unprocessable("CS_EVIDENCE_INVALID", "Evidence IDs are added once and never reused.")
    added = [
        {
            "evidence_id": e.evidence_id,
            "case_id": e.case_id,
            "ticket_id": e.ticket_id,
            "evidence_location": e.evidence_location.model_dump(),
            **{
                k: sources[e.evidence_id][k] for k in ("data_origin", "derivation_kind", "known_at")
            },
        }
        for e in write.evidence
    ]
    by_id = {e["evidence_id"]: e for e in [*evidence, *added]}
    current = {(i["kind"], i["code"]): i for i in items}
    changes: Row = {}
    for write_item in write.items:
        if not set(write_item.evidence_ids) <= set(by_id):
            raise unprocessable("CS_EVIDENCE_INVALID", "Every cited evidence ID must exist.")
        key = (write_item.kind, write_item.code)
        old = current[key]["judgement"] if key in current else "UNCONFIRMED"
        row = item_row(write_item, provenance, actor, now)
        check_item(old, row, provenance, by_id, now, removed)
        current[key] = row
        changes[f"{write_item.kind}:{write_item.code}"] = {
            "from": old,
            "to": row["judgement"],
            "reason": row["reason"],
        }
    return list(current.values()), list(by_id.values()), changes


def remove_evidence(
    items: list[Row],
    evidence: list[Row],
    evidence_id: str,
    reason: str,
    removals: Sequence[Row],
    now: datetime,
) -> tuple[list[Row], list[Row], Row, list[str]]:
    """근거 하나를 뺀 새 판본 전체를 만든다.

    그 근거만 받치던 기준·대상 값은 null과 NOT_PROVIDED가 된다. 의심 항목에 알려진 근거가
    남지 않으면 system_record가 미확인으로 내린다. 사람 판정(확정)이나 근거가 남은 의심을
    시스템이 지울 수는 없어서, 제외가 그 판정의 조건을 깨면 409로 막고 재검토를 먼저 요구한다.

    Args:
        items: 이전 판본 항목 행.
        evidence: 이전 판본 근거 행.
        evidence_id: 뺄 근거 ID.
        reason: 제외 사유.
        removals: 이전 제외 이력 행.
        now: 제외 시각.

    Returns:
        tuple[list[Row], list[Row], Row, list[str]]: 새 항목 행, 남은 근거 행, 감사 changes,
        그 근거를 인용하던 항목 키.
    """
    if evidence_id not in {e["evidence_id"] for e in evidence}:
        raise ApplicationError(
            404, "CS_EVIDENCE_NOT_FOUND", "The evidence was not found or already removed."
        )
    left = [e for e in evidence if e["evidence_id"] != evidence_id]
    by_id = {e["evidence_id"]: e for e in left}
    removed = {k for r in removals for k in r["item_keys"]}
    changes: Row = {f"evidence:{evidence_id}": {"removed": True, "reason": reason}}
    affected: list[str] = []
    shown = []
    for old in items:
        if evidence_id not in old["evidence_ids"]:
            shown.append(old)
            continue
        row = dict(old)
        affected.append(key(row))
        row["evidence_ids"] = [e for e in row["evidence_ids"] if e != evidence_id]
        for side in ("baseline", "target"):
            row[side + "_evidence_ids"] = [
                e for e in row[side + "_evidence_ids"] if e != evidence_id
            ]
            if row[side + "_value"] is not None and not row[side + "_evidence_ids"]:
                row[side + "_value"], row[side + "_missing_reason"] = None, "NOT_PROVIDED"
        judgement = row["judgement"]
        if judgement in ("SUSPECTED", "CONFIRMED"):
            known = [by_id[e] for e in row["evidence_ids"] if by_id[e]["known_at"] <= now]
            missing = "NOT_PROVIDED" in (
                row["baseline_missing_reason"],
                row["target_missing_reason"],
            )
            holds = known and not missing
            if judgement == "CONFIRMED":
                holds = holds and any(e["case_id"] is not None for e in known)
            if holds:
                shown.append(row)
                continue
            if judgement == "CONFIRMED" or known:
                raise ApplicationError(
                    409,
                    "CS_INVALID_TRANSITION",
                    f"Removing it breaks the {judgement} judgement of {key(row)}; "
                    "review the item first.",
                )
        if judgement is None:
            shown.append(row)
            continue
        lowered = expected_reason(row, by_id, removed | {key(row)}, now)
        if judgement != "UNCONFIRMED" or lowered != row["unconfirmed_reason"]:
            row |= {
                "judgement": "UNCONFIRMED",
                "finding": None,
                "unconfirmed_reason": lowered,
                "provenance_kind": "system_record",
                "decided_by": None,
                "decided_by_name": None,
                "decided_at": now,
                "reason": None,
            }
            changes[key(row)] = {
                "from": judgement,
                "to": "UNCONFIRMED",
                "reason": None,
                "by": "system_record",
            }
        shown.append(row)
    return shown, left, changes, affected


def data_origin(evidence: list[Row], fallback: str) -> str:
    """판본의 출처: 근거가 한 종류면 그 값, 섞이면 demo, 근거가 없으면 요청서 값.

    Args:
        evidence: 새 판본 근거 행.
        fallback: 근거가 없을 때 쓸 요청서의 data_origin.

    Returns:
        str: synthetic·demo·real.
    """
    origins = {e["data_origin"] for e in evidence}
    if not origins:
        return fallback
    return origins.pop() if len(origins) == 1 else "demo"
