"""조건 비교 결과 읽기 SQL. 쓰기(판정 전이)는 다음 단위다."""

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from backend.api.cs.tickets.repository import IN_DEPARTMENTS

Row = dict[str, Any]
ITEM_COLUMNS = (
    "kind, code, judgement, finding, unconfirmed_reason, baseline_value, "
    "baseline_missing_reason, baseline_evidence_ids, target_value, target_missing_reason, "
    "target_evidence_ids, masked_summary, summary_missing_reason, evidence_ids, "
    "provenance_kind, decided_by_name, decided_at"
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
