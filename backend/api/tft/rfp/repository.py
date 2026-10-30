"""RFP 질문·회신·보완 선택·요구 판본 저장소(KAN-189 PR-A). HTTP 경로는 붙이지 않는다(PR-B).

판본 행은 고치지 않는다(contracts/tft/rfp-data.md). 질문만 상태·판본 포인터(status·version)를 바꾼다.

- rnd_check: 저장하지 않고 읽을 때 rnd.rnd_check_of(그 요구의 R&D 질문들, 최신 면제 선택 시각)로 계산한다.
  requirement_versions.rnd_check(T10)는 다른 흐름이 판본을 만들 때만 채운다.
- needs_rereview: 해소 안 된 rereview_marks가 있거나, 그 요구의 INTERNAL R&D 답변이 최신 보완 선택보다 뒤에
  기록됐으면 참이다(R&D 답변은 변경 제안을 만들지 않으므로 표시 행을 남길 수 없다).
- 쓰기는 읽은 version을 받아 최신과 다르면 409 TFT_VERSION_CONFLICT다.
"""

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from backend.api.tft.rfp.rnd import DeptKind, RndQuestion, rnd_check_of
from backend.api.tft.rfp.supplement import RndCheck
from backend.shared.errors import ApplicationError

Row = dict[str, Any]
QUESTION_COLUMNS = (
    "q.id, q.project_id, q.requirement_id, q.clause_id, q.page, q.issue_type, q.question_text, "
    "q.internal_assumption, q.status, q.version, q.target, q.target_dept, q.data_origin, "
    "q.derivation_kind, q.known_at, q.created_by, q.created_at, "
    "r.received_at AS reply_received_at, r.recorded_at AS reply_recorded_at, r.rnd_feasibility"
)


def version_conflict() -> ApplicationError:
    """읽은 판본이 최신과 다를 때의 오류.

    Returns:
        ApplicationError: 409 TFT_VERSION_CONFLICT.
    """
    return ApplicationError(409, "TFT_VERSION_CONFLICT", "The record changed. Reload and retry.")


async def project_scope(conn: AsyncConnection[Row], project_id: UUID) -> Row | None:
    """접근 검사에 쓰는 프로젝트 행.

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.

    Returns:
        Row | None: id·responsible_user_id·sales_user_id·review_departments·data_origin. 없으면 None.
    """
    cursor = await conn.execute(
        "SELECT id, responsible_user_id, sales_user_id, review_departments, data_origin "
        "FROM tft.projects WHERE id = %s",
        (project_id,),
    )
    return await cursor.fetchone()


async def department_kinds(conn: AsyncConnection[Row]) -> dict[str, DeptKind]:
    """부서 코드 → 분류(SALES·RND·TECH·PURCHASE·OTHER).

    Args:
        conn: dict_row 연결.

    Returns:
        dict[str, DeptKind]: 부서 분류.
    """
    cursor = await conn.execute("SELECT dept_code, dept_kind FROM tft.department_kinds")
    return {row["dept_code"]: row["dept_kind"] for row in await cursor.fetchall()}


async def latest_requirement_version(
    conn: AsyncConnection[Row], project_id: UUID, requirement_id: UUID, *, lock: bool = False
) -> Row | None:
    """프로젝트 안 요구의 최신 판본과 조항 번호.

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID(다른 프로젝트 요구는 None).
        requirement_id: 요구 ID.
        lock: 참이면 요구 행을 잠근다(같은 요구의 선택·질문 생성을 줄 세운다).

    Returns:
        Row | None: requirement_versions 열과 project_id·latest_version·clause_no. 없으면 None.
    """
    cursor = await conn.execute(
        "SELECT v.*, req.project_id, req.latest_version, c.clause_no "
        "FROM tft.requirements req "
        "JOIN tft.requirement_versions v ON v.requirement_id = req.id AND v.version = req.latest_version "
        "LEFT JOIN tft.clauses c ON c.id = v.clause_id "
        "WHERE req.id = %s AND req.project_id = %s" + (" FOR UPDATE OF req" if lock else ""),
        (requirement_id, project_id),
    )
    return await cursor.fetchone()


async def insert_question(
    conn: AsyncConnection[Row],
    *,
    project: Mapping[str, Any],
    requirement_id: UUID | None,
    clause_id: UUID | None,
    page: int | None,
    issue_type: str,
    question_text: str,
    internal_assumption: Mapping[str, Any] | None,
    target: str,
    target_dept: str | None,
    actor: UUID,
) -> Row:
    """질문 하나를 OPEN·판본 1로 저장한다(사람 입력이라 derivation_kind PROVIDED).

    Args:
        conn: 진행 중 트랜잭션.
        project: project_scope 행.
        requirement_id: 대상 요구(없으면 조항만).
        clause_id: 원문 조항.
        page: 원문 쪽.
        issue_type: 질문 유형.
        question_text: 본문.
        internal_assumption: 회신 전 내부 가정.
        target: CUSTOMER 또는 INTERNAL.
        target_dept: INTERNAL 대상 부서.
        actor: 작성자.

    Returns:
        Row: 저장한 질문(get_question 형식).
    """
    question_id = uuid4()
    await conn.execute(
        "INSERT INTO tft.customer_questions (id, project_id, requirement_id, clause_id, page, "
        "issue_type, question_text, internal_assumption, target, target_dept, data_origin, "
        "derivation_kind, known_at, created_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PROVIDED',now(),%s)",
        (
            question_id,
            project["id"],
            requirement_id,
            clause_id,
            page,
            issue_type,
            question_text,
            Jsonb(dict(internal_assumption)) if internal_assumption is not None else None,
            target,
            target_dept,
            project["data_origin"],
            actor,
        ),
    )
    question = await get_question(conn, question_id)
    assert question is not None
    return question


async def get_question(
    conn: AsyncConnection[Row], question_id: UUID, *, lock: bool = False
) -> Row | None:
    """질문과 회신 요약.

    Args:
        conn: dict_row 연결.
        question_id: 질문 ID.
        lock: 참이면 질문 행을 잠근다(회신 기록).

    Returns:
        Row | None: QUESTION_COLUMNS. 없으면 None.
    """
    cursor = await conn.execute(
        f"SELECT {QUESTION_COLUMNS} FROM tft.customer_questions q "
        "LEFT JOIN tft.question_replies r ON r.question_id = q.id WHERE q.id = %s"
        + (" FOR UPDATE OF q" if lock else ""),
        (question_id,),
    )
    return await cursor.fetchone()


async def list_questions(
    conn: AsyncConnection[Row],
    project_id: UUID,
    *,
    status: str | None = None,
    requirement_id: UUID | None = None,
    target: str | None = None,
    target_dept: str | None = None,
) -> list[Row]:
    """프로젝트 질문 목록(작성 순).

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.
        status: OPEN·REPLIED 필터.
        requirement_id: 요구 필터.
        target: CUSTOMER·INTERNAL 필터.
        target_dept: 대상 부서 필터.

    Returns:
        list[Row]: QUESTION_COLUMNS 행.
    """
    cursor = await conn.execute(
        f"SELECT {QUESTION_COLUMNS} FROM tft.customer_questions q "
        "LEFT JOIN tft.question_replies r ON r.question_id = q.id "
        "WHERE q.project_id = %s AND (%s::text IS NULL OR q.status = %s) "
        "AND (%s::uuid IS NULL OR q.requirement_id = %s) AND (%s::text IS NULL OR q.target = %s) "
        "AND (%s::text IS NULL OR q.target_dept = %s) ORDER BY q.created_at, q.id",
        (
            project_id,
            status,
            status,
            requirement_id,
            requirement_id,
            target,
            target,
            target_dept,
            target_dept,
        ),
    )
    return await cursor.fetchall()


async def insert_reply(
    conn: AsyncConnection[Row],
    question: Mapping[str, Any],
    *,
    version: int,
    reply_text: str,
    received_at: datetime,
    source_ref: str | None,
    rnd_feasibility: str | None,
    earliest_available_on: date | None,
    schedule_fits: bool | None,
    actor: UUID,
) -> Row:
    """회신을 저장하고 질문을 REPLIED·판본+1로 바꾼다. 질문 하나에 회신은 하나다.

    Args:
        conn: 진행 중 트랜잭션(질문 행을 get_question(lock=True)로 잠근 상태).
        question: 잠근 질문 행.
        version: 요청이 읽은 질문 판본.
        reply_text: 회신 본문.
        received_at: 회신 받은 시각.
        source_ref: 메일 ID 등 참조.
        rnd_feasibility: R&D 개발 가능성(R&D 내부 질문만, 검사는 호출자가 rnd.check_reply_fields로).
        earliest_available_on: 가장 이른 가능 일자.
        schedule_fits: 일정 충족 여부.
        actor: 기록자.

    Returns:
        Row: 바뀐 질문(get_question 형식). 이미 회신됐거나 판본이 다르면 409 TFT_VERSION_CONFLICT다.
    """
    if question["status"] != "OPEN" or question["version"] != version:
        raise version_conflict()
    await conn.execute(
        "INSERT INTO tft.question_replies (id, question_id, reply_text, received_at, source_ref, "
        "recorded_by, rnd_feasibility, earliest_available_on, schedule_fits, data_origin, "
        "derivation_kind, known_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PROVIDED',%s)",
        (
            uuid4(),
            question["id"],
            reply_text,
            received_at,
            source_ref,
            actor,
            rnd_feasibility,
            earliest_available_on,
            schedule_fits,
            question["data_origin"],
            received_at,
        ),
    )
    await conn.execute(
        "UPDATE tft.customer_questions SET status = 'REPLIED', version = version + 1, "
        "updated_at = now() WHERE id = %s",
        (question["id"],),
    )
    replied = await get_question(conn, question["id"])
    assert replied is not None
    return replied


async def rnd_questions(conn: AsyncConnection[Row], requirement_id: UUID) -> list[RndQuestion]:
    """요구의 R&D 자문 질문(INTERNAL·RND_FEASIBILITY)과 답변.

    Args:
        conn: dict_row 연결.
        requirement_id: 요구 ID.

    Returns:
        list[RndQuestion]: 작성 순. rnd.rnd_check_of 입력이다.
    """
    cursor = await conn.execute(
        "SELECT q.id, q.created_at, r.id IS NOT NULL AS replied, r.rnd_feasibility, r.schedule_fits "
        "FROM tft.customer_questions q LEFT JOIN tft.question_replies r ON r.question_id = q.id "
        "WHERE q.requirement_id = %s AND q.target = 'INTERNAL' AND q.issue_type = 'RND_FEASIBILITY' "
        "ORDER BY q.created_at, q.id",
        (requirement_id,),
    )
    return [
        RndQuestion(
            question_id=row["id"],
            created_at=row["created_at"],
            replied=row["replied"],
            feasibility=row["rnd_feasibility"],
            schedule_fits=row["schedule_fits"],
        )
        for row in await cursor.fetchall()
    ]


async def rnd_check(
    conn: AsyncConnection[Row], requirement_id: UUID
) -> tuple[RndCheck, UUID | None]:
    """요구의 R&D 확인 상태를 계산한다(저장 값이 아니라 이 계산이 원천이다).

    Args:
        conn: dict_row 연결.
        requirement_id: 요구 ID.

    Returns:
        tuple: (rnd_check, 기준 R&D 질문 ID). 면제 시각은 WAIVED로 저장된 최신 보완 선택의 결정 시각이다.
    """
    cursor = await conn.execute(
        "SELECT max(decided_at) AS waived_at FROM tft.supplement_selections "
        "WHERE requirement_id = %s AND rnd_check = 'WAIVED'",
        (requirement_id,),
    )
    row = await cursor.fetchone()
    return rnd_check_of(
        await rnd_questions(conn, requirement_id), row["waived_at"] if row else None
    )


async def needs_rereview(conn: AsyncConnection[Row], requirement_id: UUID) -> bool:
    """요구가 다시 검토돼야 하는지.

    해소 안 된 rereview_marks(제안 승인)가 있거나, INTERNAL R&D 답변이 최신 보완 선택보다 뒤에 기록됐으면 참이다.
    보완 선택이 없으면 두 번째 조건은 보지 않는다(다시 볼 선택이 없음).

    Args:
        conn: dict_row 연결.
        requirement_id: 요구 ID.

    Returns:
        bool: 재검토가 필요하면 True.
    """
    cursor = await conn.execute(
        "SELECT EXISTS (SELECT 1 FROM tft.rereview_marks WHERE target_kind = 'requirement' "
        "AND target_id = %s AND cleared_at IS NULL) "
        "OR coalesce((SELECT max(r.recorded_at) FROM tft.question_replies r "
        "JOIN tft.customer_questions q ON q.id = r.question_id WHERE q.requirement_id = %s "
        "AND q.target = 'INTERNAL' AND q.issue_type = 'RND_FEASIBILITY') "
        "> (SELECT max(decided_at) FROM tft.supplement_selections WHERE requirement_id = %s), false) "
        "AS needs",
        (requirement_id, requirement_id, requirement_id),
    )
    row = await cursor.fetchone()
    return bool(row and row["needs"])


async def latest_selection(conn: AsyncConnection[Row], requirement_id: UUID) -> Row | None:
    """요구의 최신 보완 선택 판본.

    Args:
        conn: dict_row 연결.
        requirement_id: 요구 ID.

    Returns:
        Row | None: supplement_selections 행. 없으면 None.
    """
    cursor = await conn.execute(
        "SELECT * FROM tft.supplement_selections WHERE requirement_id = %s "
        "ORDER BY version DESC LIMIT 1",
        (requirement_id,),
    )
    return await cursor.fetchone()


async def insert_selection(
    conn: AsyncConnection[Row],
    requirement: Mapping[str, Any],
    *,
    version: int,
    path: str,
    candidate_codes: Sequence[str],
    role_split: str,
    conditions: Sequence[str],
    note: str | None,
    rnd_check: RndCheck,
    waiver_reason: str | None,
    actor: UUID,
) -> Row:
    """보완 선택 새 판본을 쌓는다(사람 결정 human_input).

    Args:
        conn: 진행 중 트랜잭션(latest_requirement_version(lock=True)로 요구를 잠근 상태).
        requirement: 잠근 최신 요구 판본 행.
        version: 요청이 읽은 요구 판본. 최신과 다르면 409 TFT_VERSION_CONFLICT다.
        path: 보완 경로.
        candidate_codes: 협력사 후보 코드.
        role_split: 역할 분담.
        conditions: 조건 문장.
        note: 메모.
        rnd_check: 선택 시점의 R&D 확인 상태. 면제 사유가 있으면 WAIVED로 저장한다.
        waiver_reason: 면제 사유(20자 이상, 검사는 rnd.check_selection).
        actor: 결정자.

    Returns:
        Row: 저장한 선택 판본.
    """
    if requirement["latest_version"] != version:
        raise version_conflict()
    previous = await latest_selection(conn, requirement["requirement_id"])
    cursor = await conn.execute(
        "INSERT INTO tft.supplement_selections (requirement_id, version, requirement_version, path, "
        "candidate_codes, role_split, conditions, note, decided_by, data_origin, derivation_kind, "
        "known_at, rnd_check, rnd_check_waiver_reason) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PROVIDED',now(),%s,%s) RETURNING *",
        (
            requirement["requirement_id"],
            (previous["version"] if previous else 0) + 1,
            requirement["version"],
            path,
            list(candidate_codes),
            role_split,
            json.dumps(list(conditions)),
            note,
            actor,
            requirement["data_origin"],
            "WAIVED" if waiver_reason else rnd_check,
            waiver_reason,
        ),
    )
    row = await cursor.fetchone()
    assert row is not None
    return row


async def list_change_proposals(
    conn: AsyncConnection[Row], project_id: UUID, requirement_id: UUID | None = None
) -> list[Row]:
    """변경 제안 목록(읽기만, 작성 순).

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.
        requirement_id: 요구 필터.

    Returns:
        list[Row]: change_proposals 행.
    """
    cursor = await conn.execute(
        "SELECT * FROM tft.change_proposals WHERE project_id = %s "
        "AND (%s::uuid IS NULL OR requirement_id = %s) ORDER BY known_at, id",
        (project_id, requirement_id, requirement_id),
    )
    return await cursor.fetchall()


async def requirement_project(conn: AsyncConnection[Row], requirement_id: UUID) -> UUID | None:
    """요구가 속한 프로젝트.

    Args:
        conn: dict_row 연결.
        requirement_id: 요구 ID.

    Returns:
        UUID | None: 프로젝트 ID. 요구가 없으면 None.
    """
    cursor = await conn.execute(
        "SELECT project_id FROM tft.requirements WHERE id = %s", (requirement_id,)
    )
    row = await cursor.fetchone()
    return row["project_id"] if row else None


async def stored_options(
    conn: AsyncConnection[Row], requirement_id: UUID, version: int
) -> list[Row]:
    """요구 판본에 저장된 보완 선택지 행(원인·고객 허용·경로별 후보만 쓴다).

    Args:
        conn: dict_row 연결.
        requirement_id: 요구 ID.
        version: 요구 판본.

    Returns:
        list[Row]: supplement_options 행.
    """
    cursor = await conn.execute(
        "SELECT * FROM tft.supplement_options WHERE requirement_id = %s AND requirement_version = %s "
        "ORDER BY known_at, id",
        (requirement_id, version),
    )
    return await cursor.fetchall()


async def has_customer_question(conn: AsyncConnection[Row], requirement_id: UUID) -> bool:
    """요구에 고객 질문이 있는지(지정품 대체 제안의 고객 승인 질문으로 본다).

    Args:
        conn: dict_row 연결.
        requirement_id: 요구 ID.

    Returns:
        bool: CUSTOMER 질문이 하나라도 있으면 True.
    """
    # ponytail: 승인 질문 ID 열이 없어 고객 질문 유무로 본다. designated_item.approval_question_id가 저장되면 그 값으로.
    cursor = await conn.execute(
        "SELECT EXISTS (SELECT 1 FROM tft.customer_questions WHERE requirement_id = %s "
        "AND target = 'CUSTOMER') AS found",
        (requirement_id,),
    )
    row = await cursor.fetchone()
    return bool(row and row["found"])


async def judgement_of(
    conn: AsyncConnection[Row], requirement: Mapping[str, Any]
) -> dict[str, Any]:
    """변경 제안의 current로 쓰는 요구 판본의 판단 필드(status·necessity·reason·conditions·unresolved).

    Args:
        conn: dict_row 연결.
        requirement: latest_requirement_version 행.

    Returns:
        dict: ProposedChange 형식.
    """
    cursor = await conn.execute(
        "SELECT name, value, unit FROM tft.requirement_conditions WHERE requirement_id = %s "
        "AND version = %s ORDER BY ordinal",
        (requirement["requirement_id"], requirement["version"]),
    )
    return {
        "status": requirement["status"],
        "necessity": requirement["necessity"],
        "reason": requirement["reason"],
        "conditions": await cursor.fetchall(),
        "unresolved": list(requirement["unresolved"]),
    }


async def insert_reply_proposal(
    conn: AsyncConnection[Row],
    question: Mapping[str, Any],
    requirement: Mapping[str, Any],
    *,
    current: Mapping[str, Any],
    proposed: Mapping[str, Any],
    reply_text: str,
) -> Row:
    """고객 회신에서 시스템이 만드는 변경 제안(origin customer_reply, PENDING).

    Args:
        conn: 회신 기록과 같은 트랜잭션.
        question: 회신된 질문(get_question 행).
        requirement: 질문 요구의 최신 판본.
        current: 지금 판단 필드.
        proposed: 바꿀 판단 필드.
        reply_text: 회신 본문(앞 200자를 미검증 표시와 함께 근거 문장에 쓴다).

    Returns:
        Row: change_proposals 행.
    """
    cursor = await conn.execute(
        "SELECT id FROM tft.question_replies WHERE question_id = %s", (question["id"],)
    )
    reply = await cursor.fetchone()
    assert reply is not None
    cursor = await conn.execute(
        "INSERT INTO tft.change_proposals (id, project_id, requirement_id, base_requirement_version, "
        "origin, source_ref, current, proposed, rationale, proposer_kind, data_origin, "
        "derivation_kind, known_at) VALUES (%s,%s,%s,%s,'customer_reply',%s,%s,%s,%s,'system',%s,"
        "'DERIVED',%s) RETURNING *",
        (
            uuid4(),
            question["project_id"],
            requirement["requirement_id"],
            requirement["version"],
            Jsonb({"question_id": str(question["id"]), "reply_id": str(reply["id"])}),
            Jsonb(dict(current)),
            Jsonb(dict(proposed)),
            # 짝짓기는 질문 본문 포함만 본다. 회신이 실제로 답했는지는 사람이 확인한다.
            "회신 내용 미검증(담당자 확인 필요). 고객 회신: " + reply_text[:200],
            question["data_origin"],
            question["reply_received_at"],
        ),
    )
    row = await cursor.fetchone()
    assert row is not None
    return row


async def clause_in_project(conn: AsyncConnection[Row], project_id: UUID, clause_id: UUID) -> bool:
    """조항이 프로젝트 문서의 조항인지(질문 원문 위치 검사).

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.
        clause_id: 조항 ID.

    Returns:
        bool: 프로젝트 문서의 조항이면 True.
    """
    cursor = await conn.execute(
        "SELECT EXISTS (SELECT 1 FROM tft.clauses c "
        "JOIN tft.document_versions dv ON dv.id = c.document_version_id "
        "JOIN tft.documents d ON d.id = dv.document_id WHERE c.id = %s AND d.project_id = %s) AS found",
        (clause_id, project_id),
    )
    row = await cursor.fetchone()
    return bool(row and row["found"])


async def project_document_versions(
    conn: AsyncConnection[Row], project_id: UUID, ids: Sequence[UUID]
) -> list[UUID]:
    """처리할 문서 판본. ids가 비면 프로젝트 문서마다 최신 판본, 있으면 그중 프로젝트 문서의 것만.

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.
        ids: 요청한 문서 판본 ID(빈 목록이면 최신 판본 전부).

    Returns:
        list[UUID]: 프로젝트에 속한 문서 판본 ID(정렬). ids의 일부만 나오면 나머지는 프로젝트 밖이다.
    """
    cursor = await conn.execute(
        "SELECT dv.id FROM tft.document_versions dv JOIN tft.documents d ON d.id = dv.document_id "
        "WHERE d.project_id = %s AND (CASE WHEN cardinality(%s::uuid[]) = 0 "
        "THEN dv.version = d.latest_version ELSE dv.id = ANY(%s::uuid[]) END) ORDER BY dv.id",
        (project_id, list(ids), list(ids)),
    )
    return [row["id"] for row in await cursor.fetchall()]


async def has_requirements(conn: AsyncConnection[Row], project_id: UUID) -> bool:
    """프로젝트에 요구가 이미 있는지(처리를 다시 돌리는 것이면 사유가 필요하다).

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.

    Returns:
        bool: 요구가 하나라도 있으면 True.
    """
    cursor = await conn.execute(
        "SELECT EXISTS (SELECT 1 FROM tft.requirements WHERE project_id = %s) AS found",
        (project_id,),
    )
    row = await cursor.fetchone()
    return bool(row and row["found"])
