"""Minimal starting contract, not a finalized TFT workflow state model."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class ProjectSummary(BaseModel):
    id: UUID
    name: str
    data_origin: Literal["synthetic", "demo", "real"]


class ProjectList(BaseModel):
    items: list[ProjectSummary]
    next_cursor: str | None
