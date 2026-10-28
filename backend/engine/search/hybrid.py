"""FTS·벡터 후보의 하이브리드 결합(KAN-100).

순서: 같은 SearchFilters로 두 검색기 호출 → chunk_id 중복 제거 → 인용 차단 → RRF → 리랭커 →
질의 코드와 정확히 일치하는 후보를 앞으로 → filters.limit에서 자른다. 차단을 리랭커보다 먼저 해 재정렬
단계에도 차단 내용이 가지 않게 한다. 코드 일치를 리랭커 뒤에 둬 리랭커가 정확 일치를 묻지 못하게 한다.

인용 차단은 검색 경계에서 후보 문자열(snippet·text_excerpt·table_title)을 보는 차단일 뿐이다. 원천 후보·
프롬프트·인용·최종 응답 경계의 차단과 문서·사례 ID 기반 재유입 검증은 별도다(docs/evaluation.md).
벡터 검색(KAN-144 이후)과 실제 리랭커 모델은 범위 밖이며, 없으면 FTS만·항등 순서로 동작한다.
"""

import re
from collections.abc import Callable, Iterable
from typing import Any, Protocol

from backend.engine.search.model import Candidate, Fused, HybridResult, SearchFilters, Source

Searcher = Callable[[str, SearchFilters], list[Candidate]]
EDGE = "A-Za-z0-9-"
# 규격 번호(UL 1066, IEC 60947-2) 또는 영문자로 시작하고 숫자를 품은 3자 이상 토큰(MCCB-ABS-103c, FLT-042).
# 영문자 시작 조건은 480V·15kV·4000 같은 정격값을 코드로 보지 않기 위해서다.
CODE = re.compile(
    rf"(?<![{EDGE}])(?:[A-Z]{{2,6}} \d[A-Za-z0-9.-]+[A-Za-z0-9]"
    rf"|[A-Za-z](?=[{EDGE}]*\d)[{EDGE}]+[A-Za-z0-9])(?![{EDGE}])"
)
HIGHLIGHT = re.compile(r"</?b>")


class Reranker(Protocol):
    def rerank(self, query: str, items: list[Fused]) -> list[Fused]:
        """결합 후보의 순서를 바꾼다.

        Args:
            query: 사용자 질의.
            items: 차단을 거친 RRF 순 후보.

        Returns:
            다시 정렬한 후보.
        """
        ...


def candidates_from_fts(rows: Iterable[dict[str, Any]]) -> list[Candidate]:
    """search_fts 행(tft·cs 같은 형식)을 순위대로 FTS 후보로 바꾼다.

    Args:
        rows: search_fts가 돌려준 순위 순 행.

    Returns:
        position이 1부터인 후보 목록. score는 ts_rank_cd, text_excerpt는 청크 본문이다.
    """
    return [
        Candidate(
            chunk_id=row["chunk_id"],
            document_sha256=row["document_sha256"],
            ordinal=row["ordinal"],
            section_path=row["section_path"],
            snippet=row["snippet"],
            text_excerpt=row.get("text"),
            kind=row["kind"],
            table_title=row["table_title"],
            page_start=row["page_start"],
            source="FTS",
            position=position,
            score=row["rank"],
        )
        for position, row in enumerate(rows, start=1)
    ]


def codes_in(query: str) -> list[str]:
    """질의에서 제품·고장 코드와 규격 번호를 중복 없이 순서대로 뽑는다.

    Args:
        query: 사용자 질의.

    Returns:
        대소문자를 보존한 코드 목록.
    """
    return list(dict.fromkeys(CODE.findall(query)))


def contains(candidate: Candidate, code: str, *, ignore_case: bool = False) -> bool:
    """후보 문자열에 코드가 하이픈까지 단어 경계로 정확히 들어 있는지 본다.

    Args:
        candidate: 검사할 후보. snippet의 ts_headline 강조 태그는 벗기고 본다.
        code: 찾을 코드.
        ignore_case: 대소문자를 무시할지(차단 검사용).

    Returns:
        snippet·text_excerpt·table_title 중 하나에 있으면 True.
    """
    pattern = re.compile(
        rf"(?<![{EDGE}]){re.escape(code)}(?![{EDGE}])", re.IGNORECASE if ignore_case else 0
    )
    fields = (HIGHLIGHT.sub("", candidate.snippet), candidate.text_excerpt, candidate.table_title)
    return any(pattern.search(field) for field in fields if field)


def run_hybrid(
    query: str,
    filters: SearchFilters,
    *,
    fts: Searcher,
    vector: Searcher | None = None,
    reranker: Reranker | None = None,
    k: int = 60,
    blocked_codes: frozenset[str] = frozenset({"FLT-042"}),
) -> HybridResult:
    """FTS·벡터 후보를 RRF로 결합하고 코드 일치·인용 차단을 적용한다.

    Args:
        query: 사용자 질의.
        filters: 두 검색기에 그대로 넘기고 결과에도 그대로 남기는 조건.
        fts: FTS 검색기.
        vector: 벡터 검색기. None이면 FTS만 쓴다.
        reranker: 리랭커. None이면 RRF 순서를 유지한다.
        k: RRF 상수. 점수는 검색기별 1/(k+position)의 합이다.
        blocked_codes: 대소문자 무시로 들어 있으면 결과에서 빼는 인용 차단 코드.

    Returns:
        코드 일치 후보가 앞서는 결합 결과. 일치 묶음 안팎은 리랭커 순(없으면 RRF 내림차순, 같으면 문서
        해시·순번 오름차순)이다.
    """
    searchers = [fts] if vector is None else [fts, vector]
    found: dict[str, Candidate] = {}
    scores: dict[str, float] = {}
    sources: dict[str, list[Source]] = {}
    for searcher in searchers:
        for candidate in searcher(query, filters):
            found.setdefault(candidate.chunk_id, candidate)
            scores[candidate.chunk_id] = scores.get(candidate.chunk_id, 0.0) + 1 / (
                k + candidate.position
            )
            if candidate.source not in sources.setdefault(candidate.chunk_id, []):
                sources[candidate.chunk_id].append(candidate.source)

    dropped = [
        chunk_id
        for chunk_id, candidate in found.items()
        if any(contains(candidate, code, ignore_case=True) for code in blocked_codes)
    ]
    codes = codes_in(query)
    items = sorted(
        (
            Fused(
                candidate=candidate,
                rrf_score=scores[chunk_id],
                sources=sources[chunk_id],
                exact_code_match=any(contains(candidate, code) for code in codes),
            )
            for chunk_id, candidate in found.items()
            if chunk_id not in dropped
        ),
        key=lambda item: (-item.rrf_score, item.candidate.document_sha256, item.candidate.ordinal),
    )
    if reranker is not None:
        items = reranker.rerank(query, items)
    items = sorted(items, key=lambda item: not item.exact_code_match)
    return HybridResult(
        query=query,
        filters=filters,
        items=items[: filters.limit],
        dropped_blocked=dropped,
        codes_in_query=codes,
    )
