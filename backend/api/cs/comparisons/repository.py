"""조건 비교 결과 SQL. 판본은 한 번 쓰면 고치지 않고, 최신 판본 번호만 옮긴다."""

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from backend.api.cs.tickets.repository import IN_DEPARTMENTS

Row = dict[str, Any]
ITEM_COLUMNS = (
    "kind, code, judgement, finding, unconfirmed_reason, baseline_value, "
    "baseline_missing_reason, baseline_evidence_ids, target_value, target_missing_reason, "
    "target_evidence_ids, masked_summary, summary_missing_reason, evidence_ids, "
    "provenance_kind, decided_by, decided_by_name, decided_at, reason"
)
EVIDENCE_COLUMNS = (
    "evidence_id, case_id, ticket_id, evidence_location, data_origin, derivation_kind, known_at"
)


async def get_comparison(
    conn: AsyncConnection[Row], product_code: str, fault_id: str
) -> Row | None:
    """묶음의 비교 결과를 찾는다.

    Args:
        conn: C/S DB 연결.
        product_code: 제품 코드.
        fault_id: 고장 ID.

    Returns:
        Row | None: id·묶음 키·최신 판본. 없으면 None.
    """
    cursor = await conn.execute(
        "SELECT id, product_code, fault_id, latest_version FROM cs.condition_comparisons "
        "WHERE product_code = %s AND fault_id = %s",
        (product_code, fault_id),
    )
    return await cursor.fetchone()


async def get_version(conn: AsyncConnection[Row], comparison_id: UUID, version: int) -> Row | None:
    """비교 결과의 한 판본 머리 행을 읽는다.

    Args:
        conn: C/S DB 연결.
        comparison_id: 비교 결과 ID.
        version: 판본.

    Returns:
        Row | None: 판본 행. 없으면 None.
    """
    cursor = await conn.execute(
        "SELECT version, improvement_request_id, request_version, data_origin, derivation_kind, "
        "known_at FROM cs.condition_comparison_versions "
        "WHERE comparison_id = %s AND version = %s",
        (comparison_id, version),
    )
    return await cursor.fetchone()


async def items(conn: AsyncConnection[Row], comparison_id: UUID, version: int) -> list[Row]:
    """한 판본의 조건·원인·문서 신호를 읽는다.

    Args:
        conn: C/S DB 연결.
        comparison_id: 비교 결과 ID.
        version: 판본.

    Returns:
        list[Row]: 종류·코드 순 항목 행.
    """
    cursor = await conn.execute(
        f"SELECT {ITEM_COLUMNS} FROM cs.condition_comparison_items "
        "WHERE comparison_id = %s AND version = %s ORDER BY kind, code",
        (comparison_id, version),
    )
    return await cursor.fetchall()


async def evidence(conn: AsyncConnection[Row], comparison_id: UUID, version: int) -> list[Row]:
    """한 판본의 근거 참조를 읽는다.

    Args:
        conn: C/S DB 연결.
        comparison_id: 비교 결과 ID.
        version: 판본.

    Returns:
        list[Row]: 근거 ID 순 근거 행.
    """
    cursor = await conn.execute(
        f"SELECT {EVIDENCE_COLUMNS} FROM cs.condition_comparison_evidence "
        "WHERE comparison_id = %s AND version = %s ORDER BY evidence_id",
        (comparison_id, version),
    )
    return await cursor.fetchall()


async def visible_tickets(
    conn: AsyncConnection[Row], ticket_ids: list[UUID], departments: frozenset[str]
) -> set[UUID]:
    """근거 문의 중 부서 이관 범위(permissions.md 4.1)로 볼 수 있는 것만 고른다.

    Args:
        conn: C/S DB 연결.
        ticket_ids: 근거가 가리키는 문의 ID.
        departments: 사용자가 이관 문의를 볼 수 있는 부서.

    Returns:
        set[UUID]: 볼 수 있는 문의 ID.
    """
    if not ticket_ids or not departments:
        return set()
    cursor = await conn.execute(
        f"SELECT t.id FROM cs.tickets t WHERE t.id = ANY(%s) AND {IN_DEPARTMENTS}",
        (ticket_ids, list(departments)),
    )
    return {row["id"] for row in await cursor.fetchall()}


async def lock_comparison(
    conn: AsyncConnection[Row], product_code: str, fault_id: str
) -> Row | None:
    """묶음의 비교 결과를 잠가 같은 비교에 대한 쓰기를 한 줄로 세운다.

    Args:
        conn: C/S DB 연결(트랜잭션 안).
        product_code: 제품 코드.
        fault_id: 고장 ID.

    Returns:
        Row | None: 잠근 비교 결과 행. 없으면 None.
    """
    cursor = await conn.execute(
        "SELECT id, product_code, fault_id, latest_version FROM cs.condition_comparisons "
        "WHERE product_code = %s AND fault_id = %s FOR UPDATE",
        (product_code, fault_id),
    )
    return await cursor.fetchone()


async def insert_comparison(conn: AsyncConnection[Row], row: Row) -> bool:
    """묶음의 비교 결과를 만든다. 같은 묶음이 이미 있으면 만들지 않는다.

    Args:
        conn: C/S DB 연결.
        row: id·product_code·fault_id·latest_version.

    Returns:
        bool: 새로 만들었으면 True.
    """
    cursor = await conn.execute(
        "INSERT INTO cs.condition_comparisons (id, product_code, fault_id, latest_version) "
        "VALUES (%(id)s, %(product_code)s, %(fault_id)s, %(latest_version)s) "
        "ON CONFLICT (product_code, fault_id) DO NOTHING",
        row,
    )
    return cursor.rowcount == 1


async def insert_version(
    conn: AsyncConnection[Row],
    comparison_id: UUID,
    version: Row,
    rows: list[Row],
    evidence_rows: list[Row],
) -> None:
    """판본 하나를 머리·근거·항목 전체로 쓴다.

    Args:
        conn: C/S DB 연결.
        comparison_id: 비교 결과 ID.
        version: 판본 행(version·요청서·출처·known_at·created_by).
        rows: 항목 행 전체.
        evidence_rows: 근거 행 전체.

    Returns:
        None: 세 표에 행을 추가한다.
    """
    await conn.execute(
        "INSERT INTO cs.condition_comparison_versions (comparison_id, version, "
        "improvement_request_id, request_version, data_origin, known_at, created_by) VALUES "
        "(%s, %s, %s, %s, %s, %s, %s)",
        (
            comparison_id,
            version["version"],
            version["improvement_request_id"],
            version["request_version"],
            version["data_origin"],
            version["known_at"],
            version["created_by"],
        ),
    )
    for e in evidence_rows:
        await conn.execute(
            f"INSERT INTO cs.condition_comparison_evidence (comparison_id, version, "
            f"{EVIDENCE_COLUMNS}) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                comparison_id,
                version["version"],
                e["evidence_id"],
                e["case_id"],
                e["ticket_id"],
                Jsonb(e["evidence_location"]),
                e["data_origin"],
                e["derivation_kind"],
                e["known_at"],
            ),
        )
    names = ITEM_COLUMNS.split(", ")
    for item in rows:
        await conn.execute(
            f"INSERT INTO cs.condition_comparison_items (comparison_id, version, {ITEM_COLUMNS}) "
            f"VALUES (%s, %s, {', '.join(['%s'] * len(names))})",
            (comparison_id, version["version"], *(item[name] for name in names)),
        )


async def set_latest(conn: AsyncConnection[Row], comparison_id: UUID, version: int) -> None:
    """최신 판본 번호를 옮긴다. 서비스 계정이 고칠 수 있는 유일한 열이다.

    Args:
        conn: C/S DB 연결.
        comparison_id: 비교 결과 ID.
        version: 새 최신 판본.

    Returns:
        None: latest_version을 갱신한다.
    """
    await conn.execute(
        "UPDATE cs.condition_comparisons SET latest_version = %s WHERE id = %s",
        (version, comparison_id),
    )


async def sources(
    conn: AsyncConnection[Row], ticket_ids: list[UUID], case_ids: list[UUID]
) -> tuple[dict[UUID, Row], dict[UUID, Row]]:
    """새 근거가 가리키는 문의·사례 원본의 묶음 키와 출처를 읽는다.

    Args:
        conn: C/S DB 연결.
        ticket_ids: 근거 문의 ID.
        case_ids: 근거 사례 ID.

    Returns:
        tuple[dict[UUID, Row], dict[UUID, Row]]: 문의 ID → 행, 사례 ID → 행. 아직 알려지지
        않은 사례도 포함한다(판정에서 세지 않을 뿐 근거로는 남길 수 있다).
    """
    cursor = await conn.execute(
        "SELECT id, product_code, data_origin, derivation_kind, known_at FROM cs.tickets "
        "WHERE id = ANY(%s)",
        (ticket_ids,),
    )
    tickets = {row["id"]: row for row in await cursor.fetchall()}
    cursor = await conn.execute(
        "SELECT id, ticket_id, fault_id, data_origin, derivation_kind, known_at "
        "FROM cs.resolution_cases WHERE id = ANY(%s)",
        (case_ids,),
    )
    return tickets, {row["id"]: row for row in await cursor.fetchall()}


async def insert_audit(
    conn: AsyncConnection[Row],
    event_id: UUID,
    comparison_id: UUID,
    action: str,
    version: int,
    actor: UUID,
    changes: Row,
) -> None:
    """비교 결과 변경을 개선요청과 같은 감사 표에 남긴다.

    Args:
        conn: C/S DB 연결.
        event_id: 감사 기록 ID.
        comparison_id: 비교 결과 ID.
        action: create 또는 update.
        version: 변경 뒤 판본.
        actor: 변경한 사용자.
        changes: 항목별 이전·다음 판정과 사유.

    Returns:
        None: cs.audit_events에 한 행을 추가한다.
    """
    await conn.execute(
        "INSERT INTO cs.audit_events "
        "(id, entity_type, entity_id, action, version, actor_user_id, changes) "
        "VALUES (%s, 'condition_comparison', %s, %s, %s, %s, %s)",
        (event_id, comparison_id, action, version, actor, Jsonb(changes)),
    )


async def removals(conn: AsyncConnection[Row], comparison_id: UUID) -> list[Row]:
    """비교의 근거 제외 이력을 읽는다.

    Args:
        conn: C/S DB 연결.
        comparison_id: 비교 결과 ID.

    Returns:
        list[Row]: evidence_id·item_keys 행.
    """
    cursor = await conn.execute(
        "SELECT evidence_id, item_keys FROM cs.condition_comparison_removals "
        "WHERE comparison_id = %s",
        (comparison_id,),
    )
    return await cursor.fetchall()


async def insert_removal(conn: AsyncConnection[Row], values: Row) -> None:
    """근거 제외 이력 한 행을 추가한다. 이력은 고치거나 지우지 않는다.

    Args:
        conn: C/S DB 연결.
        values: comparison_id·evidence_id·version·item_keys·reason·removed_by.

    Returns:
        None: cs.condition_comparison_removals에 한 행을 추가한다.
    """
    await conn.execute(
        "INSERT INTO cs.condition_comparison_removals "
        "(comparison_id, evidence_id, version, item_keys, reason, removed_by) VALUES "
        "(%(comparison_id)s, %(evidence_id)s, %(version)s, %(item_keys)s, %(reason)s, "
        "%(removed_by)s)",
        values,
    )
