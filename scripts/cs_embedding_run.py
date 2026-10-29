"""임베딩 후보 비교 실행기(KAN-144). 기본은 dry-run이며 네트워크를 쓰지 않는다.

분류 실행기(scripts/cs_candidate_run.py)와 같은 모양이다. 비용 추정·출력 경로 검사·후보 이름·git sha는 그
파일의 함수를 쓴다. 하네스는 eval/embedding_candidates(run_embedding_candidates·fts_baseline)다.

backend.shared.ai.embed는 문자열 하나당 API 호출 한 번이다. 그래서 하네스 배치(--batch-size)와 실제 API 호출
수(청크 수 + 질의 수)가 다르며 dry-run은 둘을 따로 보여준다. 하네스는 청크와 질의에 같은 임베딩 함수를 쓰므로
task_type은 후보마다 하나(문서·질의 같은 값)만 줄 수 있다. RETRIEVAL_DOCUMENT/QUERY처럼 비대칭으로 주려면
하네스 변경이 필요하다. 임베딩은 입력만 과금하므로 비용은 입력 근사(문자/4)와 상한(UTF-8 바이트)만 내며 실제
과금은 실행 후 usage로 확인한다. 키는 환경변수(GEMINI_API_KEY)에서만 읽는다(셸에서 set -a; . ./.env; set +a).
결과에는 벡터·본문·키를 넣지 않는다.
"""

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args

from backend.shared.ai import AISettings, create_client, embed, load_price_table
from eval.embedding_candidates import (
    EmbeddingCandidate,
    fts_baseline,
    run_embedding_candidates,
)
from eval.search_metrics import InputMode
from scripts.cs_candidate_run import estimate, git_sha, label_path, prepare_output

Embed = Callable[[list[str]], list[list[float]]]
EmbedFactory = Callable[[dict[str, Any], AISettings], Embed]
OUTPUT_KEYS = ("calls", "max_output_tokens_per_call", "output_cost_upper", "total_cost_upper")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """JSON Lines 파일을 읽는다(빈 줄은 건너뛴다).

    Args:
        path: .jsonl 경로.

    Returns:
        list[dict]: 줄마다 객체 하나.
    """
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("CS_EMBED_JSONL")
    return rows


def load_texts(path: Path, key: str) -> dict[str, str]:
    """ID별 본문을 읽는다.

    Args:
        path: {key, text} jsonl.
        key: ID 키(chunk_id 또는 query_id).

    Returns:
        dict: ID → 본문. ID 중복·빈 본문은 ValueError다.
    """
    rows = load_jsonl(path)
    texts = {row[key]: row["text"] for row in rows}
    if (
        not texts
        or len(texts) != len(rows)
        or not all(isinstance(i, str) and i for i in texts)
        or not all(isinstance(t, str) and t.strip() for t in texts.values())
    ):
        raise ValueError("CS_EMBED_TEXTS")
    return texts


def load_gold(path: Path, chunks: Mapping[str, str], queries: Mapping[str, str]) -> dict:
    """질의별 정답 근거 chunk_id를 읽는다.

    Args:
        path: {query_id, chunk_ids} jsonl.
        chunks: 청크 본문(정답 ID가 여기에 있어야 한다).
        queries: 질의 본문(질의 ID 집합이 같아야 한다).

    Returns:
        dict: query_id → frozenset(chunk_id). 없는 청크·질의 불일치는 ValueError다.
    """
    gold = {row["query_id"]: frozenset(row["chunk_ids"]) for row in load_jsonl(path)}
    if set(gold) != set(queries) or any(not ids <= set(chunks) for ids in gold.values()):
        raise ValueError("CS_EMBED_GOLD")
    return gold


def load_specs(path: Path) -> list[dict[str, Any]]:
    """임베딩 후보 목록([{name, model, dimension?, task_type?}])을 읽는다.

    Args:
        path: 후보 JSON.

    Returns:
        list[dict]: 이름이 고유하고 파일 이름으로도 안전한 후보 명세(dimension·task_type 기본 None).
    """
    specs = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(specs, list) or not specs:
        raise ValueError("CS_CANDIDATES_INVALID")
    checked = []
    for spec in specs:
        if (
            not isinstance(spec, dict)
            or not {"name", "model"} <= set(spec) <= {"name", "model", "dimension", "task_type"}
            or not isinstance(spec["model"], str)
            or not spec["model"]
            or not (spec.get("dimension") is None or type(spec["dimension"]) is int)
            or (spec.get("dimension") is not None and spec["dimension"] < 1)
            or not (spec.get("task_type") is None or isinstance(spec["task_type"], str))
        ):
            raise ValueError("CS_CANDIDATES_INVALID")
        label_path(Path("."), spec["name"])
        checked.append({"dimension": None, "task_type": None, **spec})
    if len({spec["name"] for spec in checked}) != len(checked):
        raise ValueError("CS_CANDIDATES_INVALID")
    return checked


def dry_run(
    chunks: Mapping[str, str],
    queries: Mapping[str, str],
    specs: list[dict[str, Any]],
    batch_size: int,
    settings: AISettings,
    prices: Mapping[str, Any],
) -> list[dict[str, object]]:
    """후보마다 하네스 배치 수·실제 API 호출 수와 입력 토큰·비용(근사·상한)을 계산한다.

    Args:
        chunks: 청크 본문.
        queries: 질의 본문.
        specs: 후보 명세.
        batch_size: 하네스 배치 크기.
        settings: AI 설정.
        prices: 단가표.

    Returns:
        list[dict]: 후보별 harness_batches·api_calls와 estimate()의 입력 항목. 출력 비용은 없고
            total_cost_upper는 입력 비용 상한이다. 네트워크를 쓰지 않는다.
    """
    texts = [*chunks.values(), *queries.values()]
    lengths = [len(t) for t in texts]
    sizes = [len(t.encode("utf-8")) for t in texts]
    batches = math.ceil(len(chunks) / batch_size) + math.ceil(len(queries) / batch_size)
    rows = []
    for spec in specs:
        row = estimate(lengths, sizes, spec["model"], settings, prices)
        rows.append(
            {
                **spec,
                "harness_batches": batches,
                "api_calls": len(texts),
                **{key: value for key, value in row.items() if key not in OUTPUT_KEYS},
                "total_cost_upper": row["input_cost_upper"],
                "note": "근사값이다. 임베딩은 입력만 과금하며 실제 과금은 실행 후 usage로 확인한다.",
            }
        )
    return rows


def default_factory(settings: AISettings) -> EmbedFactory:
    """임베딩용 실제 클라이언트를 한 번 만들고 후보마다 ai.embed를 감싼 배치 함수를 낸다.

    Args:
        settings: 환경변수에서 읽은 AI 설정.

    Returns:
        EmbedFactory: (명세, 설정) → 문자열 배치를 받아 벡터 배치를 내는 함수(문자열마다 API 호출 1회).
    """
    client = create_client(settings, embedding=True)

    def factory(spec: dict[str, Any], config: AISettings) -> Embed:
        def call(batch: list[str]) -> list[list[float]]:
            return [
                embed(
                    client,
                    spec["model"],
                    text,
                    dimensions=spec["dimension"],
                    task_type=spec["task_type"],
                ).values
                for text in batch
            ]

        return call

    return factory


def execute(
    chunks: Mapping[str, str],
    queries: Mapping[str, str],
    gold: Mapping[str, frozenset[str]],
    specs: list[dict[str, Any]],
    k_values: list[int],
    batch_size: int,
    input_mode: InputMode,
    fts: Mapping[str, list[str]] | None,
    settings: AISettings,
    factory: EmbedFactory,
) -> dict[str, object]:
    """하네스로 후보를 비교하고(있으면 FTS 기준선과 함께) 실행 메타를 붙인다.

    Args:
        chunks: 청크 본문.
        queries: 질의 본문.
        gold: 질의별 정답 근거.
        specs: 후보 명세.
        k_values: k 목록.
        batch_size: 하네스 배치 크기.
        input_mode: summarize_retrieval 입력 구분.
        fts: 질의별 FTS 순위(없으면 None).
        settings: AI 설정.
        factory: 후보별 임베딩 함수를 만드는 함수(테스트는 fake로 교체).

    Returns:
        dict: run_embedding_candidates 결과(+ fts 행)와 meta(후보·건수·청크·질의 ID sha256·시각·git sha). 벡터·본문 없음.
    """
    started = datetime.now(UTC)
    candidates = [
        EmbeddingCandidate(
            name=spec["name"],
            embed=factory(spec, settings),
            model=spec["model"],
            dimension=spec["dimension"],
        )
        for spec in specs
    ]
    result = run_embedding_candidates(
        chunks,
        queries,
        gold=gold,
        candidates=candidates,
        k_values=k_values,
        batch_size=batch_size,
        input_mode=input_mode,
    )
    if fts is not None:
        result["fts"] = fts_baseline(fts, gold, k_values, input_mode=input_mode)
    return {
        "meta": {
            "candidates": specs,
            "chunks": len(chunks),
            "queries": len(queries),
            "k_values": k_values,
            "batch_size": batch_size,
            "input_mode": input_mode,
            "chunk_ids_sha256": hashlib.sha256("\n".join(sorted(chunks)).encode()).hexdigest(),
            "query_ids_sha256": hashlib.sha256("\n".join(sorted(queries)).encode()).hexdigest(),
            "started_at": started.isoformat(),
            "finished_at": datetime.now(UTC).isoformat(),
            "git_sha": git_sha(),
        },
        "result": result,
    }


def main(argv: list[str] | None = None, factory: EmbedFactory | None = None) -> int:
    """인자를 읽어 dry-run 표를 출력하거나 --execute로 결과 JSON을 쓴다.

    Args:
        argv: 명령행 인자(테스트 주입용).
        factory: 실제 임베딩 대신 쓸 함수(테스트 주입용).

    Returns:
        int: 0 성공, 1 입력·출력 오류. 오류 원문·키는 출력하지 않는다.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--fts-ranked", type=Path)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--k", default="1,3,5,10")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--input-mode", choices=get_args(InputMode), default="REVIEWED")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    inputs = [args.chunks, args.queries, args.gold, args.fts_ranked]
    if any(path is not None and "test" in str(path).lower() for path in inputs):
        print("경고: 입력 경로에 'test'가 있습니다. 최종 시험 자료라면 1회 사용 원칙을 확인하세요.")
    try:
        k_values = [int(k) for k in args.k.split(",")]
    except ValueError:
        k_values = []
    if (
        args.batch_size < 1
        or not k_values
        or any(k < 1 for k in k_values)
        or (args.execute and args.out is None)
    ):
        print(
            "CS_RUN_INVALID_ARGS: --k는 1 이상 정수 목록, --batch-size는 1 이상, --execute에는 --out."
        )
        return 1
    try:
        chunks = load_texts(args.chunks, "chunk_id")
        queries = load_texts(args.queries, "query_id")
        gold = load_gold(args.gold, chunks, queries)
        fts = json.loads(args.fts_ranked.read_text(encoding="utf-8")) if args.fts_ranked else None
        if fts is not None and not isinstance(fts, dict):
            raise ValueError("CS_EMBED_FTS")
        specs = load_specs(args.candidates)
        settings = AISettings()
        if not args.execute:
            rows = dry_run(chunks, queries, specs, args.batch_size, settings, load_price_table())
            print(
                "dry-run: 네트워크 호출 없음. 토큰은 문자/4 근사와 UTF-8 바이트 상한이며 실제 과금은 "
                "실행 후 usage로 확인한다."
            )
            summary = {"chunks": len(chunks), "queries": len(queries), "candidates": rows}
            print(json.dumps(summary, ensure_ascii=False))
            return 0
        prepare_output(args.out)
        output = execute(
            chunks,
            queries,
            gold,
            specs,
            k_values,
            args.batch_size,
            args.input_mode,
            fts,
            settings,
            factory or default_factory(settings),
        )
    except (ValueError, OSError, KeyError, TypeError):
        print("CS_RUN_FAILED: 청크·질의·정답·FTS·후보 파일과 환경변수를 확인하세요.")
        return 1
    try:
        args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        print(
            "CS_RUN_OUTPUT_FAILED: 실행은 끝났지만 결과 파일을 쓰지 못했습니다. --out 경로를 확인하세요."
        )
        return 1
    print(json.dumps({"chunks": len(chunks), "queries": len(queries), "out": str(args.out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
