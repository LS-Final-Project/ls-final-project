"""tft.document_chunks 저장과 영어 전문 검색. 표는 infra/migrations/tft만 만든다.

tsv는 table_title과 text를 함께 색인한다. 표 청크의 text가 이미 표제를 맨 앞에 담고 있어 지금은 표제가
두 번 색인되지만, 청킹 규칙이 바뀌어도 표제 검색이 유지되도록 그대로 둔다.
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from backend.engine.search.model import ChunkingResult, ChunkMetadata

Row = dict[str, Any]
COLUMNS = (
    "id, document_sha256, index_version, ordinal, chunk_id, kind, section_path, text, "
    "char_count, page_start, page_end, table_title, has_deleted_values, flags, block_ids, "
    "source_position, document_kind, product_family, access_scope, meta_version, "
    "document_version_id, data_origin, known_at"
)
INSERT = f"INSERT INTO tft.document_chunks ({COLUMNS}) VALUES ({', '.join(['%s'] * 23)})"
SEARCH = (
    "SELECT chunk_id, document_sha256, ordinal, section_path, "
    "ts_headline('english', text, q, 'MaxWords=35, MinWords=10') AS snippet, "
    "ts_rank_cd(tsv, q) AS rank, kind, table_title, page_start, text "
    "FROM tft.document_chunks, websearch_to_tsquery('english', %s) AS q "
    "WHERE tsv @@ q AND index_version = %s "
    "AND (%s::text[] IS NULL OR document_sha256 = ANY(%s::text[])) "
    "ORDER BY rank DESC, document_sha256, ordinal LIMIT %s"
)


async def store_chunks(
    conn: AsyncConnection[Row],
    result: ChunkingResult,
    *,
    document_sha256: str,
    metadata: ChunkMetadata,
    data_origin: str,
    known_at: datetime,
    document_version_id: UUID | None = None,
) -> int:
    """한 문서·색인 판본의 청크를 지우고 다시 넣는다(한 트랜잭션).

    Args:
        conn: tft_app 연결.
        result: chunk_document 결과.
        document_sha256: 저장할 문서 해시. 모든 청크의 document_sha256과 같아야 한다.
        metadata: 권한·제품·판본 메타데이터(청크마다 같은 값을 쓴다).
        data_origin: synthetic·demo·real.
        known_at: 원문을 알게 된 시각.
        document_version_id: KAN-182 문서 판본 ID(아직 없으면 None).

    Returns:
        저장한 행 수.

    Raises:
        ValueError: 청크의 문서 해시나 색인 판본이 인자와 다를 때(엇갈린 문서 저장 방지).
    """
    for chunk in result.chunks:
        if chunk.document_sha256 != document_sha256 or chunk.index_version != result.index_version:
            raise ValueError("청크의 문서 해시·색인 판본이 저장 대상과 다릅니다.")
    rows = [
        (
            uuid4(),
            document_sha256,
            result.index_version,
            chunk.ordinal,
            chunk.chunk_id,
            chunk.kind,
            chunk.section_path,
            chunk.text,
            chunk.char_count,
            chunk.page_start,
            chunk.page_end,
            chunk.table_title,
            chunk.has_deleted_values,
            list(chunk.flags),
            chunk.block_ids,
            Jsonb(chunk.source_position.model_dump()),
            metadata.document_kind,
            metadata.product_family,
            metadata.access_scope,
            metadata.version,
            document_version_id,
            data_origin,
            known_at,
        )
        for chunk in result.chunks
    ]
    async with conn.transaction():
        await conn.execute(
            "DELETE FROM tft.document_chunks WHERE document_sha256 = %s AND index_version = %s",
            (document_sha256, result.index_version),
        )
        async with conn.cursor() as cursor:
            await cursor.executemany(INSERT, rows)
    return len(rows)


async def search_fts(
    conn: AsyncConnection[Row],
    query: str,
    *,
    document_sha256s: list[str] | None,
    index_version: str,
    limit: int = 20,
) -> list[Row]:
    """영어 전문 검색으로 청크를 찾는다.

    Args:
        conn: tft_app 연결(dict 행).
        query: 사용자 질의. websearch_to_tsquery 문법("…" 구, OR, -제외)을 쓴다.
        document_sha256s: 찾을 문서 해시 목록. None이면 전체.
        index_version: 검색할 색인 판본.
        limit: 최대 결과 수(1~100).

    Returns:
        chunk_id·document_sha256·ordinal·section_path·snippet·rank·kind·table_title·page_start·
        text 행을 순위 내림차순(같으면 문서·순번 오름차순)으로.

    Raises:
        ValueError: 질의가 비었거나 limit이 범위 밖일 때.
    """
    if not query.strip():
        raise ValueError("검색어가 비었습니다.")
    if not 1 <= limit <= 100:
        raise ValueError("limit은 1~100이어야 합니다.")
    cursor = await conn.execute(
        SEARCH, (query, index_version, document_sha256s, document_sha256s, limit)
    )
    return await cursor.fetchall()
