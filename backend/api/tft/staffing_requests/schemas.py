"""Department staffing request write models (KAN-122).

The read model `StaffingRequest` lives in projects/schemas.py because the project detail
embeds the same object. Issuing has no body: the request is issued as stored.
"""

from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from backend.api.tft.projects.schemas import Participation, StaffingRequest

__all__ = ["StaffingRequest", "StaffingRequestList", "StaffingRequestUpdate", "EDITABLE_FIELDS"]

# Role, project and origin of a request are fixed at creation; only these change in DRAFT.
EDITABLE_FIELDS = ("organization_id", "participation", "due_at", "due_undecided", "message")


class StaffingRequestList(BaseModel):
    items: list[StaffingRequest]


class StaffingRequestUpdate(BaseModel):
    """Edit of a DRAFT request. Omitted means unchanged; `If-Match` carries the version."""

    model_config = ConfigDict(extra="forbid")

    organization_id: UUID | None = None
    participation: Participation | None = None
    due_at: AwareDatetime | None = None
    due_undecided: bool | None = None
    message: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def check_fields(self) -> "StaffingRequestUpdate":
        sent = self.model_fields_set & set(EDITABLE_FIELDS)
        if not sent:
            raise ValueError("At least one editable field is required.")
        # NOT NULL columns cannot be cleared; due_at and message can.
        for name in ("organization_id", "participation", "due_undecided"):
            if name in sent and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null.")
        return self

    def changed_values(self) -> dict[str, Any]:
        return {
            name: getattr(self, name) for name in EDITABLE_FIELDS if name in self.model_fields_set
        }
