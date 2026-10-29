"""KAN-100 하이브리드 결합. 검색기를 함수로 꾸며 DB 없이 검사한다."""

from typing import Any

import pytest

from backend.engine.search.hybrid import candidates_from_fts, codes_in, mentions_code, run_hybrid
from backend.engine.search.model import Candidate, Fused, SearchFilters, Source

A, B = "a" * 64, "b" * 64
FILTERS = SearchFilters(document_sha256s=[A, B], index_version="chunking-v1", limit=10)


def cand(chunk_id: str, source: Source, position: int, **extra: Any) -> Candidate:
    fields: dict[str, Any] = {
        "chunk_id": chunk_id,
        "document_sha256": A,
        "ordinal": 0,
        "section_path": [],
        "snippet": "",
        "text_excerpt": None,
        "kind": "TEXT",
        "table_title": None,
        "page_start": None,
        "source": source,
        "position": position,
        "score": None,
    }
    return Candidate.model_validate(fields | extra)


def fixed(*candidates: Candidate):
    return lambda query, filters: list(candidates)


def ids(items: list[Fused]) -> list[str]:
    return [item.candidate.chunk_id for item in items]


def test_dedup_keeps_both_sources_and_sums_rrf() -> None:
    result = run_hybrid(
        "breaker",
        FILTERS,
        fts=fixed(cand("x", "FTS", 1), cand("y", "FTS", 2, snippet="from fts")),
        vector=fixed(cand("y", "VECTOR", 1), cand("z", "VECTOR", 2)),
    )
    assert ids(result.items) == ["y", "x", "z"]
    y, x, z = result.items
    assert y.sources == ["FTS", "VECTOR"] and x.sources == ["FTS"] and z.sources == ["VECTOR"]
    assert y.candidate.snippet == "from fts"
    assert y.rrf_score == pytest.approx(1 / 62 + 1 / 61)
    assert (x.rrf_score, z.rrf_score) == (pytest.approx(1 / 61), pytest.approx(1 / 62))


def test_ties_break_by_document_then_ordinal() -> None:
    result = run_hybrid(
        "breaker",
        FILTERS,
        fts=fixed(cand("b0", "FTS", 1, document_sha256=B), cand("a1", "FTS", 2, ordinal=1)),
        vector=fixed(cand("a5", "VECTOR", 1, ordinal=5), cand("a0", "VECTOR", 2)),
    )
    assert ids(result.items) == ["a5", "b0", "a0", "a1"]


def test_codes_in_query_skip_ratings_and_keep_case() -> None:
    query = "UL 1066 and IEC 60947-2 for MCCB-ABS-103c, not FLT-042. 480V 15kV 4000 A, UL 1066"
    assert codes_in(query) == ["UL 1066", "IEC 60947-2", "MCCB-ABS-103c", "FLT-042"]


def test_exact_code_match_comes_first_regardless_of_rrf() -> None:
    result = run_hybrid(
        "MCCB-ABS-103c rating",
        FILTERS,
        fts=fixed(
            cand("other", "FTS", 1, text_excerpt="MCCB-ABS-403c rated 100 A"),
            cand("suffix", "FTS", 2, text_excerpt="see MCCB-ABS-103c-X"),
            cand("lower", "FTS", 3, table_title="mccb-abs-103c ratings"),
            cand("hit", "FTS", 4, snippet="<b>MCCB</b>-<b>ABS</b>-<b>103c</b> is rated"),
            cand("title", "FTS", 5, table_title="Table 2 MCCB-ABS-103c"),
        ),
    )
    assert result.codes_in_query == ["MCCB-ABS-103c"]
    assert ids(result.items) == ["hit", "title", "other", "suffix", "lower"]
    assert [item.exact_code_match for item in result.items] == [True, True, False, False, False]


def test_blocked_codes_are_dropped_before_rerank_and_recorded() -> None:
    seen: list[list[str]] = []

    class Spy:
        def rerank(self, query: str, items: list[Fused]) -> list[Fused]:
            seen.append(ids(items))
            return items

    result = run_hybrid(
        "inverter leakage",
        FILTERS,
        fts=fixed(
            cand("plain", "FTS", 1, text_excerpt="Inverter leakage sensitivity"),
            cand("hidden", "FTS", 2, snippet="sensitivity", text_excerpt="per flt-042 advice"),
            cand("near", "FTS", 3, text_excerpt="FLT-0421 is unrelated"),
        ),
        vector=fixed(cand("tagged", "VECTOR", 1, snippet="<b>FLT</b>-<b>042</b> 100mA")),
        reranker=Spy(),
    )
    assert result.dropped_blocked == ["hidden", "tagged"]
    assert ids(result.items) == seen[0] == ["plain", "near"]


def test_without_vector_only_fts_is_used() -> None:
    result = run_hybrid("breaker", FILTERS, fts=fixed(cand("x", "FTS", 1), cand("y", "FTS", 2)))
    assert ids(result.items) == ["x", "y"]
    assert all(item.sources == ["FTS"] for item in result.items)


def test_reranker_order_is_kept_but_code_matches_stay_first() -> None:
    class Reverse:
        def rerank(self, query: str, items: list[Fused]) -> list[Fused]:
            return items[::-1]

    result = run_hybrid(
        "FLT-099 trip",
        FILTERS,
        fts=fixed(
            cand("code", "FTS", 1, text_excerpt="FLT-099 trip"),
            cand("x", "FTS", 2),
            cand("y", "FTS", 3),
            cand("z", "FTS", 4),
        ),
        reranker=Reverse(),
    )
    assert ids(result.items) == ["code", "z", "y", "x"]


def test_both_searchers_get_the_same_filters_and_limit_applies() -> None:
    calls: list[tuple[str, str, SearchFilters]] = []

    def record(name: str, *candidates: Candidate):
        def search(query: str, filters: SearchFilters) -> list[Candidate]:
            calls.append((name, query, filters))
            return list(candidates)

        return search

    filters = SearchFilters(document_sha256s=None, index_version="chunking-v1", limit=2)
    result = run_hybrid(
        "breaker",
        filters,
        fts=record("fts", cand("x", "FTS", 1), cand("y", "FTS", 2)),
        vector=record("vector", cand("z", "VECTOR", 1)),
    )
    assert [call[:2] for call in calls] == [("fts", "breaker"), ("vector", "breaker")]
    assert calls[0][2] is calls[1][2] is filters
    assert result.filters is filters and result.query == "breaker"
    assert ids(result.items) == ["x", "z"]


def test_fts_rows_become_ranked_candidates() -> None:
    row = {
        "chunk_id": "c1",
        "document_sha256": A,
        "ordinal": 3,
        "section_path": ["2.6 Switchgear"],
        "snippet": "<b>UL</b> 1066",
        "rank": 0.5,
        "kind": "TABLE",
        "table_title": "Table 1",
        "page_start": None,
        "text": "Table 1 UL 1066 ratings",
    }
    first, second = candidates_from_fts([row, row | {"chunk_id": "c2", "rank": 0.25}])
    assert (first.position, first.score, first.source) == (1, 0.5, "FTS")
    assert first.text_excerpt == row["text"] and first.section_path == ["2.6 Switchgear"]
    assert (second.chunk_id, second.position) == ("c2", 2)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("per FLT&#45;042 advice", True),
        ("per FLT–042 advice", True),
        ("per flt — 042 advice", True),
        ("per FLT 042 advice", True),
        ("per <b>Flt</b>-<b>042</b>.", True),
        ("code FLT-0420 only", False),
        ("code FLT-04 only", False),
        ("code XFLT-042 only", False),
        ("per ＦＬＴ－０４２ advice", True),
        ("per FLT_042 advice", True),
        ("per F\u200bLT-042 advice", True),
        ("per FLT-\u200d042 advice", True),
        ("per FLT\ufeff-042 advice", True),
        ("per FLT\u00ad-042 advice", True),
        ("per FLT-<B>042</B> advice", True),
        ("code FLT042 only", False),
    ],
)
def test_blocked_code_check_folds_spelling_variants(text: str, expected: bool) -> None:
    assert mentions_code(text, "FLT-042") is expected
