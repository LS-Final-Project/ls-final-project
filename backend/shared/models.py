"""Response envelopes shared by the three services."""

from typing import Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field

from backend.shared.settings import ServiceName

DataT = TypeVar("DataT")


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
