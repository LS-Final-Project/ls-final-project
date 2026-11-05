"""검색 규칙 기대값(KAN-101 2차). 규칙 표 검사, 기대 집합 풀이, 적중 집계. 파일 외 접근 없음."""

import json
import typing

import pytest

from backend.engine.cs.classification import Category
from eval.search_expected import expected_documents, load_policy, summarize_hits

INDEX = {
    "DS-A": {"doc_type": "DS", "product_code": "A", "family": "PLC"},
    "TG-A": {"doc_type": "TG", "product_code": "A", "family": "PLC"},
    "RN-A": {"doc_type": "RN", "product_code": "A", "family": "PLC"},
    "IG-A": {"doc_type": "IG", "product_code": "A", "family": "PLC"},
    "TG-B": {"doc_type": "TG", "product_code": "B", "family": "UPS"},
    "FAQ-PLC": {"doc_type": "FAQ", "product_code": "", "family": "PLC"},
    "FAQ-UPS": {"doc_type": "FAQ", "product_code": "", "family": "UPS"},
    "POL-RETURN": {"doc_type": "POL", "product_code": "", "family": ""},
    "POL-SERVICE": {"doc_type": "POL", "product_code": "", "family": ""},
}


def test_policy_is_rule_expectation_covering_every_category(tmp_path):
    """규칙 표는 정답이 아닌 규칙 기대값 표시와 분류 유형 10개를 모두 갖고, 틀린 기호는 거부한다."""
    policy = load_policy()
    assert policy["version"] == "search-expected-v1"
    assert policy["basis"] == "RULE_EXPECTATION_NOT_GOLD" and "사람 검수 전" in policy["review"]
    assert set(policy["categories"]) == set(typing.get_args(Category))
    for bad in ({**policy, "basis": "GOLD"}, {**policy, "categories": {"DEFECT": ["XX"]}}):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(ValueError, match="SEARCH_EXPECTED_POLICY"):
            load_policy(path)


def test_expected_sets_follow_product_family_and_policy():
    """제품 문서는 그 제품만, FAQ는 제품 family, 정책은 그 문서이며 없는 종류는 빠진다."""
    categories = load_policy()["categories"]
    assert expected_documents(categories["DEFECT"], "A", INDEX) == {"TG-A", "RN-A", "POL-SERVICE"}
    assert expected_documents(categories["MANUAL"], "A", INDEX) == {"IG-A", "FAQ-PLC"}
    assert expected_documents(categories["RETURN"], "", INDEX) == {"POL-RETURN"}
    assert expected_documents(categories["TECH_SPEC"], "", INDEX) == frozenset()


def test_hits_count_top_k_and_keep_unscored_out_of_denominator():
    """하나라도 상위 k면 적중, 1순위만 top1이며 라벨 없음·기대 없음은 분모에서 빠진다."""
    result = summarize_hits(
        {"t1": ["DS-A", None, "TG-A"], "t2": [None, "DS-A"], "t3": ["DS-A"], "t4": ["TG-A"]},
        {"t1": frozenset({"TG-A"}), "t2": frozenset({"POL-RETURN"}), "t3": None, "t4": frozenset()},
        {"t1": "DEFECT", "t2": "RETURN", "t3": None, "t4": "TECH_SPEC"},
        k=5,
    )
    assert (result["n"], result["no_label"], result["no_expected"]) == (2, 1, 1)
    assert (result["hit_at_5"], result["top1"]) == (0.5, 0.0)
    assert result["by_category"]["DEFECT"] == {"n": 1, "hit_at_5": 1.0, "top1": 0.0}
    with pytest.raises(ValueError, match="SEARCH_EXPECTED_INPUT"):
        summarize_hits({"t1": []}, {}, {}, k=5)
