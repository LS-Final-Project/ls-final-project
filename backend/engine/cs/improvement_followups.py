"""재요청·조치 후 재발 판정(KAN-117). DB·모델 호출 없이 테스트한다.

계약: contracts/cs/improvement-followups.md. 판정은 후보까지이고 새 회차는 사람이 연다.

- 재요청(조치 전): 가장 최근 회차가 열린 뒤 알려진 같은 묶음의 해결 기록 3건마다 후보(기록 수, 2026-10-29 결정).
- 재발(조치 뒤): 조치가 적용된 장비에서 1건이라도. 적용 안 된 장비는 따로 세고, 펌웨어면 업데이트 안내 대상.
- 미래 정보 차단: `as_of` 뒤에 알려진 기록·기록된 조치·열린 회차는 없는 것으로 본다.
- 조치 전/후는 고장 발생의 대리값인 문의 접수 시각으로 나눈다(적재 시각이 아님). 조치 적용일은 현업 달력 날짜라
  접수 시각도 Asia/Seoul 날짜로 바꿔 비교하고, 같은 날이면 미확인이다.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from backend.engine.cs.improvement_candidates import parse_version

LOCAL = ZoneInfo("Asia/Seoul")
THRESHOLD = 3
# 조치 뒤 규칙만 쓰는 요청 상태(권한 문서 2.2). 그 밖의 전달된 상태는 조치 전 규칙이다.
AFTER_ACTION = frozenset({"ACTION_TAKEN", "FOLLOW_UP", "COMPLETED"})
NOT_SENT = frozenset({"DRAFT", "REVIEWED"})

ScopeKind = Literal["FIRMWARE_VERSION", "SHIPPED_FROM", "LOT", "OTHER"]
RecordClass = Literal[
    "NEW_SINCE_SENT",
    "RECURRENCE",
    "PRE_ACTION_UNIT",
    "APPLICATION_UNKNOWN",
    "BEFORE_ACTION_DATE",
    "IN_EARLIER_ROUND",
]
Decision = Literal["REQUEST_AGAIN", "RECURRENCE", "NONE"]
Phase = Literal["NOT_SENT", "BEFORE_ACTION", "AFTER_ACTION"]
NextAction = Literal["OPEN_ROUND_REQUEST_AGAIN", "OPEN_ROUND_RECURRENCE", "NONE"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Record(Strict):
    """같은 묶음(제품 + 고장 ID)의 해결 기록 하나. 원문은 받지 않는다."""

    case_id: str
    ticket_id: str
    customer_id: str | None
    known_at: datetime = Field(description="적재 시각. 미래 정보 차단과 회차 기준점에 쓴다.")
    received_at: datetime = Field(description="문의 접수 시각. 조치 전/후 구분(발생 대리값).")
    fw_version_at_issue: str | None = None
    shipped_on: date | None = Field(default=None, description="ERP 출하일(추가 제공 가정).")
    lot: str | None = Field(default=None, description="ERP 로트(추가 제공 가정).")


class Action(Strict):
    """부서의 개선 조치. 고객 한 명의 수리가 아니다."""

    round: int
    dept_code: str
    action_date: date = Field(description="부서가 기록한 실제 적용일(현업 달력, Asia/Seoul).")
    recorded_at: datetime
    scope_kind: ScopeKind = "OTHER"
    scope_value: str | None = None


class Round(Strict):
    round: int
    opened_at: datetime


class RequestState(Strict):
    status: str
    rounds: list[Round]
    actions: list[Action]


class RecordView(Strict):
    ticket_id: str
    case_id: str
    known_at: datetime
    customer_id: str | None
    record_class: RecordClass = Field(serialization_alias="class")
    reason_code: str | None
    basis: dict[str, str] | None
    action_dept: str | None = Field(default=None, description="판정에 쓴 조치를 등록한 부서.")


class Baseline(Strict):
    last_sent_at: datetime | None
    round: int
    action_dates: list[date]


class Counts(Strict):
    new_since_sent: int
    threshold: int
    candidate_index: int
    recurrence: int
    pre_action_unit: int
    application_unknown: int


class Signal(Strict):
    as_of: datetime
    phase: Phase
    decision: Decision
    reason_code: str
    baseline: Baseline
    counts: Counts
    records: list[RecordView]
    update_notice_customers: list[str]
    next_action: NextAction


def local_date(moment: datetime) -> date:
    if moment.tzinfo is None:
        raise ValueError("Times must carry a time zone")
    return moment.astimezone(LOCAL).date()


def applied(action: Action, record: Record) -> tuple[RecordClass, str, dict[str, str]]:
    """조치 적용 범위로 그 장비에 조치가 적용됐는지 나눈다(적용일 뒤에 발생한 기록)."""
    value = action.scope_value or ""
    if action.scope_kind == "FIRMWARE_VERSION":
        have, want = parse_version(record.fw_version_at_issue), parse_version(value)
        basis = {"fw_version": record.fw_version_at_issue or "", "action_version": value}
        if have is None or want is None:
            return "APPLICATION_UNKNOWN", "UNKNOWN_FW_VERSION", basis
        if have >= want:
            return "RECURRENCE", "APPLIED_UNIT_RECURRENCE", basis
        return "PRE_ACTION_UNIT", "PRE_ACTION_UNIT_FIRMWARE", basis
    if action.scope_kind == "SHIPPED_FROM":
        basis = {"shipped_on": str(record.shipped_on or ""), "shipped_from": value}
        if record.shipped_on is None:
            return "APPLICATION_UNKNOWN", "UNKNOWN_SHIP_DATE", basis
        if record.shipped_on >= date.fromisoformat(value):
            return "RECURRENCE", "APPLIED_UNIT_RECURRENCE", basis
        return "PRE_ACTION_UNIT", "PRE_ACTION_UNIT_SHIPMENT", basis
    if action.scope_kind == "LOT":
        lots = {lot.strip() for lot in value.split(",") if lot.strip()}
        basis = {"lot": record.lot or "", "lots": ",".join(sorted(lots))}
        if record.lot is None:
            return "APPLICATION_UNKNOWN", "UNKNOWN_LOT", basis
        if record.lot in lots:
            return "RECURRENCE", "APPLIED_UNIT_RECURRENCE", basis
        return "PRE_ACTION_UNIT", "PRE_ACTION_UNIT_LOT", basis
    # 자유 문장 범위(OTHER)는 프로그램이 판단하지 않는다.
    return "APPLICATION_UNKNOWN", "UNKNOWN_SCOPE", {}


def classify(
    record: Record, actions: Sequence[Action]
) -> tuple[RecordClass, str | None, dict[str, str] | None, str | None]:
    """조치가 있으면 적용일이 가장 늦은 조치부터 본다. 접수일이 그 조치보다 뒤면 그 조치로 판단한다."""
    if not actions:
        return "NEW_SINCE_SENT", None, None, None
    occurred = local_date(record.received_at)
    for action in sorted(actions, key=lambda a: (a.action_date, a.recorded_at), reverse=True):
        if occurred < action.action_date:
            continue
        if occurred == action.action_date:
            # 같은 날은 조치 전인지 뒤인지 알 수 없다. 재발로 세지 않는다.
            basis = {"received_on": str(occurred), "action_date": str(action.action_date)}
            return "APPLICATION_UNKNOWN", "SAME_DAY_AS_ACTION", basis, action.dept_code
        record_class, reason, basis = applied(action, record)
        return record_class, reason, basis, action.dept_code
    return "BEFORE_ACTION_DATE", None, None, None


def evaluate(state: RequestState, records: Sequence[Record], as_of: datetime) -> Signal:
    """요청 하나의 재요청·재발 판정. 요청 상태를 바꾸지 않는다.

    Args:
        state: 요청 상태(2.2.1 요약), 반려 종료 여부, 회차(열린 시각), 등록된 조치.
        records: 같은 묶음의 해결 기록 전부. `as_of` 뒤 기록은 여기서 걸러낸다.
        as_of: 판정 시점(시간대 필수).

    Returns:
        Signal: 단계·판정·사유·기준 시점·건수·기록별 분류·다음 할 일.
    """
    local_date(as_of)
    rounds = [r for r in state.rounds if r.opened_at <= as_of]
    actions = [
        a for a in state.actions if a.recorded_at <= as_of and a.action_date <= local_date(as_of)
    ]
    last = max(rounds, key=lambda r: r.opened_at) if rounds else None
    baseline = Baseline(
        last_sent_at=last.opened_at if last else None,
        round=last.round if last else 0,
        action_dates=sorted({a.action_date for a in actions}),
    )
    if state.status in NOT_SENT or last is None:
        phase: Phase = "NOT_SENT"
    elif state.status in AFTER_ACTION:
        phase = "AFTER_ACTION"
    else:
        phase = "BEFORE_ACTION"
    views: list[RecordView] = []
    for record in sorted(records, key=lambda r: (r.known_at, r.case_id)):
        if record.known_at > as_of:
            continue
        if last is None or record.known_at <= last.opened_at:
            # 이미 이전 회차(또는 첫 전달)에서 다룬 기록. 새 후보의 근거가 아니다.
            record_class: RecordClass = "IN_EARLIER_ROUND"
            reason, basis, dept = None, None, None
        else:
            record_class, reason, basis, dept = classify(record, actions)
        views.append(
            RecordView(
                ticket_id=record.ticket_id,
                case_id=record.case_id,
                known_at=record.known_at,
                customer_id=record.customer_id,
                record_class=record_class,
                reason_code=reason,
                basis=basis,
                action_dept=dept,
            )
        )

    def count(kind: RecordClass) -> int:
        return sum(1 for v in views if v.record_class == kind)

    recurrence = count("RECURRENCE")
    # 재요청은 조치 전 단계에서만 센다. 재발로 분류된 기록은 넣지 않는다.
    new_since = (
        sum(1 for v in views if v.record_class not in ("IN_EARLIER_ROUND", "RECURRENCE"))
        if phase == "BEFORE_ACTION"
        else 0
    )
    index = new_since // THRESHOLD
    if phase == "NOT_SENT":
        decision: Decision = "NONE"
        reason_code, next_action = "NOT_SENT", "NONE"
    elif recurrence:
        decision, reason_code, next_action = (
            "RECURRENCE",
            "APPLIED_UNIT_RECURRENCE",
            "OPEN_ROUND_RECURRENCE",
        )
    elif phase == "BEFORE_ACTION" and index >= 1:
        decision, reason_code, next_action = (
            "REQUEST_AGAIN",
            "NEW_RECORDS_3",
            "OPEN_ROUND_REQUEST_AGAIN",
        )
    elif phase == "BEFORE_ACTION":
        decision, reason_code, next_action = "NONE", "BELOW_3", "NONE"
    else:
        decision, reason_code, next_action = "NONE", "NO_RECURRENCE", "NONE"
    notice = sorted(
        {
            v.customer_id
            for v in views
            if v.reason_code == "PRE_ACTION_UNIT_FIRMWARE" and v.customer_id is not None
        }
    )
    return Signal(
        as_of=as_of,
        phase=phase,
        decision=decision,
        reason_code=reason_code,
        baseline=baseline,
        counts=Counts(
            new_since_sent=new_since,
            threshold=THRESHOLD,
            candidate_index=index,
            recurrence=recurrence,
            pre_action_unit=count("PRE_ACTION_UNIT"),
            application_unknown=count("APPLICATION_UNKNOWN"),
        ),
        records=views,
        update_notice_customers=notice,
        next_action=next_action,
    )
