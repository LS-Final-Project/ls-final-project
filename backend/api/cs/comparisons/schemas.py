"""조건 비교 결과 응답(contracts/cs/condition-comparison.md).

코드 목록은 코드 안에서 여기 한 곳에만 둔다. 마이그레이션의 CHECK 목록은 이 값과 같아야 한다.
"""

from datetime import date, datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, model_validator

from backend.api.cs.improvements.schemas import ConditionStatus, DocSignal
from backend.api.cs.tickets.schemas import DataOrigin, DerivationKind

# info_field_codebook의 사용 조건 항목. provided_info와 같은 코드를 쓴다.
ConditionCode = Literal[
    "application",
    "site_condition",
    "operating_condition",
    "load_profile",
    "current_fw",
    "install_date",
    "string_config",
    "battery_age",
]
CONDITION_LABELS: dict[str, str] = {
    "application": "적용 조건(전압·부하·운전 주기)",
    "site_condition": "현장 조건(주변 온도·고도·습도)",
    "operating_condition": "고장 시 운전 조건(부하·속도·온도)",
    "load_profile": "부하 전류·온도 기록",
    "current_fw": "현재 펌웨어 판",
    "install_date": "설치·시운전일",
    "string_config": "PV 스트링 구성",
    "battery_age": "배터리 설치일·사용 기간",
}
CauseType = Literal["SITE_CONDITION", "HARDWARE", "SETTING", "WEAR_PART", "FIRMWARE"]
Judgement = Literal["CONFIRMED", "SUSPECTED", "UNCONFIRMED"]
Finding = Literal["DIFFERS", "SAME"]
UnconfirmedReason = Literal[
    "NO_EVIDENCE", "EVIDENCE_REMOVED", "NOT_YET_KNOWN", "VALUE_NOT_PROVIDED"
]
MissingReason = Literal["NOT_PROVIDED", "HIDDEN_BY_SCOPE", "MASKING_FAILED"]
ProvenanceKind = Literal["ai_draft", "human_input", "system_record"]


def check_reason(value: object, reason: object, name: str) -> None:
    """값이 null일 때만 사유가 있는지 확인한다.

    Args:
        value: 마스킹된 값 또는 요약.
        reason: 값이 없는 사유.
        name: 오류 메시지에 쓸 필드 이름.

    Returns:
        None: 규칙을 어기면 ValueError를 낸다.
    """
    if (value is None) != (reason is not None):
        raise ValueError(f"{name} is null exactly when it has a missing reason")


class Side(BaseModel):
    """조건의 한쪽(기준 또는 대상). 마스킹된 값, 또는 null과 사유."""

    masked_value: str | None
    missing_reason: MissingReason | None

    @model_validator(mode="after")
    def reason_for_null(self) -> Self:
        """값과 사유 중 정확히 하나만 있는지 확인한다.

        Returns:
            Self: 검사를 통과한 모델.
        """
        check_reason(self.masked_value, self.missing_reason, "masked_value")
        return self


class Judged(BaseModel):
    """조건 항목과 원인이 함께 쓰는 판정 필드."""

    judgement: Judgement
    unconfirmed_reason: UnconfirmedReason | None
    masked_summary: str | None
    summary_missing_reason: MissingReason | None
    provenance_kind: ProvenanceKind
    decided_by_name: str | None
    decided_at: datetime
    evidence_ids: list[str]
    hidden_evidence_count: int

    @model_validator(mode="after")
    def judged_rules(self) -> Self:
        """미확인 사유·요약 사유·판정자 표시명 규칙을 확인한다.

        Returns:
            Self: 검사를 통과한 모델.
        """
        if (self.unconfirmed_reason is None) != (self.judgement != "UNCONFIRMED"):
            raise ValueError("unconfirmed_reason is set exactly when UNCONFIRMED")
        check_reason(self.masked_summary, self.summary_missing_reason, "masked_summary")
        if self.decided_by_name is not None and self.provenance_kind != "human_input":
            raise ValueError("only a person's judgement names who decided")
        return self


class ConditionItem(Judged):
    """사용 조건 항목 하나. finding은 미확인일 때만 null이다."""

    item_code: ConditionCode
    item_label: str
    finding: Finding | None
    baseline: Side
    target: Side

    @model_validator(mode="after")
    def finding_rules(self) -> Self:
        """finding이 미확인일 때만 null인지 확인한다.

        Returns:
            Self: 검사를 통과한 모델.
        """
        if (self.finding is None) != (self.judgement == "UNCONFIRMED"):
            raise ValueError("finding is null exactly when UNCONFIRMED")
        return self


class CauseItem(Judged):
    """원인 판정. finding·기준·대상 값이 없다."""

    cause_type: CauseType


class DocumentSignal(BaseModel):
    """문서 문의 신호. 원인 판정이 아니며 판정을 갖지 않는다."""

    doc_signal: DocSignal
    provenance_kind: ProvenanceKind
    evidence_ids: list[str]
    hidden_evidence_count: int


class ComparisonEvidence(BaseModel):
    """근거 참조와 근거 자신의 출처. 본문은 담지 않는다."""

    evidence_id: str
    case_id: UUID | None
    ticket_id: UUID | None
    evidence_location: dict[str, Any]
    data_origin: DataOrigin
    derivation_kind: DerivationKind
    known_at: datetime


class JudgementCounts(BaseModel):
    """조건 항목의 판정별 건수. 원인은 세지 않는다."""

    confirmed: int
    suspected: int
    unconfirmed: int


class ConditionComparison(BaseModel):
    """비교 결과 한 판본."""

    comparison_id: UUID
    product_code: str
    fault_id: str
    improvement_request_id: UUID | None
    request_version: int | None
    version: int
    latest_version: int
    is_latest: bool
    data_origin: DataOrigin
    derivation_kind: Literal["DERIVED"]
    known_at: datetime
    computed_condition_status: ConditionStatus
    # 판정 기준 요청서의 현재 값. 요청서가 없으면 null.
    condition_summary: str | None
    fw_fixed_version: str | None
    fw_released_on: date | None
    summary: JudgementCounts
    conditions: list[ConditionItem]
    causes: list[CauseItem]
    document_signals: list[DocumentSignal]
    evidence: list[ComparisonEvidence]
    hidden_evidence_count: int
