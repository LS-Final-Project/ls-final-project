"""결정 패널의 결정 경로(KAN-219). 누르면 바로 확정한다.

결정 하나는 같은 트랜잭션에서 기존 쓰기(변경 제안 작성과 승인, 질문 작성·확인, 보완 선택, 편차 행)를 하고
tft.requirement_decisions에 한 행을 남긴다. 같은 사용자·Idempotency-Key 재전송은 그 행의 결과를 돌려준다.
책임자는 출력 전에 이 기록을 모아서 본다(KAN-220).
"""

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, Request
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from backend.api.tft.projects.access import can_decide_proposal
from backend.api.tft.rfp import commands, repository
from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.rnd import dept_kind, rnd_question_for
from backend.api.tft.rfp.routes_proposals import apply_approval
from backend.api.tft.rfp.routes_supplement import apply_selection, requirement_project, summary_of
from backend.shared.access import Principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError

Row = dict[str, Any]
After = Callable[[UUID], Awaitable[None]]

# 결정으로 치는 행동. 판정 바꾸기는 결정이 아니라 판정을 고치는 것이다.
DECIDING = frozenset(
    {
        "confirm_met",
        "record_deviation",
        "request_check",
        "add_query",
        "select_partner",
        "ask_rnd",
        "assign",
    }
)
VERDICT_WORDS = {
    "MET": "충족",
    "CONFIRM": "충족 확인",
    "DEVIATION": "편차",
    "QUERY": "질의",
    "SUPPLEMENT": "보완",
    "ASSIGN": "배정",
}
# 판정 바꾸기 → 요구 판단(상태, 담당). 판정 계산(KAN-220)은 이 기록을 먼저 본다.
VERDICT_JUDGEMENT: dict[str, tuple[str, str | None]] = {
    "MET": ("EVIDENCE_FOUND", None),
    "CONFIRM": ("CONDITIONAL", None),
    "DEVIATION": ("SPEC_GAP", None),
    "QUERY": ("INSUFFICIENT_EVIDENCE", "CUSTOMER"),
    "SUPPLEMENT": ("INSUFFICIENT_EVIDENCE", "TECH"),
    "ASSIGN": ("INSUFFICIENT_EVIDENCE", "SALES"),
}
OWNER_WORDS = {"SALES": "영업", "TECH": "기술", "PURCHASE": "구매"}
KIND_WORDS = {"TECH": "기술지원", "RND": "R&D", "PURCHASE": "구매", "SALES": "영업"}
NOTE = "결정 패널에서 바로 확정"


async def decide(
    request: Request,
    principal: Principal,
    requirement_id: UUID,
    payload: s.DecisionRequest,
    key: str,
) -> s.DecisionResult:
    """결정 하나를 바로 확정한다. 권한은 제안 결정과 같다(담당 sales·책임자).

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        requirement_id: 요구 ID.
        payload: 결정.
        key: Idempotency-Key.

    Returns:
        s.DecisionResult: 저장한(또는 같은 키로 처음 저장한) 결정.
    """
    digest = commands.request_hash(
        {"requirement_id": requirement_id}, payload.model_dump(mode="json")
    )
    async with connection(request.app.state.settings) as conn:
        project = await requirement_project(conn, principal, requirement_id)
        if not can_decide_proposal(principal, project):
            raise HTTPException(403)
        stored = await replay(conn, principal.user_id, key, digest)
        if stored is not None:
            return s.DecisionResult.model_validate(stored)
        requirement = await repository.latest_requirement_version(
            conn, project["id"], requirement_id, lock=True
        )
        assert requirement is not None
        if requirement["version"] != payload.version:
            raise repository.version_conflict()
        decision_id = uuid4()
        act = ACTIONS[payload.action]
        summary, detail, undoes, question_id, after = await act(
            conn, project, requirement, principal, payload
        )
        latest = await repository.latest_requirement_version(conn, project["id"], requirement_id)
        assert latest is not None
        result = s.DecisionResult(
            decision_id=decision_id,
            action=payload.action,
            summary=summary,
            requirement_id=requirement_id,
            requirement_version=latest["version"],
            question_id=question_id,
        ).model_dump(mode="json")
        try:
            await conn.execute(
                "INSERT INTO tft.requirement_decisions (id, project_id, requirement_id, "
                "base_version, result_version, action, summary, detail, undoes, actor_user_id, "
                "idempotency_key, request_hash, result) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    decision_id,
                    project["id"],
                    requirement_id,
                    requirement["version"],
                    latest["version"],
                    payload.action,
                    summary,
                    Jsonb(detail),
                    undoes,
                    principal.user_id,
                    key,
                    digest,
                    Jsonb(result),
                ),
            )
        except UniqueViolation:
            raise ApplicationError(
                409, "IDEMPOTENCY_CONFLICT", "The same request key is in progress. Retry later."
            ) from None
        for write in after:
            await write(decision_id)
        await commands.audit(
            conn,
            project_id=project["id"],
            entity="requirement",
            entity_id=requirement_id,
            action=f"decision.{payload.action}",
            actor=principal.user_id,
            detail={"summary": summary, "undoes": str(undoes) if undoes else None},
        )
    return s.DecisionResult.model_validate(result)


async def replay(
    conn: AsyncConnection[Row], actor: UUID, key: str, digest: str
) -> dict[str, Any] | None:
    """같은 사용자·키의 결정이 있으면 그 결과. 본문이 다르면 409 IDEMPOTENCY_CONFLICT다."""
    cursor = await conn.execute(
        "SELECT request_hash, result FROM tft.requirement_decisions "
        "WHERE actor_user_id = %s AND idempotency_key = %s",
        (actor, key),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    if row["request_hash"] != digest:
        raise ApplicationError(
            409, "IDEMPOTENCY_CONFLICT", "The key was already used for a different request."
        )
    return dict(row["result"])


Outcome = tuple[str, dict[str, Any], UUID | None, UUID | None, list[After]]


async def judgement(conn: AsyncConnection[Row], requirement: Row) -> dict[str, Any]:
    """제안의 current와 같은 판단 칸에 담당 부서를 더한다(되돌리기가 그대로 되살린다)."""
    current = await repository.judgement_of(conn, requirement)
    return {**current, "next_owner_field": requirement["next_owner_field"]}


async def propose_and_approve(
    conn: AsyncConnection[Row],
    project: Row,
    requirement: Row,
    actor: UUID,
    changes: dict[str, Any],
    rationale: str,
) -> Row:
    """판단을 바꾸는 제안을 쓰고 같은 트랜잭션에서 승인한다. 감사는 작성·승인 둘 다 남는다."""
    current = await repository.judgement_of(conn, requirement)
    row = await repository.insert_web_proposal(
        conn,
        requirement,
        current=current,
        proposed={**current, **changes},
        rationale=rationale,
        evidence_refs=[],
        actor=actor,
    )
    await commands.audit(
        conn,
        project_id=project["id"],
        entity="proposal",
        entity_id=row["id"],
        action="proposal.create",
        actor=actor,
        detail={"to": {"status": row["status"], "origin": "decision"}},
    )
    return await apply_approval(conn, project, row, requirement, actor, NOTE)


async def new_question(
    conn: AsyncConnection[Row],
    project: Row,
    requirement: Row,
    actor: UUID,
    *,
    issue_type: str,
    text: str,
    target: str,
    target_dept: str | None,
) -> Row:
    question = await repository.insert_question(
        conn,
        project=project,
        requirement_id=requirement["requirement_id"],
        clause_id=requirement["clause_id"],
        page=requirement["page"],
        issue_type=issue_type,
        question_text=text,
        internal_assumption=None,
        target=target,
        target_dept=target_dept,
        actor=actor,
    )
    await commands.audit(
        conn,
        project_id=project["id"],
        entity="question",
        entity_id=question["id"],
        action="question.create",
        actor=actor,
        detail={"to": {"target": target, "target_dept": target_dept, "origin": "decision"}},
    )
    return question


def withdraw(question_id: UUID, actor: UUID, conn: AsyncConnection[Row]) -> After:
    async def write(decision_id: UUID) -> None:
        await conn.execute(
            "INSERT INTO tft.question_withdrawals (question_id, decision_id, withdrawn_by) "
            "VALUES (%s,%s,%s) ON CONFLICT (question_id) DO NOTHING",
            (question_id, decision_id, actor),
        )

    return write


def deviation(
    conn: AsyncConnection[Row], requirement: Row, actor: UUID, text: str, state: str
) -> After:
    async def write(decision_id: UUID) -> None:
        await conn.execute(
            "INSERT INTO tft.deviations (id, project_id, requirement_id, requirement_version, text, "
            "state, decision_id, decided_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                uuid4(),
                requirement["project_id"],
                requirement["requirement_id"],
                requirement["version"],
                text,
                state,
                decision_id,
                actor,
            ),
        )

    return write


async def confirm_met(
    conn: AsyncConnection[Row],
    project: Row,
    requirement: Row,
    principal: Principal,
    payload: s.DecisionRequest,
) -> Outcome:
    before = await judgement(conn, requirement)
    await propose_and_approve(
        conn,
        project,
        requirement,
        principal.user_id,
        {"status": "EVIDENCE_FOUND", "reason": "담당자가 요구값 충족을 확인함", "unresolved": []},
        "결정: 충족 확정",
    )
    return "충족 확정", {"from": before}, None, None, []


async def record_deviation(
    conn: AsyncConnection[Row],
    project: Row,
    requirement: Row,
    principal: Principal,
    payload: s.DecisionRequest,
) -> Outcome:
    text = payload.text
    assert text is not None  # DecisionRequest가 행동별 필수 칸을 검사했다.
    after = [deviation(conn, requirement, principal.user_id, text, "RECORDED")]
    return "편차표에 기재", {"deviation_text": text}, None, None, after


async def request_check(
    conn: AsyncConnection[Row],
    project: Row,
    requirement: Row,
    principal: Principal,
    payload: s.DecisionRequest,
) -> Outcome:
    assert payload.target_dept is not None and payload.text is not None
    if payload.target_dept not in project["review_departments"]:
        raise ApplicationError(
            422,
            "VALIDATION_ERROR",
            "Ask a review department of the project.",
            details=[{"field": "target_dept", "reason": "not a review department"}],
        )
    kind = dept_kind(payload.target_dept, await repository.department_kinds(conn))
    question = await new_question(
        conn,
        project,
        requirement,
        principal.user_id,
        issue_type="RND_FEASIBILITY" if kind == "RND" else "TECH_ADVICE",
        text=payload.text,
        target="INTERNAL",
        target_dept=payload.target_dept,
    )
    word = KIND_WORDS.get(kind, payload.target_dept)
    return f"{word}에 확인 요청", {"question_id": str(question["id"])}, None, question["id"], []


async def add_query(
    conn: AsyncConnection[Row],
    project: Row,
    requirement: Row,
    principal: Principal,
    payload: s.DecisionRequest,
) -> Outcome:
    cursor = await conn.execute(
        "SELECT q.id FROM tft.customer_questions q WHERE q.requirement_id = %s "
        "AND q.target = 'CUSTOMER' AND q.status = 'DRAFT' AND NOT EXISTS "
        "(SELECT 1 FROM tft.question_withdrawals w WHERE w.question_id = q.id) "
        "ORDER BY q.created_at DESC LIMIT 1",
        (requirement["requirement_id"],),
    )
    found = await cursor.fetchone()
    draft = await repository.get_question(conn, found["id"], lock=True) if found else None
    after: list[After] = []
    if draft is not None and payload.text in (None, draft["question_text"]):
        question = await repository.confirm_question(conn, draft)
    elif payload.text is None:
        raise ApplicationError(
            422,
            "VALIDATION_ERROR",
            "Write the question.",
            details=[{"field": "text", "reason": "no draft question"}],
        )
    else:
        # 초안 본문은 고칠 수 없는 열이라 고친 문장은 새 질문이 되고 초안은 보내지 않는다.
        issue = (
            draft["issue_type"]
            if draft
            else ("EMPTY_VALUE" if requirement["value"] is None else "CONDITION_MISSING")
        )
        question = await new_question(
            conn,
            project,
            requirement,
            principal.user_id,
            issue_type=issue,
            text=payload.text,
            target="CUSTOMER",
            target_dept=None,
        )
        if draft is not None:
            after.append(withdraw(draft["id"], principal.user_id, conn))
    return "질의서에 넣음", {"question_id": str(question["id"])}, None, question["id"], after


async def select(
    conn: AsyncConnection[Row],
    principal: Principal,
    project: Row,
    requirement: Row,
    path: s.SupplementPath,
    codes: list[str],
    role: str,
) -> None:
    await apply_selection(
        conn,
        principal,
        project,
        requirement["requirement_id"],
        s.SupplementSelection(
            version=requirement["latest_version"], path=path, candidate_codes=codes, role_split=role
        ),
    )


async def select_partner(
    conn: AsyncConnection[Row],
    project: Row,
    requirement: Row,
    principal: Principal,
    payload: s.DecisionRequest,
) -> Outcome:
    codes = payload.candidate_codes
    assert payload.text is not None
    await select(conn, principal, project, requirement, "EXTERNAL_PRODUCT", codes, payload.text)
    return f"{', '.join(codes)} 선정", {"candidate_codes": codes}, None, None, []


async def ask_rnd(
    conn: AsyncConnection[Row],
    project: Row,
    requirement: Row,
    principal: Principal,
    payload: s.DecisionRequest,
) -> Outcome:
    ask = rnd_question_for(
        summary_of(requirement),
        requirement["clause_no"],
        project["review_departments"],
        await repository.department_kinds(conn),
    )
    question = await new_question(
        conn,
        project,
        requirement,
        principal.user_id,
        issue_type="RND_FEASIBILITY",
        text=payload.text or ask["question_text"],
        target="INTERNAL",
        target_dept=ask["target_dept"],
    )
    return "R&D에 개발 문의", {"question_id": str(question["id"])}, None, question["id"], []


async def assign(
    conn: AsyncConnection[Row],
    project: Row,
    requirement: Row,
    principal: Principal,
    payload: s.DecisionRequest,
) -> Outcome:
    assert payload.owner is not None
    before = await judgement(conn, requirement)
    if payload.holder == "PARTNER":
        role = payload.text or "협력사 수행"
        await select(conn, principal, project, requirement, "TASK_SUBCONTRACT", [], role)
    detail: dict[str, Any] = {"holder": payload.holder, "owner": payload.owner}
    if requirement["next_owner_field"] != payload.owner:
        fresh = await repository.latest_requirement_version(
            conn, project["id"], requirement["requirement_id"], lock=True
        )
        assert fresh is not None
        await propose_and_approve(
            conn,
            project,
            fresh,
            principal.user_id,
            {"next_owner_field": payload.owner},
            "결정: 담당 부서 지정",
        )
        detail["from"] = before
    who = "자사" if payload.holder == "OWN" else "협력사"
    return f"{who} 수행, {OWNER_WORDS[payload.owner]} 담당", detail, None, None, []


async def change_verdict(
    conn: AsyncConnection[Row],
    project: Row,
    requirement: Row,
    principal: Principal,
    payload: s.DecisionRequest,
) -> Outcome:
    if payload.verdict == "ASSIGN" and requirement["kind"] not in ("TASK", "DOCUMENT"):
        raise ApplicationError(
            422,
            "VALIDATION_ERROR",
            "Only a task or submission requirement can be assigned.",
            details=[{"field": "verdict", "reason": "not a task requirement"}],
        )
    assert payload.verdict is not None
    before = await judgement(conn, requirement)
    status, owner = VERDICT_JUDGEMENT[payload.verdict]
    word = VERDICT_WORDS[payload.verdict]
    changes: dict[str, Any] = {"status": status, "reason": f"담당자 판정: {word}"}
    if owner is not None:
        changes["next_owner_field"] = owner
    await propose_and_approve(
        conn, project, requirement, principal.user_id, changes, "결정: 판정 바꾸기"
    )
    return f"판정 변경: {word}", {"from": before, "verdict": payload.verdict}, None, None, []


async def undo(
    conn: AsyncConnection[Row],
    project: Row,
    requirement: Row,
    principal: Principal,
    payload: s.DecisionRequest,
) -> Outcome:
    cursor = await conn.execute(
        "SELECT d.*, EXISTS (SELECT 1 FROM tft.requirement_decisions u WHERE u.undoes = d.id) "
        "AS undone FROM tft.requirement_decisions d WHERE d.id = %s AND d.requirement_id = %s",
        (payload.decision_id, requirement["requirement_id"]),
    )
    target = await cursor.fetchone()
    if target is None or target["action"] not in DECIDING | {"change_verdict"}:
        raise ApplicationError(404, "TFT_DECISION_NOT_FOUND", "The decision was not found.")
    if target["undone"]:
        raise ApplicationError(409, "TFT_DECISION_UNDONE", "The decision was already undone.")
    detail = target["detail"]
    after: list[After] = []
    if "from" in detail:
        # 판단을 바꾼 결정은 그 뒤 판본이 지금도 최신일 때만 되돌린다(다른 사람이 바꾼 판단을 덮지 않는다).
        if target["result_version"] != requirement["version"]:
            raise repository.version_conflict()
        await propose_and_approve(
            conn, project, requirement, principal.user_id, dict(detail["from"]), "결정 되돌리기"
        )
    if "deviation_text" in detail:
        after.append(
            deviation(conn, requirement, principal.user_id, detail["deviation_text"], "WITHDRAWN")
        )
    if detail.get("question_id"):
        question = await repository.get_question(conn, UUID(detail["question_id"]))
        # 회신이 온 질문은 기록이므로 철회하지 않는다.
        if question is not None and question["status"] in ("DRAFT", "OPEN"):
            after.append(withdraw(question["id"], principal.user_id, conn))
    # ponytail: 보완 선택은 되돌리지 않는다(NONE이 선택지에 없는 요구가 있다). 다시 고르면 새 선택이 이긴다.
    return (
        f"되돌림: {target['summary']}",
        {"undone_action": target["action"]},
        target["id"],
        None,
        after,
    )


ACTIONS: dict[str, Callable[..., Awaitable[Outcome]]] = {
    "confirm_met": confirm_met,
    "record_deviation": record_deviation,
    "request_check": request_check,
    "add_query": add_query,
    "select_partner": select_partner,
    "ask_rnd": ask_rnd,
    "assign": assign,
    "change_verdict": change_verdict,
    "undo": undo,
}
