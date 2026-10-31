"""경로 21~24: 변경 제안 목록·작성·승인·기각(KAN-189).

요구의 상태·성격·조건·사유는 제안 승인으로만 바뀐다. 승인은 요구의 새 판본(human_input)을 쓰고 연결 객체에 재검토
표시를 남긴다. EVIDENCE_FOUND 승인은 요구 자체에는 남기지 않는다. 표시는 같은 요구의 보완 선택을 다시 저장하면
해소된다(routes_supplement). 범위 밖은 404, 볼 수만 있으면 403이다. 시연에서는 자기 제안을 자기가 승인할 수 있다.
"""

from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request
from psycopg import AsyncConnection

from backend.api.tft.projects.access import can_decide_proposal, can_propose_change, can_view_rfp
from backend.api.tft.rfp import commands, repository
from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.routes_questions import proposal_out, visible_project
from backend.api.tft.rfp.routes_read import requirement_out
from backend.api.tft.rfp.routes_supplement import options_of
from backend.shared.access import Principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError

Row = dict[str, Any]
PROJECT_NOT_FOUND = ("TFT_PROJECT_NOT_FOUND", "The project was not found.")


async def list_proposals(
    request: Request,
    principal: Principal,
    project_id: UUID,
    *,
    status: str | None,
    origin: str | None,
    requirement_id: UUID | None,
) -> s.ProposalList:
    """21. 승인 대기 목록(세 입구 공통). 조회 가능한 사용자 모두 본다.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        project_id: 프로젝트 ID.
        status: 상태 필터.
        origin: 입구 필터.
        requirement_id: 요구 필터.

    Returns:
        s.ProposalList: 작성 순 제안.
    """
    async with connection(request.app.state.settings) as conn:
        await visible_project(conn, principal, project_id, *PROJECT_NOT_FOUND)
        rows = await repository.list_change_proposals(
            conn, project_id, requirement_id, status=status, origin=origin
        )
    return s.ProposalList(items=[proposal_out(row) for row in rows])


async def create_proposal(
    request: Request, principal: Principal, project_id: UUID, payload: s.ProposalCreate, key: str
) -> s.Proposal:
    """22. 웹 수정 제안. 값이 온 칸만 바꾸고 나머지는 지금 판단을 잇는다.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        project_id: 프로젝트 ID.
        payload: 제안 본문.
        key: Idempotency-Key.

    Returns:
        s.Proposal: 저장한(또는 같은 키로 처음 저장한) 제안.
    """
    async with connection(request.app.state.settings) as conn:
        project = await visible_project(conn, principal, project_id, *PROJECT_NOT_FOUND)
        if not can_propose_change(principal, project):
            raise HTTPException(403)

        async def work() -> dict[str, Any]:
            requirement = await repository.latest_requirement_version(
                conn, project_id, payload.requirement_id, lock=True
            )
            if requirement is None:
                raise ApplicationError(
                    404, "TFT_REQUIREMENT_NOT_FOUND", "The requirement was not found."
                )
            current = await repository.judgement_of(conn, requirement)
            # exclude_none은 조건 안의 unit까지 지우므로 위 칸만 거른다.
            changes = payload.proposed.model_dump()
            proposed = {**current, **{k: v for k, v in changes.items() if v is not None}}
            # 코드가 건 원문 확인 표시가 있으면 값을 그대로 두는 '맞다' 확인도 제안이다.
            if proposed == current and not requirement["needs_review"]:
                raise ApplicationError(
                    422,
                    "VALIDATION_ERROR",
                    "The proposal changes nothing.",
                    details=[{"field": "proposed", "reason": "same as the current judgement"}],
                )
            row = await repository.insert_web_proposal(
                conn,
                requirement,
                current=current,
                proposed=proposed,
                rationale=payload.rationale,
                evidence_refs=payload.evidence_refs,
                actor=principal.user_id,
            )
            return proposal_out(row).model_dump(mode="json")

        result = await commands.run_command(
            conn,
            actor=principal.user_id,
            operation="proposal.create",
            key=key,
            digest=commands.request_hash(
                {"project_id": project_id}, payload.model_dump(mode="json")
            ),
            project_id=project_id,
            status_code=201,
            work=work,
        )
    return s.Proposal.model_validate(result)


async def decidable_project(
    conn: AsyncConnection[Row], principal: Principal, proposal_id: UUID
) -> Row:
    """결정할 수 있는 제안의 프로젝트. 범위 밖은 404, 볼 수만 있으면 403이다.

    Args:
        conn: dict_row 연결.
        principal: 요청 사용자.
        proposal_id: 제안 ID.

    Returns:
        Row: project_scope 행.
    """
    proposal = await repository.get_proposal(conn, proposal_id)
    project = await repository.project_scope(conn, proposal["project_id"]) if proposal else None
    if project is None or not can_view_rfp(principal, project):
        raise ApplicationError(404, "TFT_PROPOSAL_NOT_FOUND", "The proposal was not found.")
    if not can_decide_proposal(principal, project):
        raise HTTPException(403)
    return project


async def pending_proposal(conn: AsyncConnection[Row], proposal_id: UUID, version: int) -> Row:
    """결정 전 제안을 잠그고 판본을 확인한다.

    Args:
        conn: 결정과 같은 트랜잭션.
        proposal_id: 제안 ID.
        version: 요청이 읽은 제안 판본.

    Returns:
        Row: change_proposals 행. 이미 결정했으면 409 TFT_PROPOSAL_DECIDED, 판본이 다르면 409다.
    """
    proposal = await repository.get_proposal(conn, proposal_id, lock=True)
    if proposal is None:
        raise ValueError("TFT_PROPOSAL_MISSING")
    if proposal["status"] != "PENDING":
        raise ApplicationError(409, "TFT_PROPOSAL_DECIDED", "The proposal was already decided.")
    if proposal["version"] != version:
        raise repository.version_conflict()
    return proposal


async def approve_proposal(
    request: Request,
    principal: Principal,
    proposal_id: UUID,
    payload: s.ProposalDecision,
    key: str,
) -> s.RequirementDetail:
    """23. 승인. 제안이 본 요구 판본이 최신일 때만 새 판본을 쓴다.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        proposal_id: 제안 ID.
        payload: 제안 판본과 메모.
        key: Idempotency-Key.

    Returns:
        s.RequirementDetail: 승인으로 만든 요구 판본의 상세.
    """
    async with connection(request.app.state.settings) as conn:
        project = await decidable_project(conn, principal, proposal_id)

        async def work() -> dict[str, Any]:
            proposal = await pending_proposal(conn, proposal_id, payload.version)
            requirement_id = proposal["requirement_id"]
            if requirement_id is None:
                raise ApplicationError(
                    422,
                    "VALIDATION_ERROR",
                    "A note without a requirement can only be rejected.",
                    details=[{"field": "requirement_id", "reason": "clause unresolved"}],
                )
            requirement = await repository.latest_requirement_version(
                conn, project["id"], requirement_id, lock=True
            )
            if requirement is None:
                raise ValueError("TFT_REQUIREMENT_MISSING")
            if requirement["version"] != proposal["base_requirement_version"]:
                raise repository.version_conflict()
            await repository.insert_approved_version(conn, requirement, proposal, principal.user_id)
            await repository.decide_proposal(
                conn, proposal_id, "APPROVED", principal.user_id, payload.note
            )
            # 근거 확인으로 승인한 것 자체가 사람 재확인이고, 그 요구는 고를 보완 경로도 없다.
            latest = await repository.latest_requirement_version(
                conn, project["id"], requirement_id
            )
            if latest is None:
                raise ValueError("TFT_REQUIREMENT_MISSING")
            # 값이 그대로면 연결 객체가 볼 변화가 없어 재검토 표시를 남기지 않는다.
            if proposal["proposed"] != proposal["current"]:
                # 요구 표시는 보완 저장으로만 풀린다. 고를 경로가 없으면 남기지 않는다.
                options, _ = await options_of(conn, latest)
                await repository.mark_rereview(
                    conn,
                    project["id"],
                    requirement_id,
                    proposal_id,
                    requirement_too=proposal["proposed"]["status"] != "EVIDENCE_FOUND"
                    and any(option.allowed for option in options.options),
                )
            await commands.audit(
                conn,
                project_id=project["id"],
                entity="proposal",
                entity_id=proposal_id,
                action="proposal.approve",
                actor=principal.user_id,
                proposer=proposal["proposer_user_id"],
                approver=principal.user_id,
                detail={
                    "from": proposal["current"],
                    "to": proposal["proposed"],
                    "needs_review_confirmed": requirement["needs_review"],
                },
            )
            detail = await requirement_out(conn, latest)
            return detail.model_dump(mode="json")

        result = await commands.run_command(
            conn,
            actor=principal.user_id,
            operation="proposal.approve",
            key=key,
            digest=commands.request_hash(
                {"proposal_id": proposal_id}, payload.model_dump(mode="json")
            ),
            project_id=project["id"],
            status_code=200,
            work=work,
        )
    return s.RequirementDetail.model_validate(result)


async def reject_proposal(
    request: Request,
    principal: Principal,
    proposal_id: UUID,
    payload: s.ProposalRejection,
    key: str,
) -> s.Proposal:
    """24. 기각. 요구는 그대로다.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        proposal_id: 제안 ID.
        payload: 제안 판본과 사유.
        key: Idempotency-Key.

    Returns:
        s.Proposal: 기각한 제안.
    """
    async with connection(request.app.state.settings) as conn:
        project = await decidable_project(conn, principal, proposal_id)

        async def work() -> dict[str, Any]:
            await pending_proposal(conn, proposal_id, payload.version)
            row = await repository.decide_proposal(
                conn, proposal_id, "REJECTED", principal.user_id, payload.reason
            )
            return proposal_out(row).model_dump(mode="json")

        result = await commands.run_command(
            conn,
            actor=principal.user_id,
            operation="proposal.reject",
            key=key,
            digest=commands.request_hash(
                {"proposal_id": proposal_id}, payload.model_dump(mode="json")
            ),
            project_id=project["id"],
            status_code=200,
            work=work,
        )
    return s.Proposal.model_validate(result)
