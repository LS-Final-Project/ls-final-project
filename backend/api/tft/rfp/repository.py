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
from backend.api.tft.rfp.schemas import UploadForm
from backend.api.tft.rfp.supplement import RndCheck
from backend.shared.errors import ApplicationError

Row = dict[str, Any]
QUESTION_COLUMNS = (
    "q.id, q.project_id, q.requirement_id, q.clause_id, q.page, q.issue_type, q.question_text, "
    "q.internal_assumption, q.status, q.version, q.target, q.target_dept, q.data_origin, "
    "q.derivation_kind, q.known_at, q.created_by, q.created_at, "
    "r.received_at AS reply_received_at, r.recorded_at AS reply_recorded_at, r.rnd_feasibility"
)


# 요구를 다시 봐야 하는지. 해소 안 된 재검토 표시가 있거나, R&D 답변이 최신 보완 선택보다 뒤다.
NEEDS_REREVIEW = (
    "EXISTS (SELECT 1 FROM tft.rereview_marks WHERE target_kind = 'requirement' "
    "AND target_id = {requirement} AND cleared_at IS NULL) "
    "OR coalesce((SELECT max(r.recorded_at) FROM tft.question_replies r "
    "JOIN tft.customer_questions q ON q.id = r.question_id WHERE q.requirement_id = {requirement} "
    "AND q.target = 'INTERNAL' AND q.issue_type = 'RND_FEASIBILITY') "
    "> (SELECT max(decided_at) FROM tft.supplement_selections "
    "WHERE requirement_id = {requirement}), false)"
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


async def insert_project(
    conn: AsyncConnection[Row],
    *,
    project_id: UUID,
    sales_user_id: UUID,
    known_at: datetime,
    values: Mapping[str, Any],
) -> None:
    """접수한 프로젝트를 넣는다. 출처는 웹 입력(PROVIDED)이다.

    Args:
        conn: dict_row 연결.
        project_id: 새 프로젝트 ID.
        sales_user_id: 담당 sales(접수자).
        known_at: 입력 시각.
        values: 코드가 정한 칸 이름 → 값.
    """
    columns = ["id", "source", "source_instance", "derivation_kind", "known_at", "sales_user_id"]
    params = [project_id, "rfp", "web", "PROVIDED", known_at, sales_user_id]
    columns += values
    params += values.values()
    await conn.execute(
        f"INSERT INTO tft.projects ({', '.join(columns)}) "
        f"VALUES ({', '.join(['%s'] * len(columns))})",
        params,
    )


async def update_project(
    conn: AsyncConnection[Row], project_id: UUID, version: int, values: Mapping[str, Any]
) -> int:
    """읽은 판본이 최신일 때만 프로젝트 칸을 바꾸고 판본을 올린다.

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.
        version: 요청이 읽은 판본.
        values: 코드가 정한 칸 이름 → 값.

    Returns:
        int: 새 판본. 다르면 409 TFT_VERSION_CONFLICT다.
    """
    assignments = "".join(f"{column} = %s, " for column in values)
    cursor = await conn.execute(
        f"UPDATE tft.projects SET {assignments}version = version + 1, updated_at = now() "
        "WHERE id = %s AND version = %s RETURNING version",
        (*values.values(), project_id, version),
    )
    row = await cursor.fetchone()
    if row is None:
        raise version_conflict()
    return int(row["version"])


async def project_values(
    conn: AsyncConnection[Row], project_id: UUID, columns: Sequence[str]
) -> Row:
    """바꾸기 전 프로젝트 칸을 잠그고 읽는다(감사의 from).

    Args:
        conn: dict_row 연결.
        project_id: 있는 프로젝트 ID.
        columns: 코드가 정한 칸 이름.

    Returns:
        Row: 칸 이름 → 값.
    """
    if not columns:
        return {}
    cursor = await conn.execute(
        f"SELECT {', '.join(columns)} FROM tft.projects WHERE id = %s FOR UPDATE", (project_id,)
    )
    row = await cursor.fetchone()
    if row is None:
        raise ValueError("TFT_PROJECT_MISSING")
    return row


async def insert_assignment(
    conn: AsyncConnection[Row],
    *,
    project: Mapping[str, Any],
    project_version: int,
    sales_user_id: UUID | None,
    responsible_user_id: UUID | None,
    reason: str,
    changed_by: UUID,
) -> UUID:
    """담당 지정 이력 한 행을 남긴다(이전·신규 두 대상 모두).

    Args:
        conn: dict_row 연결.
        project: 지정 전 project_scope 행.
        project_version: 지정 뒤 프로젝트 판본.
        sales_user_id: 새 담당 sales.
        responsible_user_id: 새 책임자.
        reason: 사유.
        changed_by: 지정한 manager.

    Returns:
        UUID: 이력 행 ID.
    """
    assignment_id = uuid4()
    await conn.execute(
        "INSERT INTO tft.project_assignments (id, project_id, project_version, "
        "previous_sales_user_id, sales_user_id, previous_responsible_user_id, "
        "responsible_user_id, reason, changed_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            assignment_id,
            project["id"],
            project_version,
            project["sales_user_id"],
            sales_user_id,
            project["responsible_user_id"],
            responsible_user_id,
            reason,
            changed_by,
        ),
    )
    return assignment_id


async def project_detail_parts(
    conn: AsyncConnection[Row], project_id: UUID
) -> tuple[Row, list[Row], dict[str, int]]:
    """프로젝트 상세를 만들 행들.

    Args:
        conn: dict_row 연결.
        project_id: 있는 프로젝트 ID.

    Returns:
        tuple: (프로젝트 행+documents_count·draft_version, 최신 요구별 status·necessity·unresolved·
            open_questions·needs_rereview, 승인 대기 제안의 입구별 수).
    """
    cursor = await conn.execute(
        "SELECT p.*, (SELECT count(*) FROM tft.documents d WHERE d.project_id = p.id) "
        "AS documents_count, (SELECT d.latest_version FROM tft.drafts d WHERE d.project_id = p.id) "
        "AS draft_version FROM tft.projects p WHERE p.id = %s",
        (project_id,),
    )
    project = await cursor.fetchone()
    if project is None:
        raise ValueError("TFT_PROJECT_MISSING")
    rereview = NEEDS_REREVIEW.format(requirement="req.id")
    cursor = await conn.execute(
        "SELECT v.status, v.necessity, v.unresolved, "
        "(SELECT count(*) FROM tft.customer_questions q "
        "WHERE q.requirement_id = req.id AND q.status = 'OPEN') AS open_questions, "
        f"({rereview}) AS needs_rereview FROM tft.requirements req "
        "JOIN tft.requirement_versions v ON v.requirement_id = req.id "
        "AND v.version = req.latest_version WHERE req.project_id = %s",
        (project_id,),
    )
    requirements = await cursor.fetchall()
    cursor = await conn.execute(
        "SELECT origin, count(*) AS n FROM tft.change_proposals "
        "WHERE project_id = %s AND status = 'PENDING' GROUP BY origin",
        (project_id,),
    )
    pending = {row["origin"]: row["n"] for row in await cursor.fetchall()}
    return project, requirements, pending


async def insert_document_version(
    conn: AsyncConnection[Row],
    *,
    project_id: UUID,
    version_id: UUID,
    form: UploadForm,
    mime: str,
    storage_key: str,
    sha256: str,
    size: int,
    edit_trace_support: bool,
    actor: UUID,
) -> UUID:
    """업로드 판본을 넣는다. 다음 판본이면 문서의 최신 판본 번호를 원자적으로 올린다.

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.
        version_id: 새 판본 ID.
        form: 검증한 폼 칸.
        mime: 내용으로 판별한 형식.
        storage_key: 저장 키.
        sha256: 파일 해시.
        size: 파일 크기.
        edit_trace_support: 편집 흔적을 읽을 수 있는 형식인지.
        actor: 올린 사용자.

    Returns:
        UUID: 문서 ID.
    """
    document_id = form.supersedes_document_id
    if document_id is None:
        document_id, version = uuid4(), 1
        await conn.execute(
            "INSERT INTO tft.documents (id, project_id, kind, title, note, base_document_id, "
            "data_origin, derivation_kind, known_at, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,'PROVIDED',now(),%s)",
            (
                document_id,
                project_id,
                form.kind,
                form.title,
                form.note,
                form.base_document_id,
                form.data_origin,
                actor,
            ),
        )
    else:
        cursor = await conn.execute(
            "UPDATE tft.documents SET latest_version = latest_version + 1 WHERE id = %s "
            "RETURNING latest_version",
            (document_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError("TFT_DOCUMENT_MISSING")
        version = row["latest_version"]
    await conn.execute(
        "INSERT INTO tft.document_versions (id, document_id, version, mime, storage_key, sha256, "
        "size, edit_trace_support, data_origin, derivation_kind, known_at, uploaded_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'PROVIDED',now(),%s)",
        (
            version_id,
            document_id,
            version,
            mime,
            storage_key,
            sha256,
            size,
            edit_trace_support,
            form.data_origin,
            actor,
        ),
    )
    return document_id


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
        Row | None: requirement_versions 열과 project_id·latest_version·code·clause_no. 없으면 None.
    """
    cursor = await conn.execute(
        "SELECT v.*, req.project_id, req.latest_version, req.code, c.clause_no "
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


async def insert_question_draft(
    conn: AsyncConnection[Row],
    *,
    project_id: UUID,
    requirement_id: UUID | None,
    clause: Mapping[str, Any] | None,
    issue_type: str,
    question_text: str,
    internal_assumption: Mapping[str, Any],
    draft_key: str,
    data_origin: str,
    actor: UUID,
) -> bool:
    """처리 함수가 만든 고객 질문 초안을 DRAFT로 넣는다. 같은 draft_key가 있으면 건너뛴다(새 판본 재생성 방지).

    Args:
        conn: 진행 중 트랜잭션.
        project_id: 프로젝트 ID.
        requirement_id: 대상 요구(원문으로 하나로 정해질 때만).
        clause: 원문 조항 행(없으면 None).
        issue_type: 질문 유형.
        question_text: 질문 초안 문장.
        internal_assumption: 회신 전 내부 메모.
        draft_key: 같은 질문을 가리키는 키(판본과 무관).
        data_origin: 문서의 data_origin.
        actor: 작업을 요청한 사용자.

    Returns:
        bool: 새로 넣었으면 True.
    """
    cursor = await conn.execute(
        "INSERT INTO tft.customer_questions (id, project_id, requirement_id, clause_id, page, "
        "issue_type, question_text, internal_assumption, status, target, draft_key, data_origin, "
        "derivation_kind, known_at, created_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'DRAFT','CUSTOMER',%s,%s,'DERIVED',now(),%s) "
        "ON CONFLICT DO NOTHING",
        (
            uuid4(),
            project_id,
            requirement_id,
            clause["id"] if clause else None,
            clause["page"] if clause else None,
            issue_type,
            question_text,
            Jsonb(dict(internal_assumption)),
            draft_key,
            data_origin,
            actor,
        ),
    )
    return cursor.rowcount == 1


async def confirm_question(conn: AsyncConnection[Row], question: Mapping[str, Any]) -> Row:
    """초안을 사람이 확인해 OPEN으로 바꾼다(경로 31).

    Args:
        conn: 진행 중 트랜잭션.
        question: 잠근 질문 행.

    Returns:
        Row: 바뀐 질문(get_question 형식). DRAFT가 아니면 409 TFT_VERSION_CONFLICT다(회신 기록과 같은 규칙).
    """
    if question["status"] != "DRAFT":
        raise version_conflict()
    await conn.execute(
        "UPDATE tft.customer_questions SET status = 'OPEN', version = version + 1, "
        "updated_at = now() WHERE id = %s",
        (question["id"],),
    )
    confirmed = await get_question(conn, question["id"])
    assert confirmed is not None
    return confirmed


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
        f"SELECT {NEEDS_REREVIEW.format(requirement='%s')} AS needs",
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
    conn: AsyncConnection[Row],
    project_id: UUID,
    requirement_id: UUID | None = None,
    *,
    status: str | None = None,
    origin: str | None = None,
) -> list[Row]:
    """변경 제안 목록(읽기만, 작성 순).

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.
        requirement_id: 요구 필터.
        status: 상태 필터.
        origin: 입구 필터.

    Returns:
        list[Row]: change_proposals 행.
    """
    cursor = await conn.execute(
        "SELECT * FROM tft.change_proposals WHERE project_id = %s "
        "AND (%s::uuid IS NULL OR requirement_id = %s) "
        "AND (%s::text IS NULL OR status = %s) AND (%s::text IS NULL OR origin = %s) "
        "ORDER BY known_at, id",
        (project_id, requirement_id, requirement_id, status, status, origin, origin),
    )
    return await cursor.fetchall()


async def get_proposal(
    conn: AsyncConnection[Row], proposal_id: UUID, *, lock: bool = False
) -> Row | None:
    """변경 제안 한 건.

    Args:
        conn: dict_row 연결.
        proposal_id: 제안 ID.
        lock: 참이면 결정이 끝날 때까지 행을 잠근다.

    Returns:
        Row | None: change_proposals 행.
    """
    cursor = await conn.execute(
        "SELECT * FROM tft.change_proposals WHERE id = %s" + (" FOR UPDATE" if lock else ""),
        (proposal_id,),
    )
    return await cursor.fetchone()


async def insert_web_proposal(
    conn: AsyncConnection[Row],
    requirement: Mapping[str, Any],
    *,
    current: Mapping[str, Any],
    proposed: Mapping[str, Any],
    rationale: str,
    evidence_refs: Sequence[str],
    actor: UUID,
) -> Row:
    """사람이 웹에서 쓴 변경 제안(origin web, PENDING).

    Args:
        conn: 진행 중 트랜잭션.
        requirement: 요구의 최신 판본(잠근 행).
        current: 지금 판단 필드.
        proposed: 바꿀 뒤 판단 필드 전체.
        rationale: 제안 사유.
        evidence_refs: 근거 참조.
        actor: 제안자.

    Returns:
        Row: change_proposals 행.
    """
    cursor = await conn.execute(
        "INSERT INTO tft.change_proposals (id, project_id, requirement_id, "
        "base_requirement_version, origin, current, proposed, rationale, evidence_refs, "
        "proposer_kind, proposer_user_id, data_origin, derivation_kind, known_at) "
        "VALUES (%s,%s,%s,%s,'web',%s,%s,%s,%s,'user',%s,%s,'PROVIDED',now()) RETURNING *",
        (
            uuid4(),
            requirement["project_id"],
            requirement["requirement_id"],
            requirement["version"],
            Jsonb(dict(current)),
            Jsonb(dict(proposed)),
            rationale,
            Jsonb(list(evidence_refs)),
            actor,
            requirement["data_origin"],
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ValueError("TFT_PROPOSAL_MISSING")
    return row


async def decide_proposal(
    conn: AsyncConnection[Row], proposal_id: UUID, status: str, actor: UUID, note: str | None
) -> Row:
    """제안의 결정을 기록하고 판본을 올린다.

    Args:
        conn: 잠근 제안과 같은 트랜잭션.
        proposal_id: 제안 ID.
        status: APPROVED·REJECTED.
        actor: 결정한 사용자.
        note: 승인 메모 또는 기각 사유.

    Returns:
        Row: 결정한 제안.
    """
    cursor = await conn.execute(
        "UPDATE tft.change_proposals SET status = %s, decided_by = %s, decided_at = now(), "
        "decision_note = %s, version = version + 1 WHERE id = %s RETURNING *",
        (status, actor, note, proposal_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ValueError("TFT_PROPOSAL_MISSING")
    return row


async def insert_approved_version(
    conn: AsyncConnection[Row],
    requirement: Mapping[str, Any],
    proposal: Mapping[str, Any],
    actor: UUID,
) -> None:
    """승인한 제안을 요구의 새 판본(human_input)으로 쓴다. 판단 칸만 바꾸고 나머지 칸은 잇는다.

    칸 목록을 표에서 읽어 마이그레이션이 더한 칸도 빠뜨리지 않는다.

    Args:
        conn: 요구를 잠근 트랜잭션.
        requirement: 최신 판본(latest_requirement_version(lock=True)).
        proposal: 승인할 제안.
        actor: 승인자.
    """
    requirement_id = requirement["requirement_id"]
    cursor = await conn.execute(
        "SELECT * FROM tft.requirement_versions WHERE requirement_id = %s AND version = %s",
        (requirement_id, requirement["version"]),
    )
    base = await cursor.fetchone()
    if base is None:
        raise ValueError("TFT_REQUIREMENT_MISSING")
    proposed = proposal["proposed"]
    changed = {
        "version": requirement["version"] + 1,
        "status": proposed["status"],
        "necessity": proposed["necessity"],
        "reason": proposed["reason"],
        "unresolved": Jsonb(proposed["unresolved"]),
        "provenance_kind": "human_input",
        "decided_by": actor,
        "proposal_id": proposal["id"],
        "derivation_kind": "PROVIDED",
    }
    kept = [
        c for c in base if c not in changed and c not in ("decided_at", "known_at", "created_at")
    ]
    await conn.execute(
        f"INSERT INTO tft.requirement_versions ({', '.join(kept)}, {', '.join(changed)}, "
        f"decided_at, known_at) SELECT {', '.join(kept)}, "
        f"{', '.join(['%s'] * len(changed))}, now(), now() FROM tft.requirement_versions "
        "WHERE requirement_id = %s AND version = %s",
        (*changed.values(), requirement_id, requirement["version"]),
    )
    for ordinal, condition in enumerate(proposed["conditions"]):
        await conn.execute(
            "INSERT INTO tft.requirement_conditions (requirement_id, version, ordinal, name, value, "
            "unit) VALUES (%s,%s,%s,%s,%s,%s)",
            (
                requirement_id,
                changed["version"],
                ordinal,
                condition["name"],
                condition["value"],
                condition["unit"],
            ),
        )
    await conn.execute(
        "UPDATE tft.requirements SET latest_version = %s WHERE id = %s",
        (changed["version"], requirement_id),
    )


async def mark_rereview(
    conn: AsyncConnection[Row],
    project_id: UUID,
    requirement_id: UUID,
    proposal_id: UUID,
    *,
    requirement_too: bool,
) -> None:
    """승인한 제안의 연결 객체(요구·비교·보완·최신 초안 문장)마다 재검토 표시를 남긴다.

    Args:
        conn: 승인과 같은 트랜잭션.
        project_id: 프로젝트 ID.
        requirement_id: 요구 ID.
        proposal_id: 원인 제안.
        requirement_too: 요구 자체에도 표시를 남길지.
    """
    await conn.execute(
        "INSERT INTO tft.rereview_marks (id, project_id, target_kind, target_id, cause_proposal_id) "
        "SELECT gen_random_uuid(), %(project)s, kind, target, %(proposal)s FROM ("
        "SELECT 'requirement' AS kind, %(requirement)s::uuid AS target "
        "WHERE %(requirement_too)s "
        "UNION SELECT 'comparison', %(requirement)s FROM tft.product_comparisons "
        "WHERE requirement_id = %(requirement)s "
        "UNION SELECT 'supplement', %(requirement)s FROM tft.supplement_selections "
        "WHERE requirement_id = %(requirement)s "
        "UNION SELECT 'draft_sentence', s.sentence_key FROM tft.draft_sentences s "
        "JOIN tft.drafts d ON d.project_id = s.project_id AND d.latest_version = s.draft_version "
        "JOIN tft.draft_sentence_evidence e ON e.sentence_id = s.id "
        "WHERE s.project_id = %(project)s AND e.evidence_type = 'requirement' "
        "AND e.evidence_id = %(requirement_text)s) AS linked",
        {
            "project": project_id,
            "proposal": proposal_id,
            "requirement": requirement_id,
            "requirement_text": str(requirement_id),
            "requirement_too": requirement_too,
        },
    )


async def clear_rereview(conn: AsyncConnection[Row], requirement_id: UUID, actor: UUID) -> None:
    """보완 선택을 다시 저장한 사람이 요구를 다시 확인한 것으로 보고 표시를 해소한다.

    초안 문장 표시는 문장을 고칠 때의 일이라 여기서 지우지 않는다.

    Args:
        conn: 보완 선택과 같은 트랜잭션.
        requirement_id: 요구 ID.
        actor: 다시 확인한 사용자.
    """
    await conn.execute(
        "UPDATE tft.rereview_marks SET cleared_by = %s, cleared_at = now() "
        "WHERE target_id = %s AND target_kind IN ('requirement', 'comparison', 'supplement') "
        "AND cleared_at IS NULL",
        (actor, requirement_id),
    )


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
    """요구 판본까지 저장된 가장 최근 보완 선택지 행(승인 판본은 선택지를 새로 만들지 않는다)(원인·고객 허용·경로별 후보만 쓴다).

    Args:
        conn: dict_row 연결.
        requirement_id: 요구 ID.
        version: 요구 판본.

    Returns:
        list[Row]: supplement_options 행.
    """
    cursor = await conn.execute(
        "SELECT * FROM tft.supplement_options WHERE requirement_id = %s AND requirement_version = "
        "(SELECT max(requirement_version) FROM tft.supplement_options "
        "WHERE requirement_id = %s AND requirement_version <= %s) ORDER BY known_at, id",
        (requirement_id, requirement_id, version),
    )
    return await cursor.fetchall()


async def has_customer_question(conn: AsyncConnection[Row], requirement_id: UUID) -> bool:
    """요구에 고객 질문이 있는지(지정품 대체 제안의 고객 승인 질문으로 본다). 초안(DRAFT)은 보낸 질문이 아니다.

    Args:
        conn: dict_row 연결.
        requirement_id: 요구 ID.

    Returns:
        bool: 확인된 CUSTOMER 질문이 하나라도 있으면 True.
    """
    # ponytail: 승인 질문 ID 열이 없어 고객 질문 유무로 본다. designated_item.approval_question_id가 저장되면 그 값으로.
    cursor = await conn.execute(
        "SELECT EXISTS (SELECT 1 FROM tft.customer_questions WHERE requirement_id = %s "
        "AND target = 'CUSTOMER' AND status <> 'DRAFT') AS found",
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


# --- 처리 함수(worker_processor) 저장, KAN-199·KAN-188 -------------------------------------------


async def all_document_versions(conn: AsyncConnection[Row], project_id: UUID) -> list[Row]:
    """프로젝트 문서의 모든 판본(처리 함수가 접수 입력 판본을 찾는 후보).

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.

    Returns:
        list[Row]: document_versions 열과 kind·title·latest(최신 판본인지).
    """
    cursor = await conn.execute(
        "SELECT dv.*, d.kind, d.title, dv.version = d.latest_version AS latest "
        "FROM tft.document_versions dv "
        "JOIN tft.documents d ON d.id = dv.document_id WHERE d.project_id = %s ORDER BY dv.id",
        (project_id,),
    )
    return await cursor.fetchall()


async def document_processed(conn: AsyncConnection[Row], document_version_id: UUID) -> bool:
    """이 문서 판본을 끝까지 처리했는지(재실행이면 그 문서를 다시 처리하지 않는다).

    처리 함수는 조항을 요구·비교와 같은 트랜잭션에 쓰므로 조항이 있으면 끝까지 처리한 판본이다(요구 0건 포함).

    Args:
        conn: dict_row 연결.
        document_version_id: 문서 판본 ID.

    Returns:
        bool: 조항이 있으면 True.
    """
    cursor = await conn.execute(
        "SELECT EXISTS (SELECT 1 FROM tft.clauses WHERE document_version_id = %s) AS found",
        (document_version_id,),
    )
    row = await cursor.fetchone()
    return bool(row and row["found"])


async def store_clauses(
    conn: AsyncConnection[Row], document_version: Mapping[str, Any], clauses: Sequence[Any]
) -> dict[str, Row]:
    """조항 트리를 저장한다. 그 판본의 조항이 이미 있으면 쓰지 않고 있는 행을 돌려준다.

    Args:
        conn: 진행 중 트랜잭션.
        document_version: document_versions 행.
        clauses: clauses.Clause 목록(트리 순서).

    Returns:
        dict: 조항 번호 → 조항 행(id·page).
    """
    cursor = await conn.execute(
        "SELECT id, clause_no, page FROM tft.clauses WHERE document_version_id = %s",
        (document_version["id"],),
    )
    stored = {row["clause_no"]: row for row in await cursor.fetchall()}
    if stored:
        return stored
    allowed_flags = {"HIGHLIGHT", "TEMPLATE_PLACEHOLDER", "LINE_BREAK_REJOINED"}
    for ordinal, clause in enumerate(clauses):
        clause_id = uuid4()
        parent = stored.get(clause.parent_no) if clause.parent_no else None
        await conn.execute(
            "INSERT INTO tft.clauses (id, document_version_id, clause_no, parent_clause_id, depth, "
            "ordinal, title, text, page, cross_references, data_origin, derivation_kind, known_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'DERIVED',now())",
            (
                clause_id,
                document_version["id"],
                clause.clause_no[:64],
                parent["id"] if parent else None,
                clause.depth,
                ordinal,
                clause.title,
                clause.text,
                clause.page,
                Jsonb([ref.model_dump(mode="json") for ref in clause.cross_references]),
                document_version["data_origin"],
            ),
        )
        for n, value in enumerate(clause.deleted_values):
            await conn.execute(
                "INSERT INTO tft.clause_deleted_values (clause_id, ordinal, text, position) "
                "VALUES (%s,%s,%s,%s)",
                (clause_id, n, value.text, value.position),
            )
        for flag in sorted(set(clause.flags) & allowed_flags):
            await conn.execute(
                "INSERT INTO tft.clause_flags (clause_id, flag) VALUES (%s,%s)", (clause_id, flag)
            )
        stored[clause.clause_no] = {
            "id": clause_id,
            "clause_no": clause.clause_no,
            "page": clause.page,
        }
    return stored


async def insert_requirement(
    conn: AsyncConnection[Row],
    *,
    project_id: UUID,
    code: str,
    document_version: Mapping[str, Any],
    clause: Mapping[str, Any] | None,
    spec: Mapping[str, Any],
    judgement: Mapping[str, Any],
) -> UUID:
    """AI 초안 요구와 판본 1(판단 포함)·조건을 쓴다.

    Args:
        conn: 진행 중 트랜잭션.
        project_id: 프로젝트 ID.
        code: 프로젝트 안에서 유일한 요구 코드.
        document_version: 원문 문서 판본 행.
        clause: 원문 조항 행(없으면 None).
        spec: quote·equipment·task·attribute·value·unit·operator·standard_system·necessity·designated_model·
            designated_manufacturer·assembly_clause·tolerance·needs_review·attribute_key·
            equipment_family·conditions(name·value·unit 목록).
        judgement: status·reason·unresolved·next_owner.

    Returns:
        UUID: 요구 ID.
    """
    requirement_id = uuid4()
    origin = document_version["data_origin"]
    designated = spec["designated_model"] is not None
    await conn.execute(
        "INSERT INTO tft.requirements (id, project_id, code, data_origin, derivation_kind, known_at) "
        "VALUES (%s,%s,%s,%s,'DERIVED',now())",
        (requirement_id, project_id, code[:64], origin),
    )
    await conn.execute(
        "INSERT INTO tft.requirement_versions (requirement_id, version, document_version_id, "
        "clause_id, page, quote, equipment, task, attribute, value, unit, operator, standard_system, "
        "necessity, designated_model, designated_manufacturer, designated_clause_id, "
        "designated_procurement, assembly_clause, tolerance, "
        "needs_review, attribute_key, equipment_family, status, reason, unresolved, "
        "next_owner_field, provenance_kind, data_origin, derivation_kind, known_at) "
        "VALUES (%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        "'ai_draft',%s,'DERIVED',now())",
        (
            requirement_id,
            document_version["id"],
            clause["id"] if clause else None,
            clause["page"] if clause else None,
            spec["quote"],
            spec["equipment"],
            spec["task"],
            spec["attribute"],
            spec["value"],
            spec["unit"],
            spec["operator"],
            spec["standard_system"],
            spec["necessity"],
            spec["designated_model"],
            spec["designated_manufacturer"],
            # 지정품 상세는 지정 모델이 있을 때만 둔다(표 CHECK). 조달 방식의 시작값은 명세 기본이다.
            clause["id"] if clause and designated else None,
            "SPECIFIED_PROCUREMENT" if designated else None,
            spec["assembly_clause"],
            spec["tolerance"],
            spec["needs_review"],
            spec["attribute_key"],
            spec["equipment_family"],
            judgement["status"],
            judgement["reason"],
            Jsonb(list(judgement["unresolved"])),
            judgement["next_owner"],
            origin,
        ),
    )
    for ordinal, condition in enumerate(spec["conditions"]):
        await conn.execute(
            "INSERT INTO tft.requirement_conditions (requirement_id, version, ordinal, name, value, unit) "
            "VALUES (%s,1,%s,%s,%s,%s)",
            (requirement_id, ordinal, condition["name"], condition["value"], condition["unit"]),
        )
    return requirement_id


async def insert_comparison(
    conn: AsyncConnection[Row],
    requirement_id: UUID,
    candidate: Mapping[str, Any],
    *,
    matched_attribute: str | None,
    search_trace: Mapping[str, Any],
    data_origin: str,
) -> None:
    """자사 후보 비교 한 건과 근거를 쓴다(요구 판본 1).

    Args:
        conn: 진행 중 트랜잭션.
        requirement_id: 요구 ID.
        candidate: comparison.CandidateMatch 덤프.
        matched_attribute: 비교한 속성(인증 요구면 None).
        search_trace: 후보를 찾은 범위.
        data_origin: 요구의 자료 출처.
    """
    comparison_id = uuid4()
    await conn.execute(
        "INSERT INTO tft.product_comparisons (id, requirement_id, requirement_version, product_code, "
        "lifecycle_status, matched_attribute, our_value, our_unit, our_conditions, comparable, result, "
        "search_trace, provenance_kind, data_origin, derivation_kind, known_at) "
        "VALUES (%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ai_draft',%s,'DERIVED',now())",
        (
            comparison_id,
            requirement_id,
            candidate["product_code"],
            candidate["lifecycle_status"],
            matched_attribute,
            candidate["our_value"],
            candidate["our_unit"],
            Jsonb(candidate["our_conditions"]),
            candidate["comparable"],
            candidate["result"],
            Jsonb(dict(search_trace)),
            data_origin,
        ),
    )
    for ordinal, evidence in enumerate(candidate["evidence"]):
        await conn.execute(
            "INSERT INTO tft.comparison_evidence (comparison_id, ordinal, document, clause, "
            "applicability, limits) VALUES (%s,%s,%s,%s,%s,%s)",
            (
                comparison_id,
                ordinal,
                evidence["document"],
                evidence["clause"],
                evidence["applicability"],
                evidence["limits"],
            ),
        )


async def insert_option(
    conn: AsyncConnection[Row],
    requirement_id: UUID,
    option: Mapping[str, Any],
    *,
    deficiency_cause: str | None,
    customer_permission: str,
    data_origin: str,
) -> None:
    """보완 선택지 한 행을 쓴다(요구 판본 1, 계산 시점의 값).

    Args:
        conn: 진행 중 트랜잭션.
        requirement_id: 요구 ID.
        option: supplement.Option 덤프.
        deficiency_cause: 부족 원인.
        customer_permission: 고객 허용.
        data_origin: 요구의 자료 출처.
    """
    await conn.execute(
        "INSERT INTO tft.supplement_options (id, requirement_id, requirement_version, deficiency_cause, "
        "customer_permission, path, allowed, conditions_to_confirm, required_expertise, blocked_reason, "
        "data_origin, derivation_kind, known_at) VALUES (%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,'DERIVED',now())",
        (
            uuid4(),
            requirement_id,
            deficiency_cause,
            customer_permission,
            option["path"],
            option["allowed"],
            Jsonb(option["conditions_to_confirm"]),
            Jsonb(option["required_expertise"]),
            option["blocked_reason"],
            data_origin,
        ),
    )


async def partner_profiles(conn: AsyncConnection[Row]) -> list[Row]:
    """협력사 후보마다 최신 프로필 판본.

    Args:
        conn: dict_row 연결.

    Returns:
        list[Row]: partner_profiles 행(후보 코드 순).
    """
    cursor = await conn.execute(
        "SELECT DISTINCT ON (candidate_code) * FROM tft.partner_profiles "
        "ORDER BY candidate_code, profile_version DESC"
    )
    return await cursor.fetchall()


async def insert_grade(
    conn: AsyncConnection[Row],
    *,
    project_id: UUID,
    candidate_code: str,
    profile_version: int,
    grade: Mapping[str, Any],
    data_origin: str,
) -> None:
    """후보 × 요구 등급 한 행을 쓴다(요구 판본 1).

    Args:
        conn: 진행 중 트랜잭션.
        project_id: 프로젝트 ID.
        candidate_code: 후보 코드.
        profile_version: 프로필 판본.
        grade: grading.RequirementGrade 덤프.
        data_origin: 요구의 자료 출처.
    """
    await conn.execute(
        "INSERT INTO tft.partner_grades (id, project_id, candidate_code, profile_version, "
        "requirement_id, requirement_version, grade, reasons, exclusion_reasons, mandatory_check, "
        "data_origin, derivation_kind, known_at) VALUES (%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,'DERIVED',now())",
        (
            uuid4(),
            project_id,
            candidate_code,
            profile_version,
            grade["requirement_id"],
            grade["grade"],
            Jsonb(grade["reasons"]),
            Jsonb(grade["exclusion_reasons"]),
            Jsonb(grade["mandatory_check"]),
            data_origin,
        ),
    )


# --- 읽기 경로(KAN-188, 경로 7~10·12~14·17) ------------------------------------------------


async def document_project(conn: AsyncConnection[Row], document_id: UUID) -> UUID | None:
    """문서가 속한 프로젝트.

    Args:
        conn: dict_row 연결.
        document_id: 문서 ID.

    Returns:
        UUID | None: 프로젝트 ID. 문서가 없으면 None.
    """
    cursor = await conn.execute(
        "SELECT project_id FROM tft.documents WHERE id = %s", (document_id,)
    )
    row = await cursor.fetchone()
    return row["project_id"] if row else None


async def list_document_versions(conn: AsyncConnection[Row], project_id: UUID) -> list[Row]:
    """프로젝트의 문서 판본 전부(문서 순, 같은 문서는 새 판본 먼저).

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.

    Returns:
        list[Row]: document_versions 열과 kind·title·base_document_id.
    """
    cursor = await conn.execute(
        "SELECT dv.*, d.kind, d.title, d.base_document_id FROM tft.document_versions dv "
        "JOIN tft.documents d ON d.id = dv.document_id WHERE d.project_id = %s "
        "ORDER BY d.created_at, d.id, dv.version DESC",
        (project_id,),
    )
    return await cursor.fetchall()


async def latest_document_version(conn: AsyncConnection[Row], document_id: UUID) -> Row | None:
    """문서의 최신 판본과 조항·삭제 값 수.

    Args:
        conn: dict_row 연결.
        document_id: 문서 ID.

    Returns:
        Row | None: document_versions 열과 kind·title·base_document_id·clauses_count·deleted_values_count.
    """
    cursor = await conn.execute(
        "SELECT dv.*, d.kind, d.title, d.base_document_id, "
        "(SELECT count(*) FROM tft.clauses c WHERE c.document_version_id = dv.id) AS clauses_count, "
        "(SELECT count(*) FROM tft.clause_deleted_values cd JOIN tft.clauses c ON c.id = cd.clause_id "
        "WHERE c.document_version_id = dv.id) AS deleted_values_count "
        "FROM tft.documents d JOIN tft.document_versions dv "
        "ON dv.document_id = d.id AND dv.version = d.latest_version WHERE d.id = %s",
        (document_id,),
    )
    return await cursor.fetchone()


async def list_clauses(
    conn: AsyncConnection[Row],
    document_version_id: UUID,
    *,
    parent: UUID | None,
    after: int | None,
    limit: int,
) -> list[Row]:
    """조항을 문서 순서(ordinal)로 읽는다. limit+1개를 읽어 다음 쪽 유무를 알린다.

    Args:
        conn: dict_row 연결.
        document_version_id: 문서 판본 ID.
        parent: 이 조항의 바로 아래만. None이면 전체 조항이다.
        after: 이 ordinal 다음부터.
        limit: 쪽 크기.

    Returns:
        list[Row]: clauses 열과 deleted_values·flags·requirement_ids.
    """
    cursor = await conn.execute(
        "SELECT c.*, "
        "(SELECT coalesce(jsonb_agg(jsonb_build_object('text', d.text, 'position', d.position) "
        "ORDER BY d.ordinal), '[]'::jsonb) FROM tft.clause_deleted_values d "
        "WHERE d.clause_id = c.id) AS deleted_values, "
        "ARRAY(SELECT f.flag FROM tft.clause_flags f WHERE f.clause_id = c.id ORDER BY f.flag) "
        "AS flags, "
        "ARRAY(SELECT req.id FROM tft.requirements req JOIN tft.requirement_versions v "
        "ON v.requirement_id = req.id AND v.version = req.latest_version "
        "WHERE v.clause_id = c.id ORDER BY req.code) AS requirement_ids "
        "FROM tft.clauses c WHERE c.document_version_id = %s "
        "AND (%s::uuid IS NULL OR c.parent_clause_id = %s) AND (%s::int IS NULL OR c.ordinal > %s) "
        "ORDER BY c.ordinal LIMIT %s",
        (document_version_id, parent, parent, after, after, limit + 1),
    )
    return await cursor.fetchall()


async def clause_ids_by_no(
    conn: AsyncConnection[Row], document_version_id: UUID
) -> dict[str, UUID]:
    """판본의 조항 번호 → 조항 ID(교차 참조 연결용).

    Args:
        conn: dict_row 연결.
        document_version_id: 문서 판본 ID.

    Returns:
        dict[str, UUID]: 번호별 조항 ID.
    """
    cursor = await conn.execute(
        "SELECT clause_no, id FROM tft.clauses WHERE document_version_id = %s",
        (document_version_id,),
    )
    return {row["clause_no"]: row["id"] for row in await cursor.fetchall()}


async def list_requirements(
    conn: AsyncConnection[Row],
    project_id: UUID,
    *,
    filters: Mapping[str, Any],
    after: str | None,
    limit: int,
) -> list[Row]:
    """최신 요구 판본을 코드 순으로 읽는다. limit+1개를 읽어 다음 쪽 유무를 알린다.

    필터는 쪽이 어긋나지 않게 모두 SQL에서 건다.

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.
        filters: document_version_id·equipment·task·necessity·status·needs_rereview·has_open_question.
            None인 키는 조건이 아니다.
        after: 이 코드 다음부터.
        limit: 쪽 크기.

    Returns:
        list[Row]: 요구 요약 칸과 open_questions·needs_rereview.
    """
    rereview = NEEDS_REREVIEW.format(requirement="req.id")
    open_questions = (
        "(SELECT count(*) FROM tft.customer_questions q "
        "WHERE q.requirement_id = req.id AND q.status = 'OPEN')"
    )
    where = ["req.project_id = %s"]
    params: list[Any] = [project_id]
    for column in ("document_version_id", "equipment", "task", "necessity", "status"):
        if filters.get(column) is not None:
            where.append(f"v.{column} = %s")
            params.append(filters[column])
    if filters.get("needs_rereview") is not None:
        where.append(f"({rereview}) = %s")
        params.append(filters["needs_rereview"])
    if filters.get("has_open_question") is not None:
        where.append(f"({open_questions} > 0) = %s")
        params.append(filters["has_open_question"])
    if after is not None:
        where.append("req.code > %s")
        params.append(after)
    cursor = await conn.execute(
        "SELECT req.id AS requirement_id, req.code, v.equipment, v.task, v.attribute, v.value, v.unit, "
        "v.necessity, v.status, v.path, v.version, v.designated_model, v.needs_review, "
        f"{open_questions} AS open_questions, ({rereview}) AS needs_rereview "
        "FROM tft.requirements req JOIN tft.requirement_versions v "
        "ON v.requirement_id = req.id AND v.version = req.latest_version "
        f"WHERE {' AND '.join(where)} ORDER BY req.code LIMIT %s",
        (*params, limit + 1),
    )
    return await cursor.fetchall()


async def requirement_detail_parts(
    conn: AsyncConnection[Row], requirement: Mapping[str, Any]
) -> dict[str, Any]:
    """요구 상세의 조건·원문 삭제 값·연결 ID를 읽는다.

    Args:
        conn: dict_row 연결.
        requirement: latest_requirement_version 행.

    Returns:
        dict: conditions·deleted_values·edit_trace_support·question_ids·pending_proposal_ids·
            partner_candidate_ids·draft_sentence_ids·comparison_results·selected_path.
    """
    requirement_id = requirement["requirement_id"]

    async def rows(sql: str, *params: Any) -> list[Row]:
        cursor = await conn.execute(sql, params)
        return await cursor.fetchall()

    conditions = await rows(
        "SELECT name, value, unit FROM tft.requirement_conditions "
        "WHERE requirement_id = %s AND version = %s ORDER BY ordinal",
        requirement_id,
        requirement["version"],
    )
    deleted = await rows(
        "SELECT text, position FROM tft.clause_deleted_values WHERE clause_id = %s ORDER BY ordinal",
        requirement["clause_id"],
    )
    trace = await rows(
        "SELECT edit_trace_support FROM tft.document_versions WHERE id = %s",
        requirement["document_version_id"],
    )
    questions = await rows(
        "SELECT id FROM tft.customer_questions WHERE requirement_id = %s ORDER BY created_at, id",
        requirement_id,
    )
    proposals = await rows(
        "SELECT id FROM tft.change_proposals WHERE requirement_id = %s AND status = 'PENDING' "
        "ORDER BY id",
        requirement_id,
    )
    partners = await rows(
        "SELECT DISTINCT candidate_code FROM tft.partner_grades WHERE requirement_id = %s "
        "ORDER BY candidate_code",
        requirement_id,
    )
    sentences = await rows(
        "SELECT DISTINCT sentence_id FROM tft.draft_sentence_evidence "
        "WHERE evidence_type = 'requirement' AND evidence_id = %s ORDER BY sentence_id",
        str(requirement_id),
    )
    selection = await latest_selection(conn, requirement_id)
    return {
        "conditions": conditions,
        "deleted_values": deleted,
        "edit_trace_support": trace[0]["edit_trace_support"],
        "question_ids": [r["id"] for r in questions],
        "pending_proposal_ids": [r["id"] for r in proposals],
        "partner_candidate_ids": [r["candidate_code"] for r in partners],
        "draft_sentence_ids": [r["sentence_id"] for r in sentences],
        "comparison_results": [r["result"] for r in await latest_comparisons(conn, requirement_id)],
        "selected_path": selection["path"] if selection else None,
    }


async def latest_comparisons(conn: AsyncConnection[Row], requirement_id: UUID) -> list[Row]:
    """비교가 저장된 가장 최근 요구 판본의 후보별 최신 비교. 처리 함수는 판본 1에만 비교를 쓴다.

    Args:
        conn: dict_row 연결.
        requirement_id: 요구 ID.

    Returns:
        list[Row]: product_comparisons 행(제품 코드 순).
    """
    cursor = await conn.execute(
        "SELECT DISTINCT ON (p.product_code, p.variant) p.* FROM tft.product_comparisons p "
        "WHERE p.requirement_id = %s AND p.requirement_version = (SELECT max(requirement_version) "
        "FROM tft.product_comparisons WHERE requirement_id = %s) "
        "ORDER BY p.product_code, p.variant, p.version DESC",
        (requirement_id, requirement_id),
    )
    return await cursor.fetchall()


async def comparison_evidence(
    conn: AsyncConnection[Row], comparison_ids: Sequence[UUID]
) -> list[Row]:
    """비교의 제품 쪽 근거.

    Args:
        conn: dict_row 연결.
        comparison_ids: product_comparisons ID.

    Returns:
        list[Row]: comparison_evidence 행(비교·순번 순).
    """
    cursor = await conn.execute(
        "SELECT * FROM tft.comparison_evidence WHERE comparison_id = ANY(%s) "
        "ORDER BY comparison_id, ordinal",
        (list(comparison_ids),),
    )
    return await cursor.fetchall()


async def project_grades(conn: AsyncConnection[Row], project_id: UUID) -> list[Row]:
    """후보×요구마다 저장된 최신 등급. 대상은 저장된 등급 행이 정한다(특정 요구 상태에 묶지 않는다).

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.

    Returns:
        list[Row]: partner_grades 행(후보 코드·요구 순).
    """
    cursor = await conn.execute(
        "SELECT DISTINCT ON (candidate_code, requirement_id) * FROM tft.partner_grades "
        "WHERE project_id = %s "
        "ORDER BY candidate_code, requirement_id, profile_version DESC, requirement_version DESC, "
        "version DESC",
        (project_id,),
    )
    return await cursor.fetchall()


async def profiles_of(
    conn: AsyncConnection[Row], keys: Sequence[tuple[str, int]]
) -> dict[tuple[str, int], Row]:
    """등급이 가리키는 프로필 판본.

    Args:
        conn: dict_row 연결.
        keys: (후보 코드, 프로필 판본).

    Returns:
        dict: (후보 코드, 프로필 판본) → partner_profiles 행.
    """
    cursor = await conn.execute(
        "SELECT * FROM tft.partner_profiles WHERE candidate_code = ANY(%s)",
        (sorted({code for code, _ in keys}),),
    )
    rows = await cursor.fetchall()
    return {(r["candidate_code"], r["profile_version"]): r for r in rows}
