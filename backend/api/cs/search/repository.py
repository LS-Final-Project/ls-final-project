"""cs.document_chunks 저장과 영어 전문 검색. 표는 infra/migrations/cs만 만든다.

tsv는 table_title과 text를 함께 색인한다. 표 청크의 text가 이미 표제를 맨 앞에 담고 있어 지금은 표제가
두 번 색인되지만, 청킹 규칙이 바뀌어도 표제 검색이 유지되도록 그대로 둔다.
"""

import math
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from backend.engine.search.chunking import search_context
from backend.engine.search.model import ChunkingResult, ChunkMetadata

Row = dict[str, Any]
COLUMNS = (
    "id, document_sha256, index_version, ordinal, chunk_id, kind, section_path, text, "
    "char_count, page_start, page_end, table_title, has_deleted_values, flags, block_ids, "
    "source_position, document_kind, product_family, access_scope, meta_version, "
    "document_version_id, data_origin, known_at, search_context"
)
INSERT = f"INSERT INTO cs.document_chunks ({COLUMNS}) VALUES ({', '.join(['%s'] * 24)})"
SEARCH = (
    "SELECT chunk_id, document_sha256, ordinal, section_path, "
    "ts_headline('english', text, q, 'MaxWords=35, MinWords=10') AS snippet, "
    "ts_rank_cd(tsv, q) AS rank, kind, table_title, page_start, text "
    "FROM cs.document_chunks, websearch_to_tsquery('english', %s) AS q "
    "WHERE tsv @@ q AND index_version = %s "
    "AND (%s::text[] IS NULL OR document_sha256 = ANY(%s::text[])) "
    "AND (%s::text IS NULL OR product_family = %s) "
    "AND (%s::text[] IS NULL OR document_kind = ANY(%s::text[])) "
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

    트랜잭션 첫 문장에서 (document_sha256, index_version) 키의 advisory 잠금을 잡아 같은 문서의 동시
    재색인을 차례로 처리한다. 잠금은 commit·rollback에서 풀린다.

    Args:
        conn: cs_app 연결.
        result: chunk_document 결과.
        document_sha256: 저장할 문서 해시. 모든 청크의 document_sha256과 같아야 한다.
        metadata: 권한·제품·판본 메타데이터(청크마다 같은 값을 쓴다).
        data_origin: synthetic·demo·real.
        known_at: 원문을 알게 된 시각.
        document_version_id: 원문 판본 ID(아직 없으면 None).

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
            search_context(chunk, metadata),
        )
        for chunk in result.chunks
    ]
    async with conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s::text || ':' || %s::text))",
            (document_sha256, result.index_version),
        )
        await conn.execute(
            "DELETE FROM cs.document_chunks WHERE document_sha256 = %s AND index_version = %s",
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
    product_family: str | None = None,
    document_kinds: list[str] | None = None,
) -> list[Row]:
    """영어 전문 검색으로 청크를 찾는다.

    Args:
        conn: cs_app 연결(dict 행).
        query: 사용자 질의. websearch_to_tsquery 문법("…" 구, OR, -제외)을 쓴다.
        document_sha256s: 찾을 문서 해시 목록. None이면 전체.
        index_version: 검색할 색인 판본.
        limit: 최대 결과 수(1~100).
        product_family: 청크 메타데이터 product_family가 이 값인 청크만 찾는다. None이면 전체.
        document_kinds: 청크 메타데이터 document_kind가 이 중 하나인 청크만 찾는다. None이면 전체.

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
        SEARCH,
        (query, index_version, document_sha256s, document_sha256s, product_family, product_family)
        + (document_kinds, document_kinds, limit),
    )
    return await cursor.fetchall()


# KAN-99 벡터 검색(C20 cs.chunk_embeddings). pgvector 파이썬 어댑터 없이 '[x,y,…]' 문자열을 vector로 변환한다.
MISSING_EMBEDDINGS = (
    "SELECT c.id, c.chunk_id, c.text, c.search_context FROM cs.document_chunks c "
    "WHERE c.index_version = %s AND NOT EXISTS (SELECT 1 FROM cs.chunk_embeddings e "
    "WHERE e.chunk_row_id = c.id AND e.embedding_version = %s) "
    "ORDER BY c.document_sha256, c.ordinal"
)
SEARCH_VECTOR = (
    "SELECT c.chunk_id, c.document_sha256, c.ordinal, c.section_path, c.kind, c.table_title, "
    "c.page_start, c.text, e.embedding <=> %s::vector AS distance "
    "FROM cs.chunk_embeddings e JOIN cs.document_chunks c ON c.id = e.chunk_row_id "
    "WHERE e.embedding_version = %s AND c.index_version = %s "
    "AND (%s::text[] IS NULL OR c.document_sha256 = ANY(%s::text[])) "
    "AND (%s::text IS NULL OR c.product_family = %s) "
    "AND (%s::text[] IS NULL OR c.document_kind = ANY(%s::text[])) "
    "ORDER BY distance, c.document_sha256, c.ordinal LIMIT %s"
)


def vector_literal(values: list[float], dimensions: int = 768) -> str:
    """pgvector 입력 문자열을 만든다.

    Args:
        values: 임베딩 값.
        dimensions: 기대 차원(C20 열은 768).

    Returns:
        str: '[v1,v2,…]'. 차원이 다르거나 유한한 수가 아니면 ValueError다.
    """
    if len(values) != dimensions or not all(
        isinstance(v, int | float) and math.isfinite(v) for v in values
    ):
        raise ValueError("임베딩 차원 또는 값이 올바르지 않습니다.")
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


async def missing_embeddings(
    conn: AsyncConnection[Row], *, index_version: str, embedding_version: str
) -> list[Row]:
    """그 임베딩 판본이 아직 없는 청크를 문서·순번 순으로 읽는다.

    Args:
        conn: cs_app 연결(dict 행).
        index_version: 청크 색인 판본.
        embedding_version: 임베딩 판본(모델:차원:task_type).

    Returns:
        list[Row]: id(청크 행)·chunk_id·text.
    """
    cursor = await conn.execute(MISSING_EMBEDDINGS, (index_version, embedding_version))
    return await cursor.fetchall()


async def store_embeddings(
    conn: AsyncConnection[Row], rows: list[tuple[UUID, list[float]]], *, embedding_version: str
) -> int:
    """청크 행별 임베딩을 넣는다. 이미 있는 (청크, 판본)은 건너뛴다(재실행 멱등).

    Args:
        conn: cs_app 연결.
        rows: (청크 행 ID, 768차원 값).
        embedding_version: 임베딩 판본.

    Returns:
        int: 새로 넣은 행 수. 차원·값이 틀리면 ValueError다.
    """
    values = [(row_id, embedding_version, vector_literal(vector)) for row_id, vector in rows]
    stored = 0
    async with conn.transaction():
        for value in values:
            cursor = await conn.execute(
                "INSERT INTO cs.chunk_embeddings (chunk_row_id, embedding_version, embedding) "
                "VALUES (%s, %s, %s::vector) ON CONFLICT DO NOTHING",
                value,
            )
            stored += cursor.rowcount
    return stored


async def search_vector(
    conn: AsyncConnection[Row],
    vector: list[float],
    *,
    embedding_version: str,
    index_version: str,
    document_sha256s: list[str] | None,
    limit: int = 20,
    product_family: str | None = None,
    document_kinds: list[str] | None = None,
) -> list[Row]:
    """질의 임베딩과 코사인 거리가 가까운 청크를 search_fts와 같은 조건으로 찾는다.

    Args:
        conn: cs_app·cs_worker 연결(dict 행).
        vector: 질의 임베딩(768차원).
        embedding_version: 청크 임베딩 판본(질의와 같은 모델·차원).
        index_version: 청크 색인 판본.
        document_sha256s: 찾을 문서 해시 목록. None이면 전체.
        limit: 최대 결과 수(1~100).
        product_family: 청크 product_family 조건(None이면 전체).
        document_kinds: 청크 document_kind 조건(None이면 전체).

    Returns:
        list[Row]: chunk_id·document_sha256·ordinal·section_path·kind·table_title·page_start·text·distance를
            거리 오름차순(같으면 문서·순번)으로. limit 범위 밖·잘못된 벡터는 ValueError다.
    """
    if not 1 <= limit <= 100:
        raise ValueError("limit은 1~100이어야 합니다.")
    cursor = await conn.execute(
        SEARCH_VECTOR,
        (vector_literal(vector), embedding_version, index_version)
        + (document_sha256s, document_sha256s, product_family, product_family)
        + (document_kinds, document_kinds, limit),
    )
    return await cursor.fetchall()
