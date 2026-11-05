"""경로 25~29: 초안 판본·사람 편집·검토본·DOCX 내보내기(KAN-188)."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, Request, Response
from psycopg import AsyncConnection

from backend.api.tft.projects.access import can_edit_project
from backend.api.tft.projects.repository import integration_state
from backend.api.tft.rfp import (
    commands,
    draft_exports,
    draft_rewrite,
    drafts,
    jobs,
    repository,
    storage,
)
from backend.api.tft.rfp import draft_repository as db
from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.counts import count_unresolved_mandatory, is_unresolved_mandatory
from backend.api.tft.rfp.integration import integration_body
from backend.api.tft.rfp.routes_questions import visible_project
from backend.shared.access import Principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError


async def visible(
    conn: AsyncConnection[dict], principal: Principal, project_id: UUID, *, write: bool = False
) -> dict:
    """조회는 기존 프로젝트 범위, 쓰기는 담당 sales·책임자에 한정한다."""
    project = await visible_project(
        conn, principal, project_id, "TFT_PROJECT_NOT_FOUND", "The project was not found."
    )
    if write and not can_edit_project(principal, project):
        raise HTTPException(403)
    return project


async def get_draft(
    request: Request, principal: Principal, project_id: UUID, version: int | None
) -> s.Draft:
    """현재 또는 지정 판본을 조회한다. 범위 밖과 없는 프로젝트는 같은 404다."""
    async with connection(request.app.state.settings) as conn:
        await visible(conn, principal, project_id)
        result = await db.load(conn, project_id, version)
        if result is None:
            raise ApplicationError(404, "TFT_DRAFT_NOT_FOUND", "The draft version was not found.")
        result.evidence_labels = await db.evidence_labels(conn, project_id)
    return result


async def rewrite_summary(
    request: Request, principal: Principal, project_id: UUID, job_id: UUID
) -> s.RewriteSummary:
    """프로젝트를 볼 수 있으면 모델 작성 작업의 결과 요약을 준다."""
    async with connection(request.app.state.settings) as conn:
        await visible(conn, principal, project_id)
        return await draft_rewrite.summary(conn, project_id, job_id)


async def edit_draft(
    request: Request, principal: Principal, project_id: UUID, payload: s.DraftEdit
) -> s.Draft:
    """이유·원래 공개 문장 ID를 보존하고 CAS로 새 판본을 만든다."""
    async with connection(request.app.state.settings) as conn:
        await visible(conn, principal, project_id, write=True)
        await db.lock(conn, project_id)
        current = await db.load_snapshot(conn, project_id, await db.head(conn, project_id))
        if current is None:
            raise ApplicationError(404, "TFT_DRAFT_NOT_FOUND", "The draft was not found.")
        result, reasons = drafts.edit(
            current, payload, await drafts.valid_references(conn, project_id)
        )
        await db.save(conn, result, actor=principal.user_id, reasons=reasons)
        await repository.clear_sentence_rereview(conn, project_id, list(reasons), principal.user_id)
        await commands.audit(
            conn,
            project_id=project_id,
            actor=principal.user_id,
            entity="draft",
            entity_id=result.version,
            action="draft.edit",
            detail={
                "previous_version": current.version,
                "reasons": {str(k): v for k, v in reasons.items()},
            },
        )
        display = await db.load(conn, project_id, result.version)
        assert display is not None
        display.evidence_labels = await db.evidence_labels(conn, project_id)
    return display


async def regenerate(
    request: Request,
    principal: Principal,
    project_id: UUID,
    payload: s.DraftRegenerate,
    key: str,
) -> s.Draft:
    """처리 작업 없이 저장된 결과로 초안 판본을 새로 만든다. 권한은 검토본 저장과 같다."""
    async with connection(request.app.state.settings) as conn:
        await visible(conn, principal, project_id, write=True)
        result = await drafts.regenerate(conn, project_id, principal.user_id, payload.version, key)
        result.evidence_labels = await db.evidence_labels(conn, project_id)
    return result


async def review_versions(
    request: Request, principal: Principal, project_id: UUID
) -> s.ReviewVersionList:
    """검토본 이력, 최신순. 저장은 언제나 REVIEW_DRAFT로 바꾸므로 그 값을 준다."""
    async with connection(request.app.state.settings) as conn:
        await visible(conn, principal, project_id)
        cursor = await conn.execute(
            "SELECT review_version,draft_version,note,judgement_status,saved_by,saved_at "
            "FROM tft.review_versions WHERE project_id=%s ORDER BY review_version DESC",
            (project_id,),
        )
        rows = await cursor.fetchall()
    return s.ReviewVersionList(
        items=[s.ReviewVersionItem(**row, output_status="REVIEW_DRAFT") for row in rows]
    )


async def exports(request: Request, principal: Principal, project_id: UUID) -> s.ExportList:
    """내보내기 이력, 최신순. 파일 이름은 내려받기 응답의 이름과 같다."""
    async with connection(request.app.state.settings) as conn:
        await visible(conn, principal, project_id)
        cursor = await conn.execute(
            "SELECT id,review_version,format,created_by,created_at,completed_at "
            "FROM tft.exports WHERE project_id=%s ORDER BY created_at DESC, id DESC",
            (project_id,),
        )
        rows = await cursor.fetchall()
    return s.ExportList(
        items=[
            s.ExportItem(
                export_id=row["id"],
                review_version=row["review_version"],
                format=row["format"],
                status="SUCCEEDED" if row["completed_at"] else "QUEUED",
                requested_by=row["created_by"],
                requested_at=row["created_at"],
                finished_at=row["completed_at"],
                file_name=draft_exports.file_name(row["id"], row["format"])
                if row["completed_at"]
                else None,
            )
            for row in rows
        ]
    )


GATE_FIELDS = ("undecided", "deviation_missing", "open_queries", "review_pending")


async def gate_closed(conn: AsyncConnection[dict], project_id: UUID) -> None:
    """저장 조건(KAN-220 decision_gate)이 남으면 409 TFT_DECISION_GATE_OPEN. 화면과 같은 규칙이다.

    결정은 요구 행을, 질문 확정·회신은 질문 행을 FOR UPDATE로 잠그므로 FOR SHARE로 그 쓰기를 기다리게 한 뒤 센다.
    """
    from backend.api.tft.rfp.decisions import gate

    # ponytail: 새 요구·질문 INSERT(직접 추가, 질문 작성)는 행 잠금에 안 걸린다. 경합이 실제로 보이면 결정 쪽도 db.lock을 잡는다.
    await conn.execute(
        "SELECT 1 FROM tft.requirements WHERE project_id=%s FOR SHARE", (project_id,)
    )
    await conn.execute(
        "SELECT 1 FROM tft.customer_questions WHERE project_id=%s FOR SHARE", (project_id,)
    )
    found = await gate(conn, project_id)
    left = [(name, getattr(found, name)) for name in GATE_FIELDS if getattr(found, name)]
    if left:
        raise ApplicationError(
            409,
            "TFT_DECISION_GATE_OPEN",
            "Decisions remain before saving a review version.",
            details=[{"field": name, "reason": str(n)} for name, n in left],
        )


async def save_review(
    request: Request,
    principal: Principal,
    project_id: UUID,
    payload: s.ReviewVersionCreate,
    key: str,
) -> s.ReviewVersion:
    """미해결 필수라도 다음 행동이 있으면 조건 확인 상태로 저장하며 자동 승인하지 않는다."""
    async with connection(request.app.state.settings) as conn:
        await visible(conn, principal, project_id, write=True)
        await db.lock(conn, project_id)

        async def work() -> dict:
            current = await db.load(conn, project_id)
            if current is None:
                raise ApplicationError(404, "TFT_DRAFT_NOT_FOUND", "The draft was not found.")
            if current.version != payload.version:
                raise repository.version_conflict()
            requirements = await drafts.requirements_of(conn, project_id)
            # 담당이 결정하고 책임자가 확인한 요구는 그 결정이 다음 행동이다(KAN-219·220). 사양 차이(편차 기재 등)는
            # 상태가 조건부로 남아도 미해결로 세지 않는다. 남은 결정은 아래 gate_closed가 따로 막는다.
            settled = await drafts.settled_requirements(conn, project_id)
            missing = []
            for req in requirements:
                if req.spec.necessity not in {"MANDATORY", "CONDITION_BOUND"}:
                    continue
                if req.requirement_id in settled:
                    continue
                grounded = bool(req.origin.quote.strip() and req.judgement.reason.strip())
                resolved = grounded and not is_unresolved_mandatory(req) and not req.needs_review
                # 사양 차이는 미확인 항목이 비어 있어도 담당이나 저장한 보완이 다음 행동이다.
                next_action = bool(req.judgement.next_owner_field or req.links.supplement_summary)
                if not resolved and not next_action:
                    missing.append(str(req.requirement_id))
            bad_sentences = [
                str(row.sentence_id)
                for part in current.sections
                for row in part.sentences
                if row.evidence_missing or row.needs_rereview
            ]
            integration = integration_body(await integration_state(conn, project_id))
            blocked = integration is None or (
                integration["required"] and integration["holder"] == "UNRESOLVED"
            )
            if (
                missing
                or bad_sentences
                or blocked
                or not any(part.sentences for part in current.sections)
            ):
                raise ApplicationError(
                    422,
                    "TFT_DRAFT_INCOMPLETE",
                    "The draft needs review.",
                    details=[
                        *[{"field": "requirement_id", "reason": key} for key in missing],
                        *[{"field": "sentence_id", "reason": key} for key in bad_sentences],
                        *(
                            [{"field": "integration_responsibility", "reason": "UNRESOLVED"}]
                            if blocked
                            else []
                        ),
                        *(
                            [{"field": "draft", "reason": "empty"}]
                            if not any(p.sentences for p in current.sections)
                            else []
                        ),
                    ],
                )
            await gate_closed(conn, project_id)
            cursor = await conn.execute(
                "SELECT coalesce(max(review_version),0)+1 AS version FROM tft.review_versions WHERE project_id=%s",
                (project_id,),
            )
            next_review = await cursor.fetchone()
            assert next_review is not None
            review_version = next_review["version"]
            judgement: s.JudgementStatus = (
                "CONDITION_CHECK" if count_unresolved_mandatory(requirements) else "OWNER_CONFIRMED"
            )
            saved = datetime.now(UTC)
            await conn.execute(
                "INSERT INTO tft.review_versions (project_id,review_version,draft_version,judgement_status,note,saved_by,saved_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    project_id,
                    review_version,
                    current.version,
                    judgement,
                    payload.note,
                    principal.user_id,
                    saved,
                ),
            )
            await db.capture_review_context(conn, project_id, review_version)
            await conn.execute(
                "UPDATE tft.projects SET review_version=%s,output_status='REVIEW_DRAFT',judgement_status=%s,version=version+1 WHERE id=%s",
                (review_version, judgement, project_id),
            )
            return s.ReviewVersion(
                project_id=project_id,
                review_version=review_version,
                draft_version=current.version,
                output_status="REVIEW_DRAFT",
                judgement_status=judgement,
                saved_by=principal.user_id,
                saved_at=saved,
            ).model_dump(mode="json")

        result = await commands.run_command(
            conn,
            actor=principal.user_id,
            operation="review_version.create",
            key=key,
            digest=commands.request_hash(
                {"project_id": project_id}, payload.model_dump(mode="json")
            ),
            project_id=project_id,
            status_code=201,
            work=work,
        )
    return s.ReviewVersion.model_validate(result)


async def export(
    request: Request, principal: Principal, project_id: UUID, payload: s.ExportRequest, key: str
) -> s.ExportAccepted:
    """PDF는 아무 예약/접수도 하기 전에 거부한다. DOCX·XLSX는 기존 비동기 worker로 넘긴다."""
    draft_exports.require_supported(payload.format)
    accepted = await jobs.admit(request, principal, project_id, "rfp.export", payload, key)
    return s.ExportAccepted(
        **accepted.model_dump(),
        export_id=draft_exports.export_id(project_id, principal.user_id, key),
    )


async def download(
    request: Request, principal: Principal, project_id: UUID, export_id: UUID
) -> Response:
    """완료된 내보내기만 무결성 확인 후 첨부로 반환한다. 저장 키를 클라이언트에게 노출하지 않는다."""
    async with connection(request.app.state.settings) as conn:
        await visible(conn, principal, project_id)
        cursor = await conn.execute(
            "SELECT * FROM tft.exports WHERE id=%s AND project_id=%s", (export_id, project_id)
        )
        row = await cursor.fetchone()
    if row is None:
        raise ApplicationError(404, "TFT_EXPORT_NOT_FOUND", "The export was not found.")
    if row["completed_at"] is None:
        raise ApplicationError(409, "TFT_EXPORT_NOT_READY", "The export is not complete.")
    draft_exports.require_supported(row["format"])
    store = storage.document_store(storage.DocumentStorageSettings())
    if store is None:
        raise ApplicationError(503, "TFT_DOCUMENT_NOT_READABLE", "Export storage is unavailable.")
    if row["size"] > draft_exports.MAX_EXPORT_BYTES:
        raise ApplicationError(413, "TFT_EXPORT_TOO_LARGE", "The export is too large.")
    try:
        data = await storage.read_document(store, row["storage_key"], row["sha256"], row["size"])
    except storage.DocumentUnavailable:
        raise ApplicationError(
            503, "TFT_DOCUMENT_NOT_READABLE", "The export is unavailable."
        ) from None
    return Response(
        data,
        media_type=draft_exports.MIME[row["format"]],
        headers={
            "Content-Disposition": "attachment; "
            f'filename="{draft_exports.file_name(export_id, row["format"])}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
