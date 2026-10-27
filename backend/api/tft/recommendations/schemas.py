"""요청 부서의 추천·경험·가용성 입력. 행위자와 출처는 서버가 정한다."""

from datetime import date
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.api.tft.projects.schemas import CheckStatus, DataOrigin, DerivationKind


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RecommendationCreate(Input):
    candidate_id: UUID
    rationale: str = Field(min_length=1, max_length=4000)


class CheckInput(Input):
    status: CheckStatus = "NEEDS_CHECK"
    basis: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def require_basis(self) -> Self:
        if self.status in {"CONFIRMED", "UNMET"} and not self.basis:
            raise ValueError("확인/미충족 판정에는 근거가 필요합니다.")
        return self


class PeriodInput(CheckInput):
    period_start: date | None = None
    period_end: date | None = None

    @model_validator(mode="after")
    def ordered_period(self) -> Self:
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")
        return self


class ExperienceCreate(PeriodInput):
    summary: str = Field(min_length=1, max_length=4000, description="실제 수행 업무와 경험")
    project_name: str | None = Field(default=None, min_length=1, max_length=500)
    role_title: str | None = Field(default=None, min_length=1, max_length=500)
    evidence_source: str | None = Field(default=None, min_length=1, max_length=2000)


class AvailabilityCreate(PeriodInput):
    allocation_rate: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    constraints_note: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def confirmed_period(self) -> Self:
        if self.status == "CONFIRMED" and (
            self.period_start is None or self.period_end is None or self.allocation_rate is None
        ):
            raise ValueError("가용성 확인에는 대상 기간과 투입률이 필요합니다.")
        return self


class CompetencyCreate(CheckInput):
    competency_id: UUID


class Candidate(BaseModel):
    id: UUID
    organization_id: UUID
    display_name: str
    job_title: str | None
    data_origin: DataOrigin
    derivation_kind: DerivationKind


class CandidateList(BaseModel):
    items: list[Candidate]
    next_cursor: UUID | None


class CreatedRecord(BaseModel):
    id: UUID
    request_version: int
