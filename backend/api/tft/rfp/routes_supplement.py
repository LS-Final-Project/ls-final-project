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


def is_certification(requirement: Mapping[str, Any]) -> bool:
    """저장 판본으로 인증 요구를 읽는다.

    ponytail: 판본에 추출 kind가 없어 규격 칸으로 읽는다. 어긋나면 kind를 저장한다.

    Args:
        requirement: latest_requirement_version 행.

    Returns:
        bool: 값이 글자이고 규격 체계가 있으며 속성이 비교 어휘에 없으면 True.
    """
    return (
        requirement["operator"] == "TEXT"
        and requirement["standard_system"] != "NONE"
        and requirement["attribute_key"] in (None, "OTHER")
    )


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
    graded = await repository.graded_candidates(conn, requirement_id)
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
        partner_graded=bool(graded),
        own_certification=is_certification(requirement)
        and bool(await repository.latest_comparisons(conn, requirement_id)),
    )
    candidates = {row["path"]: list(row["candidate_codes"]) for row in rows}
    # 처리는 등급보다 먼저 선택지를 저장하므로 외부 조달 후보는 등급에서 읽는다.
    candidates["EXTERNAL_PRODUCT"] = candidates.get("EXTERNAL_PRODUCT") or graded
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


async def check_candidates(
    conn: AsyncConnection[Row],
    requirement_id: UUID,
    options: s.SupplementOptions,
    payload: s.SupplementSelection,
) -> None:
    """고른 후보가 그 경로의 후보이고 제외되지 않았는지 본다. 화면이 막아도 서버가 막는다.

    Args:
        conn: dict_row 연결.
        requirement_id: 요구 ID.
        options: 계산한 선택지(경로별 저장 후보).
        payload: 선택 본문.

    Returns:
        None. 제외 후보는 422 TFT_CANDIDATE_EXCLUDED, 경로 밖 후보는 422 VALIDATION_ERROR다.
    """
    excluded = await repository.excluded_candidates(conn, requirement_id, payload.candidate_codes)
    if excluded:
        raise ApplicationError(
            422,
            "TFT_CANDIDATE_EXCLUDED",
            "An excluded candidate cannot be selected.",
            details=[{"field": "candidate_codes", "reason": code} for code in excluded],
        )
    allowed = next((o.candidate_codes for o in options.options if o.path == payload.path), [])
    outside = sorted(set(payload.candidate_codes) - set(allowed))
    if outside:
        raise ApplicationError(
            422,
            "VALIDATION_ERROR",
            "The candidate is not on this supplement path.",
            details=[{"field": "candidate_codes", "reason": code} for code in outside],
        )


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
            computed, options = await options_of(conn, requirement)
            option = next((o for o in computed.options if o.path == payload.path), None)
            state, rnd_question_id = await repository.rnd_check(conn, requirement_id)
            decided = check_selection(
                option, state, payload.rnd_check_waiver_reason, rnd_question_id
            )
            await check_candidates(conn, requirement_id, options, payload)
            # 열린 질문이 있거나 가능하다고 답을 받았으면 다시 묻지 않는다. 답을 받은 뒤 같은
            # 경로를 다시 저장해 재검토를 푸는 흐름이 질문을 되살리면 끝나지 않는다.
            # 불가 답이면 경로가 막혀 여기 오지 않는다.
            if payload.path == "OWN_DEVELOPMENT" and decided not in (
                "PENDING",
                "CONFIRMED_FEASIBLE",
            ):
                ask = rnd_question_for(
                    summary_of(requirement),
                    requirement["clause_no"],
                    project["review_departments"],
                    await repository.department_kinds(conn),
                )
                question = await repository.insert_question(
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
                await commands.audit(
                    conn,
                    project_id=project["id"],
                    entity="question",
                    entity_id=question["id"],
                    action="question.create",
                    actor=principal.user_id,
                    detail={"to": {"target": "INTERNAL", "target_dept": ask["target_dept"]}},
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
            await repository.clear_rereview(conn, requirement_id, principal.user_id)
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
