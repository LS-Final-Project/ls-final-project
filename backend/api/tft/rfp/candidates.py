"""자사 후보 찾기: 제품 마스터 계열 필터 + 문서 검색(R10 하이브리드)(KAN-186). DB·모델 호출 없이 테스트한다.

검색은 비슷한 후보를 넓게 찾는 일이고, 충족 여부는 `comparison.compare`가 같은 속성·단위·조건 규칙으로만 정한다.
검색 엔진(R08~R11: 청킹·FTS·벡터·RRF·리랭킹, `backend/engine/search/hybrid.py`)은 다른 담당자 구현이다. 이 모듈은 그
결과를 소비해 후보 제품과 검색 범위(`SearchTrace`)로 바꾸는 쪽이다.

- 범위 지정: 계열 필터(`family=`)와 검색 조건(색인 판본·상한·문서 해시 목록)을 그대로 `SearchTrace.filters`에 남긴다.
  "후보 없음"은 이 범위의 결과로만 말한다(compare 사유에 범위가 들어간다).
- 문서 → 제품: 청크에는 제품 코드가 없어 색인 manifest의 문서 해시 → 제품 코드 표로 잇는다. 표에 없는 문서와 자사
  후보 목록에 없는 제품은 후보로 만들지 않는다(근거 없는 후보 금지).
- 검색이 없거나 실패해도 비교는 계열 필터만으로 동작한다. 검색 실패를 제품 부재로 확정하지 않는다: 계열 필터 후보가
  있으면 비교하고 범위에 `document_search=failed`를 남기며, 후보가 하나도 없을 때만 `search_failed`로 판정을 보류한다.
"""

from collections.abc import Mapping, Sequence

from backend.api.tft.rfp.comparison import Product, Requirement, SearchTrace
from backend.engine.search.hybrid import Searcher, run_hybrid, token_or_query
from backend.engine.search.model import SearchFilters


def query_for(requirement: Requirement) -> tuple[str, str]:
    """요구에서 검색 질의(토큰 OR)와 코드 일치용 원문을 만든다.

    Returns:
        tuple: (질의, 원문). 원문은 'UL 1066'·'IEEE C57.12.01' 같은 코드가 쪼개지지 않게 run_hybrid에 따로 넘긴다.
    """
    parts = [
        requirement.item,
        (requirement.attribute or "").replace("_", " "),
        requirement.value,
        requirement.unit,
        requirement.certification_standard,
    ]
    original = " ".join(p for p in parts if p)
    return token_or_query(*parts), original


def find_candidates(
    requirement: Requirement,
    products: Sequence[Product],
    family: str | None,
    *,
    fts: Searcher | None = None,
    vector: Searcher | None = None,
    filters: SearchFilters | None = None,
    document_products: Mapping[str, str] | None = None,
) -> tuple[list[Product], SearchTrace]:
    """계열 필터와 문서 검색으로 자사 후보를 찾고 검색 범위를 돌려준다.

    Args:
        requirement: 비교할 요구.
        products: 자사 후보 전체(own_spec_facts).
        family: 요구 장비의 제품 계열. None이면 계열 필터를 쓰지 않는다.
        fts: FTS 검색기(R10). None이면 문서 검색 없이 계열 필터만 쓴다.
        vector: 벡터 검색기(R10, 선택).
        filters: 검색 조건. fts가 있으면 필요하다.
        document_products: 문서 해시 → 제품 코드(색인 manifest).

    Returns:
        tuple: (후보 제품, 검색 범위). 후보 순서는 계열 필터 결과 다음 검색 순위다.
    """
    by_code = {p.product_code: p for p in products}
    scope: list[str] = []
    found: list[Product] = []
    if family is not None:
        scope.append(f"family={family}")
        found = [p for p in products if p.family == family]
    documents = 0
    failed = False
    if fts is not None:
        if filters is None:
            raise ValueError("TFT_SEARCH_FILTERS_REQUIRED")
        scope.append(f"index={filters.index_version}")
        scope.append(f"limit={filters.limit}")
        if filters.document_sha256s is not None:
            scope.append(f"documents={len(filters.document_sha256s)}")
        query, original = query_for(requirement)
        try:
            result = run_hybrid(query, filters, fts=fts, vector=vector, code_text=original)
        except Exception:
            # 검색 실패는 제품 부재가 아니다. 사유를 남기고 계열 필터 후보로 계속한다.
            failed = True
            scope.append("document_search=failed")
        else:
            hits = [item.candidate.document_sha256 for item in result.items]
            documents = len(set(hits))
            mapping = document_products or {}
            for sha in hits:
                product = by_code.get(mapping.get(sha, ""))
                if product is not None and product not in found:
                    found.append(product)
    return found, SearchTrace(
        filters=scope,
        documents_searched=documents if fts is not None else len(found),
        # 후보가 하나도 없고 검색도 실패했을 때만 판정을 보류한다(제품 없음으로 보지 않음).
        search_failed=failed and not found,
    )
