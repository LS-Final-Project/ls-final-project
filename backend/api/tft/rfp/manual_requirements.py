"""원문에서 요구 직접 추가와 요구에서 빼기·되살리기(KAN-225).

추가는 사람이 원문에서 고른 글과 직접 넣은 값으로 요구 판본 1(human_input)을 쓰고, 규칙 비교(모델 호출 없음)로 바로
판정한다. 빼기·되살리기는 결정 API의 행동이고 요구 행을 지우지 않는다. 셋 다 requirement_decisions 한 행으로 남아
책임자 확인 대상이다. 처리 작업은 처리하지 않은 문서 판본의 요구만 새로 쓰고 기존 요구 행을 지우거나 고치지 않으므로
재처리 뒤에도 사람이 추가하거나 뺀 요구는 그대로다.
"""

import re
from typing import Any
from uuid import UUID, uuid4

from fastapi import Request
from psycopg import AsyncConnection

from backend.api.tft.rfp import commands, comparison, repository, supplement
from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.decisions import check_role, record, replay
from backend.api.tft.rfp.routes_questions import visible_project
from backend.api.tft.rfp.routes_read import PROJECT_NOT_FOUND
from backend.engine.rfp.requirements import ExtractedRequirement
from backend.shared.access import Principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError

Row = dict[str, Any]


def normalized(text: str | None) -> str:
    """추출 근거 검사(requirements.validate_output)와 같은 정규화: 공백 하나, 곧은 따옴표, 대소문자 무시."""
    return re.sub(r"\s+", " ", (text or "").replace("’", "'").replace("‘", "'")).strip().casefold()


def grounded(payload: s.ManualRequirementRequest, clause_text: str) -> None:
    """고른 글이 조항 현행 텍스트 안에, 값이 고른 글 안에 있어야 한다. 아니면 422다."""
    if normalized(payload.quote) not in normalized(clause_text):
        raise ApplicationError(
            422,
            "TFT_QUOTE_NOT_IN_CLAUSE",
            "The selected text is not in the clause.",
            details=[{"field": "quote", "reason": "not in clause text"}],
        )
    if payload.value is not None and normalized(payload.value) not in normalized(payload.quote):
        raise ApplicationError(
            422,
            "TFT_VALUE_NOT_IN_QUOTE",
            "The value is not in the selected text.",
            details=[{"field": "value", "reason": "not in quote"}],
        )


async def clause_of(conn: AsyncConnection[Row], project_id: UUID, clause_id: UUID) -> Row:
    """프로젝트 문서의 조항(clause_id·clause_no·text·page)과 그 문서 판본 열(id는 문서 판본). 없으면 404다."""
    cursor = await conn.execute(
        "SELECT c.id AS clause_id, c.clause_no, c.text, c.page, dv.* FROM tft.clauses c "
        "JOIN tft.document_versions dv ON dv.id = c.document_version_id "
        "JOIN tft.documents d ON d.id = dv.document_id WHERE c.id = %s AND d.project_id = %s",
        (clause_id, project_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ApplicationError(404, "TFT_CLAUSE_NOT_FOUND", "The clause was not found.")
    return row


async def duplicates_of(
    conn: AsyncConnection[Row], project_id: UUID, payload: s.ManualRequirementRequest
) -> list[str]:
    """설비·속성·값이 같은, 빼지 않은 요구의 ID. 속성은 원문 표현이나 비교 어휘 키가 같으면 같다."""
    cursor = await conn.execute(
        "SELECT req.id, v.equipment, v.attribute, v.attribute_key, v.value FROM tft.requirements req "
        "JOIN tft.requirement_versions v ON v.requirement_id = req.id AND v.version = req.latest_version "
        f"WHERE req.project_id = %s AND NOT {repository.EXCLUDED.format(requirement='req.id')}",
        (project_id,),
    )
    key = payload.attribute_key
    return [
        str(row["id"])
        for row in await cursor.fetchall()
        if normalized(row["equipment"]) == normalized(payload.equipment)
        and normalized(row["value"]) == normalized(payload.value)
        and (
            normalized(row["attribute"]) == normalized(payload.attribute)
            or (key != "OTHER" and row["attribute_key"] == key)
        )
    ]


async def add(
    request: Request,
    principal: Principal,
    project_id: UUID,
    payload: s.ManualRequirementRequest,
    key: str,
) -> s.DecisionResult:
    """원문에서 고른 글로 요구를 추가하고 규칙 비교로 바로 판정한다. 담당 sales만 한다.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        project_id: 프로젝트 ID.
        payload: 고른 글과 사람이 넣은 값.
        key: Idempotency-Key.

    Returns:
        s.DecisionResult: 추가 기록(requirement_id가 새 요구). 같은 키 재전송은 처음 결과다.
    """
    # 워커 모듈은 이 경계 안에서만 가져온다(모듈 수준 순환 import 방지, workflow.recompare와 같다).
    from backend.api.tft.worker_processor import image_comparison_review, to_comparison

    digest = commands.request_hash({"project_id": project_id}, payload.model_dump(mode="json"))
    async with connection(request.app.state.settings) as conn:
        project = await visible_project(conn, principal, project_id, *PROJECT_NOT_FOUND)
        check_role(project, principal, "add_requirement")
        stored = await replay(conn, principal.user_id, key, digest)
        if stored is not None:
            return s.DecisionResult.model_validate(stored)
        # 처리 작업의 저장 트랜잭션과 같은 잠금. 요구 코드 순번이 겹치지 않는다.
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,187))", (str(project_id),)
        )
        source = await clause_of(conn, project_id, payload.clause_id)
        grounded(payload, source["text"])
        duplicates = await duplicates_of(conn, project_id, payload)
        if duplicates and not payload.allow_duplicate:
            raise ApplicationError(
                409,
                "TFT_REQUIREMENT_DUPLICATE",
                "A requirement with the same equipment, attribute and value exists.",
                details=[{"field": "requirement_id", "reason": rid} for rid in duplicates],
            )
        item = ExtractedRequirement(
            clause_no=source["clause_no"],
            quote=payload.quote,
            kind=payload.kind,
            equipment=payload.equipment,
            task=payload.task,
            attribute=payload.attribute,
            value=payload.value,
            unit=payload.unit,
            operator=payload.operator,
            conditions=[c.model_dump() for c in payload.conditions],
            # ponytail: 규격 체계·지정품·허용 오차는 받지 않는다(NONE·없음). 필요하면 입력 칸을 더한다.
            standard_system="NONE",
            necessity=payload.necessity,
            designated_model=None,
            attribute_key=payload.attribute_key,
            equipment_family=payload.equipment_family,
        )
        outcome = image_comparison_review(
            item,
            comparison.compare_in_family(
                to_comparison(
                    item,
                    voltages=await repository.equipment_voltages(conn, project_id, source["id"]),
                    insulations=await repository.equipment_insulations(
                        conn, project_id, source["id"]
                    ),
                ),
                item.equipment_family,
                comparison.load_own_products(),
            ),
        )
        cursor = await conn.execute(
            "SELECT count(*) AS n FROM tft.requirements WHERE project_id = %s AND code LIKE %s",
            (project_id, f"{source['id'].hex[:8]}:{source['clause_no']}:m%"),
        )
        row = await cursor.fetchone()
        # 처리 코드(문서 판본:조항:순번) 옆에 m 순번으로 둔다. 같은 조항의 추출 요구 뒤에 정렬된다.
        code = f"{source['id'].hex[:8]}:{source['clause_no']}:m{(row['n'] if row else 0) + 1}"
        requirement_id = await repository.insert_requirement(
            conn,
            project_id=project_id,
            code=code,
            document_version=source,
            clause={"id": source["clause_id"], "page": source["page"]},
            spec={
                **item.model_dump(
                    include={
                        "quote",
                        "equipment",
                        "task",
                        "attribute",
                        "value",
                        "unit",
                        "operator",
                        "standard_system",
                        "necessity",
                        "designated_model",
                        "designated_manufacturer",
                        "assembly_clause",
                        "tolerance",
                        "needs_review",
                        "attribute_key",
                        "equipment_family",
                        "conditions",
                        "kind",
                        "certification_listed",
                        "canonical_value",
                    }
                )
            },
            judgement={
                "status": outcome.status,
                "reason": outcome.reason,
                "unresolved": outcome.unresolved,
                "next_owner": outcome.next_owner,
            },
            actor=principal.user_id,
        )
        for candidate in outcome.candidates:
            await repository.insert_comparison(
                conn,
                requirement_id,
                candidate.model_dump(mode="json"),
                matched_attribute=item.attribute_key,
                search_trace=outcome.search_trace.model_dump(),
                data_origin=source["data_origin"],
                provenance_kind="system_record",
            )
        options = supplement.options_for(outcome, "UNSTATED", designated=False)
        for option in options.options:
            await repository.insert_option(
                conn,
                requirement_id,
                option.model_dump(),
                deficiency_cause=options.deficiency_cause,
                customer_permission=options.customer_permission,
                data_origin=source["data_origin"],
            )
        result = await record(
            conn,
            decision_id=uuid4(),
            project_id=project_id,
            requirement_id=requirement_id,
            base_version=1,
            result_version=1,
            action="add_requirement",
            summary=f"직접 추가: {payload.equipment or payload.task or ''} {payload.attribute}".strip(),
            detail={"clause_no": source["clause_no"], "duplicates": duplicates},
            undoes=None,
            question_id=None,
            actor=principal.user_id,
            key=key,
            digest=digest,
        )
    return s.DecisionResult.model_validate(result)
