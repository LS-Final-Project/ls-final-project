"""Response envelopes shared by the three services."""

from typing import Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field

from backend.shared.settings import ServiceName

DataT = TypeVar("DataT")
# Customer ID convention (KAN-178): ASCII alphanumeric first, then alphanumerics, ".", "_" or
# "-", 1-64 characters. The platform relay matches it on paths and C/S validates input with it;
# docs/openapi/business-relay-routes.json keeps the same value for the web relay test.
CUSTOMER_ID = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"


class ResponseMeta(BaseModel):
    request_id: UUID


class SuccessResponse(BaseModel, Generic[DataT]):
    data: DataT
    meta: ResponseMeta


class HealthData(BaseModel):
    status: Literal["ok"] = "ok"
    service: ServiceName


class ErrorData(BaseModel):
    code: str
    message: str
    retryable: bool = False
    details: list[dict[str, str]] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorData
    meta: ResponseMeta
