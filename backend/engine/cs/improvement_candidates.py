"""개선요청 후보 결정 규칙(KAN-113, 기획서 4.4). 순수 함수이며 DB·모델 호출이 없다.

후보는 "사람이 검토할 거리"다. 후보가 생겼다고 요청이 만들어지거나 전달되지 않는다(permissions.md 2.2:
시스템은 DRAFT 후보만 만들고 cs_agent가 검토·전달한다).

고장 후보(FAULT): 같은 제품·같은 고장의 해결 기록을 묶는다.
- 기준 시각은 해결 기록의 **적재 시각**(`known_at`)이다. 기획서대로 현재 데이터에서는 해결 시각이 적재 시각의
  대리값이다. `as_of`보다 뒤에 알려진 기록은 보지 않는다(미래 정보 차단).
- 일반 고장: 어느 90일 구간 안에 서로 다른 고객 3곳이면 후보다. 도달 시각은 세 번째 고객의 기록이 알려진
  시각이다(`reached_at`). 같은 고객의 재발은 고객 수를 늘리지 않는다.
- 펌웨어 예외: 원인 유형이 `FIRMWARE`이고, 그 제품에 **고장 당시 버전보다 높은 수정 펌웨어가 기록이 알려진
  시각 이전에 배포**돼 있으면 고객 1곳부터 후보다(업데이트 안내 검토). 고장 당시 버전이 없거나 비교할 수 없으면
  이 예외를 적용하지 않고 `INSUFFICIENT_EVIDENCE` 사유로 남긴다(일반 기준은 그대로 본다).
- 사용 조건이 다르면 다른 묶음이어야 하지만 조건 판정은 KAN-114가 한다. 여기서는 묶음마다 조건 확인 여부를
  입력으로 받아 `UNKNOWN`이면 후보에 `condition_status=UNKNOWN`을 붙인다(보류 표시, 후보 자체는 낸다).

문서 후보(DOCUMENT): 같은 제품·같은 신호(·같은 문서)의 문의를 묶는다.
- 신호는 KAN-114가 조건 비교에서 기록한 `document_signals`다. 근거 위치가 없는 신호는 세지 않는다.
- `NOT_FOUND`·`VALUE_CONFLICT`·`BROKEN_LINK`: 근거가 있는 문의 1건이면 후보다(문서 오류는 반복이 아니라
  근거로 판단한다).
- `REPEATED_SPEC`: 서로 다른 문의 3건부터 후보다(같은 사양 질문의 반복).
- 받는 부서는 부서 목록에 없어 `receiver_unconfirmed=True`다.

결과는 묶음마다 하나다: `ELIGIBLE`(후보), `NOT_ELIGIBLE`(기준 미달, 건수 함께), `INSUFFICIENT_EVIDENCE`
(판단에 필요한 값이 없음). 숫자는 항상 분모와 함께 낸다(예: 고객 2/3).
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CANDIDATE_RULES_VERSION = "improvement-candidates-v1"
WINDOW_DAYS = 90
FAULT_CUSTOMERS = 3
FIRMWARE_CUSTOMERS = 1
REPEATED_SPEC_TICKETS = 3
DocSignal = Literal["NOT_FOUND", "VALUE_CONFLICT", "BROKEN_LINK", "REPEATED_SPEC"]
RootCauseType = Literal["SITE_CONDITION", "HARDWARE", "SETTING", "WEAR_PART", "FIRMWARE"]
Outcome = Literal["ELIGIBLE", "NOT_ELIGIBLE", "INSUFFICIENT_EVIDENCE"]
ReasonCode = Literal[
    "REPEAT_90D_3_CUSTOMERS",
    "FIRMWARE_FIX_RELEASED",
    "DOCUMENT_ERROR_EVIDENCE",
    "REPEATED_SPEC_3_TICKETS",
    "BELOW_THRESHOLD",
    "NO_CUSTOMER_ID",
    "NO_KNOWN_AT",
    "FW_VERSION_UNKNOWN",
    "FW_VERSION_NOT_COMPARABLE",
    "NO_EVIDENCE_LOCATION",
]
_VERSION = re.compile(r"^\s*v?(\d+(?:\.\d+)*)\s*$", re.I)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CaseRecord(Strict):
    """해결 기록 한 건. 원문은 받지 않는다."""

    case_id: str
    ticket_id: str
    product_code: str
    fault_id: str
    customer_id: str | None = Field(description="고객 식별자. 없으면 고객 수를 셀 수 없다.")
    known_at: datetime = Field(description="적재 시각(지금 데이터에서는 해결 시각 대리값).")
    root_cause_type: RootCauseType | None = None
    fw_version_at_issue: str | None = None


class FirmwareRelease(Strict):
    product_code: str
    fw_version: str
    release_date: date


class DocumentSignalRecord(Strict):
    """KAN-114 조건 비교가 남긴 문서 신호 하나."""

    ticket_id: str
    product_code: str
    doc_signal: DocSignal
    doc_id: str | None = None
    known_at: datetime
    has_evidence_location: bool = Field(description="텍스트 없는 위치를 추적할 수 있는가.")


class EvidenceRef(Strict):
    ticket_id: str
    case_id: str | None
    customer_id: str | None
    known_at: datetime


class FaultCandidate(Strict):
    request_type: Literal["FAULT"] = "FAULT"
    product_code: str
    fault_id: str
    outcome: Outcome
    reason_code: ReasonCode
    reason: str
    reached_at: datetime | None = Field(description="기준 도달 시각. 미달이면 null.")
    first_known_at: datetime | None
    distinct_customers: int
    distinct_customers_in_best_window: int
    required_customers: int
    cases_total: int
    cases_without_customer: int
    firmware_rule_applied: bool
    fw_fixed_version: str | None = None
    fw_released_on: date | None = None
    condition_status: Literal["CONFIRMED", "UNKNOWN"]
    evidence: list[EvidenceRef] = Field(description="as_of까지 알려진 기록 전부, 시각순.")


class DocumentCandidate(Strict):
    request_type: Literal["DOCUMENT"] = "DOCUMENT"
    product_code: str
    doc_signal: DocSignal
    doc_id: str | None
    outcome: Outcome
    reason_code: ReasonCode
    reason: str
    reached_at: datetime | None
    tickets_with_evidence: int
    tickets_without_evidence: int
    required_tickets: int
    receiver_unconfirmed: Literal[True] = True
    evidence: list[EvidenceRef]


@dataclass(frozen=True)
class CandidateReport:
    version: str
    as_of: datetime
    faults: list[FaultCandidate] = field(default_factory=list)
    documents: list[DocumentCandidate] = field(default_factory=list)


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """`1.72`·`v2.0.1` 같은 판본을 숫자 튜플로. 비교할 수 없으면 None."""
    if text is None:
        return None
    match = _VERSION.match(text)
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def applicable_fix(
    case: CaseRecord, releases: list[FirmwareRelease]
) -> tuple[FirmwareRelease | None, ReasonCode | None]:
    """고장 당시 버전보다 높고 기록이 알려진 시각 이전에 배포된 수정 펌웨어(가장 이른 것).

    Returns:
        (수정 배포, None) 또는 (None, 적용하지 못한 이유). 이유가 None이고 배포도 None이면 그런 배포가 없다.
    """
    at_issue = parse_version(case.fw_version_at_issue)
    if case.fw_version_at_issue is None:
        return None, "FW_VERSION_UNKNOWN"
    if at_issue is None:
        return None, "FW_VERSION_NOT_COMPARABLE"
    fixes = []
    for release in releases:
        if release.product_code != case.product_code:
            continue
        parsed = parse_version(release.fw_version)
        if parsed is None:
            continue
        if parsed > at_issue and release.release_date <= case.known_at.date():
            fixes.append(release)
    if not fixes:
        return None, None
    return min(fixes, key=lambda r: (r.release_date, parse_version(r.fw_version) or ())), None


def _best_window(cases: list[CaseRecord]) -> tuple[int, datetime | None]:
    """고객 수가 처음 기준에 닿는 시각과, 어느 90일 구간에서든 최대 고객 수."""
    known = sorted((c for c in cases if c.customer_id is not None), key=lambda c: c.known_at)
    best = 0
    reached: datetime | None = None
    window = timedelta(days=WINDOW_DAYS)
    for end_index, end in enumerate(known):
        start_at = end.known_at - window
        customers = {c.customer_id for c in known[: end_index + 1] if c.known_at >= start_at}
        best = max(best, len(customers))
        if reached is None and len(customers) >= FAULT_CUSTOMERS:
            reached = end.known_at
    return best, reached


def fault_candidates(
    cases: list[CaseRecord],
    releases: list[FirmwareRelease],
    *,
    as_of: datetime,
    condition_confirmed: dict[tuple[str, str], bool] | None = None,
) -> list[FaultCandidate]:
    """제품·고장 묶음마다 후보 판정 하나.

    Args:
        cases: 해결 기록. `as_of`보다 뒤에 알려진 것은 뺀다.
        releases: 펌웨어 배포 이력.
        as_of: 판정 시점. 미래 정보를 쓰지 않기 위한 기준.
        condition_confirmed: 묶음(제품, 고장)의 사용 조건 확인 여부(KAN-114). 없으면 UNKNOWN.

    Returns:
        list[FaultCandidate]: 제품·고장 순.
    """
    confirmed = condition_confirmed or {}
    groups: dict[tuple[str, str], list[CaseRecord]] = defaultdict(list)
    for case in cases:
        if case.known_at <= as_of:
            groups[(case.product_code, case.fault_id)].append(case)
    out: list[FaultCandidate] = []
    for (product, fault), members in sorted(groups.items()):
        members.sort(key=lambda c: c.known_at)
        customers = {c.customer_id for c in members if c.customer_id is not None}
        without = sum(c.customer_id is None for c in members)
        best, reached = _best_window(members)
        evidence = [
            EvidenceRef(
                ticket_id=c.ticket_id,
                case_id=c.case_id,
                customer_id=c.customer_id,
                known_at=c.known_at,
            )
            for c in members
        ]
        base = {
            "product_code": product,
            "fault_id": fault,
            "first_known_at": members[0].known_at,
            "distinct_customers": len(customers),
            "distinct_customers_in_best_window": best,
            "cases_total": len(members),
            "cases_without_customer": without,
            "condition_status": "CONFIRMED" if confirmed.get((product, fault)) else "UNKNOWN",
            "evidence": evidence,
        }
        # 펌웨어 예외: 수정판이 이미 배포된 뒤의 기록이 하나라도 있으면 고객 1곳부터.
        fw_hits = []
        fw_reasons: set[ReasonCode] = set()
        for case in members:
            if case.root_cause_type != "FIRMWARE":
                continue
            fix, why = applicable_fix(case, releases)
            if fix is not None:
                fw_hits.append((case, fix))
            elif why is not None:
                fw_reasons.add(why)
        if fw_hits and customers:
            case, fix = min(fw_hits, key=lambda pair: pair[0].known_at)
            out.append(
                FaultCandidate(
                    outcome="ELIGIBLE",
                    reason_code="FIRMWARE_FIX_RELEASED",
                    reason=(
                        f"펌웨어 원인이고 수정판 {fix.fw_version}({fix.release_date})이 기록 전에 배포됨. "
                        f"고객 {len(customers)}/{FIRMWARE_CUSTOMERS}곳."
                    ),
                    reached_at=case.known_at,
                    required_customers=FIRMWARE_CUSTOMERS,
                    firmware_rule_applied=True,
                    fw_fixed_version=fix.fw_version,
                    fw_released_on=fix.release_date,
                    **base,
                )
            )
            continue
        if reached is not None:
            out.append(
                FaultCandidate(
                    outcome="ELIGIBLE",
                    reason_code="REPEAT_90D_3_CUSTOMERS",
                    reason=f"{WINDOW_DAYS}일 안 서로 다른 고객 {best}/{FAULT_CUSTOMERS}곳.",
                    reached_at=reached,
                    required_customers=FAULT_CUSTOMERS,
                    firmware_rule_applied=False,
                    **base,
                )
            )
            continue
        if (
            without
            and len(customers) < FAULT_CUSTOMERS
            and without + len(customers) >= FAULT_CUSTOMERS
        ):
            out.append(
                FaultCandidate(
                    outcome="INSUFFICIENT_EVIDENCE",
                    reason_code="NO_CUSTOMER_ID",
                    reason=f"고객 식별자 없는 기록 {without}건. 고객 {len(customers)}/{FAULT_CUSTOMERS}곳만 셈.",
                    reached_at=None,
                    required_customers=FAULT_CUSTOMERS,
                    firmware_rule_applied=False,
                    **base,
                )
            )
            continue
        if fw_reasons and len(customers) < FAULT_CUSTOMERS:
            why = sorted(fw_reasons)[0]
            out.append(
                FaultCandidate(
                    outcome="INSUFFICIENT_EVIDENCE",
                    reason_code=why,
                    reason=(
                        "펌웨어 원인인데 고장 당시 버전을 비교할 수 없어 1곳 예외를 판단하지 못함. "
                        f"일반 기준 고객 {best}/{FAULT_CUSTOMERS}곳."
                    ),
                    reached_at=None,
                    required_customers=FAULT_CUSTOMERS,
                    firmware_rule_applied=False,
                    **base,
                )
            )
            continue
        out.append(
            FaultCandidate(
                outcome="NOT_ELIGIBLE",
                reason_code="BELOW_THRESHOLD",
                reason=f"{WINDOW_DAYS}일 안 서로 다른 고객 {best}/{FAULT_CUSTOMERS}곳.",
                reached_at=None,
                required_customers=FAULT_CUSTOMERS,
                firmware_rule_applied=False,
                **base,
            )
        )
    return out


def document_candidates(
    signals: list[DocumentSignalRecord], *, as_of: datetime
) -> list[DocumentCandidate]:
    """제품·신호·문서 묶음마다 후보 판정 하나. 근거 위치 없는 신호는 세지 않는다."""
    groups: dict[tuple[str, str, str | None], list[DocumentSignalRecord]] = defaultdict(list)
    for signal in signals:
        if signal.known_at <= as_of:
            groups[(signal.product_code, signal.doc_signal, signal.doc_id)].append(signal)
    out: list[DocumentCandidate] = []
    for (product, signal_kind, doc_id), members in sorted(
        groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or "")
    ):
        members.sort(key=lambda s: s.known_at)
        with_evidence = [s for s in members if s.has_evidence_location]
        tickets = []
        seen: set[str] = set()
        for s in with_evidence:
            if s.ticket_id not in seen:
                seen.add(s.ticket_id)
                tickets.append(s)
        required = REPEATED_SPEC_TICKETS if signal_kind == "REPEATED_SPEC" else 1
        evidence = [
            EvidenceRef(ticket_id=s.ticket_id, case_id=None, customer_id=None, known_at=s.known_at)
            for s in tickets
        ]
        common = {
            "product_code": product,
            "doc_signal": signal_kind,
            "doc_id": doc_id,
            "tickets_with_evidence": len(tickets),
            "tickets_without_evidence": len({s.ticket_id for s in members} - seen),
            "required_tickets": required,
            "evidence": evidence,
        }
        if len(tickets) >= required:
            code: ReasonCode = (
                "REPEATED_SPEC_3_TICKETS"
                if signal_kind == "REPEATED_SPEC"
                else "DOCUMENT_ERROR_EVIDENCE"
            )
            out.append(
                DocumentCandidate(
                    outcome="ELIGIBLE",
                    reason_code=code,
                    reason=f"근거 있는 문의 {len(tickets)}/{required}건.",
                    reached_at=tickets[required - 1].known_at,
                    **common,
                )
            )
        elif not tickets and members:
            out.append(
                DocumentCandidate(
                    outcome="INSUFFICIENT_EVIDENCE",
                    reason_code="NO_EVIDENCE_LOCATION",
                    reason=f"신호 {len(members)}건 모두 근거 위치가 없음.",
                    reached_at=None,
                    **common,
                )
            )
        else:
            out.append(
                DocumentCandidate(
                    outcome="NOT_ELIGIBLE",
                    reason_code="BELOW_THRESHOLD",
                    reason=f"근거 있는 문의 {len(tickets)}/{required}건.",
                    reached_at=None,
                    **common,
                )
            )
    return out


def evaluate(
    cases: list[CaseRecord],
    releases: list[FirmwareRelease],
    signals: list[DocumentSignalRecord],
    *,
    as_of: datetime,
    condition_confirmed: dict[tuple[str, str], bool] | None = None,
) -> CandidateReport:
    """고장·문서 후보를 한 번에 판정한다."""
    if as_of.tzinfo is None:
        raise ValueError("as_of needs a time zone")
    return CandidateReport(
        version=CANDIDATE_RULES_VERSION,
        as_of=as_of,
        faults=fault_candidates(
            cases, releases, as_of=as_of, condition_confirmed=condition_confirmed
        ),
        documents=document_candidates(signals, as_of=as_of),
    )
