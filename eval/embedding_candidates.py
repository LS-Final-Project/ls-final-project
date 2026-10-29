"""임베딩 후보를 DB 없이 메모리 내 코사인 순위로 비교한다(KAN-144 3차).

벡터 열·pgvector가 생기기 전 모델 선택용이다. C/S·RFP는 각각 따로 돌린다. 채점은
eval.search_metrics.summarize_retrieval을 그대로 쓰고 새 계산식을 만들지 않는다. 임베딩 호출은 호출자가 주입한
콜러블(backend.shared.ai.embed를 감싼 것)이며 이 모듈은 DB·파일에 접근하지 않는다. 벡터·본문과 합격 판정은
결과에 넣지 않는다.
"""

import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from eval.search_metrics import InputMode, summarize_retrieval

PROVIDER_ERROR = "PROVIDER_ERROR"


class _ProviderFailed(Exception):
    """공급자 호출 실패. 원문 없이 후보 행 오류로만 바꾼다."""


@dataclass(frozen=True)
class EmbeddingCandidate:
    """비교할 임베딩 후보. embed는 문자열 배치를 받아 같은 순서의 벡터 배치를 돌려준다."""

    name: str
    embed: Callable[[list[str]], list[list[float]]]
    model: str
    dimension: int | None
    normalize: bool = True


def _unit(vector: Sequence[float], dimension: int | None, normalize: bool) -> list[float]:
    """벡터를 검사하고 코사인 비교용으로 정규화한다.

    Args:
        vector: 공급자가 돌려준 벡터.
        dimension: 기대 차원. None이면 검사하지 않는다(대신 후보 안의 모든 벡터 길이가 같아야 한다).
        normalize: True면 길이 1로 나눈다. False면 공급자가 이미 정규화했다고 보고 그대로 쓴다.

    Returns:
        list[float]: 비교용 벡터. 차원 불일치·비유한 값·0 벡터는 EVAL_INVALID_INPUT ValueError다.
    """
    values = [float(v) for v in vector]
    if (
        not values
        or (dimension is not None and len(values) != dimension)
        or not all(math.isfinite(v) for v in values)
    ):
        raise ValueError("EVAL_INVALID_INPUT")
    norm = math.sqrt(sum(v * v for v in values))
    if norm == 0:
        raise ValueError("EVAL_INVALID_INPUT")
    return [v / norm for v in values] if normalize else values


def _embed_all(
    candidate: EmbeddingCandidate, texts: list[str], batch_size: int
) -> tuple[list[list[float]], int]:
    """텍스트를 batch_size씩 임베딩한다.

    Args:
        candidate: 후보.
        texts: 임베딩할 문자열(순서 유지).
        batch_size: 한 호출의 최대 문자열 수.

    Returns:
        tuple: (검사·정규화한 벡터 목록, 호출 횟수). 공급자가 개수가 다른 배치를 돌려주면 EVAL_INVALID_INPUT이고,
            공급자 예외는 _ProviderFailed다.
    """
    vectors: list[list[float]] = []
    calls = 0
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        try:
            result = candidate.embed(batch)
        except Exception:
            # 공급자 원문·예외 이름은 기록하지 않는다. 벡터 검사 오류와 섞이지 않게 따로 올린다.
            raise _ProviderFailed from None
        calls += 1
        if not isinstance(result, list) or len(result) != len(batch):
            raise ValueError("EVAL_INVALID_INPUT")
        vectors.extend(_unit(v, candidate.dimension, candidate.normalize) for v in result)
    return vectors, calls


def _metrics(summary: dict) -> dict:
    """summarize_retrieval 결과에서 비교표 지표만 꺼낸다.

    Args:
        summary: summarize_retrieval 결과.

    Returns:
        dict: recall_at_k·hit_at_k·mrr.
    """
    return {key: summary[key] for key in ("recall_at_k", "hit_at_k", "mrr")}


def run_embedding_candidates(
    chunks: Mapping[str, str],
    queries: Mapping[str, str],
    *,
    gold: Mapping[str, frozenset[str]],
    candidates: list[EmbeddingCandidate],
    k_values: Sequence[int] = (1, 3, 5, 10),
    batch_size: int = 64,
    input_mode: InputMode = "REVIEWED",
) -> dict:
    """후보마다 청크·질의를 임베딩해 코사인 상위 순위를 만들고 summarize_retrieval로 비교한다.

    Args:
        chunks: chunk_id별 본문.
        queries: query_id별 질의. ID 집합이 gold와 같아야 한다.
        gold: query_id별 정답 근거 chunk_id(chunks 안에서만).
        candidates: 이름이 고유한 후보.
        k_values: 1 이상의 k 목록. 순위는 max(k)개까지 만든다.
        batch_size: 임베딩 호출 한 번의 최대 문자열 수(1 이상).
        input_mode: summarize_retrieval에 그대로 넘기는 실험 입력 구분.

    Returns:
        dict: candidates(후보 행: name·model·dimension(실측)·recall_at_k·hit_at_k·mrr·embed_calls·elapsed_ms·
            chunk_count·query_count·error)와 ranking. 순위 동률은 chunk_id 정렬로 정한다. 공급자 예외는
            error=PROVIDER_ERROR로 두고 지표·실측 차원은 None이다(원문 없음). ranking은 hit@5(없으면 가장 큰 k)
            내림차순 → MRR 내림차순 → 이름순이며 오류 행은 지표가 None이라 뒤로 간다. 입력·벡터 위반(차원 불일치·0 벡터·개수
            불일치)은 EVAL_INVALID_INPUT ValueError다.
    """
    names = [c.name for c in candidates if isinstance(c, EmbeddingCandidate)]
    if (
        not chunks
        or not queries
        or not candidates
        or len(names) != len(candidates)
        or not all(names)
        or len(set(names)) != len(names)
        or set(queries) != set(gold)
        or any(not ids <= set(chunks) for ids in gold.values())
        or type(batch_size) is not int
        or batch_size < 1
        or not k_values
        or any(type(k) is not int or k < 1 for k in k_values)
    ):
        raise ValueError("EVAL_INVALID_INPUT")
    chunk_ids = sorted(chunks)
    query_ids = list(queries)
    depth = max(k_values)
    rows = []
    for candidate in candidates:
        started = time.perf_counter()
        row: dict[str, Any] = {
            "name": candidate.name,
            "model": candidate.model,
            "chunk_count": len(chunk_ids),
            "query_count": len(query_ids),
        }
        try:
            chunk_vectors, chunk_calls = _embed_all(
                candidate, [chunks[c] for c in chunk_ids], batch_size
            )
            query_vectors, query_calls = _embed_all(
                candidate, [queries[q] for q in query_ids], batch_size
            )
        except _ProviderFailed:
            rows.append(
                row
                | {
                    "dimension": None,
                    "recall_at_k": None,
                    "hit_at_k": None,
                    "mrr": None,
                    "embed_calls": None,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                    "error": PROVIDER_ERROR,
                }
            )
            continue
        if len({len(v) for v in (*chunk_vectors, *query_vectors)}) != 1:
            raise ValueError("EVAL_INVALID_INPUT")
        ranked = {}
        for query_id, query_vector in zip(query_ids, query_vectors, strict=True):
            scores = [
                (-sum(a * b for a, b in zip(query_vector, vector, strict=True)), chunk_id)
                for chunk_id, vector in zip(chunk_ids, chunk_vectors, strict=True)
            ]
            ranked[query_id] = [chunk_id for _, chunk_id in sorted(scores)[:depth]]
        summary = summarize_retrieval(ranked, gold, k_values=k_values, input_mode=input_mode)
        rows.append(
            row
            | {"dimension": len(chunk_vectors[0])}
            | _metrics(summary)
            | {
                "embed_calls": chunk_calls + query_calls,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "error": None,
            }
        )
    key = 5 if 5 in k_values else max(k_values)

    def order(row: dict) -> tuple:
        """정렬 키.

        Args:
            row: 후보 행.

        Returns:
            tuple: hit@key 내림차순 → MRR 내림차순 → 이름. None(오류 행)은 가장 낮게 본다.
        """
        hit = row["hit_at_k"][key] if row["hit_at_k"] else None
        return (
            -(hit if hit is not None else -1.0),
            -(row["mrr"] if row["mrr"] is not None else -1.0),
            row["name"],
        )

    return {
        "ranking_key": f"hit_at_{key}",
        "candidates": rows,
        "ranking": [row["name"] for row in sorted(rows, key=order)],
    }


def fts_baseline(
    ranked_fts: Mapping[str, list[str]],
    gold: Mapping[str, frozenset[str]],
    k_values: Sequence[int] = (1, 3, 5, 10),
    *,
    input_mode: InputMode = "REVIEWED",
) -> dict:
    """호출자가 FTS로 만든 순위를 같은 채점으로 한 행 만든다(FTS는 DB라 여기서 돌리지 않는다).

    Args:
        ranked_fts: query_id별 FTS 순위 chunk_id.
        gold: query_id별 정답 근거 chunk_id.
        k_values: 1 이상의 k 목록.
        input_mode: summarize_retrieval에 넘기는 실험 입력 구분.

    Returns:
        dict: name='FTS'·model=None과 recall_at_k·hit_at_k·mrr. 임베딩 후보 행과 나란히 비교한다.
            잘못된 입력은 summarize_retrieval과 같은 EVAL_INVALID_INPUT ValueError다.
    """
    summary = summarize_retrieval(ranked_fts, gold, k_values=k_values, input_mode=input_mode)
    return {"name": "FTS", "model": None, "query_count": summary["queries"]} | _metrics(summary)
