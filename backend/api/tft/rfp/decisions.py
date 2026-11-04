"""결정 패널의 결정 경로(KAN-219). 누르면 바로 확정한다.

결정 하나는 같은 트랜잭션에서 기존 쓰기(변경 제안 작성과 승인, 질문 작성·확인, 보완 선택, 편차 행)를 하고
tft.requirement_decisions에 한 행을 남긴다. 같은 사용자·Idempotency-Key 재전송은 그 행의 결과를 돌려준다.
책임자는 출력 전에 이 기록을 모아서 본다(KAN-220).
"""

from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from uuid import UUID, uuid4

from fastapi import Request
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

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
    """결정 하나를 바로 확정한다. 결정은 담당 sales만, 책임자 확인·되돌려 보내기는 책임자만 한다.

    결정한 사람과 확인한 사람이 같으면 확인이 의미를 잃으므로 책임자는 결정하지 않는다(2026-11-04 사용자 확정).

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        requirement_id: 요구 ID.
        payload: 결정.
        key: Idempotency-Key.

    Returns:
        s.DecisionResult: 저장한(또는 같은 키로 처음 저장한) 결정.
    """
    # 본체는 decide_on(같은 트랜잭션에서 여러 건을 부르는 일괄 결정과 공유).
    async with connection(request.app.state.settings) as conn:
        result = await decide_on(conn, principal, requirement_id, payload, key)
    return s.DecisionResult.model_validate(result)


async def decide_on(
    conn: AsyncConnection[Row],
    principal: Principal,
    requirement_id: UUID,
    payload: s.DecisionRequest,
    key: str,
    *,
    project_id: UUID | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """decide의 본체. 호출자의 트랜잭션에서 돈다(일괄 결정 KAN-228이 여러 번 부른다).

    Args:
        project_id: 있으면 요구가 이 프로젝트 것이어야 한다(아니면 404).
        extra: 결정 기록 detail에 더할 칸(일괄 묶음 ID 등).

    Returns:
        dict: DecisionResult JSON.
    """
    digest = commands.request_hash(
        {"requirement_id": requirement_id}, payload.model_dump(mode="json")
    )
    project = await requirement_project(conn, principal, requirement_id)
    if project_id is not None and project["id"] != project_id:
        raise ApplicationError(404, "TFT_REQUIREMENT_NOT_FOUND", "The requirement was not found.")
    check_role(project, principal, payload.action)
    stored = await replay(conn, principal.user_id, key, digest)
    if stored is not None:
        return stored
    requirement = await repository.latest_requirement_version(
        conn, project["id"], requirement_id, lock=True
    )
    assert requirement is not None
    if requirement["version"] != payload.version:
        raise repository.version_conflict()
    if payload.action not in ON_EXCLUDED and await is_excluded(conn, requirement_id):
        raise ApplicationError(
            409, "TFT_REQUIREMENT_EXCLUDED", "The requirement is excluded. Restore it first."
        )
    decision_id = uuid4()
    act = ACTIONS[payload.action]
    summary, detail, undoes, question_id, after = await act(
        conn, project, requirement, principal, payload
    )
    latest = await repository.latest_requirement_version(conn, project["id"], requirement_id)
    assert latest is not None
    result = await record(
        conn,
        decision_id=decision_id,
        project_id=project["id"],
        requirement_id=requirement_id,
        base_version=requirement["version"],
        result_version=latest["version"],
        action=payload.action,
        summary=summary,
        detail={**detail, **(extra or {})},
        undoes=undoes,
        question_id=question_id,
        actor=principal.user_id,
        key=key,
        digest=digest,
    )
    for write in after:
        await write(decision_id)
    return result


async def record(
    conn: AsyncConnection[Row],
    *,
    decision_id: UUID,
    project_id: UUID,
    requirement_id: UUID,
    base_version: int,
    result_version: int,
    action: str,
    summary: str,
    detail: dict[str, Any],
    undoes: UUID | None,
    question_id: UUID | None,
    actor: UUID,
    key: str,
    digest: str,
) -> dict[str, Any]:
    """결정 기록 한 행과 감사 한 건을 쓴다. 같은 키가 진행 중이면 409 IDEMPOTENCY_CONFLICT다.

    Returns:
        dict: 저장한 결과(DecisionResult JSON). 같은 키 재전송은 이 값을 돌려준다.
    """
    result = s.DecisionResult(
        decision_id=decision_id,
        action=action,  # type: ignore[arg-type]
        summary=summary,
        requirement_id=requirement_id,
        requirement_version=result_version,
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
                project_id,
                requirement_id,
                base_version,
                result_version,
                action,
                summary[:200],
                Jsonb(detail),
                undoes,
                actor,
                key,
                digest,
                Jsonb(result),
            ),
        )
    except UniqueViolation:
        raise ApplicationError(
            409, "IDEMPOTENCY_CONFLICT", "The same request key is in progress. Retry later."
        ) from None
    await commands.audit(
        conn,
        project_id=project_id,
        entity="requirement",
        entity_id=requirement_id,
        action=f"decision.{action}",
        actor=actor,
        detail={"summary": summary, "undoes": str(undoes) if undoes else None},
    )
    return result


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
    approved = await apply_approval(conn, project, row, requirement, actor, NOTE)
    # 결정은 사람이 지금 이 요구를 본 결과다. 승인이 요구 자체에 남긴 재검토 표시는 지운다(KAN-220 저장 조건과 함께
    # 쓰면 배정·판정 바꾸기 뒤 검토본을 영영 저장할 수 없다). 초안 문장 표시는 문장을 다시 확인할 때 지운다.
    await conn.execute(
        "UPDATE tft.rereview_marks SET cleared_by = %s, cleared_at = now() "
        "WHERE cause_proposal_id = %s AND target_kind = 'requirement' AND cleared_at IS NULL",
        (actor, row["id"]),
    )
    return approved


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


REVIEW = frozenset({"review_confirm", "review_return"})
# KAN-225: 요구 목록을 바꾸는 행동. 판단이 아니라 요구가 있는지를 바꾸고, 책임자 확인 대상이다.
STRUCTURAL = frozenset({"add_requirement", "exclude_requirement", "restore_requirement"})
STRUCTURAL_WORDS = {
    "add_requirement": "직접 추가",
    "exclude_requirement": "요구에서 뺌",
    "restore_requirement": "되살림",
}
# 뺀 요구에도 할 수 있는 행동. 나머지 결정은 되살린 뒤에 한다.
ON_EXCLUDED = frozenset({"restore_requirement", "review_confirm", "review_return"})


def check_role(project: Row, principal: Principal, action: str) -> None:
    """결정은 담당 sales만, 책임자 확인·되돌려 보내기는 책임자만 한다."""
    if action in REVIEW:
        if project["responsible_user_id"] != principal.user_id:
            raise ApplicationError(
                403,
                "TFT_DECISION_RESPONSIBLE_ONLY",
                "Only the project's responsible user can review decisions.",
            )
    elif project["sales_user_id"] != principal.user_id:
        raise ApplicationError(
            403, "TFT_DECISION_SALES_ONLY", "Only the project's sales owner can decide."
        )


async def current_decision(conn: AsyncConnection[Row], requirement: Row, decision_id: UUID) -> Row:
    """요구의 지금 결정(되돌리지 않았고 지금 판본에서 한 결정)이나 마지막 추가·빼기·되살리기가 decision_id인지 확인한다."""
    found = (await states(conn, requirement["project_id"], [requirement["requirement_id"]])).get(
        requirement["requirement_id"]
    )
    if found is None or decision_id not in (found[1].decision_id, found[1].change_id):
        raise ApplicationError(404, "TFT_DECISION_NOT_FOUND", "The decision was not found.")
    cursor = await conn.execute(
        "SELECT * FROM tft.requirement_decisions WHERE id = %s", (decision_id,)
    )
    row = await cursor.fetchone()
    assert row is not None
    return row


async def review_confirm(
    conn: AsyncConnection[Row],
    project: Row,
    requirement: Row,
    principal: Principal,
    payload: s.DecisionRequest,
) -> Outcome:
    assert payload.decision_id is not None
    target = await current_decision(conn, requirement, payload.decision_id)
    return "책임자 확인", {"decision_id": str(target["id"])}, None, None, []


async def review_return(
    conn: AsyncConnection[Row],
    project: Row,
    requirement: Row,
    principal: Principal,
    payload: s.DecisionRequest,
) -> Outcome:
    assert payload.decision_id is not None
    target = await current_decision(conn, requirement, payload.decision_id)
    if target["action"] in STRUCTURAL:
        return revert(target)
    summary, detail, undoes, question, after = await undo(
        conn, project, requirement, principal, payload
    )
    return summary.replace("되돌림", "책임자가 되돌림", 1), detail, undoes, question, after


# 비교 사유가 남긴 확인 항목 중 조건 차이가 아닌 것(값을 못 읽음, 단위, 지속시간 환산).
NOT_GAPS = ("값 확인:", "단위 확인:", "요구 지속시간의 시험값 확인", "정격 피크 확인")


def bid_verdict(row: Row, *, differs: bool, customer_question: bool, override: str | None) -> str:
    """요구 판본 → 입찰 판정. 규칙은 contracts/tft/rfp-data.md "입찰 판정"과 같다.

    Args:
        row: 최신 요구 판본(status·kind·task·attribute_key·operator·value·unresolved·next_owner_field).
        differs: 최신 비교의 자사 후보가 모두 다른지(DIFFERS, 자료 없는 후보 제외).
        customer_question: 보내지 않기로 한 것을 뺀 고객 질문이 있는지.
        override: 지금 판본을 만든 "판정 바꾸기" 결정의 판정.

    Returns:
        str: MET·CONFIRM·DEVIATION·QUERY·SUPPLEMENT·ASSIGN.
    """
    if override:
        return override
    status = row["status"]
    if status == "EVIDENCE_FOUND":
        return "MET"
    if status == "SPEC_GAP":
        return "DEVIATION"
    if status == "CONDITIONAL":
        if row["next_owner_field"] == "CUSTOMER":
            return "QUERY"
        if differs:
            return "DEVIATION"
        if row["attribute_key"] == "form":
            # 형식 문장은 축이 맞아도 비교기가 확정하지 않는다. 사람이 확정한다.
            return "CONFIRM"
        if row["operator"] == "TEXT":
            return "DEVIATION"
        if any(not str(item).startswith(NOT_GAPS) for item in row["unresolved"]):
            return "DEVIATION"
        return "CONFIRM"
    if row["kind"] in ("TASK", "DOCUMENT") or row["task"]:
        return "ASSIGN"
    if row["value"] is None or customer_question or row["next_owner_field"] == "CUSTOMER":
        return "QUERY"
    return "SUPPLEMENT"


async def states(
    conn: AsyncConnection[Row], project_id: UUID, requirement_ids: Sequence[UUID] | None = None
) -> dict[UUID, tuple[str, s.DecisionState]]:
    """요구별 (입찰 판정, 결정 상태). 저장된 기록에서만 계산한다.

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.
        requirement_ids: 이 요구만(없으면 프로젝트 전체).

    Returns:
        dict: 요구 ID → (판정, 결정 상태).
    """
    only = " AND req.id = ANY(%(ids)s)" if requirement_ids is not None else ""
    params = {"p": project_id, "ids": list(requirement_ids or [])}
    cursor = await conn.execute(
        "SELECT v.requirement_id, v.version, v.status, v.kind, v.task, v.attribute_key, v.operator, "
        "v.value, v.unresolved, v.next_owner_field, req.derivation_kind = 'PROVIDED' AS manually_added "
        "FROM tft.requirements req "
        "JOIN tft.requirement_versions v ON v.requirement_id = req.id AND v.version = req.latest_version "
        f"WHERE req.project_id = %(p)s{only}",
        params,
    )
    rows = await cursor.fetchall()
    # 같은 계열의 다른 제품(유입식 변압기, MCCB)이 다르다고 편차로 보지 않는다. 비교한 후보가 모두 다를 때만 편차다.
    # ponytail: 재비교 결과(requirement_recomparisons)는 보지 않는다. 처리 때 비교만 본다.
    cursor = await conn.execute(
        "SELECT pc.requirement_id FROM tft.product_comparisons pc "
        "JOIN tft.requirements req ON req.id = pc.requirement_id "
        f"WHERE req.project_id = %(p)s{only} AND pc.requirement_version = "
        "(SELECT max(x.requirement_version) FROM tft.product_comparisons x "
        "WHERE x.requirement_id = pc.requirement_id) GROUP BY pc.requirement_id "
        "HAVING bool_or(pc.result = 'DIFFERS') "
        "AND NOT bool_or(pc.result IN ('MEETS', 'NOT_COMPARABLE'))",
        params,
    )
    differs = {r["requirement_id"] for r in await cursor.fetchall()}
    cursor = await conn.execute(
        "SELECT DISTINCT q.requirement_id FROM tft.customer_questions q "
        "WHERE q.project_id = %(p)s AND q.target = 'CUSTOMER' AND q.requirement_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM tft.question_withdrawals w WHERE w.question_id = q.id)",
        params,
    )
    asked = {r["requirement_id"] for r in await cursor.fetchall()}
    cursor = await conn.execute(
        "SELECT id, requirement_id, action, summary, result_version, detail, undoes, created_at "
        "FROM tft.requirement_decisions WHERE project_id = %(p)s ORDER BY created_at, id",
        params,
    )
    decisions = await cursor.fetchall()
    undone = {d["undoes"] for d in decisions if d["undoes"] is not None}
    confirmed = {
        d["detail"].get("decision_id") for d in decisions if d["action"] == "review_confirm"
    }
    out: dict[UUID, tuple[str, s.DecisionState]] = {}
    for row in rows:
        mine = [
            d
            for d in decisions
            if d["requirement_id"] == row["requirement_id"]
            and d["id"] not in undone
            and d["result_version"] == row["version"]
        ]
        override = next(
            (d["detail"]["verdict"] for d in reversed(mine) if d["action"] == "change_verdict"),
            None,
        )
        verdict = bid_verdict(
            row,
            differs=row["requirement_id"] in differs,
            customer_question=row["requirement_id"] in asked,
            override=override,
        )
        current = next((d for d in reversed(mine) if d["action"] in DECIDING), None)
        history = [d for d in decisions if d["requirement_id"] == row["requirement_id"]]
        mark = next((d for d in reversed(history) if "excluded" in d["detail"]), None)
        excluded = bool(mark and mark["detail"]["excluded"])
        change = next(
            (d for d in reversed(history) if d["action"] in STRUCTURAL and d["id"] not in undone),
            None,
        )
        out[row["requirement_id"]] = (
            verdict,
            s.DecisionState(
                needed=verdict != "MET" and not excluded,
                decided=current is not None,
                decision_id=current["id"] if current else None,
                action=current["action"] if current else None,
                summary=current["summary"] if current else None,
                decided_at=current["created_at"] if current else None,
                reviewed=current is not None and str(current["id"]) in confirmed,
                manually_added=row["manually_added"],
                excluded=excluded,
                excluded_reason=mark["detail"].get("reason") if mark and excluded else None,
                change_id=change["id"] if change else None,
                change_action=change["action"] if change else None,
                change_reviewed=change is not None and str(change["id"]) in confirmed,
            ),
        )
    return out


async def gate(conn: AsyncConnection[Row], project_id: UUID) -> s.DecisionGate:
    """검토본 저장 조건 셋과 책임자 확인 대기.

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.

    Returns:
        s.DecisionGate: 판정 안 된 요구, 편차표 누락, 회신 대기 질의, 책임자 확인 대기.
    """
    found = await states(conn, project_id)
    cursor = await conn.execute(
        "SELECT DISTINCT ON (d.requirement_id) d.requirement_id, d.state, d.requirement_version, "
        "req.latest_version FROM tft.deviations d JOIN tft.requirements req ON req.id = d.requirement_id "
        "WHERE d.project_id = %s ORDER BY d.requirement_id, d.created_at DESC",
        (project_id,),
    )
    excluded = {rid for rid, (_, state) in found.items() if state.excluded}
    stale = {
        r["requirement_id"]
        for r in await cursor.fetchall()
        if r["state"] == "RECORDED" and r["requirement_version"] != r["latest_version"]
    } - excluded
    missing = {
        rid
        for rid, (verdict, state) in found.items()
        if verdict == "DEVIATION"
        and not state.excluded
        and state.action not in ("record_deviation", "request_check")
    }
    cursor = await conn.execute(
        "SELECT count(*) AS n FROM tft.customer_questions q WHERE q.project_id = %s "
        "AND q.target = 'CUSTOMER' AND q.status = 'OPEN' "
        "AND NOT EXISTS (SELECT 1 FROM tft.question_withdrawals w WHERE w.question_id = q.id) "
        f"AND NOT {repository.EXCLUDED.format(requirement='q.requirement_id')}",
        (project_id,),
    )
    row = await cursor.fetchone()
    cursor = await conn.execute(
        "SELECT sales_user_id = responsible_user_id AS same FROM tft.projects WHERE id = %s",
        (project_id,),
    )
    same = await cursor.fetchone()
    return s.DecisionGate(
        undecided=sum(state.needed and not state.decided for _, state in found.values()),
        deviation_missing=len(missing | stale),
        open_queries=row["n"] if row else 0,
        # 뺀 요구의 결정은 확인하지 않는다. 추가·빼기·되살리기는 빼도 확인한다(KAN-225).
        review_pending=sum(
            (state.decided and not state.reviewed and not state.excluded)
            + (state.change_id is not None and not state.change_reviewed)
            for _, state in found.values()
        ),
        self_review=bool(same and same["same"]),
    )


async def is_excluded(conn: AsyncConnection[Row], requirement_id: UUID) -> bool:
    cursor = await conn.execute(
        f"SELECT {repository.EXCLUDED.format(requirement='%s')} AS excluded", (requirement_id,)
    )
    row = await cursor.fetchone()
    return bool(row and row["excluded"])


async def exclude(
    conn: AsyncConnection[Row],
    project: Row,
    requirement: Row,
    principal: Principal,
    payload: s.DecisionRequest,
) -> Outcome:
    """요구에서 뺀다(사유 필수, KAN-225). 이미 뺀 요구는 decide가 409 TFT_REQUIREMENT_EXCLUDED로 막는다."""
    assert payload.text is not None
    return (
        f"요구에서 뺌: {payload.text}",
        {"excluded": True, "reason": payload.text},
        None,
        None,
        [],
    )


async def restore(
    conn: AsyncConnection[Row],
    project: Row,
    requirement: Row,
    principal: Principal,
    payload: s.DecisionRequest,
) -> Outcome:
    """뺀 요구를 되살린다. 빼지 않은 요구면 409다."""
    if not await is_excluded(conn, requirement["requirement_id"]):
        raise ApplicationError(
            409, "TFT_REQUIREMENT_NOT_EXCLUDED", "The requirement is not excluded."
        )
    return "되살림", {"excluded": False}, None, None, []


def revert(target: Row) -> Outcome:
    """책임자가 추가·빼기·되살리기를 되돌려 보낸다. 추가와 되살리기는 빼고, 빼기는 되살린다."""
    excluded = target["action"] != "exclude_requirement"
    return (
        f"책임자가 되돌림: {STRUCTURAL_WORDS[target['action']]}",
        {"undone_action": target["action"], "excluded": excluded},
        target["id"],
        None,
        [],
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
    "review_confirm": review_confirm,
    "review_return": review_return,
    "exclude_requirement": exclude,
    "restore_requirement": restore,
}
