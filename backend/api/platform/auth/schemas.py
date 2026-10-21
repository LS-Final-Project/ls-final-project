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

    @model_validator(mode="after")
    def require_department(self) -> Self:
        if "dept_staff" in self.roles and not self.dept:
            raise ValueError("Department staff requires a department")
        return self


class DemoAccounts(BaseModel):
    items: list[Identity]


class SelectAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
