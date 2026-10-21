"""Minimal starting contract, not a finalized C/S workflow state model."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class TicketSummary(BaseModel):
    id: UUID
    subject: str
    data_origin: Literal["synthetic", "demo", "real"]


class TicketList(BaseModel):
    items: list[TicketSummary]
    next_cursor: str | None
