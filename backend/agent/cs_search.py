"""C/S 검색기 어댑터. 비동기 search_fts(KAN-194 3차)·search_vector(KAN-99, C20)를 hybrid.Searcher(동기 콜러블)로 감싼다."""

import asyncio
import logging
from collections.abc import Callable
from functools import lru_cache
from typing import Any, Literal

import psycopg
from psycopg.rows import dict_row
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.api.cs.search.repository import search_fts, search_vector
from backend.engine.search.hybrid import Searcher, candidates_from_fts
from backend.engine.search.model import Candidate, SearchFilters
from backend.shared.ai import embed

LOGGER = logging.getLogger(__name__)
# 벡터 후보 snippet 길이(FTS의 ts_headline 대신 본문 앞부분).
VECTOR_SNIPPET_CHARS = 200


class CsVectorSettings(BaseSettings):
    """C/S 벡터 검색 설정(KAN-99/100). 기본 꺼짐이다.

    gemini-embedding-2는 현재 호출 경로(EmbedContentConfig.task_type)에서 task_type을 반영하지 않는다(2026-10-30
    실측: 같은 질의를 두 설정으로 임베딩한 코사인 1.0). 그래서 질의 기본값을 문서 쪽과 같은 RETRIEVAL_DOCUMENT로 둔다.
    """

    model_config = SettingsConfigDict(env_prefix="CS_", extra="ignore")
    vector_search_enabled: bool = False
    embedding_model: str = "gemini-embedding-2"
    embedding_dimension: Literal[768] = 768
    embedding_query_task_type: str = "RETRIEVAL_DOCUMENT"

    @property
    def embedding_version(self) -> str:
        """청크 임베딩 판본(적재 cs_index_docs --embed와 같은 문서 쪽 이름)."""
        return f"{self.embedding_model}:{self.embedding_dimension}:RETRIEVAL_DOCUMENT"


def fts_searcher_from_dsn(dsn: str, index_version: str) -> Searcher:
    """cs_app DSN으로 cs.document_chunks를 검색하는 동기 검색기를 만든다.

    Args:
        dsn: cs_app 연결 문자열. 호출자가 환경변수(DATABASE_URL)에서 읽어 넘긴다.
        index_version: 검색할 색인 판본. SearchFilters.index_version과 같아야 한다.

    Returns:
        Searcher: 호출마다 asyncio.run으로 AsyncConnection을 열어 search_fts를 부르고 candidates_from_fts로
            바꾸는 콜러블. 실행 중인 이벤트 루프 안에서 부르면 RuntimeError("CS_SEARCH_IN_EVENT_LOOP"),
            필터 판본이 다르면 ValueError("CS_SEARCH_INDEX_MISMATCH")다.
    """

    def search(query: str, filters: SearchFilters) -> list[Candidate]:
        """질의 하나를 FTS로 검색한다.

        Args:
            query: 검색어.
            filters: 문서 해시·판본·개수 조건.

        Returns:
            list[Candidate]: 순위 순 FTS 후보.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            # asyncio.run은 실행 중인 루프 안에서 쓸 수 없다. 비동기 호출자는 search_fts를 직접 쓴다.
            raise RuntimeError("CS_SEARCH_IN_EVENT_LOOP")
        if filters.index_version != index_version:
            raise ValueError("CS_SEARCH_INDEX_MISMATCH")

        async def run() -> list[dict[str, Any]]:
            """연결 하나로 검색한다.

            Returns:
                list[dict]: search_fts 행.
            """
            async with await psycopg.AsyncConnection.connect(
                dsn, row_factory=dict_row, autocommit=True
            ) as conn:
                return await search_fts(
                    conn,
                    query,
                    document_sha256s=filters.document_sha256s,
                    index_version=index_version,
                    limit=filters.limit,
                    product_family=filters.product_family,
                    document_kinds=filters.document_kinds,
                )

        return candidates_from_fts(asyncio.run(run()))

    return search


def candidates_from_vector(rows: list[dict[str, Any]]) -> list[Candidate]:
    """search_vector 행을 거리·문서·순번 순으로 다시 정렬해 벡터 후보로 바꾼다.

    hnsw.iterative_scan=relaxed_order는 순서가 조금 어긋날 수 있어 여기서 다시 정렬한다.

    Args:
        rows: search_vector 행.

    Returns:
        list[Candidate]: position 1부터, source VECTOR, score는 1 - 코사인 거리.
    """
    ordered = sorted(rows, key=lambda r: (r["distance"], r["document_sha256"], r["ordinal"]))
    return [
        Candidate(
            chunk_id=row["chunk_id"],
            document_sha256=row["document_sha256"],
            ordinal=row["ordinal"],
            section_path=row["section_path"],
            snippet=row["text"][:VECTOR_SNIPPET_CHARS],
            text_excerpt=row["text"],
            kind=row["kind"],
            table_title=row["table_title"],
            page_start=row["page_start"],
            source="VECTOR",
            position=position,
            score=1 - row["distance"],
        )
        for position, row in enumerate(ordered, start=1)
    ]


class VectorSearcher:
    """질의 원문을 임베딩해 cs.chunk_embeddings를 찾는 동기 검색기. 실패하면 []로 FTS에 맡기고 failures를 센다."""

    def __init__(
        self,
        dsn: str,
        index_version: str,
        embedding_version: str,
        embed: Callable[[str], list[float]],
    ) -> None:
        """연결·판본·임베딩 함수를 받는다.

        Args:
            dsn: cs_app·cs_worker 연결 문자열.
            index_version: 검색할 청크 색인 판본. SearchFilters.index_version과 같아야 한다.
            embedding_version: 청크 임베딩 판본(CsVectorSettings.embedding_version).
            embed: 질의 원문 → 768차원 값. 같은 원문은 lru_cache로 한 번만 부른다(단계마다 재호출하지 않음).
        """
        self.dsn, self.index_version, self.embedding_version = dsn, index_version, embedding_version
        self.embed = lru_cache(maxsize=8)(embed)
        self.failures = 0

    def __call__(self, query: str, filters: SearchFilters) -> list[Candidate]:
        """질의 하나를 벡터로 찾는다.

        Args:
            query: 임베딩할 질의 원문(그래프·근거 생성기는 마스킹 원문을 넘긴다).
            filters: 문서 해시·판본·product_family·document_kinds·개수 조건.

        Returns:
            list[Candidate]: 거리 순 벡터 후보. 임베딩·DB 오류면 [](failures를 늘리고 CS_VECTOR_SEARCH_FAILED와 예외
                클래스 이름만 경고로 남긴다). 실행 중인
                이벤트 루프 안이면 RuntimeError("CS_SEARCH_IN_EVENT_LOOP"), 판본이 다르면
                ValueError("CS_SEARCH_INDEX_MISMATCH")다.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("CS_SEARCH_IN_EVENT_LOOP")
        if filters.index_version != self.index_version:
            raise ValueError("CS_SEARCH_INDEX_MISMATCH")

        async def run(vector: list[float]) -> list[dict[str, Any]]:
            """트랜잭션 하나에서 iterative_scan을 켜고 검색한다.

            좁은 필터(문서 해시·product_family)는 HNSW 후보(ef_search) 뒤에 걸려 limit보다 적게 나올 수 있다.
            relaxed_order면 조건에 맞는 행이 limit만큼 찰 때까지 인덱스를 더 훑는다(pgvector 0.8).
            """
            async with await psycopg.AsyncConnection.connect(
                self.dsn, row_factory=dict_row, autocommit=True
            ) as conn:
                async with conn.transaction():
                    await conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
                    return await search_vector(
                        conn,
                        vector,
                        embedding_version=self.embedding_version,
                        index_version=self.index_version,
                        document_sha256s=filters.document_sha256s,
                        limit=filters.limit,
                        product_family=filters.product_family,
                        document_kinds=filters.document_kinds,
                    )

        try:
            rows = asyncio.run(run(self.embed(query)))
        except Exception as error:
            # 공급자·DB 원문·메시지는 남기지 않는다(예외 클래스 이름만). 그 단계는 FTS 결과만 쓴다.
            self.failures += 1
            LOGGER.warning("CS_VECTOR_SEARCH_FAILED %s", type(error).__name__)
            return []
        return candidates_from_vector(rows)


def vector_searcher_from_settings(
    dsn: str, index_version: str, settings: CsVectorSettings, client: Any
) -> VectorSearcher:
    """설정과 임베딩 클라이언트(create_client(embedding=True))로 벡터 검색기를 만든다.

    Args:
        dsn: cs_app·cs_worker 연결 문자열.
        index_version: 청크 색인 판본.
        settings: 벡터 검색 설정.
        client: genai 클라이언트.

    Returns:
        VectorSearcher: 질의 원문마다 ai.embed 1회.
    """
    return VectorSearcher(
        dsn,
        index_version,
        settings.embedding_version,
        lambda text: (
            embed(
                client,
                settings.embedding_model,
                text,
                dimensions=settings.embedding_dimension,
                task_type=settings.embedding_query_task_type,
            ).values
        ),
    )
