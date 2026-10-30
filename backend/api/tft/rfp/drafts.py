"""기존 공개 snapshot 변환과 순수 초안 엔진을 같은 DB 연결에 잇는다(KAN-188)."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from backend.api.tft.projects.repository import integration_state
from backend.api.tft.projects.routes import integration_body
from backend.api.tft.rfp import draft_repository as db
from backend.api.tft.rfp import repository
from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.routes_questions import question_out
from backend.api.tft.rfp.routes_read import comparison_of, detail_of, partner_candidate
from backend.api.tft.rfp.routes_supplement import options_of, selected_out
from backend.engine.rfp.draft import build_draft
from backend.shared.errors import ApplicationError

Row = dict[str, Any]


async def requirements_of(
    conn: AsyncConnection[Row], project_id: UUID
) -> list[s.RequirementDetail]:
    """페이지 결과가 아니라 프로젝트 최신 요구 전체를 기존 상세 변환으로 읽는다."""
    cursor = await conn.execute(
        "SELECT id FROM tft.requirements WHERE project_id = %s ORDER BY id", (project_id,)
    )
    result = []
    for row in await cursor.fetchall():
        req = await repository.latest_requirement_version(conn, project_id, row["id"])
        if req is None:
            raise ValueError("TFT_DRAFT_REQUIREMENT_DISAPPEARED")
        state, question_id = await repository.rnd_check(conn, row["id"])
        result.append(
            detail_of(
                req,
                await repository.requirement_detail_parts(conn, req),
                state=state,
                rnd_question=question_id,
                needs_rereview=await repository.needs_rereview(conn, row["id"]),
            )
        )
    return result


async def generate(
    conn: AsyncConnection[Row], project_id: UUID, actor: UUID, job_id: UUID
) -> s.Draft:
    """같은 작업은 한 번만 생성한다. 사람 편집 뒤 재시도해도 최신 head를 덮어쓰지 않는다."""
    await db.lock(conn, project_id)
    existing = await db.generated(conn, project_id, job_id)
    if existing is not None:
        result = await db.load(conn, project_id, existing)
        if result is None:
            raise ValueError("TFT_DRAFT_VERSION_MISSING")
        return result
    project = await repository.project_scope(conn, project_id)
    if project is None:
        raise ValueError("TFT_PROJECT_NOT_FOUND")
    requirements = await requirements_of(conn, project_id)
    comparisons, options, selections = [], [], []
    for req in requirements:
        raw = await repository.latest_requirement_version(conn, project_id, req.requirement_id)
        if raw is None:
            raise ValueError("TFT_DRAFT_REQUIREMENT_DISAPPEARED")
        comparisons.append(await comparison_of(conn, raw))
        _, option = await options_of(conn, raw)
        options.append(option)
        selected = await repository.latest_selection(conn, req.requirement_id)
        if selected:
            selections.append(selected_out(selected))
    grades = await repository.project_grades(conn, project_id)
    profiles = await repository.profiles_of(
        conn, [(g["candidate_code"], g["profile_version"]) for g in grades]
    )
    groups: dict[str, list[Row]] = {}
    for grade in grades:
        groups.setdefault(grade["candidate_code"], []).append(grade)
    partners = []
    for code, mine in groups.items():
        profile = profiles[(code, max(g["profile_version"] for g in mine))]
        partners.append(partner_candidate(profile, mine, mine))
    integration = integration_body(await integration_state(conn, project_id))
    if integration is None:
        raise ValueError("TFT_INTEGRATION_SCHEMA_MISSING")
    result = build_draft(
        project_id=project_id,
        version=await db.head(conn, project_id) + 1,
        data_origin=project["data_origin"],
        known_at=datetime.now(UTC),
        requirements=requirements,
        integration=s.IntegrationResponsibility.model_validate(integration),
        comparisons=comparisons,
        options=options,
        selections=selections,
        partners=partners,
        questions=[question_out(q) for q in await repository.list_questions(conn, project_id)],
    )
    await db.save(conn, result, actor=actor, source_job_id=job_id)
    await audit(
        conn,
        project_id,
        actor,
        "draft",
        str(result.version),
        "draft.generate",
        {"job_id": str(job_id)},
    )
    return result


async def audit(
    conn: AsyncConnection[Row],
    project_id: UUID,
    actor: UUID,
    entity: str,
    entity_id: str,
    action: str,
    detail: Row,
) -> None:
    """업무 쓰기와 같은 트랜잭션에 행위·대상·사유를 기록한다."""
    await conn.execute(
        "INSERT INTO tft.rfp_audit_events (id,project_id,entity,entity_id,action,actor_user_id,detail) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (uuid4(), project_id, entity, entity_id, action, actor, Jsonb(detail)),
    )


def edit(
    current: s.Draft,
    payload: s.DraftEdit,
    valid_refs: set[tuple[str, str]],
) -> tuple[s.Draft, dict[UUID, str]]:
    """사람 수정은 새 판본이다. 조건·미확약·재검토와 고지를 바꾸는 DTO는 없다."""
    if payload.version != current.version:
        raise repository.version_conflict()
    result = current.model_copy(deep=True)
    result.version += 1
    result.known_at = datetime.now(UTC)
    slots = {row.sentence_id: (part, row) for part in result.sections for row in part.sentences}
    reasons: dict[UUID, str] = {}
    for change in payload.edits:
        if not change.text.strip() or not change.reason.strip():
            raise ApplicationError(422, "VALIDATION_ERROR", "Text and reason must not be blank.")
        refs = {(e.type, e.id) for e in change.evidence}
        missing = not refs or not refs.issubset(valid_refs)
        if change.sentence_id is None:
            if change.section is None:
                raise ApplicationError(422, "VALIDATION_ERROR", "A new sentence needs a section.")
            row = s.Sentence(
                sentence_id=uuid4(),
                text=change.text,
                evidence=change.evidence,
                conditions=[],
                unconfirmed=True,
                needs_rereview=False,
                evidence_missing=missing,
                provenance_kind="human_input",
            )
            next(
                part for part in result.sections if part.section == change.section
            ).sentences.append(row)
        else:
            if change.sentence_id not in slots or change.sentence_id in reasons:
                raise ApplicationError(422, "VALIDATION_ERROR", "Unknown or repeated sentence.")
            old_part, row = slots[change.sentence_id]
            row.text, row.evidence = change.text, [e.model_copy() for e in change.evidence]
            row.evidence_missing, row.provenance_kind = missing, "human_input"
            if change.section is not None and change.section != old_part.section:
                old_part.sentences.remove(row)
                next(
                    part for part in result.sections if part.section == change.section
                ).sentences.append(row)
        reasons[row.sentence_id] = change.reason
    result.evidence_missing_count = sum(
        row.evidence_missing for part in result.sections for row in part.sentences
    )
    return result, reasons


async def valid_references(conn: AsyncConnection[Row], project_id: UUID) -> set[tuple[str, str]]:
    """편집 근거를 프로젝트 안에서만 확인한다. 다른 프로젝트 존재 여부는 조회하지 않는다."""
    cursor = await conn.execute(
        "SELECT 'requirement' AS kind,r.id::text AS ref FROM tft.requirements r WHERE r.project_id=%s "
        "UNION SELECT 'clause',c.id::text FROM tft.clauses c JOIN tft.document_versions v ON v.id=c.document_version_id "
        "JOIN tft.documents d ON d.id=v.document_id WHERE d.project_id=%s "
        "UNION SELECT 'question',q.id::text FROM tft.customer_questions q WHERE q.project_id=%s "
        "UNION SELECT 'product',c.product_code FROM tft.product_comparisons c JOIN tft.requirements r "
        "ON r.id=c.requirement_id WHERE r.project_id=%s "
        "UNION SELECT 'partner',g.candidate_code FROM tft.partner_grades g WHERE g.project_id=%s",
        (project_id,) * 5,
    )
    return {(row["kind"], row["ref"]) for row in await cursor.fetchall()}
