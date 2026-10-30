"""경로 18·19·20: 자문·고객 질문 목록·작성·회신 기록(KAN-189 PR-B).

범위 밖 프로젝트·질문은 없는 것과 같이 404, 볼 수는 있지만 쓸 수 없으면 403이다. 쓰기는 commands.run_command로
멱등 처리하고, 상태에 따른 검사(판본 409 등)는 재전송이 첫 결과를 받도록 기록 안쪽(work)에 둔다.
"""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request
from psycopg import AsyncConnection

from backend.api.tft.projects.access import can_create_question, can_reply, can_view_rfp
from backend.api.tft.rfp import commands, repository
from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.rnd import check_reply_fields
from backend.shared.access import Principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError

Row = dict[str, Any]


async def visible_project(
    conn: AsyncConnection[Row], principal: Principal, project_id: UUID, code: str, message: str
) -> Row:
    """RFP를 볼 수 있는 프로젝트 행. 없거나 범위 밖이면 같은 404다.

    Args:
        conn: dict_row 연결.
        principal: 요청 사용자.
        project_id: 프로젝트 ID.
        code: 404 오류 코드(경로가 가리키는 자원).
        message: 404 메시지.

    Returns:
        Row: project_scope 행.
    """
    project = await repository.project_scope(conn, project_id)
    if project is None or not can_view_rfp(principal, project):
        raise ApplicationError(404, code, message)
    return project


def question_out(row: Mapping[str, Any]) -> s.Question:
    """질문 행 → 응답 모델.

    Args:
        row: repository.get_question 형식 행.

    Returns:
        s.Question: 응답 질문.
    """
    return s.Question(
        version=row["version"],
        data_origin=row["data_origin"],
        derivation_kind=row["derivation_kind"],
        known_at=row["known_at"],
        question_id=row["id"],
        requirement_id=row["requirement_id"],
        origin=s.ClauseRef(clause_id=row["clause_id"], page=row["page"]),
        issue_type=row["issue_type"],
        question_text=row["question_text"],
        internal_assumption=row["internal_assumption"],
        status=row["status"],
        reply_received_at=row["reply_received_at"],
        created_by=row["created_by"],
        created_at=row["created_at"],
        target=row["target"],
        target_dept=row["target_dept"],
        rnd_feasibility=row["rnd_feasibility"],
    )


def proposal_out(row: Mapping[str, Any]) -> s.Proposal:
    """변경 제안 행 → 응답 모델.

    Args:
        row: change_proposals 행.

    Returns:
        s.Proposal: 응답 제안.
    """
    return s.Proposal.model_validate(
        {
            **{k: row[k] for k in s.Proposal.model_fields if k in row},
            "proposal_id": row["id"],
        }
    )


async def list_questions(
    request: Request,
    principal: Principal,
    project_id: UUID,
    *,
    status: str | None,
    requirement_id: UUID | None,
    target: str | None,
    target_dept: str | None,
) -> s.QuestionList:
    """18. 질문 목록.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        project_id: 프로젝트 ID.
        status: OPEN·REPLIED 필터.
        requirement_id: 요구 필터.
        target: CUSTOMER·INTERNAL 필터.
        target_dept: 대상 부서 필터.

    Returns:
        s.QuestionList: 작성 순 질문.
    """
    async with connection(request.app.state.settings) as conn:
        await visible_project(
            conn, principal, project_id, "TFT_PROJECT_NOT_FOUND", "The project was not found."
        )
        rows = await repository.list_questions(
            conn,
            project_id,
            status=status,
            requirement_id=requirement_id,
            target=target,
            target_dept=target_dept,
        )
    return s.QuestionList(items=[question_out(row) for row in rows])


async def create_question(
    request: Request, principal: Principal, project_id: UUID, payload: s.QuestionCreate, key: str
) -> s.Question:
    """19. 질문 작성. INTERNAL은 프로젝트 검토 부서 안의 target_dept만 받는다.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        project_id: 프로젝트 ID.
        payload: 질문 본문.
        key: Idempotency-Key.

    Returns:
        s.Question: 저장한(또는 같은 키로 처음 저장했던) 질문.
    """
    async with connection(request.app.state.settings) as conn:
        project = await visible_project(
            conn, principal, project_id, "TFT_PROJECT_NOT_FOUND", "The project was not found."
        )
        if not can_create_question(principal, project):
            raise HTTPException(403)

        async def work() -> dict[str, Any]:
            if (
                payload.target == "INTERNAL"
                and payload.target_dept not in project["review_departments"]
            ):
                raise ApplicationError(
                    422,
                    "VALIDATION_ERROR",
                    "Ask a review department of the project.",
                    details=[{"field": "target_dept", "reason": "not a review department"}],
                )
            if payload.requirement_id is not None and (
                await repository.latest_requirement_version(
                    conn, project_id, payload.requirement_id
                )
                is None
            ):
                raise ApplicationError(
                    404, "TFT_REQUIREMENT_NOT_FOUND", "The requirement was not found."
                )
            clause_id = payload.origin.clause_id
            if clause_id is not None and not await repository.clause_in_project(
                conn, project_id, clause_id
            ):
                raise ApplicationError(
                    422,
                    "VALIDATION_ERROR",
                    "The clause is not in this project.",
                    details=[{"field": "origin.clause_id", "reason": "not in the project"}],
                )
            assumption = payload.internal_assumption
            row = await repository.insert_question(
                conn,
                project=project,
                requirement_id=payload.requirement_id,
                clause_id=clause_id,
                page=payload.origin.page,
                issue_type=payload.issue_type,
                question_text=payload.question_text,
                internal_assumption=assumption.model_dump(mode="json") if assumption else None,
                target=payload.target,
                target_dept=payload.target_dept,
                actor=principal.user_id,
            )
            return question_out(row).model_dump(mode="json")

        result = await commands.run_command(
            conn,
            actor=principal.user_id,
            operation="question.create",
            key=key,
            digest=commands.request_hash(
                {"project_id": project_id}, payload.model_dump(mode="json")
            ),
            project_id=project_id,
            status_code=201,
            work=work,
        )
    return s.Question.model_validate(result)


def without_answered(unresolved: list[str], question_text: str) -> list[str]:
    """질문 본문에 그대로 들어 있는 미해결 항목을 뺀다(질문과 항목을 잇는 열이 없어서다, rfp-data.md).

    Args:
        unresolved: 요구 판본의 미해결 항목.
        question_text: 회신된 질문 본문.

    Returns:
        list[str]: 남는 미해결 항목.
    """
    return [item for item in unresolved if not (item and item in question_text)]


async def reply_question(
    request: Request, principal: Principal, question_id: UUID, payload: s.QuestionReply, key: str
) -> s.QuestionReplyResult:
    """20. 회신 기록. CUSTOMER 회신이 미해결 항목을 줄이면 customer_reply 변경 제안을 만든다.

    INTERNAL 회신은 제안을 만들지 않는다. rnd_check·needs_rereview는 읽을 때 계산하므로 저장할 것이 없다.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        question_id: 질문 ID.
        payload: 회신 본문과 읽은 질문 판본.
        key: Idempotency-Key.

    Returns:
        s.QuestionReplyResult: 회신된 질문과 만든 제안(없으면 null).
    """
    async with connection(request.app.state.settings) as conn:
        question = await repository.get_question(conn, question_id)
        if question is None:
            raise ApplicationError(404, "TFT_QUESTION_NOT_FOUND", "The question was not found.")
        project = await visible_project(
            conn,
            principal,
            question["project_id"],
            "TFT_QUESTION_NOT_FOUND",
            "The question was not found.",
        )
        if not can_reply(principal, project, question):
            raise HTTPException(403)

        async def work() -> dict[str, Any]:
            locked = await repository.get_question(conn, question_id, lock=True)
            assert locked is not None
            check_reply_fields(
                locked["target"],
                locked["target_dept"],
                await repository.department_kinds(conn),
                payload.rnd_feasibility,
                payload.earliest_available_on,
                payload.schedule_fits,
            )
            replied = await repository.insert_reply(
                conn,
                locked,
                version=payload.version,
                reply_text=payload.reply_text,
                received_at=payload.received_at,
                source_ref=payload.source_ref,
                rnd_feasibility=payload.rnd_feasibility,
                earliest_available_on=payload.earliest_available_on,
                schedule_fits=payload.schedule_fits,
                actor=principal.user_id,
            )
            proposal = None
            if replied["target"] == "CUSTOMER" and replied["requirement_id"] is not None:
                requirement = await repository.latest_requirement_version(
                    conn, project["id"], replied["requirement_id"], lock=True
                )
                assert requirement is not None
                current = await repository.judgement_of(conn, requirement)
                left = without_answered(current["unresolved"], replied["question_text"])
                if left != current["unresolved"]:
                    proposal = await repository.insert_reply_proposal(
                        conn,
                        replied,
                        requirement,
                        current=current,
                        proposed={**current, "unresolved": left},
                        reply_text=payload.reply_text,
                    )
            result = s.QuestionReplyResult(
                **question_out(replied).model_dump(),
                created_proposal=proposal_out(proposal) if proposal else None,
            )
            return result.model_dump(mode="json")

        result = await commands.run_command(
            conn,
            actor=principal.user_id,
            operation="question.reply",
            key=key,
            digest=commands.request_hash(
                {"question_id": question_id}, payload.model_dump(mode="json")
            ),
            project_id=project["id"],
            status_code=200,
            work=work,
        )
    return s.QuestionReplyResult.model_validate(result)
