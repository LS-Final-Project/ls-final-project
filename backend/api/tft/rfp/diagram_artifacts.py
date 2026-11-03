"""Preserve exact vision inputs independently of candidate topology (KAN-188)."""

import hashlib
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from backend.api.tft.rfp.storage import DocumentStore
from backend.engine.documents.diagrams import DiagramGraph
from backend.engine.documents.vision import VisionPlan, VisionResult


class DiagramAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(pattern=r"^OCR-(?:DOCX-\d{4,}|PDF-P\d{4,})$")
    location: str
    page: int | None
    mime: Literal["application/pdf", "image/png", "image/jpeg", "image/webp"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0, le=8 * 1024 * 1024)
    storage_key: str
    graph: DiagramGraph | None


class DiagramManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    items: list[DiagramAsset] = Field(max_length=16)
    warnings: list[str] = Field(max_length=512)


async def preserve_images(
    store: DocumentStore, document_version_id: UUID, plan: VisionPlan, result: VisionResult
) -> dict[str, Any]:
    """No recompression/resizing; failed DB transactions may leave unreferenced immutable files."""
    observed = {row.source.source_id: row for row in result.items}
    assets = []
    for item in plan.inputs:
        digest = hashlib.sha256(item.data).hexdigest()
        key = f"diagrams/{document_version_id}/{uuid4()}/{digest}"
        observation = observed.get(item.source_id)
        asset = DiagramAsset(
            source_id=item.source_id,
            location=item.location,
            page=item.page,
            mime=item.mime,
            sha256=digest,
            size=len(item.data),
            storage_key=key,
            graph=observation.diagram if observation else None,
        )
        await store.put(key, item.data)
        assets.append(asset)
    warnings = [f"{issue.source_id}: {issue.reason}" for issue in result.skipped]
    # Preserve bounded diagnostics and explicitly report truncation rather than silently dropping it.
    if len(warnings) > 511:
        warnings = warnings[:511] + ["OCR_ADDITIONAL_WARNINGS_OMITTED"]
    return DiagramManifest(items=assets, warnings=warnings).model_dump(mode="json")
