"""검색 hit@5 측정용 규칙 기대 문서(KAN-101 2차). 정답이 아니라 규칙 기대값이며 사람 검수 전이다.

문의 유형별 기대 기호(eval/policy/search_expected_v1.json)와 문서 묶음 구조(doc_index.csv)로 문의의 기대 doc_id
집합을 만든다. DS·IG·TG·MG·RN은 문의 제품의 그 종류 문서, FAQ는 FAQ-{제품 family}, POL-*는 그 정책 문서다.
기호의 합집합이 기대 집합이며 하나라도 상위 k에 있으면 적중이다. 파일 읽기 외에 DB·모델에 접근하지 않는다.
"""

import csv
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

POLICY_PATH = Path(__file__).parent / "policy" / "search_expected_v1.json"
PRODUCT_TYPES = frozenset({"DS", "IG", "TG", "MG", "RN"})


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    """규칙 표를 읽고 기호를 검사한다.

    Args:
        path: 규칙 표 JSON.

    Returns:
        dict: version·basis·review·categories. 기호가 틀리거나 정답 표시가 아니면 SEARCH_EXPECTED_POLICY ValueError.
    """
    policy = json.loads(path.read_text(encoding="utf-8"))
    categories = policy.get("categories")
    if (
        policy.get("basis") != "RULE_EXPECTATION_NOT_GOLD"
        or not isinstance(policy.get("version"), str)
        or not isinstance(categories, dict)
        or not categories
        or any(
            not isinstance(symbols, list)
            or not symbols
            or any(
                not isinstance(s, str)
                or not (s in PRODUCT_TYPES or s == "FAQ" or s.startswith("POL-"))
                for s in symbols
            )
            for symbols in categories.values()
        )
    ):
        raise ValueError("SEARCH_EXPECTED_POLICY")
    return policy


def load_doc_index(path: Path) -> dict[str, dict[str, str]]:
    """doc_index.csv를 doc_id별 {doc_type, product_code, family}로 읽는다(BOM 허용).

    Args:
        path: 문서 묶음의 doc_index.csv.

    Returns:
        dict: doc_id별 문서 구조. 빈 doc_id나 중복은 SEARCH_EXPECTED_DOC_INDEX ValueError.
    """
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    index = {
        row["doc_id"]: {key: row.get(key) or "" for key in ("doc_type", "product_code", "family")}
        for row in rows
    }
    if not rows or "" in index or len(index) != len(rows):
        raise ValueError("SEARCH_EXPECTED_DOC_INDEX")
    return index


def expected_documents(
    symbols: Sequence[str], product_code: str, doc_index: Mapping[str, Mapping[str, str]]
) -> frozenset[str]:
    """기대 기호를 문의 제품에 맞는 doc_id 집합으로 푼다.

    Args:
        symbols: 문의 유형의 기대 기호.
        product_code: 문의 제품(없으면 빈 문자열이고 제품·FAQ 기호는 비게 된다).
        doc_index: load_doc_index 결과.

    Returns:
        frozenset[str]: 기대 doc_id. 제품에 그 종류 문서가 없으면 그 기호는 빠진다.
    """
    family = next(
        (
            d["family"]
            for d in doc_index.values()
            if product_code and d["product_code"] == product_code
        ),
        "",
    )
    found: set[str] = set()
    for symbol in symbols:
        if symbol in PRODUCT_TYPES:
            found |= {
                doc_id
                for doc_id, doc in doc_index.items()
                if product_code
                and doc["doc_type"] == symbol
                and doc["product_code"] == product_code
            }
        elif symbol == "FAQ":
            found |= {
                doc_id
                for doc_id, doc in doc_index.items()
                if family and doc["doc_type"] == "FAQ" and doc["family"] == family
            }
        elif symbol in doc_index:
            found.add(symbol)
    return frozenset(found)


def summarize_hits(
    ranked: Mapping[str, Sequence[str | None]],
    expected: Mapping[str, frozenset[str] | None],
    categories: Mapping[str, str | None],
    *,
    k: int = 5,
) -> dict[str, Any]:
    """문의별 순위 doc_id로 hit@k·top1을 내고 유형별로 나눈다.

    Args:
        ranked: 문의별 순위순 doc_id(README 등 doc_id 없는 문서는 None).
        expected: 문의별 기대 집합. None은 라벨 없음, 빈 집합은 기대 없음이다.
        categories: 문의별 분류 결과 유형(None은 라벨 없음).
        k: 적중 판정 순위.

    Returns:
        dict: tickets·no_label·no_expected 수, 채점 수 n, hit_at_k·top1(분모 n, 없으면 None), 유형별 같은 값.
    """
    if set(ranked) != set(expected) or set(expected) != set(categories) or k < 1:
        raise ValueError("SEARCH_EXPECTED_INPUT")
    groups: dict[str, list[tuple[bool, bool]]] = {}
    no_label = no_expected = 0
    for ticket_id, docs in ranked.items():
        want = expected[ticket_id]
        if want is None or categories[ticket_id] is None:
            no_label += 1
            continue
        if not want:
            no_expected += 1
            continue
        top = list(docs)[:k]
        groups.setdefault(str(categories[ticket_id]), []).append(
            (any(d in want for d in top), bool(top) and top[0] in want)
        )

    def rates(items: list[tuple[bool, bool]]) -> dict[str, Any]:
        n = len(items)
        return {
            "n": n,
            f"hit_at_{k}": sum(h for h, _ in items) / n if n else None,
            "top1": sum(t for _, t in items) / n if n else None,
        }

    every = [item for items in groups.values() for item in items]
    return {
        "tickets": len(ranked),
        "no_label": no_label,
        "no_expected": no_expected,
        **rates(every),
        "by_category": {name: rates(items) for name, items in sorted(groups.items())},
    }
