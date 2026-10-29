"""FTS·벡터 후보의 하이브리드 결합(KAN-100).

순서: 같은 SearchFilters로 두 검색기 호출 → chunk_id 중복 제거 → 인용 차단 → RRF → 리랭커 →
질의 코드와 정확히 일치하는 후보를 앞으로 → filters.limit에서 자른다. 차단을 리랭커보다 먼저 해 재정렬
단계에도 차단 내용이 가지 않게 한다. 코드 일치를 리랭커 뒤에 둬 리랭커가 정확 일치를 묻지 못하게 한다.

인용 차단은 검색 경계에서 후보 문자열(snippet·text_excerpt·table_title)을 보는 차단일 뿐이다. 원천 후보·
프롬프트·인용·최종 응답 경계의 차단과 문서·사례 ID 기반 재유입 검증은 별도다(docs/evaluation.md).
벡터 검색(KAN-144 이후)과 실제 리랭커 모델은 범위 밖이며, 없으면 FTS만·항등 순서로 동작한다.
"""

import html
import re
import unicodedata
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
BLOCKED_CODES = frozenset({"FLT-042"})
# 질의 토큰 상한과 근거 스니펫 글자 수(KAN-144 근거 생성기·KAN-194 그래프 retrieve 공용).
MAX_QUERY_TERMS = 40
EXCERPT_CHARS = 800
_MASK_MARKER = re.compile(r"\[[A-Z]+_PROVIDED\]")
_WORD = re.compile(r"\w+")
# 차단 판정은 표기 변형을 접는다: 영문자와 숫자 사이의 하이픈·각종 대시·공백을 '-' 하나로 본다.
SEPARATOR = re.compile(r"(?<=[a-z])[-‐‑–—_\s]+(?=\d)")
# 제로폭 문자(U+200B~U+200D, U+FEFF)와 소프트 하이픈(U+00AD)은 보이지 않으므로 지우고 본다.
INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u00ad"))


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


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(text)).translate(INVISIBLE)
    # 대문자 태그(<B>)도 지우도록 casefold를 태그 제거보다 먼저 한다.
    return SEPARATOR.sub("-", HIGHLIGHT.sub("", text.casefold()))


def mentions_code(text: str, code: str) -> bool:
    """차단 판정용으로 텍스트가 코드를 언급하는지 본다(hybrid·drafting 공용).

    Args:
        text: 검사할 문자열. HTML 엔티티·NFKC(전각)·제로폭·소프트 하이픈·강조 태그를 풀고 대소문자를
            무시한다.
        code: 차단 코드(예 FLT-042).

    Returns:
        bool: 영문자와 숫자 사이 구분자(하이픈·대시·밑줄·공백)를 '-'로 접은 뒤 영숫자 경계로 코드가
            있으면 True. FLT-0420·FLT-04처럼 다른 코드는 아니다. 구분자 없는 FLT042는 접지 않는다
            (FLT0420 같은 다른 코드와 구분할 수 없어서다).
    """
    target = re.escape(_fold(code))
    return re.search(rf"(?<![a-z0-9]){target}(?![a-z0-9])", _fold(text)) is not None


def contains(candidate: Candidate, code: str, *, ignore_case: bool = False) -> bool:
    """후보 문자열에 코드가 하이픈까지 단어 경계로 정확히 들어 있는지 본다.

    Args:
        candidate: 검사할 후보. snippet의 ts_headline 강조 태그는 벗기고 본다.
        code: 찾을 코드.
        ignore_case: 차단 검사면 True. 이때는 mentions_code로 표기 변형까지 본다.

    Returns:
        snippet·text_excerpt·table_title 중 하나에 있으면 True.
    """
    if ignore_case:
        fields = (candidate.snippet, candidate.text_excerpt, candidate.table_title)
        return any(mentions_code(field, code) for field in fields if field)
    pattern = re.compile(rf"(?<![{EDGE}]){re.escape(code)}(?![{EDGE}])")
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
    blocked_codes: frozenset[str] = BLOCKED_CODES,
    code_text: str | None = None,
) -> HybridResult:
    """FTS·벡터 후보를 RRF로 결합하고 코드 일치·인용 차단을 적용한다.

    Args:
        query: 사용자 질의.
        filters: 두 검색기에 그대로 넘기고 결과에도 그대로 남기는 조건.
        fts: FTS 검색기.
        vector: 벡터 검색기. None이면 FTS만 쓴다.
        reranker: 리랭커. None이면 RRF 순서를 유지한다.
        k: RRF 상수. 점수는 검색기별 1/(k+position)의 합이다.
        blocked_codes: mentions_code로 걸리면 결과에서 빼는 인용 차단 코드.
        code_text: 코드 일치(codes_in)를 뽑을 원문. None이면 query다. 검색 질의를 토큰 OR로 바꾼 호출자는
            'UL 1066'·'XYZ-123' 같은 코드가 쪼개지지 않게 원문을 넘긴다.

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
    codes = codes_in(query if code_text is None else code_text)
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


def token_or_query(*texts: str | None) -> str:
    """마스킹한 문의 텍스트에서 websearch OR 질의를 만든다.

    websearch_to_tsquery는 공백으로 이은 어휘를 모두 AND로 묶으므로 문의 본문을 통째로 넘기면 거의 맞지 않는다.
    그래서 단어 토큰을 ' or '로 잇는다.

    Args:
        *texts: 마스킹한 제목·본문 등(None·빈 값은 건너뛴다).

    Returns:
        str: 2자 이상 \\w+ 토큰(마스킹 표식 [*_PROVIDED]·'or' 제외, casefold 기준 중복 제거, 앞 40개)을 ' or '로
            이은 질의. 토큰이 없으면 빈 문자열이다.
    """
    text = _MASK_MARKER.sub(" ", " ".join(part for part in texts if part))
    terms: dict[str, str] = {}
    for token in _WORD.findall(text):
        key = token.casefold()
        if len(token) >= 2 and key != "or":
            terms.setdefault(key, token)
    return " or ".join(list(terms.values())[:MAX_QUERY_TERMS])


def excerpt(candidate: Candidate, chars: int = EXCERPT_CHARS) -> str | None:
    """근거로 쓸 청크 본문 앞부분. ts_headline 스니펫(<b> 태그)은 프롬프트에 넣지 않는다.

    Args:
        candidate: 검색 후보.
        chars: 최대 글자 수.

    Returns:
        str | None: text_excerpt 앞 chars자. 본문이 없거나 공백뿐이면 None이다.
    """
    text = candidate.text_excerpt
    return text[:chars] if text and text.strip() else None


def scoped_then_global(
    query: str,
    filters: SearchFilters,
    scoped: SearchFilters | None,
    *,
    fts: Searcher,
    limit: int,
    vector: Searcher | None = None,
    code_text: str | None = None,
) -> tuple[list[Fused], set[str]]:
    """좁힌 조건(제품 등)으로 먼저 찾고, 모자라면 전체 조건으로 보충한다.

    좁힌 단계는 FTS만 쓴다. product_family 같은 조건은 C/S FTS 검색기만 적용하므로 벡터 결과를 섞으면 좁힌 범위
    밖 청크가 앞설 수 있다. 벡터 검색기는 전체 단계에서만 쓴다.

    Args:
        query: 질의.
        filters: 전체 조건.
        scoped: 먼저 쓸 좁힌 조건. None이면 전체 조건만 쓴다.
        fts: FTS 검색기.
        limit: 최대 결과 수.
        vector: 벡터 검색기(선택, 전체 단계에서만).
        code_text: 코드 일치를 뽑을 원문(run_hybrid의 code_text).

    Returns:
        tuple: (본문 있는 결합 후보, 좁힌 결과가 앞서고 chunk_id 중복 없이 limit개 이하; 두 단계에서 인용 차단으로
            뺀 chunk_id). 두 단계 모두 run_hybrid라 차단 코드(FLT-042) 청크는 결과에 없다.
    """
    items: list[Fused] = []
    blocked: set[str] = set()
    for stage in (scoped, filters):
        if stage is None or len(items) >= limit:
            continue
        result = run_hybrid(
            query,
            stage,
            fts=fts,
            vector=vector if stage is filters else None,
            code_text=code_text,
        )
        blocked |= set(result.dropped_blocked)
        seen = {item.candidate.chunk_id for item in items}
        items += [
            item
            for item in result.items
            if excerpt(item.candidate) is not None and item.candidate.chunk_id not in seen
        ][: limit - len(items)]
    return items, blocked
