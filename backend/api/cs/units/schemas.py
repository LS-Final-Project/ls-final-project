"""Installed units and ticket-to-unit links (C/S proposal 4.3, 6.1).

Units are assumed install-base data. A ticket may have several candidate units but at most
one confirmed unit, and only a confirmed unit carries history between tickets.
"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.api.cs.tickets.schemas import (
    Channel,
    DataOrigin,
    DerivationKind,
    ResolutionCase,
)

LinkStatus = Literal["CANDIDATE", "CONFIRMED", "REJECTED"]
LinkBasis = Literal["SERIAL_PROVIDED", "CUSTOMER_CONFIRMED", "AGENT_REVIEW"]
RepeatType = Literal["SAME_UNIT", "REPLACEMENT", "MULTIPLE_UNITS", "CONFLICT", "DIFFERENT_UNIT"]


class InstalledUnit(BaseModel):
    id: UUID
    external_id: str | None
    data_origin: DataOrigin
    derivation_kind: DerivationKind
    serial_no: str = Field(description="Virtual serial (VSN-...) in demo data.")
    customer_external_id: str
    product_code: str
    site_code: str | None
    commissioning_date: date | None
    commissioning_status: Literal["CONFIRMED", "PLANNED", "UNKNOWN"]
    ship_date: date | None
    lot_no: str | None
    fw_version_last: str | None
    unit_status: Literal["ACTIVE", "REPLACED", "RETIRED"]
    replaced_by_unit_id: UUID | None


class UnitLink(BaseModel):
    id: UUID
    version: int
    link_status: LinkStatus
    link_basis: LinkBasis
    repeat_type: RepeatType | None
    decided_by: UUID | None
    decided_at: datetime | None


class TicketUnit(BaseModel):
    unit: InstalledUnit
    matches_ticket: bool = Field(
        description="Same customer and product as the ticket. Only matching units can be "
        "a candidate or confirmed."
    )
    link: UnitLink | None


class TicketUnitList(BaseModel):
    items: list[TicketUnit]


class UnitLinkWrite(BaseModel):
    """Sets the link between the ticket and one unit. `version` is null for a new link."""

    model_config = ConfigDict(extra="forbid")

    version: int | None = Field(default=None, ge=1)
    link_status: LinkStatus
    link_basis: LinkBasis
    repeat_type: RepeatType | None = None

    @model_validator(mode="after")
    def check_repeat_type(self) -> "UnitLinkWrite":
        # A different unit is a rejection, never a confirmed link that carries history.
        if self.link_status == "CONFIRMED" and self.repeat_type == "DIFFERENT_UNIT":
            raise ValueError("A confirmed unit cannot be a different unit.")
        return self


class HistoryTicket(BaseModel):
    id: UUID
    external_id: str | None
    received_at: datetime
    channel: Channel
    subject: str | None
    product_code: str
    fw_version_reported: str | None
    error_code_reported: str | None
    unit_id: UUID
    serial_no: str
    relation: Literal["SAME_UNIT", "REPLACED_UNIT"] = Field(
        description="REPLACED_UNIT: the ticket was about a unit this one replaced. Values of "
        "that earlier unit (commissioning, shipping) are not copied to this unit."
    )
    resolution_case: ResolutionCase | None


class UnitHistory(BaseModel):
    status: Literal["CONFIRMED", "NO_CONFIRMED_UNIT"] = Field(
        description="Without a confirmed unit no earlier ticket is returned, even for the "
        "same customer and product."
    )
    unit: InstalledUnit | None
    items: list[HistoryTicket] = Field(
        description="Earlier tickets confirmed on the same unit or a unit it replaced, "
        "received before this ticket. Newest first."
    )
