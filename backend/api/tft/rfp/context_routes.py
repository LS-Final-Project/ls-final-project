"""Authenticated immutable context reads and reasoned human corrections."""

from uuid import UUID

from fastapi import HTTPException, Request

from backend.api.tft.projects.access import can_edit_project, manages_project
from backend.api.tft.rfp import commands
from backend.api.tft.rfp import context_repository as db
from backend.api.tft.rfp.context_schemas import ContextCorrection, ContextResponse
from backend.api.tft.rfp.routes_questions import visible_project
from backend.shared.access import Principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError


async def get_context(
    request: Request, principal: Principal, project_id: UUID, version: int | None = None
) -> ContextResponse:
    async with connection(request.app.state.settings) as conn:
        await visible_project(
            conn, principal, project_id, "TFT_PROJECT_NOT_FOUND", "The project was not found."
        )
        return await db.load_context(conn, project_id, version)


async def correct_context(
    request: Request, principal: Principal, project_id: UUID, payload: ContextCorrection, key: str
) -> ContextResponse:
    """This edits grouping candidates, never requirement judgement, approval or source text."""
    if not key.strip() or len(key) > 200:
        raise ApplicationError(422, "VALIDATION_ERROR", "A bounded idempotency key is required.")
    async with connection(request.app.state.settings) as conn:
        project = await visible_project(
            conn, principal, project_id, "TFT_PROJECT_NOT_FOUND", "The project was not found."
        )
        if not (can_edit_project(principal, project) or manages_project(principal, project_id)):
            raise HTTPException(403)
        await db.lock_source(conn, project_id)
        digest = commands.request_hash({"project_id": project_id}, payload.model_dump(mode="json"))
        cursor = await conn.execute(
            "SELECT * FROM tft.context_versions WHERE project_id=%s AND created_by=%s AND request_key=%s",
            (project_id, principal.user_id, key),
        )
        previous = await cursor.fetchone()
        source = await db.load_source(conn, project_id)
        if previous:
            if previous["request_hash"] != digest:
                raise ApplicationError(
                    409, "IDEMPOTENCY_CONFLICT", "The key was used for another correction."
                )
            return db.response(project_id, previous, source)
        head = await db.latest_row(conn, project_id)
        if head is None or head["version"] != payload.expected_version:
            raise ApplicationError(
                409, "VERSION_CONFLICT", "Context version changed. Reload before editing."
            )
        if (
            source.fingerprint != payload.expected_source_fingerprint
            or head["source_fingerprint"] != source.fingerprint
        ):
            raise ApplicationError(
                409,
                "TFT_CONTEXT_SOURCE_CHANGED",
                "Source requirements changed. Run context analysis again.",
            )
        result = db.checked_result(payload.result, source, human=True)
        row = await db.append(
            conn,
            project_id,
            principal.user_id,
            source,
            result,
            reason=payload.reason,
            request_key=key,
            request_hash=digest,
        )
        await commands.audit(
            conn,
            project_id=project_id,
            entity="project",
            entity_id=project_id,
            action="context.edit",
            actor=principal.user_id,
            detail={
                "version": row["version"],
                "previous_version": head["version"],
                "reason": payload.reason,
                "source_fingerprint": source.fingerprint,
            },
        )
        return db.response(project_id, row, source)
