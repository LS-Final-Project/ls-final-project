"""KAN-101 검색 채점기. 합성 질의·청크만 쓰고 실제 데이터·정답 파일은 쓰지 않는다."""

import pytest

from eval.search_metrics import citation_position, condition_coverage, summarize_retrieval

GOLD = {
    "q1": frozenset({"a", "b"}),
    "q2": frozenset({"c"}),
    "q3": frozenset({"d"}),
    "q4": frozenset(),
}
RANKED = {"q1": ["x", "a", "y", "b"], "q2": ["c"], "q4": ["z"]}


def test_retrieval_matches_hand_calculation():
    """recall·hit·MRR 손계산과 gold 없는 질의·결과 없는 질의 처리를 확인한다.

    Args:
        없음.

    Returns:
        None: q1(2·4위 적중), q2(1위), q3(결과 없음), q4(gold 없음, 분모 제외).
    """
    result = summarize_retrieval(RANKED, GOLD, k_values=(1, 3, 5), input_mode="REVIEWED")
    assert result["input_mode"] == "REVIEWED"
    assert (result["queries"], result["no_gold_queries"], result["missing_results"]) == (4, 1, 1)
    assert result["recall_at_k"] == pytest.approx({1: 1 / 3, 3: 0.5, 5: 2 / 3})
    assert result["hit_at_k"] == pytest.approx({1: 1 / 3, 3: 2 / 3, 5: 2 / 3})
    assert result["mrr"] == pytest.approx(0.5)
    rows = {row["query_id"]: row for row in result["per_query"]}
    assert rows["q1"]["first_hit_rank"] == 2 and rows["q1"]["recall_at_k"][3] == 0.5
    assert rows["q3"]["first_hit_rank"] is None and not rows["q3"]["has_results"]
    assert rows["q4"]["recall_at_k"] == {1: None, 3: None, 5: None}


def test_retrieval_without_gold_queries_is_none():
    """gold가 있는 질의가 없으면 평균은 None이다.

    Args:
        없음.

    Returns:
        None: 0으로 채우지 않음을 확인한다.
    """
    result = summarize_retrieval({}, {"q": frozenset()}, input_mode="RAW")
    assert result["mrr"] is None and result["recall_at_k"][1] is None


@pytest.mark.parametrize(
    "ranked,kwargs",
    [
        ({"q1": ["a", "a"]}, {}),
        (RANKED, {"k_values": (0, 1)}),
        (RANKED, {"k_values": ()}),
        (RANKED, {"input_mode": "MIXED"}),
        ({"unknown": ["a"]}, {}),
    ],
    ids=["duplicate-chunk", "k-zero", "no-k", "bad-mode", "unknown-query"],
)
def test_retrieval_rejects_invalid_input(ranked, kwargs):
    """중복 청크·잘못된 k·미지원 입력 구분·정답에 없는 질의를 거부한다.

    Args:
        ranked: 순위 결과.
        kwargs: 바꿀 인자.

    Returns:
        None: EVAL_INVALID_INPUT을 확인한다.
    """
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        summarize_retrieval(ranked, GOLD, **({"input_mode": "RAW"} | kwargs))


def test_condition_coverage_reports_missing_terms():
    """상위 k 본문에서 조건 문구를 대소문자 무시 부분 문자열로 찾는다.

    Args:
        없음.

    Returns:
        None: k에 따른 누락, 결과 없는 질의, 빈 조건을 확인한다.
    """
    texts = {"q1": ["Rated 95 kV BIL", "IP54 enclosure"]}
    terms = {"q1": frozenset({"95 kV", "ip54"}), "q2": frozenset({"x"}), "q3": frozenset()}
    top2 = condition_coverage(texts, terms, k=2)
    assert top2["covered_rate"] == pytest.approx(2 / 3)
    rows = {row["query_id"]: row for row in condition_coverage(texts, terms, k=1)["per_query"]}
    assert rows["q1"] == {"query_id": "q1", "covered": False, "missing_terms": ["ip54"]}
    assert rows["q2"]["missing_terms"] == ["x"] and rows["q3"]["covered"]
    assert "enclosure" not in str(top2)
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        condition_coverage(texts, terms, k=0)


def test_citation_position_separates_wrong_position_in_same_document():
    """같은 문서의 다른 위치는 정확 일치나 다른 문서와 따로 센다.

    Args:
        없음.

    Returns:
        None: 건수와 정확 일치율을 확인한다.
    """
    gold = {"q1": frozenset({("A", 3)}), "q2": frozenset({("B", 0)})}
    cited = {
        "q1": [
            {"document_sha256": "A", "ordinal": 3},
            {"document_sha256": "A", "ordinal": 4},
            {"document_sha256": "B", "ordinal": 3},
        ]
    }
    result = citation_position(cited, gold)
    assert (result["citations"], result["exact"]) == (3, 1)
    assert result["same_document_wrong_position"] == 1 and result["other_document"] == 1
    assert result["exact_rate"] == pytest.approx(1 / 3)
    assert result["per_query"][1] == {
        "query_id": "q2",
        "citations": 0,
        "exact": 0,
        "same_document_wrong_position": 0,
        "other_document": 0,
    }
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        citation_position({"q1": [{"document_sha256": "A", "ordinal": -1}]}, gold)
