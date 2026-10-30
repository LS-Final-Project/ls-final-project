"""경로 15·16: 보완 경로 선택지와 선택(KAN-189 PR-B).

선택지는 저장 행의 원인·고객 허용·경로별 후보만 읽고, 순서·게이트·차단은 지금 R&D 확인 상태로
supplement.options_for가 다시 계산한다(저장된 allowed·rank는 쓰지 않는다). R&D 답이 오면 바로 반영된다.
"""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request
from psycopg import AsyncConnection

from backend.api.tft.projects.access import can_select_supplement
from backend.api.tft.rfp import commands, repository, supplement
from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.comparison import Outcome
from backend.api.tft.rfp.rnd import check_selection, rnd_question_for
from backend.api.tft.rfp.routes_questions import visible_project
from backend.shared.access import Principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError

Row = dict[str, Any]
NOT_FOUND = ("TFT_REQUIREMENT_NOT_FOUND", "The requirement was not found.")


async def requirement_project(
    conn: AsyncConnection[Row], principal: Principal, requirement_id: UUID
) -> Row:
    """요구가 속한, 볼 수 있는 프로젝트. 요구가 없거나 범위 밖이면 404 TFT_REQUIREMENT_NOT_FOUND다.

    Args:
        conn: dict_row 연결.
        principal: 요청 사용자.
        requirement_id: 요구 ID.

    Returns:
        Row: project_scope 행.
    """
    project_id = await repository.requirement_project(conn, requirement_id)
    if project_id is None:
        raise ApplicationError(404, *NOT_FOUND)
    return await visible_project(conn, principal, project_id, *NOT_FOUND)


async def options_of(
    conn: AsyncConnection[Row], requirement: Mapping[str, Any]
) -> tuple[supplement.Options, s.SupplementOptions]:
    """요구 최신 판본의 보완 선택지를 계산한다.

    Args:
        conn: dict_row 연결.
        requirement: latest_requirement_version 행.

    Returns:
        tuple: (규칙 결과 — 선택 검사용, 응답 모델).
    """
    requirement_id = requirement["requirement_id"]
    rows = await repository.stored_options(conn, requirement_id, requirement["version"])
    first = rows[0] if rows else None
    state, _ = await repository.rnd_check(conn, requirement_id)
    computed = supplement.options_for(
        # 규칙은 판단 상태와 부족 원인만 본다.
        Outcome.model_construct(
            **{
                "status": requirement["status"],
                "deficiency_cause": first["deficiency_cause"] if first else None,
            }
        ),
        first["customer_permission"] if first else "UNSTATED",
        designated=requirement["designated_model"] is not None,
        rnd_check=state,
        approval_question=await repository.has_customer_question(conn, requirement_id),
    )
    candidates = {row["path"]: list(row["candidate_codes"]) for row in rows}
    out = s.SupplementOptions(
        version=requirement["version"],
        data_origin=requirement["data_origin"],
        derivation_kind="DERIVED",
        known_at=requirement["known_at"],
        requirement_id=requirement_id,
        deficiency_cause=computed.deficiency_cause,
        customer_permission=computed.customer_permission,
        permission_clause_id=first["permission_clause_id"] if first else None,
        options=[
            s.SupplementOption(
                **option.model_dump(), candidate_codes=candidates.get(option.path, [])
            )
            for option in computed.options
        ],
    )
    return computed, out


async def get_supplement_options(
    request: Request, principal: Principal, requirement_id: UUID
) -> s.SupplementOptions:
    """15. 보완 경로 선택지.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        requirement_id: 요구 ID.

    Returns:
        s.SupplementOptions: path_rank 순 선택지. 저장 행이 없으면 원인 없음·빈 목록(지정품은 예외).
    """
    async with connection(request.app.state.settings) as conn:
        project = await requirement_project(conn, principal, requirement_id)
        requirement = await repository.latest_requirement_version(
            conn, project["id"], requirement_id
        )
        assert requirement is not None
        _, out = await options_of(conn, requirement)
    return out


def summary_of(requirement: Mapping[str, Any]) -> str:
    """R&D 질문 본문에 넣을 요구 요약(장비·속성·값 단위, 없으면 원문 앞부분).

    Args:
        requirement: 요구 판본 행.

    Returns:
        str: 요약.
    """
    value = " ".join(x for x in (requirement["value"], requirement["unit"]) if x)
    parts = [x for x in (requirement["equipment"], requirement["attribute"], value) if x]
    return " ".join(parts) or requirement["quote"][:80]


def selected_out(row: Mapping[str, Any]) -> s.SupplementSelected:
    """선택 판본 행 → 응답 모델.

    Args:
        row: supplement_selections 행.

    Returns:
        s.SupplementSelected: 응답 선택.
    """
    return s.SupplementSelected.model_validate(
        {k: row[k] for k in s.SupplementSelected.model_fields}
    )


async def select_supplement(
    request: Request,
    principal: Principal,
    requirement_id: UUID,
    payload: s.SupplementSelection,
    key: str,
) -> s.SupplementSelected:
    """16. 보완 경로 선택(사람 결정 새 판본).

    막힌 경로는 409 TFT_INVALID_TRANSITION, 열린 R&D 질문 중 협력사 경로를 사유 없이 고르면 422
    TFT_RND_CHECK_REQUIRED(rnd.check_selection). OWN_DEVELOPMENT를 고르면 열린 R&D 질문이 없을 때 R&D 부서
    대상 내부 질문을 같은 트랜잭션에서 만든다.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        requirement_id: 요구 ID.
        payload: 선택과 읽은 요구 판본.
        key: Idempotency-Key.

    Returns:
        s.SupplementSelected: 저장한 선택 판본.
    """
    async with connection(request.app.state.settings) as conn:
        project = await requirement_project(conn, principal, requirement_id)
        if not can_select_supplement(principal, project):
            raise HTTPException(403)

        async def work() -> dict[str, Any]:
            requirement = await repository.latest_requirement_version(
                conn, project["id"], requirement_id, lock=True
            )
            assert requirement is not None
            if requirement["latest_version"] != payload.version:
                raise repository.version_conflict()
            computed, _ = await options_of(conn, requirement)
            option = next((o for o in computed.options if o.path == payload.path), None)
            state, rnd_question_id = await repository.rnd_check(conn, requirement_id)
            decided = check_selection(
                option, state, payload.rnd_check_waiver_reason, rnd_question_id
            )
            if payload.path == "OWN_DEVELOPMENT" and decided != "PENDING":
                ask = rnd_question_for(
                    summary_of(requirement),
                    requirement["clause_no"],
                    project["review_departments"],
                    await repository.department_kinds(conn),
                )
                await repository.insert_question(
                    conn,
                    project=project,
                    requirement_id=requirement_id,
                    clause_id=requirement["clause_id"],
                    page=requirement["page"],
                    issue_type=ask["issue_type"],
                    question_text=ask["question_text"],
                    internal_assumption=None,
                    target="INTERNAL",
                    target_dept=ask["target_dept"],
                    actor=principal.user_id,
                )
                decided = "PENDING"
            row = await repository.insert_selection(
                conn,
                requirement,
                version=payload.version,
                path=payload.path,
                candidate_codes=payload.candidate_codes,
                role_split=payload.role_split,
                conditions=payload.conditions,
                note=payload.note,
                rnd_check=decided,
                # 면제로 넘긴 선택에만 사유를 남긴다(사유 ⇒ WAIVED, 표 CHECK).
                waiver_reason=payload.rnd_check_waiver_reason if decided == "WAIVED" else None,
                actor=principal.user_id,
            )
            return selected_out(row).model_dump(mode="json")

        result = await commands.run_command(
            conn,
            actor=principal.user_id,
            operation="supplement.select",
            key=key,
            digest=commands.request_hash(
                {"requirement_id": requirement_id}, payload.model_dump(mode="json")
            ),
            project_id=project["id"],
            status_code=200,
            work=work,
        )
    return s.SupplementSelected.model_validate(result)
