"""C/S FTS 검색기 어댑터(KAN-194 3차). 비동기 search_fts를 hybrid.Searcher(동기 콜러블)로 감싼다."""

import asyncio
from typing import Any

import psycopg
from psycopg.rows import dict_row

from backend.api.cs.search.repository import search_fts
from backend.engine.search.hybrid import Searcher, candidates_from_fts
from backend.engine.search.model import Candidate, SearchFilters


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
                )

        return candidates_from_fts(asyncio.run(run()))

    return search
