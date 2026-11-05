"""Read-only source references and immutable context review HTTP contracts."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from backend.engine.rfp.context_models import ContextRequirement, ContextResult

Fingerprint = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    requirement_id: UUID
    version: int = Field(ge=1)
    code: str
    document_id: UUID
    document_version_id: UUID
    clause_id: UUID | None
    page: int | None
    quote: str


class ContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: UUID
    version: int | None = None
    status: Literal["NOT_ANALYZED", "CURRENT", "STALE"]
    source_fingerprint: Fingerprint | None = None
    current_source_fingerprint: Fingerprint
    provenance: Literal["ai_draft", "human_input"] | None = None
    created_at: datetime | None = None
    created_by: UUID | None = None
    reason: str | None = None
    result: ContextResult | None = None
    requirements: list[ContextRequirement]
    source_refs: list[SourceReference]


class ContextCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1, strict=True)
    expected_source_fingerprint: Fingerprint
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    result: ContextResult
