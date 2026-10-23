"""The calling user as the C/S and TFT services see it (contracts/platform/permissions.md 1.4).

The platform verifies the session and relays this context. That relay does not exist
yet, so services read it only in local and test and stay closed in production.
"""

import json
from typing import Literal, Self
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Role = Literal["cs_agent", "dept_staff", "manager", "sales"]

PRINCIPAL_HEADER = "X-Team4-Principal"
# Department code of the C/S department in identity.departments.
CS_DEPARTMENT = "GCS_L1"


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: UUID
    roles: frozenset[Role] = Field(min_length=1)
    dept: str | None = None
    managed_departments: frozenset[str] = frozenset()
    managed_projects: frozenset[UUID] = frozenset()

    @model_validator(mode="after")
    def check_role_attributes(self) -> Self:
        if "dept_staff" in self.roles and not self.dept:
            raise ValueError("Department staff requires a department")
        if "manager" not in self.roles and (self.managed_departments or self.managed_projects):
            raise ValueError("Only a manager has managed departments or projects")
        return self

    def has(self, *roles: Role) -> bool:
        return not self.roles.isdisjoint(roles)


def current_principal(request: Request) -> Principal:
    """FastAPI dependency: the calling user, checked before any data access (1.3 step 1)."""
    if request.app.state.settings.environment not in {"local", "test"}:
        # No authenticated platform relay yet. Never trust a caller-supplied header here.
        raise HTTPException(404)
    raw = request.headers.get(PRINCIPAL_HEADER)
    if raw is None:
        raise HTTPException(401)
    try:
        return Principal.model_validate(json.loads(raw))
    except (ValueError, ValidationError):
        raise HTTPException(401) from None


def principal_header(principal: Principal) -> dict[str, str]:
    """Header a local caller or test sends for the given user."""
    return {PRINCIPAL_HEADER: principal.model_dump_json()}
