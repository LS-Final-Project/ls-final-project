"""Stored-input workflow: explicit comparison and immutable draft input tracking."""

from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, Request
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from backend.api.tft.projects.access import can_propose_change
from backend.api.tft.rfp import commands, comparison, repository
from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.routes_questions import proposal_out, visible_project
from backend.api.tft.rfp.routes_read import comparison_of, latest_visible_requirement
from backend.shared.access import Principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError

Row = dict[str, Any]


async def inputs(conn: AsyncConnection[Row], project_id: UUID) -> dict[str, Any]:
    """Version identities, not live contents: immutable drafts retain their original inputs."""
    cursor = await conn.execute(
        "SELECT r.id::text, jsonb_build_object('requirement',r.latest_version, "
        "'comparisons',coalesce((SELECT jsonb_agg(p.id::text ORDER BY p.id) FROM tft.product_comparisons p WHERE p.requirement_id=r.id),'[]'), "
        "'recomparisons',coalesce((SELECT jsonb_agg(rc.id::text ORDER BY rc.id) FROM tft.requirement_recomparisons rc WHERE rc.requirement_id=r.id),'[]'), "
        "'selections',coalesce((SELECT max(ss.version) FROM tft.supplement_selections ss WHERE ss.requirement_id=r.id),0), "
        "'questions',coalesce((SELECT jsonb_agg(jsonb_build_array(q.id::text,q.version) ORDER BY q.id) FROM tft.customer_questions q WHERE q.requirement_id=r.id),'[]')) "
        # KAN-225: 뺀 요구만 표시를 더한다(빼지 않은 요구의 키는 그대로라 기존 초안이 STALE이 되지 않는다).
        f"|| CASE WHEN {repository.EXCLUDED.format(requirement='r.id')} "
        "THEN jsonb_build_object('excluded',true) ELSE '{}'::jsonb END AS state "
        "FROM tft.requirements r WHERE r.project_id=%s ORDER BY r.id",
        (project_id,),
    )
    return {row["id"]: row["state"] for row in await cursor.fetchall()}


async def impact(conn: AsyncConnection[Row], requirement: Row) -> s.RequirementWorkflow:
    compared = await comparison_of(conn, requirement)
    cursor = await conn.execute(
        "SELECT target_kind,target_id FROM tft.rereview_marks WHERE project_id=%s AND cleared_at IS NULL "
        "AND (target_id=%s OR (target_kind='draft_sentence' AND target_id IN ("
        "SELECT ds.sentence_key FROM tft.draft_sentences ds JOIN tft.drafts d ON d.project_id=ds.project_id AND d.latest_version=ds.draft_version "
        "JOIN tft.draft_sentence_evidence e ON e.sentence_id=ds.id WHERE ds.project_id=%s AND e.evidence_type='requirement' AND e.evidence_id=%s)))",
        (
            requirement["project_id"],
            requirement["requirement_id"],
            requirement["project_id"],
            str(requirement["requirement_id"]),
        ),
    )
    marks = await cursor.fetchall()
    return s.RequirementWorkflow(
        comparison_stale=compared.requirement_version != requirement["version"],
        supplement_stale=any(m["target_kind"] == "supplement" for m in marks),
        draft_sentence_ids=[m["target_id"] for m in marks if m["target_kind"] == "draft_sentence"],
    )


async def recompare(
    request: Request,
    principal: Principal,
    requirement_id: UUID,
    payload: s.RecompareRequest,
    key: str,
) -> s.RecompareResult:
    """Recompare stored extraction against the local own-product facts; never call a model."""
    # Import inside this service boundary to avoid the worker's module-level import cycle.
    from backend.api.tft.worker_processor import (
        image_comparison_review,
        stored_extraction,
        to_comparison,
    )

    async with connection(request.app.state.settings) as conn:
        initial = await latest_visible_requirement(conn, principal, requirement_id)
        project = await visible_project(
            conn,
            principal,
            initial["project_id"],
            "TFT_PROJECT_NOT_FOUND",
            "The project was not found.",
        )
        if not can_propose_change(principal, project):
            raise HTTPException(403)

        async def work() -> dict[str, Any]:
            requirement = await repository.latest_requirement_version(
                conn, project["id"], requirement_id, lock=True
            )
            assert requirement is not None
            if requirement["version"] != payload.version:
                raise repository.version_conflict()
            current = await repository.judgement_of(conn, requirement)
            try:
                item = stored_extraction({**requirement, "conditions": current["conditions"]})
            except ValidationError as exc:
                raise ApplicationError(
                    409,
                    "TFT_RECOMPARE_INPUT_INCOMPLETE",
                    "저장된 요구의 종류·속성·조건을 확인해야 합니다. 원문과 대조해 보완한 뒤 다시 비교하세요.",
                ) from exc
            if item is None:
                raise ApplicationError(
                    409,
                    "TFT_RECOMPARE_INPUT_INCOMPLETE",
                    "요구 종류와 인증 등록 여부가 저장되지 않았습니다. 원문과 추출 정보를 확인한 뒤 다시 비교하세요.",
                )
            outcome = image_comparison_review(
                item,
                comparison.compare_in_family(
                    to_comparison(
                        item,
                        voltages=await repository.equipment_voltages(
                            conn, project["id"], requirement["document_version_id"]
                        ),
                        insulations=await repository.equipment_insulations(
                            conn, project["id"], requirement["document_version_id"]
                        ),
                    ),
                    requirement["equipment_family"],
                    comparison.load_own_products(),
                ),
            )
            prior = await repository.latest_comparisons(conn, requirement_id)
            version = max((r["version"] for r in prior), default=0) + 1
            for candidate in outcome.candidates:
                await repository.insert_comparison(
                    conn,
                    requirement_id,
                    candidate.model_dump(mode="json"),
                    matched_attribute=None if item.kind == "COMPLIANCE" else item.attribute_key,
                    search_trace=outcome.search_trace.model_dump(),
                    data_origin=requirement["data_origin"],
                    requirement_version=payload.version,
                    comparison_version=version,
                    provenance_kind="system_record",
                )
            proposed = {
                **current,
                "status": outcome.status,
                "reason": outcome.reason,
                "unresolved": outcome.unresolved,
            }
            proposal = None
            if proposed != current:
                proposal = await repository.insert_web_proposal(
                    conn,
                    requirement,
                    current=current,
                    proposed=proposed,
                    rationale="저장된 요구와 자사 사양을 다시 비교한 결과입니다. 적용 범위와 근거를 확인한 뒤 승인하세요.",
                    evidence_refs=[e.document for c in outcome.candidates for e in c.evidence][:20],
                    actor=principal.user_id,
                )
                await commands.audit(
                    conn,
                    project_id=project["id"],
                    entity="proposal",
                    entity_id=proposal["id"],
                    action="proposal.create",
                    actor=principal.user_id,
                )
            await commands.audit(
                conn,
                project_id=project["id"],
                entity="requirement",
                entity_id=requirement_id,
                action="requirement.recompare",
                actor=principal.user_id,
            )
            # Empty results must not resurrect candidates from an older version.
            result = await comparison_of(conn, requirement, stored_recomparison=False)
            if not outcome.candidates:
                result = result.model_copy(
                    update={
                        "requirement_version": payload.version,
                        "candidates": [],
                        "search_trace": s.SearchTrace(**outcome.search_trace.model_dump()),
                    }
                )
            # The current candidate set replaces only this result snapshot, not old rows.
            codes = {candidate.product_code for candidate in outcome.candidates}
            result = result.model_copy(
                update={
                    "candidates": [
                        candidate
                        for candidate in result.candidates
                        if candidate.product_code in codes
                    ],
                    "provenance_kind": "system_record",
                }
            )
            await conn.execute(
                "INSERT INTO tft.requirement_recomparisons(id,requirement_id,requirement_version,result) VALUES (%s,%s,%s,%s)",
                (uuid4(), requirement_id, payload.version, Jsonb(result.model_dump(mode="json"))),
            )
            return s.RecompareResult(
                comparison=result, proposal=proposal_out(proposal) if proposal else None
            ).model_dump(mode="json")

        result = await commands.run_command(
            conn,
            actor=principal.user_id,
            operation="requirement.recompare",
            key=key,
            digest=commands.request_hash({"requirement_id": requirement_id}, payload.model_dump()),
            project_id=project["id"],
            status_code=200,
            work=work,
            audit=False,
        )
    return s.RecompareResult.model_validate(result)
