from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemberInput(Input):
    required_role_id: UUID
    recommendation_id: UUID | None = None
    status: Literal["PROPOSED", "NEEDS_REINFORCEMENT", "CONFIRMED"] = "PROPOSED"
    rationale: Text | None = None
    remaining_conditions: Text | None = None
    gap_note: Text | None = None
    next_action_organization_id: UUID | None = None
    follow_up_due_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def gap(self) -> "MemberInput":
        if self.recommendation_id is None and (not self.gap_note or self.status == "CONFIRMED"):
            raise ValueError("공백에는 사유가 필요하며 확정할 수 없습니다.")
        if self.status == "NEEDS_REINFORCEMENT" and not self.remaining_conditions:
            raise ValueError("보완 요청에는 남은 조건이 필요합니다.")
        return self


class CompositionInput(Input):
    members: list[MemberInput] = Field(min_length=1, max_length=200)
    note: Text


class DecisionInput(Input):
    composition_version: int = Field(ge=1)
    reason: Text


class ReopenInput(Input):
    reason: Text


class DecisionResult(BaseModel):
    id: UUID
    project_version: int
    composition_version: int | None
    status: str


class DecisionHistory(BaseModel):
    id: UUID
    action: str
    actor_user_id: UUID
    reason: str
    project_version: int
    composition_version: int | None
    occurred_at: datetime
