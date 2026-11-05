"""검색 결과를 잠긴 정답 근거로 채점한다(KAN-101). 파일·DB·모델에 접근하지 않는다.

실험 구분(원문·검수 텍스트·정답 근거 제공)은 호출자가 input_mode로 주며 채점기는 섞지 않는다.
합격 판정과 청크 본문은 결과에 넣지 않는다.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Literal, TypedDict

InputMode = Literal["RAW", "REVIEWED", "GOLD_EVIDENCE"]
INPUT_MODES = frozenset({"RAW", "REVIEWED", "GOLD_EVIDENCE"})


class Citation(TypedDict):
    document_sha256: str
    ordinal: int


def _mean(values: list[float]) -> float | None:
    """평균. 값이 없으면 None이다.

    Args:
        values: 질의별 값.

    Returns:
        float | None: 산술 평균.
    """
    return sum(values) / len(values) if values else None


def summarize_retrieval(
    ranked: Mapping[str, Sequence[str]],
    gold: Mapping[str, frozenset[str]],
    *,
    k_values: Sequence[int] = (1, 3, 5, 10),
    input_mode: InputMode,
) -> dict:
    """질의별 순위 결과의 recall@k·hit@k·MRR을 계산한다.

    Args:
        ranked: 질의 ID별 순위순 chunk_id. 중복은 허용하지 않는다. 없는 질의는 결과 없음으로 센다.
        gold: 질의 ID별 정답 근거 chunk_id. 빈 집합은 "gold 없음"으로 따로 세고 평균 분모에서 뺀다.
        k_values: 1 이상의 k 목록.
        input_mode: 실험 입력 구분. 값만 기록한다.

    Returns:
        dict: input_mode, 질의 수, gold 없는 질의 수, 결과 없는 질의 수(hit 0으로 계산),
            k별 평균 recall·hit, MRR, 질의별 첫 적중 순위와 recall@k. 평균 분모는 gold가 있는 질의이며
            그런 질의가 없으면 None이다. 잘못된 입력은 EVAL_INVALID_INPUT ValueError다.
    """
    if (
        input_mode not in INPUT_MODES
        or not k_values
        or any(type(k) is not int or k < 1 for k in k_values)
        or not set(ranked) <= set(gold)
        or any(len(set(chunks)) != len(chunks) for chunks in ranked.values())
    ):
        raise ValueError("EVAL_INVALID_INPUT")
    ks = sorted(set(k_values))
    per_query: list[dict[str, Any]] = []
    for query_id, expected in gold.items():
        chunks = list(ranked.get(query_id, ()))
        hits = [rank for rank, chunk in enumerate(chunks, 1) if chunk in expected]
        per_query.append(
            {
                "query_id": query_id,
                "has_gold": bool(expected),
                "has_results": query_id in ranked,
                "first_hit_rank": hits[0] if hits else None,
                "recall_at_k": {
                    k: sum(rank <= k for rank in hits) / len(expected) if expected else None
                    for k in ks
                },
            }
        )
    scored = [row for row in per_query if row["has_gold"]]
    return {
        "input_mode": input_mode,
        "quality_gate_evaluated": False,
        "queries": len(gold),
        "no_gold_queries": len(gold) - len(scored),
        "missing_results": sum(not row["has_results"] for row in scored),
        "recall_at_k": {k: _mean([row["recall_at_k"][k] for row in scored]) for k in ks},
        "hit_at_k": {
            k: _mean(
                [
                    float(row["first_hit_rank"] is not None and row["first_hit_rank"] <= k)
                    for row in scored
                ]
            )
            for k in ks
        },
        "mrr": _mean(
            [1 / row["first_hit_rank"] if row["first_hit_rank"] else 0.0 for row in scored]
        ),
        "per_query": per_query,
    }


def condition_coverage(
    ranked_texts: Mapping[str, Sequence[str]],
    required_terms: Mapping[str, frozenset[str]],
    *,
    k: int,
) -> dict:
    """상위 k개 본문에 필수 조건 문구가 모두 있는 질의 비율을 센다.

    Args:
        ranked_texts: 질의 ID별 순위순 청크 본문. 없는 질의는 본문 없음으로 센다.
        required_terms: 질의 ID별 필수 조건 문구. 정확한 부분 문자열을 대소문자 무시로 찾는다
            (유사도·토큰화 없음). 빈 집합은 충족으로 본다.
        k: 1 이상의 상위 개수.

    Returns:
        dict: k, 질의 수, 충족 비율(질의 0이면 None), 질의별 충족 여부와 누락 문구(정렬). 본문은 넣지 않는다.
    """
    if type(k) is not int or k < 1 or not set(ranked_texts) <= set(required_terms):
        raise ValueError("EVAL_INVALID_INPUT")
    per_query: list[dict[str, Any]] = []
    for query_id, terms in required_terms.items():
        haystack = "\n".join(ranked_texts.get(query_id, ())[:k]).casefold()
        missing = sorted(term for term in terms if term.casefold() not in haystack)
        per_query.append({"query_id": query_id, "covered": not missing, "missing_terms": missing})
    return {
        "k": k,
        "queries": len(per_query),
        "covered_rate": _mean([float(row["covered"]) for row in per_query]),
        "per_query": per_query,
    }


def citation_position(
    cited: Mapping[str, Sequence[Citation]],
    gold_positions: Mapping[str, frozenset[tuple[str, int]]],
) -> dict:
    """인용한 (문서, ordinal)이 정답 위치와 정확히 같은지 센다.

    Args:
        cited: 질의 ID별 인용 목록. 각 항목은 document_sha256·ordinal만 가진다. 없는 질의는 인용 없음이다.
        gold_positions: 질의 ID별 정답 (document_sha256, ordinal) 집합.

    Returns:
        dict: 전체 인용 수, 정확 일치·같은 문서 다른 위치·다른 문서 건수, 정확 일치율(인용 0이면 None),
            질의별 같은 건수. 잘못된 입력은 EVAL_INVALID_INPUT ValueError다.
    """
    if not set(cited) <= set(gold_positions) or any(
        not isinstance(item, Mapping)
        or set(item) != {"document_sha256", "ordinal"}
        or not isinstance(item["document_sha256"], str)
        or type(item["ordinal"]) is not int
        or item["ordinal"] < 0
        for items in cited.values()
        for item in items
    ):
        raise ValueError("EVAL_INVALID_INPUT")
    per_query: list[dict[str, Any]] = []
    for query_id, expected in gold_positions.items():
        documents = {sha for sha, _ in expected}
        counts = {
            "citations": 0,
            "exact": 0,
            "same_document_wrong_position": 0,
            "other_document": 0,
        }
        for item in cited.get(query_id, ()):
            counts["citations"] += 1
            if (item["document_sha256"], item["ordinal"]) in expected:
                counts["exact"] += 1
            elif item["document_sha256"] in documents:
                counts["same_document_wrong_position"] += 1
            else:
                counts["other_document"] += 1
        per_query.append({"query_id": query_id, **counts})
    totals = {
        key: sum(row[key] for row in per_query)
        for key in ("citations", "exact", "same_document_wrong_position", "other_document")
    }
    return {
        "queries": len(per_query),
        **totals,
        "exact_rate": totals["exact"] / totals["citations"] if totals["citations"] else None,
        "per_query": per_query,
    }
