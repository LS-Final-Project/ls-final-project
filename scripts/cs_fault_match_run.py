"""고장 코드 없이 같은 고장 판단 평가 실행기(KAN-204). 기본은 dry-run이며 네트워크를 쓰지 않는다.

문의는 개발 manifest(load_dev_tickets, train·valid만)로 받고, 해결 기록은 읽기 전용 resolution_cases.csv에서
그 문의에 연결된 것만 쓴다. test 분할 문의는 manifest에 없으므로 그 해결 기록도 쓰이지 않는다. 문의 원문은
build_input(분류와 같은 마스킹)을 거쳐 모델에 간다. 고장 ID·해결 기록 문장은 채점에만 쓴다.

dry-run은 호출 수와 근사 비용·상한을 낸다. 실제 호출은 --execute로만 하며 --max-cost-usd보다 비용 상한이 크면
거부한다. 키는 환경변수(GEMINI_API_KEY)에서만 읽는다(셸에서 set -a; . ./.env.integrations; set +a).
요약 결과(--out)에는 원문·키·사례 ID를 넣지 않는다. 사례별 판단은 --labels-out(저장소 밖, 0600)에만 쓴다.
"""

import argparse
import csv
import json
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend.engine.cs.classification import build_input
from backend.engine.cs.fault_matching import (
    MAX_EXAMPLES_PER_GROUP,
    MAX_TEXT_CHARS,
    PROMPT_VERSION,
    MatchOutput,
    build_prompt,
    parse_output,
)
from backend.shared.ai import (
    AISettings,
    create_client,
    embed,
    estimate_cost,
    generate,
    load_price_table,
)
from backend.shared.tracing import TRACING_BLOCKED, tracing_requested
from eval.fault_matching import (
    CaseRow,
    Result,
    Task,
    build_tasks,
    downstream,
    nearest_group,
    result_of,
    summarize,
    summarize_by,
)
from scripts.cs_candidate_run import estimate, git_sha, prepare_output, write_private
from tests.cs.cs_dev_data import load_dev_tickets

EMBEDDING_DIMENSIONS = 768
EMBEDDING_TASK = "RETRIEVAL_DOCUMENT"


def load_cases(manifest: Path, resolution_cases: Path) -> list[CaseRow]:
    """개발 split 문의에 연결된 해결 기록만 모델 입력과 함께 읽는다.

    Args:
        manifest: 개발 manifest(test 분할이 있으면 load_dev_tickets가 거부한다).
        resolution_cases: 제공 resolution_cases.csv(읽기 전용).

    Returns:
        list[CaseRow]: known_at은 접수 시각 + time_to_resolve_hours(기획서의 해결 시각 대리값)다.
    """
    tickets = {row["ticket_id"]: row for row in load_dev_tickets(manifest)}
    out: list[CaseRow] = []
    with resolution_cases.open(encoding="utf-8-sig", newline="") as handle:
        for case in csv.DictReader(handle):
            ticket = tickets.get(case["ticket_id"])
            if ticket is None:
                continue
            prepared = build_input(
                {
                    "channel": ticket["channel"],
                    "body_language": ticket["body_language"],
                    "subject": ticket.get("inquiry_subject") or None,
                    "body": ticket["inquiry_body"],
                },
                sensitive_values={},
            ).payload
            text = "\n".join(part for part in (prepared.subject, prepared.body) if part)
            received = datetime.fromisoformat(ticket["received_at_utc"].replace("Z", "+00:00"))
            out.append(
                CaseRow(
                    case_id=case["case_id"],
                    ticket_id=case["ticket_id"],
                    product_code=case["product_code"],
                    product_name=ticket.get("product_name") or case["product_code"],
                    fault_id=case["fault_id"],
                    customer_id=ticket.get("customer_id") or None,
                    known_at=(
                        received + timedelta(hours=float(case["time_to_resolve_hours"]))
                    ).astimezone(UTC),
                    split=ticket["data_split"],
                    language=ticket.get("language") or ticket["body_language"],
                    text=text,
                    symptom_in_text=case["symptom"].casefold() in ticket["inquiry_body"].casefold(),
                    category=ticket.get("category_code") or "",
                )
            )
    return out


def run_llm(
    tasks: list[Task], settings: AISettings, max_output_tokens: int, retries: int = 1
) -> tuple[list[Result], dict[str, Any]]:
    """작업마다 Gemini를 부른다(묶음이 없으면 부르지 않고 NEW).

    호출 실패나 규칙 밖 응답이면 retries번까지 다시 부른다(분류 실행의 재호출과 같은 취지). 그래도 실패하면 ERROR로
    센다. 재호출 수와 실패 종류를 따로 남긴다.
    """
    client = create_client(settings)
    prices = load_price_table()
    results: list[Result] = []
    cost = Decimal(0)
    calls = retried = call_errors = parse_errors = 0
    elapsed: list[int] = []
    for task in tasks:
        if not task.payload.groups:
            results.append(result_of(task, "NEW", None))
            continue
        out = None
        for attempt in range(retries + 1):
            calls += 1
            retried += attempt > 0
            try:
                generation = generate(
                    client,
                    settings,
                    build_prompt(task.payload),
                    response_schema=MatchOutput,
                    max_output_tokens=max_output_tokens,
                )
            except Exception:  # noqa: BLE001 - 호출 실패는 재호출 뒤 ERROR 한 건으로 센다.
                call_errors += 1
                continue
            cost += estimate_cost(generation.usage, settings.gemini_model, prices).total or 0
            if generation.elapsed_ms is not None:
                elapsed.append(generation.elapsed_ms)
            try:
                out = parse_output(generation.text, set(task.ref_to_fault))
                break
            except ValueError:
                parse_errors += 1
        if out is None:
            results.append(result_of(task, "ERROR", None))
        else:
            results.append(result_of(task, out.decision, out.group_ref))
    usage = {
        "calls": calls,
        "retried_calls": retried,
        "call_errors": call_errors,
        "parse_errors": parse_errors,
        "cost_usd": format(cost, "f"),
        "mean_elapsed_ms": round(sum(elapsed) / len(elapsed)) if elapsed else None,
    }
    return results, usage


def run_embedding(
    tasks: list[Task], settings: AISettings, model: str, new_below: float | None
) -> tuple[list[Result], dict[str, Any]]:
    """임베딩 기준선. 같은 문자열은 한 번만 임베딩한다."""
    client = create_client(settings, embedding=True)
    cache: dict[str, list[float]] = {}
    chars = 0

    def vector(text: str) -> list[float]:
        nonlocal chars
        if text not in cache:
            chars += len(text)
            cache[text] = embed(
                client, model, text, dimensions=EMBEDDING_DIMENSIONS, task_type=EMBEDDING_TASK
            ).values
        return cache[text]

    results = [result_of(task, *nearest_group(task, vector, new_below)) for task in tasks]
    return results, {"calls": len(cache), "input_chars": chars}


def report(
    name: str, tasks: list[Task], cases: list[CaseRow], results: list[Result], usage: Mapping
) -> dict[str, Any]:
    return {
        "name": name,
        "usage": dict(usage),
        "summary": summarize(results),
        "by_group": summarize_by(results),
        "downstream": downstream(cases, tasks, results),
        "downstream_human_resolves_unsure": downstream(
            cases, tasks, results, human_resolves_unsure=True
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--resolution-cases", type=Path, required=True)
    parser.add_argument("--split", choices=("train", "valid"), required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--mode", choices=("llm", "embedding", "both"), default="llm")
    parser.add_argument("--model")
    parser.add_argument("--max-output-tokens", type=int, default=1024)
    parser.add_argument("--embedding-model", default="gemini-embedding-2")
    parser.add_argument("--new-below", type=float)
    parser.add_argument("--max-cost-usd", type=Decimal, default=Decimal("5"))
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--labels-out", type=Path)
    args = parser.parse_args(argv)

    settings = AISettings()
    if args.model:
        settings = settings.model_copy(update={"gemini_model": args.model})
    cases = load_cases(args.manifest, args.resolution_cases)
    tasks = build_tasks(cases, args.split, limit=args.limit)
    prompts = [build_prompt(t.payload) for t in tasks if t.payload.groups]
    plan = {
        "split": args.split,
        "cases_in_dev_splits": len(cases),
        "tasks": len(tasks),
        "tasks_without_groups": sum(not t.payload.groups for t in tasks),
        "prompt_version": PROMPT_VERSION,
        "max_examples_per_group": MAX_EXAMPLES_PER_GROUP,
        "max_text_chars": MAX_TEXT_CHARS,
        "model": settings.gemini_model,
        "estimate": estimate(
            [len(p) for p in prompts],
            [len(p.encode()) for p in prompts],
            settings.gemini_model,
            settings,
            load_price_table(),
            args.max_output_tokens,
        ),
    }
    if not args.execute:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    if tracing_requested(os.environ):
        print(TRACING_BLOCKED, file=sys.stderr)
        return 2
    upper = plan["estimate"]["total_cost_upper"]
    if args.mode != "embedding" and (upper is None or Decimal(upper) > args.max_cost_usd):
        print(f"cost upper {upper} exceeds --max-cost-usd {args.max_cost_usd}", file=sys.stderr)
        return 2
    if args.out:
        prepare_output(args.out)
    runs: list[dict[str, Any]] = []
    labels: dict[str, list[dict[str, Any]]] = {}
    if args.mode in {"llm", "both"}:
        results, usage = run_llm(tasks, settings, args.max_output_tokens)
        runs.append(report(f"llm:{settings.gemini_model}", tasks, cases, results, usage))
        labels["llm"] = [r.__dict__ for r in results]
    if args.mode in {"embedding", "both"}:
        results, usage = run_embedding(tasks, settings, args.embedding_model, args.new_below)
        name = f"embedding:{args.embedding_model}:new_below={args.new_below}"
        runs.append(report(name, tasks, cases, results, usage))
        labels["embedding"] = [r.__dict__ for r in results]
    output = {
        **plan,
        "git_sha": git_sha(),
        "started_note": "사례 ID·원문은 넣지 않는다",
        "runs": runs,
    }
    text = json.dumps(output, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    if args.labels_out:
        write_private(args.labels_out, json.dumps(labels, ensure_ascii=False).encode())
    return 0


if __name__ == "__main__":
    sys.exit(main())
