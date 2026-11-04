"""경로 18·19·20·31: 자문·고객 질문 목록·작성·회신 기록(KAN-189 PR-B)과 초안 확인(KAN-199).

범위 밖 프로젝트·질문은 없는 것과 같이 404, 볼 수는 있지만 쓸 수 없으면 403이다. 쓰기는 commands.run_command로
멱등 처리하고, 상태에 따른 검사(판본 409 등)는 재전송이 첫 결과를 받도록 기록 안쪽(work)에 둔다.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from psycopg import AsyncConnection
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.api.tft.projects.access import can_create_question, can_reply, can_view_rfp
from backend.api.tft.rfp import commands, reply_read, repository
from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.rnd import check_reply_fields
from backend.engine.rfp.reply_values import read_reply_value
from backend.shared.access import Principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError

Row = dict[str, Any]


class TftReplySettings(BaseSettings):
    """회신 값 읽기의 AI 보조(KAN-224). 켜면 규칙이 못 읽은 고객 회신을 worker 작업(rfp.reply_read)으로 넘긴다.

    API 프로세스는 모델 클라이언트를 만들지 않는다. 모델·비용 원장은 worker 쪽 설정(TFT_REPLY_READ_*)이다.
    """

    model_config = SettingsConfigDict(
        env_prefix="TFT_REPLY_AI_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    enabled: bool = False


def configure_reply_reader(app: FastAPI, value: TftReplySettings) -> None:
    """회신 AI 읽기 작업 접수 여부를 앱에 둔다.

    Args:
        app: TFT 앱.
        value: 설정.
    """
    app.state.reply_ai_enabled = value.enabled


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
        reply=s.RecordedReply(
            reply_id=row["reply_id"],
            reply_text=row["reply_text"],
            source_ref=row["reply_source_ref"],
            received_at=row["reply_received_at"],
            recorded_at=row["reply_recorded_at"],
            recorded_by=row["recorded_by"],
            rnd_feasibility=row["rnd_feasibility"],
            earliest_available_on=row["earliest_available_on"],
            schedule_fits=row["schedule_fits"],
        )
        if row.get("reply_id")
        else None,
        created_by=row["created_by"],
        created_at=row["created_at"],
        target=row["target"],
        target_dept=row["target_dept"],
        rnd_feasibility=row["rnd_feasibility"],
        follows_question_id=row.get("follows_question_id"),
        value_candidate=row.get("value_candidate"),
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
        status: DRAFT·OPEN·REPLIED 필터.
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
        from backend.api.tft.rfp.bulk import sends, waiting_days

        sent = await sends(conn, [row["id"] for row in rows])
    now = datetime.now(UTC)
    items = []
    for row in rows:
        question = question_out(row)
        if (send := sent.get(question.question_id)) is not None:
            question.sent_at = send.sent_at
            question.waiting_days = waiting_days(question, send, now)
        items.append(question)
    return s.QuestionList(items=items)


async def check_question(
    conn: AsyncConnection[Row], project: Row, payload: s.QuestionCreate
) -> None:
    """질문 한 건의 서버 검사(부서 범위·요구·조항). 쓰지 않는다. 단건·묶음 작성이 같이 쓴다.

    Args:
        conn: dict_row 연결.
        project: project_scope 행.
        payload: 질문 본문.

    Returns:
        None. 어긋나면 ApplicationError(422 VALIDATION_ERROR·404 TFT_REQUIREMENT_NOT_FOUND).
    """
    if payload.target == "INTERNAL" and payload.target_dept not in project["review_departments"]:
        raise ApplicationError(
            422,
            "VALIDATION_ERROR",
            "Ask a review department of the project.",
            details=[{"field": "target_dept", "reason": "not a review department"}],
        )
    if payload.requirement_id is not None and (
        await repository.latest_requirement_version(conn, project["id"], payload.requirement_id)
        is None
    ):
        raise ApplicationError(404, "TFT_REQUIREMENT_NOT_FOUND", "The requirement was not found.")
    if payload.follows_question_id is not None:
        original = await repository.get_question(conn, payload.follows_question_id)
        if original is None or original["project_id"] != project["id"]:
            raise ApplicationError(
                422,
                "VALIDATION_ERROR",
                "The original question is not in this project.",
                details=[{"field": "follows_question_id", "reason": "not in the project"}],
            )
    clause_id = payload.origin.clause_id
    if clause_id is not None and not await repository.clause_in_project(
        conn, project["id"], clause_id
    ):
        raise ApplicationError(
            422,
            "VALIDATION_ERROR",
            "The clause is not in this project.",
            details=[{"field": "origin.clause_id", "reason": "not in the project"}],
        )


async def store_question(
    conn: AsyncConnection[Row], project: Row, payload: s.QuestionCreate, actor: UUID
) -> dict[str, Any]:
    """검사를 마친 질문 한 건을 저장하고 응답 data를 돌려준다.

    Args:
        conn: 쓰기 트랜잭션.
        project: project_scope 행.
        payload: 질문 본문.
        actor: 작성자.

    Returns:
        dict: Question 응답 data.
    """
    assumption = payload.internal_assumption
    row = await repository.insert_question(
        conn,
        project=project,
        requirement_id=payload.requirement_id,
        clause_id=payload.origin.clause_id,
        page=payload.origin.page,
        issue_type=payload.issue_type,
        question_text=payload.question_text,
        internal_assumption=assumption.model_dump(mode="json") if assumption else None,
        target=payload.target,
        target_dept=payload.target_dept,
        actor=actor,
        follows_question_id=payload.follows_question_id,
    )
    return question_out(row).model_dump(mode="json")


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
            await check_question(conn, project, payload)
            return await store_question(conn, project, payload, principal.user_id)

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


async def create_questions_batch(
    request: Request,
    principal: Principal,
    project_id: UUID,
    payload: s.QuestionBatchCreate,
    key: str,
) -> s.QuestionList:
    """19-b. 질문 묶음 작성. 한 건이라도 검사에 걸리면 아무것도 저장하지 않고 422(index별 사유)다.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        project_id: 프로젝트 ID.
        payload: 질문 1~50건.
        key: Idempotency-Key.

    Returns:
        s.QuestionList: 입력 순서대로 저장한(또는 같은 키로 처음 저장했던) 질문.
    """
    async with connection(request.app.state.settings) as conn:
        project = await visible_project(
            conn, principal, project_id, "TFT_PROJECT_NOT_FOUND", "The project was not found."
        )
        if not can_create_question(principal, project):
            raise HTTPException(403)

        async def work() -> dict[str, Any]:
            problems: list[dict[str, str]] = []
            for index, item in enumerate(payload.items):
                try:
                    if item.requirement_id is None:
                        raise ApplicationError(
                            422, "VALIDATION_ERROR", "A requirement is required."
                        )
                    await check_question(conn, project, item)
                except ApplicationError as error:
                    problems.append(
                        {"index": str(index), "code": error.code, "message": error.message}
                    )
            if problems:
                raise ApplicationError(
                    422, "VALIDATION_ERROR", "Some questions are invalid.", details=problems
                )
            items = []
            for item in payload.items:
                stored = await store_question(conn, project, item, principal.user_id)
                await commands.audit_result(
                    conn, project_id, "question.create", principal.user_id, stored
                )
                items.append(stored)
            return {"items": items}

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
            audit=False,
        )
    return s.QuestionList.model_validate(result)


async def confirm_question(
    request: Request, principal: Principal, project_id: UUID, question_id: UUID, key: str
) -> s.Question:
    """31. 처리 함수가 만든 초안을 사람이 확인해 OPEN으로 바꾼다. 권한은 질문 작성과 같다.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        project_id: 프로젝트 ID.
        question_id: 질문 ID.
        key: Idempotency-Key.

    Returns:
        s.Question: 확인한(또는 같은 키로 처음 확인했던) 질문. OPEN·REPLIED는 409다.
    """
    async with connection(request.app.state.settings) as conn:
        project = await visible_project(
            conn, principal, project_id, "TFT_PROJECT_NOT_FOUND", "The project was not found."
        )
        if not can_create_question(principal, project):
            raise HTTPException(403)

        async def work() -> dict[str, Any]:
            question = await repository.get_question(conn, question_id, lock=True)
            if question is None or question["project_id"] != project_id:
                raise ApplicationError(404, "TFT_QUESTION_NOT_FOUND", "The question was not found.")
            row = await repository.confirm_question(conn, question)
            return question_out(row).model_dump(mode="json")

        result = await commands.run_command(
            conn,
            actor=principal.user_id,
            operation="question.confirm",
            key=key,
            digest=commands.request_hash(
                {"project_id": project_id, "question_id": question_id}, {}
            ),
            project_id=project_id,
            status_code=200,
            work=work,
        )
    return s.Question.model_validate(result)


async def reply_question(
    request: Request, principal: Principal, question_id: UUID, payload: s.QuestionReply, key: str
) -> s.QuestionReplyResult:
    """20. 회신 근거 기록. 해결 여부와 요구 변경은 별도 명시적 변경 제안으로 검토한다.

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
        inputs = await reply_read.reading_inputs(conn, question)
        # 규칙만 읽는다. 못 읽으면 아래에서 worker 작업으로 넘긴다(API는 모델을 부르지 않는다).
        candidate = read_reply_value(payload.reply_text, **inputs) if inputs else None

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
                value_candidate=candidate.model_dump(mode="json") if candidate else None,
            )
            # A reply records evidence. Only an explicit reviewed proposal can resolve items.
            result = s.QuestionReplyResult(**question_out(replied).model_dump())
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
    replied = s.QuestionReplyResult.model_validate(result)
    if ai_wanted(request, replied.value_candidate) and replied.reply is not None:
        await reply_read.enqueue(request, principal, project["id"], replied.reply.reply_id, key)
        async with connection(request.app.state.settings) as conn:
            row = await repository.get_question(conn, question_id)
        assert row is not None
        replied.value_candidate = question_out(row).value_candidate
    return replied


def ai_wanted(request: Request, candidate: s.ReplyValueCandidate | None) -> bool:
    """규칙이 못 읽은 고객 회신이고 AI 읽기가 켜져 있는지."""
    return (
        candidate is not None
        and candidate.method != "rule"
        and bool(getattr(request.app.state, "reply_ai_enabled", False))
    )


async def read_again(
    request: Request, principal: Principal, question_id: UUID, key: str
) -> s.Question:
    """회신 값을 AI로 다시 읽는다(접수 못 했거나 실패했을 때). 같은 키 재전송은 같은 작업이다.

    Args:
        request: 요청(설정·플랫폼 클라이언트).
        principal: 요청 사용자(회신을 기록할 수 있는 사람).
        question_id: 질문 ID.
        key: Idempotency-Key.

    Returns:
        s.Question: 질문(value_candidate.ai에 새 상태).
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
    current = question_out(question).value_candidate
    if question["reply_id"] is None or current is None:
        raise ApplicationError(409, "TFT_REPLY_NOT_READABLE", "There is no customer reply to read.")
    if not getattr(request.app.state, "reply_ai_enabled", False):
        raise ApplicationError(503, "TFT_REPLY_AI_DISABLED", "AI reading of replies is turned off.")
    # 대기(QUEUED)도 다시 접수할 수 있다. worker가 실패로 끝나 결과 행이 없을 수 있다. 같은 프로젝트에 진행 중인
    # 작업이 있으면 플랫폼이 409로 막아 UNAVAILABLE이 된다.
    if current.method in ("rule", "ai"):
        raise ApplicationError(409, "TFT_REPLY_ALREADY_READ", "The reply already has a value.")
    await reply_read.enqueue(request, principal, project["id"], question["reply_id"], key)
    async with connection(request.app.state.settings) as conn:
        row = await repository.get_question(conn, question_id)
    assert row is not None
    return question_out(row)
