"""Identity DTOs. Opaque sessions contain no client-controlled roles."""

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Role = Literal["cs_agent", "dept_staff", "manager", "sales"]


class Identity(BaseModel):
    id: UUID
    tid: UUID
    oid: UUID
    email: str
    display_name: str
    dept: str | None
    roles: list[Role] = Field(min_length=1)
    # Departments and TFT projects a manager may see in detail (permissions.md 1.2).
    managed_departments: list[str] = Field(default_factory=list)
    managed_projects: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_department(self) -> Self:
        if "dept_staff" in self.roles and not self.dept:
            raise ValueError("Department staff requires a department")
        if "manager" not in self.roles and (self.managed_departments or self.managed_projects):
            raise ValueError("Only a manager has managed departments or projects")
        return self


class DemoAccounts(BaseModel):
    items: list[Identity]


class SelectAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
