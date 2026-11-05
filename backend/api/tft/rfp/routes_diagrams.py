"""Authorized immutable diagram assets and explicit tag-only requirement candidates."""

import re
from typing import Any
from uuid import UUID

from fastapi import Request, Response
from psycopg import AsyncConnection
from pydantic import ValidationError

from backend.api.tft.rfp import diagram_repository, repository, storage
from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.diagram_artifacts import DiagramManifest
from backend.api.tft.rfp.routes_questions import visible_project
from backend.shared.access import Principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError

Row = dict[str, Any]


async def authorized_manifest(
    conn: AsyncConnection[Row], principal: Principal, document_id: UUID, version_id: UUID
) -> tuple[Row, DiagramManifest | None]:
    row = await repository.document_version_row(conn, version_id)
    if row is None or row["document_id"] != document_id:
        raise ApplicationError(404, "TFT_DOCUMENT_NOT_FOUND", "The document was not found.")
    await visible_project(
        conn, principal, row["project_id"], "TFT_DOCUMENT_NOT_FOUND", "The document was not found."
    )
    stored = await diagram_repository.read_diagrams(conn, version_id)
    if stored is None:
        return row, None
    try:
        if stored["source_sha256"] != row["sha256"]:
            raise ValueError("source mismatch")
        manifest = DiagramManifest.model_validate(stored["payload"])
    except (ValidationError, ValueError):
        raise ApplicationError(
            503, "TFT_DIAGRAM_UNAVAILABLE", "Diagram data is unavailable."
        ) from None
    return row, manifest


def tag_links(item: Any, requirements: list[Row]) -> list[s.DiagramRequirementLink]:
    """Exact printed tag token only; no fuzzy equipment identity or feasibility inference."""
    links: list[s.DiagramRequirementLink] = []
    if item.graph is None:
        return links
    for node in item.graph.nodes:
        tag = node.tag
        if not tag or not tag.strip():
            continue
        pattern = re.compile(r"(?<![\w.-])" + re.escape(tag) + r"(?![\w.-])")
        for row in requirements:
            if pattern.search(row["equipment"] or ""):
                links.append(
                    s.DiagramRequirementLink(
                        node_id=node.id,
                        requirement_id=row["requirement_id"],
                        requirement_version=row["version"],
                        code=row["code"],
                        equipment=row["equipment"],
                    )
                )
    return links


async def list_diagrams(
    request: Request, principal: Principal, document_id: UUID, version_id: UUID
) -> s.DiagramList:
    async with connection(request.app.state.settings) as conn:
        row, manifest = await authorized_manifest(conn, principal, document_id, version_id)
        requirements: list[Row] = []
        if manifest and any(item.graph and item.graph.nodes for item in manifest.items):
            cursor = await conn.execute(
                "SELECT r.id AS requirement_id,r.code,v.version,v.equipment "
                "FROM tft.requirements r JOIN tft.requirement_versions v "
                "ON v.requirement_id=r.id AND v.version=r.latest_version WHERE r.project_id=%s "
                "ORDER BY r.code",
                (row["project_id"],),
            )
            requirements = await cursor.fetchall()
    return s.DiagramList(
        document_id=document_id,
        document_version_id=version_id,
        processing_status=(
            "NOT_ANALYZED" if manifest is None else "PARTIAL" if manifest.warnings else "ANALYZED"
        ),
        warnings=manifest.warnings if manifest else [],
        items=[
            s.DiagramItem(
                **item.model_dump(exclude={"storage_key"}),
                requirement_links=tag_links(item, requirements),
            )
            for item in manifest.items
        ]
        if manifest
        else [],
    )


async def image(
    request: Request, principal: Principal, document_id: UUID, version_id: UUID, source_id: str
) -> Response:
    async with connection(request.app.state.settings) as conn:
        _, manifest = await authorized_manifest(conn, principal, document_id, version_id)
    asset = (
        next((item for item in manifest.items if item.source_id == source_id), None)
        if manifest
        else None
    )
    if asset is None:
        raise ApplicationError(404, "TFT_DIAGRAM_NOT_FOUND", "The diagram was not found.")
    store = storage.document_store(storage.DocumentStorageSettings())
    try:
        if store is None:
            raise storage.DocumentUnavailable("missing")
        data = await storage.read_document(store, asset.storage_key, asset.sha256, asset.size)
    except storage.DocumentUnavailable:
        raise ApplicationError(
            503, "TFT_DIAGRAM_UNAVAILABLE", "Diagram data is unavailable."
        ) from None
    extension = {
        "application/pdf": "pdf",
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
    }[asset.mime]
    return Response(
        data,
        media_type=asset.mime,
        headers={
            "Content-Disposition": f'attachment; filename="{asset.source_id}.{extension}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
