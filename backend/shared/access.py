"""플랫폼 호출자 검증 뒤 업무 사용자 정보를 읽는다. 운영 중계는 기본 비활성이다."""

import json
from typing import Literal, Self
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from backend.shared.service_auth import CALLER_HEADER

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
    """호출자 인증 이후에만 전달된 사용자 정보를 읽고 업무 조회 전에 검증한다.

    Args:
        request: 사용자 맥락과 별도 플랫폼 호출자 토큰을 포함한 요청.

    Returns:
        Principal: 호출자와 등록 조건을 통과한 업무 사용자 정보.

    Raises:
        HTTPException: 비활성 운영 경로는 404, 맥락·호출자 위반은 401, 서명키 장애는 503.
    """
    settings = request.app.state.settings
    if settings.business_relay_enabled:
        tokens = request.headers.getlist(CALLER_HEADER)
        if len(tokens) != 1:
            raise HTTPException(401)
        request.app.state.platform_caller.verify(tokens[0])
    elif settings.environment not in {"local", "test"}:
        raise HTTPException(404)
    values = request.headers.getlist(PRINCIPAL_HEADER)
    if len(values) != 1 or len(values[0].encode()) > 8192:
        raise HTTPException(401)
    try:
        return Principal.model_validate(json.loads(values[0]))
    except (ValueError, ValidationError):
        raise HTTPException(401) from None


def principal_header(principal: Principal) -> dict[str, str]:
    """Header a local caller or test sends for the given user."""
    return {PRINCIPAL_HEADER: principal.model_dump_json()}
