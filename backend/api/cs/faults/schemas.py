"""Fault catalog view (C/S proposal ④ transfer hints and ⑧ resolution case fault ID)."""

from pydantic import BaseModel, Field


class FaultEntry(BaseModel):
    fault_id: str = Field(
        pattern=r"^FLT-[0-9]{3}$", description="Fault ID a resolution case records."
    )
    family: str = Field(description="Product family.")
    applies_to: list[str] = Field(description="Product codes this fault applies to.")
    error_code: str | None = Field(description="Error code shown by the product, if any.")
    symptom: str
    root_cause: str = Field(
        description="Typical cause in the catalog. Not the cause of a given ticket."
    )
    diagnosis: str
    action: str = Field(description="Typical action in the catalog.")
    severity_hint: str
    needs_onsite: bool = Field(description="The catalog marks this fault as needing a site visit.")
    needs_rma: bool = Field(description="The catalog marks this fault as needing an RMA.")
    replacement_part: str | None
    fw_fix_version: str | None = Field(description="Firmware version that fixes the fault, if any.")
    typical_resolution_hours: int


class FaultList(BaseModel):
    items: list[FaultEntry]
    source: str = Field(
        description="Where the list comes from: the provided ref/fault_catalog.csv, unmodified."
    )
