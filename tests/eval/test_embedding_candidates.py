"""KAN-144 3차 임베딩 후보 비교. 결정적 fake 임베딩만 쓰고 모델·DB·정답 파일에 접근하지 않는다."""

import json
import random

import pytest

from eval.embedding_candidates import EmbeddingCandidate, fts_baseline, run_embedding_candidates

# 손계산용 3차원 벡터. 본문 문자열로 찾는다.
VECTORS = {
    "chunk text c1": [1.0, 0.0, 0.0],
    "chunk text c2": [0.0, 1.0, 0.0],
    "chunk text c3": [0.0, 0.9, 0.1],
    "chunk text c4": [0.1, 0.0, 1.0],
    "chunk text c5": [0.5, 0.5, 0.7],
    "chunk text c6": [0.0, 0.0, 1.0],
    "chunk text c7": [0.3, 0.3, 0.3],
    "chunk text c8": [-1.0, -1.0, 0.0],
    "query text q1": [1.0, 0.0, 0.0],
    "query text q2": [0.0, 1.0, 0.0],
    "query text q3": [0.0, 0.0, 1.0],
    "query text q4": [1.0, 1.0, 0.0],
}
CHUNKS = {f"c{n}": f"chunk text c{n}" for n in range(1, 9)}
QUERIES = {f"q{n}": f"query text q{n}" for n in range(1, 5)}
GOLD = {
    "q1": frozenset({"c1"}),
    "q2": frozenset({"c2", "c3"}),
    "q3": frozenset({"c4"}),
    "q4": frozenset({"c8"}),
}


def table_embed(texts):
    """손계산 벡터를 돌려준다.

    Args:
        texts: 본문 배치.

    Returns:
        list[list[float]]: 벡터 배치.
    """
    return [list(VECTORS[text]) for text in texts]


def seeded_embed(texts):
    """본문마다 고정 시드의 무작위 벡터를 돌려준다.

    Args:
        texts: 본문 배치.

    Returns:
        list[list[float]]: 벡터 배치.
    """
    return [[random.Random(text).uniform(-1, 1) for _ in range(3)] for text in texts]


def run(candidates, **kwargs):
    """기본 설정으로 하네스를 돌린다.

    Args:
        candidates: 후보.
        kwargs: 바꿀 인자.

    Returns:
        dict: 비교표.
    """
    return run_embedding_candidates(
        CHUNKS, QUERIES, gold=GOLD, candidates=candidates, **({"k_values": (1, 3, 5)} | kwargs)
    )


def test_cosine_ranking_matches_hand_calculation():
    """코사인 순위의 hit·recall·MRR이 손계산과 같고 고정 시드 후보와 순위를 비교한다.

    Args:
        없음.

    Returns:
        None: q1·q2는 1위, q3은 2위, q4 정답은 8위(상위 5 밖)다.
    """
    result = run(
        [
            EmbeddingCandidate("seeded", seeded_embed, "fake-b", 3),
            EmbeddingCandidate("table", table_embed, "fake-a", 3),
        ]
    )
    rows = {row["name"]: row for row in result["candidates"]}
    table = rows["table"]
    assert table["hit_at_k"] == pytest.approx({1: 0.5, 3: 0.75, 5: 0.75})
    assert table["recall_at_k"] == pytest.approx({1: 0.375, 3: 0.75, 5: 0.75})
    assert table["mrr"] == pytest.approx((1 + 1 + 0.5 + 0) / 4)
    assert table["dimension"] == 3 and table["error"] is None
    assert (table["chunk_count"], table["query_count"]) == (8, 4)
    assert result["ranking_key"] == "hit_at_5"
    assert result["ranking"][0] == "table"


def test_batches_are_counted():
    """청크 8·질의 4를 3개씩 나누면 3+2회 호출한다.

    Args:
        없음.

    Returns:
        None: 호출 수와 배치 크기를 확인한다.
    """
    sizes = []

    def counting(texts):
        """배치 크기를 기록한다.

        Args:
            texts: 본문 배치.

        Returns:
            list[list[float]]: 벡터 배치.
        """
        sizes.append(len(texts))
        return table_embed(texts)

    [row] = run([EmbeddingCandidate("table", counting, "fake-a", None)], batch_size=3)["candidates"]
    assert row["embed_calls"] == 5 and sizes == [3, 3, 2, 3, 1]
    assert row["dimension"] == 3


@pytest.mark.parametrize(
    "embed,dimension",
    [
        (table_embed, 4),
        (lambda texts: [[0.0, 0.0, 0.0] if "c5" in t else VECTORS[t] for t in texts], 3),
        (lambda texts: table_embed(texts)[:-1], 3),
        (lambda texts: [[float("nan"), 0.0, 1.0] for _ in texts], 3),
    ],
    ids=["dimension-mismatch", "zero-vector", "short-batch", "nan"],
)
def test_invalid_vectors_are_rejected(embed, dimension):
    """차원 불일치·0 벡터·개수 불일치·비유한 값은 공급자 오류가 아니라 입력 오류다.

    Args:
        embed: 잘못된 벡터를 내는 공급자.
        dimension: 선언 차원.

    Returns:
        None: EVAL_INVALID_INPUT을 확인한다.
    """
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        run([EmbeddingCandidate("bad", embed, "fake", dimension)])


def test_provider_error_row_and_fts_baseline_side_by_side():
    """공급자 예외는 원문 없이 오류 행으로 두고 뒤로 보내며, FTS 순위는 같은 채점으로 한 행 만든다.

    Args:
        없음.

    Returns:
        None: 오류 행·순위·FTS 행·원문 비포함을 확인한다.
    """

    def failing(texts):
        """항상 실패하는 공급자.

        Args:
            texts: 본문 배치.

        Returns:
            list[list[float]]: 반환하지 않는다.
        """
        raise RuntimeError("PRIVATE_PROVIDER_MESSAGE")

    result = run(
        [
            EmbeddingCandidate("broken", failing, "fake-x", 3, usage=lambda: {"calls": 0}),
            EmbeddingCandidate("table", table_embed, "fake-a", 3),
        ]
    )
    rows = {row["name"]: row for row in result["candidates"]}
    assert rows["broken"]["error"] == "PROVIDER_ERROR"
    assert rows["broken"]["hit_at_k"] is None and rows["broken"]["mrr"] is None
    assert rows["broken"]["usage"] == {"calls": 0, "partial": True}
    assert result["ranking"] == ["table", "broken"]
    fts = fts_baseline({"q1": ["c1"], "q2": ["c3", "c9"], "q3": [], "q4": ["c8"]}, GOLD, (1, 3, 5))
    assert fts["name"] == "FTS" and fts["hit_at_k"] == pytest.approx({1: 0.75, 3: 0.75, 5: 0.75})
    dumped = json.dumps(result) + json.dumps(fts)
    for secret in ("PRIVATE_PROVIDER_MESSAGE", "chunk text", "query text"):
        assert secret not in dumped
    # 벡터는 행에 넣지 않는다.
    assert set(rows["table"]) == {
        "name",
        "model",
        "dimension",
        "recall_at_k",
        "hit_at_k",
        "mrr",
        "embed_calls",
        "elapsed_ms",
        "chunk_count",
        "query_count",
        "error",
        "usage",
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"candidates": []},
        {"candidates": [EmbeddingCandidate("a", table_embed, "m", 3)] * 2},
        {"gold": GOLD | {"q1": frozenset({"c99"})}},
        {"batch_size": 0},
        {"k_values": (0,)},
    ],
    ids=["no-candidates", "duplicate-name", "gold-outside-chunks", "zero-batch", "bad-k"],
)
def test_invalid_setup_is_rejected(kwargs):
    """빈 후보·이름 중복·청크 밖 정답·잘못된 배치·k를 거부한다.

    Args:
        kwargs: 잘못된 설정.

    Returns:
        None: EVAL_INVALID_INPUT을 확인한다.
    """
    args = {"gold": GOLD, "candidates": [EmbeddingCandidate("a", table_embed, "m", 3)]} | kwargs
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        run_embedding_candidates(CHUNKS, QUERIES, **({"k_values": (1, 3, 5)} | args))


def test_per_query_ranks_and_usage_hook_are_recorded():
    """per_query는 성공한 후보마다 질의별 첫 적중 순위·상위 5 chunk_id를 남기고, usage 콜러블 결과를 후보 행에 둔다(KAN-144)."""
    stats = {"calls": 0}

    def counted(texts):
        stats["calls"] += len(texts)
        return table_embed(texts)

    result = run_embedding_candidates(
        CHUNKS,
        QUERIES,
        gold=GOLD,
        candidates=[EmbeddingCandidate("table", counted, "m", None, usage=lambda: dict(stats))],
    )
    row = result["candidates"][0]
    assert row["usage"] == {"calls": len(CHUNKS) + len(QUERIES)}
    entries = {item["query_id"]: item for item in result["per_query"]}
    assert set(entries) == set(QUERIES)
    assert all(
        item["candidate"] == "table" and len(item["top_ids"]) == 5 for item in entries.values()
    )
    for query_id, item in entries.items():
        rank = item["first_hit_rank"]
        assert rank is None or rank > 5 or item["top_ids"][rank - 1] in GOLD[query_id]
