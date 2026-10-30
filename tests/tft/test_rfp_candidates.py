"""KAN-186 후보 찾기: 계열 필터 + R10 하이브리드 검색 소비. 가짜 검색기, DB·모델 없음.

검색 엔진(R08~R11)은 다른 담당자 구현이고 여기서는 그 결과를 후보 제품·검색 범위로 바꾸는 쪽만 본다. 검색으로 찾은
후보에도 같은 속성·단위·조건 규칙과 IEC→IEEE 확장 금지가 그대로 적용되는지 확인한다.
"""

import pytest

from backend.api.tft.rfp.candidates import find_candidates, query_for
from backend.api.tft.rfp.comparison import Requirement, compare, load_own_products
from backend.engine.search.model import Candidate, SearchFilters
from tests.tft.test_rfp_comparison import DEMO_A, switchgear

FILTERS = SearchFilters(document_sha256s=None, index_version="chunking-v1", limit=5)
TRF_DOC, SWGR_DOC, UNKNOWN_DOC = "a" * 64, "b" * 64, "c" * 64
DOCS = {TRF_DOC: "TRF-DRY-630", SWGR_DOC: "SWGR-TEST"}
VOLTAGE = Requirement(
    clause_no="2.4",
    item="정격 15 kV",
    attribute="rated_max_voltage",
    value="15",
    unit="kV",
    operator="GTE",
)


def chunk(sha, position, text="Rated voltage 12 kV"):
    return Candidate(
        chunk_id=f"{sha[:4]}-{position}",
        document_sha256=sha,
        ordinal=position,
        section_path=["2"],
        snippet=text,
        text_excerpt=text,
        kind="TEXT",
        table_title=None,
        page_start=None,
        source="FTS",
        position=position,
        score=None,
    )


def searcher(*shas):
    seen = []

    def search(query, filters):
        seen.append((query, filters))
        return [chunk(sha, i + 1) for i, sha in enumerate(shas)]

    search.seen = seen
    return search


def products():
    return [*load_own_products(), switchgear("12")]


def test_search_hits_become_candidates_and_the_rules_still_decide():
    """15 kV 요구에 검색은 12 kV 개폐장치를 찾고, 규칙은 같은 속성·단위로 미달을 판정한다."""
    fts = searcher(SWGR_DOC, UNKNOWN_DOC)
    found, trace = find_candidates(
        VOLTAGE, products(), "SWITCHGEAR", fts=fts, filters=FILTERS, document_products=DOCS
    )
    # The family filter finds it too; a document not in the map adds nothing.
    assert [p.product_code for p in found] == ["SWGR-TEST"]
    assert trace.filters == ["family=SWITCHGEAR", "index=chunking-v1", "limit=5"]
    assert (trace.documents_searched, trace.search_failed) == (2, False)
    outcome = compare(VOLTAGE, found, trace)
    assert (outcome.status, outcome.candidates[0].result) == ("SPEC_GAP", "DIFFERS")
    query, _ = fts.seen[0]
    assert "15" in query and "kV" in query


def test_search_widens_beyond_the_family_but_never_invents_a_product():
    """계열이 달라도 문서 검색으로 찾은 자사 제품은 후보가 되고, 자사 목록에 없는 제품은 만들지 않는다."""
    found, trace = find_candidates(
        VOLTAGE,
        products(),
        "TRANSFORMER",
        fts=searcher(SWGR_DOC, UNKNOWN_DOC),
        filters=FILTERS,
        document_products={**DOCS, UNKNOWN_DOC: "NOT-OURS"},
    )
    assert [p.product_code for p in found] == ["TRF-DRY-630", "SWGR-TEST"]
    # The transformer has no rated_max_voltage fact: missing data, not unmet; the best is the gap.
    outcome = compare(VOLTAGE, found, trace)
    assert {c.product_code: c.status for c in outcome.candidates} == {
        "TRF-DRY-630": "INSUFFICIENT_EVIDENCE",
        "SWGR-TEST": "SPEC_GAP",
    }


def test_rules_apply_to_searched_candidates_iec_is_not_widened_to_ieee():
    ieee = DEMO_A[10]
    found, trace = find_candidates(
        ieee, products(), None, fts=searcher(TRF_DOC), filters=FILTERS, document_products=DOCS
    )
    assert [p.product_code for p in found] == ["TRF-DRY-630"]
    outcome = compare(ieee, found, trace)
    assert outcome.status == "INSUFFICIENT_EVIDENCE" and "IEC 체계만" in outcome.reason
    _, original = query_for(ieee)
    assert "IEEE C57.12.01" in original


def test_no_candidate_is_the_search_scope_result():
    found, trace = find_candidates(
        VOLTAGE,
        products(),
        "GIS",
        fts=searcher(UNKNOWN_DOC),
        filters=FILTERS,
        document_products=DOCS,
    )
    assert found == []
    outcome = compare(VOLTAGE, found, trace)
    assert outcome.status == "INSUFFICIENT_EVIDENCE"
    assert "검색 범위(family=GIS, index=chunking-v1, limit=5, 문서 1건)" in outcome.reason
    assert "참여 불가를 뜻하지 않음" in outcome.reason


def test_a_failed_search_is_not_product_absence():
    def broken(query, filters):
        raise RuntimeError("search down")

    # Family candidates exist: compare them and record the failed document search.
    found, trace = find_candidates(VOLTAGE, products(), "SWITCHGEAR", fts=broken, filters=FILTERS)
    assert ([p.product_code for p in found], trace.search_failed) == (["SWGR-TEST"], False)
    assert "document_search=failed" in trace.filters
    # Nothing at all: the judgement is held, not "no product".
    none, held = find_candidates(VOLTAGE, products(), "GIS", fts=broken, filters=FILTERS)
    assert (none, held.search_failed) == ([], True)
    assert compare(VOLTAGE, none, held).unresolved == ["후보 검색 재실행"]


def test_without_r10_the_family_filter_alone_works():
    found, trace = find_candidates(DEMO_A[0], products(), "TRANSFORMER")
    assert [p.product_code for p in found] == ["TRF-DRY-630"]
    assert trace.filters == ["family=TRANSFORMER"] and trace.documents_searched == 1
    with pytest.raises(ValueError):
        find_candidates(DEMO_A[0], products(), "TRANSFORMER", fts=searcher())
