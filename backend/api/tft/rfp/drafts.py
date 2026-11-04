"""기존 공개 snapshot 변환과 순수 초안 엔진을 같은 DB 연결에 잇는다(KAN-188)."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4, uuid5

from psycopg import AsyncConnection

from backend.api.tft.projects.repository import integration_state
from backend.api.tft.rfp import commands, repository
from backend.api.tft.rfp import draft_repository as db
from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.integration import integration_body
from backend.api.tft.rfp.routes_questions import question_out
from backend.api.tft.rfp.routes_read import comparison_of, detail_of, partner_candidate
from backend.api.tft.rfp.routes_supplement import options_of, selected_out
from backend.engine.rfp.draft import build_draft
from backend.shared.errors import ApplicationError

Row = dict[str, Any]


# 비교에 쓰는 칸. 결정 승인(insert_approved_version)은 판단 칸만 바꾸므로 이 칸이 같으면 앞 판본의 비교가 그대로 맞다.
COMPARED_FIELDS = (
    "equipment",
    "equipment_family",
    "attribute",
    "attribute_key",
    "value",
    "unit",
    "operator",
    "tolerance",
    "standard_system",
    "canonical_value",
)


async def same_compared_fields(
    conn: AsyncConnection[Row], requirement_id: UUID, compared: int, current: int
) -> bool:
    """비교한 판본과 현재 판본의 비교 칸이 같은지. 결정만으로 판본이 올라간 경우 재검토를 붙이지 않는다."""
    cursor = await conn.execute(
        f"SELECT {', '.join(COMPARED_FIELDS)} FROM tft.requirement_versions "
        "WHERE requirement_id = %s AND version = ANY(%s)",
        (requirement_id, [compared, current]),
    )
    rows = await cursor.fetchall()
    return len(rows) == 2 and rows[0] == rows[1]


async def settled_requirements(conn: AsyncConnection[Row], project_id: UUID) -> set[UUID]:
    """담당이 결정하고 책임자가 확인한 요구(와 그 중복 구성원). 추출의 '검토 필요' 표시가 풀린 것으로 본다.

    KAN-219·220. 그러지 않으면 결정을 모두 끝내도 초안 문장에 재검토가 남아 검토본을 저장할 수 없다(40444 P26-0002).
    """
    from backend.api.tft.rfp.decisions import states

    found = await states(conn, project_id)
    # 결정 뒤 요구가 새 판본으로 바뀌었으면(재처리 등) 그 판본은 아직 사람이 보지 않았다.
    cursor = await conn.execute(
        "SELECT d.id FROM tft.requirement_decisions d JOIN tft.requirements r ON r.id = d.requirement_id "
        "WHERE r.project_id = %s AND coalesce(d.result_version, d.base_version) >= r.latest_version",
        (project_id,),
    )
    current = {row["id"] for row in await cursor.fetchall()}
    settled = {
        key
        for key, (_, state) in found.items()
        if state.decided and state.reviewed and state.decision_id in current
    }
    # 중복 구성원은 대표의 결정을 따른다(KAN-231).
    return settled | {key for key, (_, state) in found.items() if state.follows in settled}


async def requirements_of(
    conn: AsyncConnection[Row], project_id: UUID
) -> list[s.RequirementDetail]:
    """페이지 결과가 아니라 프로젝트 최신 요구 전체를 기존 상세 변환으로 읽는다."""
    # 뺀 요구(KAN-225)는 초안·출력에 넣지 않는다.
    cursor = await conn.execute(
        "SELECT id FROM tft.requirements req WHERE project_id = %s "
        f"AND NOT {repository.EXCLUDED.format(requirement='req.id')} ORDER BY id",
        (project_id,),
    )
    settled = await settled_requirements(conn, project_id)
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
        if row["id"] in settled:
            result[-1].needs_review = False
    return result


async def generate(
    conn: AsyncConnection[Row], project_id: UUID, actor: UUID, job_id: UUID
) -> s.Draft:
    """같은 작업은 한 번만 생성한다. 사람 편집 뒤 재시도해도 최신 head를 덮어쓰지 않는다."""
    await db.lock(conn, project_id)
    existing = await db.generated(conn, project_id, job_id)
    if existing is not None:
        return await loaded(conn, project_id, existing)
    source_inputs = await captured_inputs(conn, project_id)
    result, reasons = await build(conn, project_id)
    await check_inputs(conn, project_id, source_inputs)
    await db.save(
        conn,
        result,
        actor=actor,
        source_job_id=job_id,
        reasons=reasons,
        source_inputs=source_inputs,
    )
    await commands.audit(
        conn,
        project_id=project_id,
        actor=actor,
        entity="draft",
        entity_id=result.version,
        action="draft.generate",
        detail={"job_id": str(job_id)},
    )
    return await loaded(conn, project_id, result.version)


def regenerate_id(project_id: UUID, actor: UUID, key: str) -> UUID:
    """다시 만들기 요청 하나의 source_job_id. 같은 사용자·키의 재전송은 같은 판본을 찾는다."""
    return uuid5(project_id, f"regenerate:{actor}:{key}")


async def regenerate(
    conn: AsyncConnection[Row], project_id: UUID, actor: UUID, seen: int, key: str
) -> s.Draft:
    """저장된 요구·비교·보완·질문으로 초안 판본을 새로 만든다.

    모델 호출·원문 해석·요구 등급 재계산은 하지 않는다. 사람 문장은 generate와 같이 이관한다.

    Args:
        conn: 호출자가 커밋하는 트랜잭션.
        project_id: 프로젝트 ID.
        actor: 요청자.
        seen: 화면이 본 최신 판본(없으면 0).
        key: Idempotency-Key.

    Returns:
        s.Draft: 새 판본. 같은 키 재전송이면 처음 만든 판본이다.
    """
    await db.lock(conn, project_id)
    source = regenerate_id(project_id, actor, key)
    existing = await db.generated(conn, project_id, source)
    if existing is not None:
        if existing != seen + 1:
            raise ApplicationError(409, "IDEMPOTENCY_CONFLICT", "The request key is already used.")
        return await loaded(conn, project_id, existing)
    # 처리 worker는 저장 단계 내내 이 잠금을 쥔다(worker_processor.process). 기다리지 않고 거절한다.
    # ponytail: 플랫폼 대기열(QUEUED)은 tft가 볼 수 없다. 그 작업은 나중에 이 판본 위에 새 판본을 만든다.
    cursor = await conn.execute(
        "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,187)) AS free", (str(project_id),)
    )
    row = await cursor.fetchone()
    if row is None or not row["free"]:
        raise ApplicationError(
            409, "TFT_DRAFT_JOB_ACTIVE", "처리 작업이 진행 중입니다. 끝난 뒤 다시 만드세요."
        )
    if await db.head(conn, project_id) != seen:
        raise repository.version_conflict()
    source_inputs = await captured_inputs(conn, project_id)
    result, reasons = await build(conn, project_id)
    await check_inputs(conn, project_id, source_inputs)
    await db.save(
        conn,
        result,
        actor=actor,
        source_job_id=source,
        reasons=reasons,
        source_inputs=source_inputs,
    )
    await commands.audit(
        conn,
        project_id=project_id,
        actor=actor,
        entity="draft",
        entity_id=result.version,
        action="draft.regenerate",
        detail={"previous_version": seen},
    )
    # 결정 승인이 문장에 남긴 재검토 표시(명세 결정 10)는 그 문장을 다시 확인할 때 풀린다. 다시 만들기는 저장된
    # 결정으로 시스템 문장을 새로 쓰는 일이라 그 확인에 해당한다. 사람이 고친 문장(이관)의 표시는 남긴다.
    human = [
        row.sentence_id
        for part in result.sections
        for row in part.sentences
        if row.provenance_kind == "human_input"
    ]
    await conn.execute(
        "UPDATE tft.rereview_marks SET cleared_by = %s, cleared_at = now() "
        "WHERE project_id = %s AND target_kind = 'draft_sentence' AND cleared_at IS NULL "
        "AND NOT (target_id = ANY(%s))",
        (actor, project_id, human),
    )
    return await loaded(conn, project_id, result.version)


async def loaded(conn: AsyncConnection[Row], project_id: UUID, version: int) -> s.Draft:
    result = await db.load(conn, project_id, version)
    if result is None:
        raise ValueError("TFT_DRAFT_VERSION_MISSING")
    return result


async def build(conn: AsyncConnection[Row], project_id: UUID) -> tuple[s.Draft, dict[UUID, str]]:
    """잠금 안에서 head+1 판본과 이관할 사람 편집 이유를 만든다. 저장은 호출자가 한다."""
    version = await db.head(conn, project_id)
    previous = await db.load_snapshot(conn, project_id, version) if version else None
    human = (
        [
            row
            for part in previous.sections
            for row in part.sentences
            if row.provenance_kind == "human_input"
        ]
        if previous
        else []
    )
    reasons, replacements = await db.human_lineage(
        conn, project_id, version, [row.sentence_id for row in human]
    )
    project = await repository.project_scope(conn, project_id)
    if project is None:
        raise ValueError("TFT_PROJECT_NOT_FOUND")
    requirements = await requirements_of(conn, project_id)
    comparisons, options, selections = [], [], []
    for req in requirements:
        raw = await repository.latest_requirement_version(conn, project_id, req.requirement_id)
        if raw is None:
            raise ValueError("TFT_DRAFT_REQUIREMENT_DISAPPEARED")
        compared = await comparison_of(conn, raw)
        if compared.requirement_version != req.version and not await same_compared_fields(
            conn, req.requirement_id, compared.requirement_version, req.version
        ):
            # An older match is evidence history, not a match for an approved new value.
            req.needs_rereview = True
            compared = compared.model_copy(
                update={
                    "candidates": [
                        candidate.model_copy(
                            update={
                                "attribute_match": candidate.attribute_match.model_copy(
                                    update={"comparable": False, "result": "NOT_COMPARABLE"}
                                )
                            }
                        )
                        for candidate in compared.candidates
                    ]
                }
            )
        comparisons.append(compared)
        _, option = await options_of(conn, raw)
        options.append(option)
        selected = await repository.latest_selection(conn, req.requirement_id)
        # 회신·승인으로 막히거나 사라진 경로의 선택은 결정으로 쓰지 않는다. 처리를 실패시키지
        # 않고, 재검토 표시로 사람에게 돌아간다.
        if (
            selected
            and not await repository.invalid_selection(conn, req.requirement_id)
            and any(o.path == selected["path"] and o.allowed for o in option.options)
        ):
            selections.append(selected_out(selected))
    inputs = await repository.project_constraints(conn, project_id)
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
        partners.append(
            partner_candidate(
                profile, mine, mine, manufacturer_list=inputs["customer_manufacturer_list"]
            )
        )
    integration = integration_body(await integration_state(conn, project_id))
    if integration is None:
        raise ValueError("TFT_INTEGRATION_SCHEMA_MISSING")
    result = build_draft(
        project_id=project_id,
        version=version + 1,
        data_origin=project["data_origin"],
        known_at=datetime.now(UTC),
        requirements=requirements,
        integration=s.IntegrationResponsibility.model_validate(integration),
        comparisons=comparisons,
        options=options,
        selections=selections,
        partners=partners,
        questions=[question_out(q) for q in await repository.list_questions(conn, project_id)],
        replaced=replacements,
        clause_ids=await repository.project_clause_ids(conn, project_id),
    )
    return preserve_human(result, previous, await valid_references(conn, project_id)), reasons


def preserve_human(
    generated: s.Draft, previous: s.Draft | None, valid_refs: set[tuple[str, str]]
) -> s.Draft:
    """사람 문장을 원형대로 이관한다. live mark를 고착시키지 않고 사라진 근거만 누락 표시한다."""
    result = generated.model_copy(deep=True)
    if previous is None:
        return result
    sections = {part.section: part for part in result.sections}
    for part in previous.sections:
        for old in part.sentences:
            if old.provenance_kind != "human_input":
                continue
            row = old.model_copy(deep=True)
            if not row.evidence or any((e.type, e.id) not in valid_refs for e in row.evidence):
                row.evidence_missing = True
                row.needs_rereview = True
            sections[part.section].sentences.append(row)
    result.evidence_missing_count = sum(
        row.evidence_missing for part in result.sections for row in part.sentences
    )
    return result


def edit(
    current: s.Draft,
    payload: s.DraftEdit,
    valid_refs: set[tuple[str, str]],
) -> tuple[s.Draft, dict[UUID, str]]:
    """사람 수정은 새 판본이다. 고쳐 저장한 문장만 재검토가 풀린다(명세 결정 10)."""
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
            row.needs_rereview = False
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


async def captured_inputs(conn: AsyncConnection[Row], project_id: UUID) -> dict[str, Any]:
    from backend.api.tft.rfp.workflow import inputs

    return await inputs(conn, project_id)


async def check_inputs(
    conn: AsyncConnection[Row], project_id: UUID, before: dict[str, Any]
) -> None:
    if await captured_inputs(conn, project_id) != before:
        raise ApplicationError(
            409,
            "TFT_VERSION_CONFLICT",
            "초안을 만드는 동안 검토 기록이 바뀌었습니다. 최신 내용을 확인한 뒤 다시 만드세요.",
        )
