"""C/S 처리 그래프 로컬 실행(KAN-194 2차). 기본은 fake 공급자로 네트워크를 쓰지 않는다.

시연은 두 번 부른다: 먼저 실행해 사람 확인 직전에 멈추고, 같은 --thread-id로 --resume을 붙여 끝낸다. 별도
프로세스로 재개하려면 체크포인트가 DB에 있어야 하므로 --resume은 DATABASE_URL(cs_app, checkpoint_cs 적용 DB)이
필요하다. --execute는 실제 Gemini 공급자를 쓰며(승인 뒤에만), 키는 환경변수 GEMINI_API_KEY에서만 읽는다. 검색기는
기본이 메모리 fake이고, --fts-db면 DATABASE_URL(cs_app)의 cs.document_chunks를 FTS로 찾는다(KAN-194 3차).
같은 --thread-id로 다시 실행하면 CS_GRAPH_THREAD_EXISTS, 멈춘 곳이 없는 thread를 재개하면
CS_GRAPH_NOTHING_TO_RESUME으로 종료 1이다(중복 유료 호출·오타 재개 방지).

--ticket-file은 {ticket_id: {record, sensitive_values, product_code, customer_tier, received_at(ISO, 시간대 포함),
required_info_codes, already_asked_codes}} JSON이다. 원문이 들어 있으므로 저장소에 두지 않는다. --out에는 상태
요약(단계·다음 노드·오류 코드·시도 ID·시도 요약·재호출 수 retried·회복 수 recovered)만 쓴다.
--invalid-output-retries(0|1, 기본 1)는 분류·초안 형식 위반(CS_INVALID_OUTPUT) 시도 2 재호출이다(KAN-194 4차).
--evidence-limit(기본 cs_runner.DRAFT_DEFAULTS 5)은 초안에 넘길 근거 수이고, --draft-max-output-tokens(기본 6000)는
--execute의 초안 출력 상한이다(KAN-194 7차, 초안 실측 PROPOSED 설정).

--batch(KAN-194 8차): ticket-file의 모든 문의(또는 --ticket-ids)를 순서대로 thread_id=<--thread-id>-<ticket_id>로
실행해 사람 확인 직전에 멈춘다. 문의마다 요약 줄(단계·오류 코드·시도 outcome/error_code/finish_reasons·근거 수·
소요 ms·사용량·비용, 원문·초안 본문 없음)을 <--out>.jsonl(0600)에 덧붙이고 줄마다 flush·fsync해 중단돼도 남긴다.
끝나면 그 jsonl 전체(문의별 마지막 줄)를 집계해 --out에 쓴다. 사람 확인 직전에 멈췄거나 끝난 thread는 건너뛰고
skipped로 센다(실행 중 끊긴 thread는 incomplete로 남겨 단건 --resume으로 잇는다). 이 재개 효과는 체크포인트가
DB(DATABASE_URL)에 있을 때만 프로세스를 넘어 생긴다(메모리 체크포인터는 매번 새로 시작). 비용은 시도 기록(record_attempt)의 사용량을 smoke_worker.attempt_cost 규칙으로 합산한다. --batch
--dry-run은 그래프를 돌리지 않고 건수·예상 호출 수와 비용 상한만 낸다. 분류 입력은 실제 프롬프트, 초안 입력은
근거 --evidence-limit개×800자를 넣은 상한 프롬프트로 잰다.
"""

import argparse
import json
import math
import os
import statistics
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend.agent.cs_runner import (
    DRAFT_DEFAULTS,
    InMemoryTicketSource,
    build_cs_runner,
    make_checkpointer,
    resume,
    run_ticket,
)
from backend.agent.cs_search import fts_searcher_from_dsn
from backend.agent.ticket_source_registry import policy_documents_from_manifest
from backend.engine.cs.classification import ClassificationOutput
from backend.engine.cs.classification_call import ClassificationAttempt
from backend.engine.cs.drafting import EvidenceSnippet, build_draft_input, render_prompt
from backend.engine.cs.drafting_call import DraftAttempt
from backend.engine.cs.graph import POLICY_SLOT_DOCUMENTS, TicketInputs, build_graph, thread_config
from backend.engine.cs.prompt_registry import DEFAULT_VERSIONS
from backend.engine.cs.providers import GenerationKnobs
from backend.engine.cs.verification_call import VerificationAttempt
from backend.engine.search.hybrid import EXCERPT_CHARS
from backend.shared.ai import AISettings, create_client, load_price_table
from backend.shared.settings import Settings
from scripts.cs_candidate_run import audit_entry, dry_run, estimate
from tests.cs.graph_fakes import (
    FILTERS,
    classification_provider,
    draft_provider,
    fts_searcher,
    verification_provider,
)

RUNNER_ERRORS = frozenset({"CS_GRAPH_THREAD_EXISTS", "CS_GRAPH_NOTHING_TO_RESUME"})
TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "thought_tokens",
    "cached_input_tokens",
    "total_tokens",
)


def load_tickets(path: Path) -> dict[str, TicketInputs]:
    """사례 입력 JSON을 읽는다.

    Args:
        path: --ticket-file 경로.

    Returns:
        dict[str, TicketInputs]: 문의 ID별 입력. 형식이 틀리면 CS_GRAPH_TICKET_FILE ValueError다.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        tickets = {
            ticket_id: TicketInputs(
                record=item["record"],
                sensitive_values=item.get("sensitive_values", {}),
                product_code=item["product_code"],
                customer_tier=item["customer_tier"],
                received_at=datetime.fromisoformat(item["received_at"]),
                required_info_codes=frozenset(item.get("required_info_codes", [])),
                already_asked_codes=frozenset(item.get("already_asked_codes", [])),
                required_info_by_category=(
                    {k: frozenset(v) for k, v in item["required_info_by_category"].items()}
                    if item.get("required_info_by_category") is not None
                    else None
                ),
            )
            for ticket_id, item in data.items()
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise ValueError("CS_GRAPH_TICKET_FILE") from None
    if not tickets or any(t.received_at.tzinfo is None for t in tickets.values()):
        raise ValueError("CS_GRAPH_TICKET_FILE")
    return tickets


def info_codes(ticket: TicketInputs) -> frozenset[str]:
    """사례 하나가 쓸 수 있는 정보 코드 전체(필요 코드와 유형별 행의 합집합).

    Args:
        ticket: 사례 입력.

    Returns:
        frozenset[str]: 코드 합집합.
    """
    by_category = ticket.required_info_by_category or {}
    return ticket.required_info_codes.union(*by_category.values())


@dataclass
class UsageRecordingSource(InMemoryTicketSource):
    """시도마다 감사 항목(사용량·요청 모델·비용, 원문 없음)을 문의별로 모으는 TicketSource."""

    entries: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def record_attempt(
        self,
        kind: str,
        ticket_id: str,
        attempt: ClassificationAttempt | DraftAttempt | VerificationAttempt,
    ) -> str:
        """감사 항목을 모으고 기본 기록을 한다(raw_response는 버린다).

        Args:
            kind: 시도 종류.
            ticket_id: 문의 ID.
            attempt: 시도 기록.

        Returns:
            str: 시도 ID.
        """
        self.entries.setdefault(ticket_id, []).append(audit_entry(attempt) | {"kind": kind})
        return super().record_attempt(kind, ticket_id, attempt)


def batch_line(
    ticket_id: str,
    thread_id: str,
    outcome: Any,
    evidence: int,
    elapsed_ms: float,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """문의 하나의 요약 줄(원문·초안 본문 없음).

    Args:
        ticket_id: 문의 ID.
        thread_id: thread ID.
        outcome: run_ticket 결과(RunOutcome).
        evidence: 근거 수.
        elapsed_ms: 소요 시간.
        entries: 이 문의 시도의 감사 항목.

    Returns:
        dict: 단계·다음 노드·오류 코드·시도 요약·재호출·회복·근거 수·소요 ms·사용량 합·비용 합(확정분)·비용
            미확정 시도 수.
    """
    known = [Decimal(e["cost_usd"]) for e in entries if e["cost_usd"] is not None]
    return {
        "ticket_id": ticket_id,
        "thread_id": thread_id,
        "status": "done",
        "stage": outcome.stage,
        "next_nodes": list(outcome.next_nodes),
        "errors": [f"{e['node']}:{e['code']}" for e in outcome.errors],
        "attempts": [
            {
                key: a[key]
                for key in ("kind", "attempt_number", "outcome", "error_code", "finish_reasons")
            }
            for a in outcome.attempts
        ],
        "retried": outcome.retried,
        "recovered": outcome.recovered,
        "evidence": evidence,
        "elapsed_ms": round(elapsed_ms, 3),
        "usage": {
            name: sum(v for e in entries if (v := e["usage"][name]) is not None)
            if any(e["usage"][name] is not None for e in entries)
            else None
            for name in TOKEN_FIELDS
        },
        "cost_usd": format(sum(known, Decimal(0)), "f") if known else None,
        "cost_unknown_attempts": len(entries) - len(known),
    }


def append_line(handle: Any, line: Mapping[str, Any]) -> None:
    """jsonl에 한 줄을 쓰고 디스크까지 내린다(중단 시 보존).

    Args:
        handle: 열린 파일.
        line: 줄 내용.
    """
    handle.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def percentile(values: list[float], fraction: float) -> float | None:
    """정렬한 값의 백분위(최근접 순위).

    Args:
        values: 값.
        fraction: 0~1.

    Returns:
        float | None: 값이 없으면 None이다.
    """
    if not values:
        return None
    ordered = sorted(values)
    # 최근접 순위는 ceil(p·n)번째다(round는 .5를 짝수로 보내 한 칸 낮아질 수 있다).
    return ordered[min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))]


def aggregate(path: Path, skipped: int) -> dict[str, Any]:
    """jsonl의 문의별 마지막 실행 줄(done·failed, 없으면 incomplete)로 묶음 집계를 만든다.

    Args:
        path: <out>.jsonl.
        skipped: 이번 실행에서 건너뛴 thread 수.

    Returns:
        dict: 문의 수, 상태(done·failed·incomplete)·단계별 건수, 분류·초안 최종 outcome 집계, 재호출·회복 수, 근거 평균, 소요 ms
            p50·p95, 비용 합(확정분)과 미확정 시도 수, 이번 실행의 skipped.
    """
    latest: dict[str, dict[str, Any]] = {}
    for text in path.read_text(encoding="utf-8").splitlines():
        line = json.loads(text)
        if line["status"] not in ("skipped", "incomplete"):
            latest[line["ticket_id"]] = line
        elif line["ticket_id"] not in latest:
            latest[line["ticket_id"]] = line
    done = [line for line in latest.values() if line["status"] == "done"]

    def finals(kind: str) -> Counter[str]:
        return Counter(
            attempts[-1]["outcome"]
            for line in done
            if (attempts := [a for a in line["attempts"] if a["kind"] == kind])
        )

    elapsed = [line["elapsed_ms"] for line in done]
    known = [Decimal(line["cost_usd"]) for line in done if line["cost_usd"] is not None]
    return {
        "tickets": len(latest),
        "statuses": dict(Counter(line["status"] for line in latest.values())),
        "skipped_this_run": skipped,
        "stages": dict(Counter(line["stage"] for line in done)),
        "classification": dict(finals("classification")),
        "draft": dict(finals("draft")),
        "retried": sum(line["retried"] for line in done),
        "recovered": sum(line["recovered"] for line in done),
        "evidence_mean": statistics.fmean(line["evidence"] for line in done) if done else None,
        "elapsed_ms": {"p50": percentile(elapsed, 0.5), "p95": percentile(elapsed, 0.95)},
        "cost_usd": {
            "known_sum": format(sum(known, Decimal(0)), "f") if known else None,
            "unknown_attempts": sum(line["cost_unknown_attempts"] for line in done),
        },
    }


def run_batch(
    graph: Any, source: UsageRecordingSource, ids: list[str], prefix: str, out: Path
) -> dict[str, Any]:
    """문의를 순서대로 실행해 줄을 덧붙이고 집계를 돌려준다.

    Args:
        graph: 컴파일한 그래프.
        source: 사용량을 모으는 TicketSource.
        ids: 실행할 문의 ID.
        prefix: thread ID 접두어.
        out: --out(집계 JSON). 줄은 <out>.jsonl에 쓴다.

    Returns:
        dict: aggregate 결과. 문의별 오류는 종류와 무관하게 failed 줄(code: 실행기 코드 또는 CS_GRAPH_FAILED,
            원문 없음)로 남기고 계속한다. KeyboardInterrupt는 그대로 올린다(그때까지의 줄은 남는다).
    """
    lines = Path(f"{out}.jsonl")
    fd = os.open(lines, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    skipped = 0
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        for ticket_id in ids:
            thread_id = f"{prefix}-{ticket_id}"
            started = time.perf_counter()
            try:
                outcome = run_ticket(graph, thread_id, ticket_id)
            except Exception as error:
                # 문의 하나의 오류로 묶음 전체를 멈추지 않는다. 알려진 실행기 코드만 그대로 남기고 그 밖은
                # 원문 없이 CS_GRAPH_FAILED다. KeyboardInterrupt는 Exception이 아니라 그대로 올라간다.
                status = "failed"
                code = str(error) if str(error) in RUNNER_ERRORS else "CS_GRAPH_FAILED"
                if code == "CS_GRAPH_THREAD_EXISTS":
                    # 사람 확인 직전에 멈췄거나 끝난 thread만 건너뛴다. 실행 중 끊긴 thread는 incomplete로 남겨
                    # 단건 --resume으로 잇게 한다(건너뛰면 영영 끝나지 않는다).
                    paused = graph.get_state(thread_config(thread_id)).next in (
                        ("human_review",),
                        (),
                    )
                    status = "skipped" if paused else "incomplete"
                skipped += status == "skipped"
                source.entries.pop(ticket_id, None)
                append_line(
                    handle,
                    {"ticket_id": ticket_id, "thread_id": thread_id, "status": status}
                    | ({"code": code} if status == "failed" else {}),
                )
                continue
            values = graph.get_state(thread_config(thread_id)).values
            append_line(
                handle,
                batch_line(
                    ticket_id,
                    thread_id,
                    outcome,
                    len(values.get("evidence") or []),
                    (time.perf_counter() - started) * 1000,
                    source.entries.pop(ticket_id, []),
                ),
            )
    return aggregate(lines, skipped)


def batch_estimate(
    tickets: Mapping[str, TicketInputs],
    allowed: frozenset[str],
    settings: AISettings,
    evidence_limit: int,
    draft_max_output_tokens: int,
    verification_enabled: bool = True,
) -> dict[str, Any]:
    """묶음 실행 전 호출 수와 비용 상한을 잰다(네트워크 없음).

    Args:
        tickets: 실행할 사례.
        allowed: 허용 정보 코드.
        settings: AI 설정(모델·분류 출력 상한).
        evidence_limit: 초안 근거 수.
        draft_max_output_tokens: 초안 출력 상한.

    Returns:
        dict: 건수, 검증 활성 시 최대7회(분류2·최초초안2·재작성1·검증2), 비활성 시 최대4회.
            활성 시 검증·피드백 크기 상한이 미확정이므로 총비용 상한은 null이다. 초안 입력은 근거 evidence_limit개×800자를 넣은 상한 프롬프트다.
    """
    prices = load_price_table()
    records = {ticket_id: ticket.record for ticket_id, ticket in tickets.items()}
    [classification] = dry_run(
        records,
        [
            {
                "name": "classification",
                "model": settings.gemini_model,
                "prompt_version": DEFAULT_VERSIONS["classification"],
            }
        ],
        allowed,
        {},
        settings,
        prices,
    )
    filler = ClassificationOutput(
        category_code="TECH_SPEC",
        secondary_category=None,
        urgency_flag="NONE",
        provided_info=[],
        return_subtype=None,
    )
    evidence = [
        EvidenceSnippet(
            chunk_id=f"chunk-{n}",
            snippet="x" * EXCERPT_CHARS,
            section_path=["x" * 40],
            document_sha256="0" * 64,
        )
        for n in range(evidence_limit)
    ]
    lengths, sizes = [], []
    for ticket in tickets.values():
        try:
            payload = build_draft_input(
                ticket.record,
                classification=filler,
                required_info_codes=info_codes(ticket),
                already_asked_codes=ticket.already_asked_codes,
                evidence=evidence,
                sensitive_values=ticket.sensitive_values,
            ).payload
        except ValueError:
            continue
        prompt = render_prompt(payload)
        lengths.append(len(prompt))
        sizes.append(len(prompt.encode("utf-8")))
    draft = estimate(
        lengths, sizes, settings.gemini_model, settings, prices, draft_max_output_tokens
    )
    totals = [row.get("total_upper") for row in (classification, draft)]
    upper = (
        sum((Decimal(str(value)) for value in totals), Decimal(0))
        if all(value is not None for value in totals)
        else None
    )
    return {
        "mode": "dry-run",
        "tickets": len(tickets),
        "calls": {
            "classification": len(tickets),
            "draft": len(tickets),
            "verification": len(tickets) if verification_enabled else 0,
        },
        "max_rewrite_calls": len(tickets) if verification_enabled else 0,
        "max_verification_calls": 2 * len(tickets) if verification_enabled else 0,
        "verification_enabled": verification_enabled,
        "max_calls_with_retries": (7 if verification_enabled else 4) * len(tickets),
        "classification": classification,
        "draft": draft,
        "total_upper_usd": None if verification_enabled or upper is None else format(upper, "f"),
        "cost_bound_reason": "CS_VERIFICATION_INPUT_BOUND_UNKNOWN"
        if verification_enabled
        else None,
        "total_upper_with_retries_usd": None
        if verification_enabled or upper is None
        else format(2 * upper, "f"),
    }


def fake_classifier(category: str | None) -> Callable[[Any], Any]:
    """fake 분류 공급자. 유형을 고르면 그 유형으로 바꿔 낸다(정책 문서 예약 확인용).

    Args:
        category: 정책 유형(RETURN 등). None이면 fake 기본(WARRANTY) 그대로다.

    Returns:
        Callable: 분류 입력을 받아 Generation을 돌려주는 공급자.
    """
    if category is None:
        return classification_provider

    def provider(payload: Any) -> Any:
        """fake 분류 결과의 유형만 바꾼다.

        Args:
            payload: 정제 분류 입력.

        Returns:
            Generation: 유형을 바꾼 합성 응답.
        """
        generation = classification_provider(payload)
        body = json.loads(generation.text)
        body["category_code"] = category
        return replace(generation, text=json.dumps(body))

    return provider


def main(
    argv: list[str] | None = None, client_factory: Callable[[AISettings], Any] = create_client
) -> int:
    """인자를 읽어 그래프를 실행하거나 재개하고 상태 요약을 쓴다.

    Args:
        argv: 명령행 인자. None이면 sys.argv.
        client_factory: --execute에서 SDK 클라이언트를 만드는 함수(검사에서 바꾼다).

    Returns:
        int: 성공 0, 인자·설정·키·입력 오류 1. 오류 출력에 원문·키 값은 넣지 않는다.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticket-file", type=Path, required=True)
    parser.add_argument("--ticket-id")
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--resume", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fake", action="store_true", help="기본값. fake 공급자, 네트워크 0회")
    mode.add_argument("--execute", action="store_true", help="실제 Gemini 공급자(승인 뒤)")
    parser.add_argument(
        "--allowed-codes", help="쉼표 구분 정보 코드. 없으면 파일의 required 합집합"
    )
    parser.add_argument("--fts-db", action="store_true", help="DATABASE_URL의 cs FTS 검색기")
    parser.add_argument(
        "--invalid-output-retries",
        type=int,
        choices=(0, 1),
        default=1,
        help="형식 위반(CS_INVALID_OUTPUT) 시도 2 재호출 횟수(기본 1)",
    )
    parser.add_argument(
        "--evidence-limit",
        type=int,
        default=DRAFT_DEFAULTS["evidence_limit"],
        help="초안에 넘길 근거 상위 개수(기본 5)",
    )
    parser.add_argument(
        "--draft-max-output-tokens",
        type=int,
        default=DRAFT_DEFAULTS["max_output_tokens"],
        help="--execute 초안 출력 상한(thinking 포함, 기본 6000)",
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--batch", action="store_true", help="파일의 문의를 모두(또는 --ticket-ids) 순서대로 실행"
    )
    parser.add_argument("--ticket-ids", help="--batch에서 쉼표 구분 문의 ID")
    parser.add_argument(
        "--dry-run", action="store_true", help="--batch에서 실행 없이 호출 수·비용 상한만"
    )
    parser.add_argument(
        "--debug", action="store_true", help="알 수 없는 오류를 원문 그대로 전파(로컬 디버깅만)"
    )
    parser.add_argument(
        "--index-manifest",
        type=Path,
        help="색인 manifest(cs_index_docs --manifest-out). POL-* doc_id→sha로 정책 문서 1자리를 예약",
    )
    parser.add_argument(
        "--fake-category",
        choices=sorted(POLICY_SLOT_DOCUMENTS),
        help="fake 분류 유형(기본 WARRANTY). 정책 문서 예약 확인용, --execute와 함께 쓰지 않음",
    )
    args = parser.parse_args(argv)
    if args.fake_category and args.execute:
        print("CS_GRAPH_INVALID_ARGS: --fake-category는 fake 실행에서만 씁니다.", file=sys.stderr)
        return 1
    if args.batch and (args.resume or args.ticket_id or (args.out is None and not args.dry_run)):
        print(
            "CS_GRAPH_INVALID_ARGS: --batch는 --out이 필요하고 --resume·--ticket-id와 함께 쓰지 않습니다.",
            file=sys.stderr,
        )
        return 1
    if (args.dry_run or args.ticket_ids) and not args.batch:
        print(
            "CS_GRAPH_INVALID_ARGS: --dry-run·--ticket-ids는 --batch와 함께 씁니다.",
            file=sys.stderr,
        )
        return 1
    if args.evidence_limit < 1 or not 1 <= args.draft_max_output_tokens <= 65536:
        print(
            "CS_GRAPH_INVALID_ARGS: --evidence-limit은 1 이상, --draft-max-output-tokens는 1~65536입니다.",
            file=sys.stderr,
        )
        return 1
    try:
        settings = Settings()
    except ValueError as error:
        # 추적 차단(KAN-89) 등 설정 거부. pydantic 오류 문자열에는 입력값이 없다.
        print(f"CS_GRAPH_SETTINGS: {error}", file=sys.stderr)
        return 1
    if args.resume and settings.database_url is None:
        print(
            "CS_GRAPH_RESUME_NEEDS_DB: --resume은 DATABASE_URL(checkpoint_cs를 적용한 DB)이 필요합니다.",
            file=sys.stderr,
        )
        return 1
    if args.fts_db and settings.database_url is None:
        print(
            "CS_GRAPH_FTS_NEEDS_DB: --fts-db는 DATABASE_URL(cs_app)이 필요합니다.", file=sys.stderr
        )
        return 1
    try:
        tickets = load_tickets(args.ticket_file)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    batch_ids = (
        [i.strip() for i in args.ticket_ids.split(",") if i.strip()]
        if args.ticket_ids
        else list(tickets)
    )
    if args.batch and (not batch_ids or not set(batch_ids) <= set(tickets)):
        print(
            "CS_GRAPH_TICKET_ID: --ticket-ids가 비었거나 파일에 없는 문의가 있습니다.",
            file=sys.stderr,
        )
        return 1
    ticket_id = args.ticket_id or (next(iter(tickets)) if len(tickets) == 1 else None)
    # 재개는 멈춘 thread만 이어 가므로 사례 선택이 필요 없다.
    if not args.resume and not args.batch and ticket_id not in tickets:
        print("CS_GRAPH_TICKET_ID: 파일에 사례가 여럿이면 --ticket-id를 고른다.", file=sys.stderr)
        return 1
    allowed = (
        frozenset(code.strip() for code in args.allowed_codes.split(",") if code.strip())
        if args.allowed_codes
        else frozenset().union(*(info_codes(t) for t in tickets.values()))
    )
    if args.dry_run:
        report = batch_estimate(
            {i: tickets[i] for i in batch_ids},
            allowed,
            AISettings(),
            args.evidence_limit,
            args.draft_max_output_tokens,
            verification_enabled=settings.cs_draft_verification_enabled,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    try:
        policy_documents = (
            policy_documents_from_manifest(args.index_manifest, FILTERS.index_version)
            if args.index_manifest
            else None
        )
    except (OSError, ValueError) as error:
        code = str(error) if str(error).startswith("CS_POLICY_") else "CS_POLICY_MANIFEST_INVALID"
        print(f"{code}: --index-manifest를 읽지 못했습니다.", file=sys.stderr)
        return 1
    source = UsageRecordingSource(tickets)
    searcher = (
        fts_searcher_from_dsn(settings.database_url.get_secret_value(), FILTERS.index_version)
        if args.fts_db and settings.database_url is not None
        else fts_searcher
    )
    with make_checkpointer(settings) as saver:
        if args.execute:
            ai_settings = AISettings()
            try:
                client = client_factory(ai_settings)
            except ValueError:
                print(
                    "CS_GRAPH_KEY_MISSING: --execute는 GEMINI_API_KEY가 필요합니다.",
                    file=sys.stderr,
                )
                return 1
            graph = build_cs_runner(
                ai_settings,
                client=client,
                ticket_source=source,
                fts_searcher=searcher,
                search_filters=FILTERS,
                checkpointer=saver,
                model=ai_settings.gemini_model,
                allowed_info_codes=allowed,
                allow_tracing=settings.ai_tracing_allowed,
                verification_enabled=settings.cs_draft_verification_enabled,
                invalid_output_retries=args.invalid_output_retries,
                draft_knobs=GenerationKnobs(max_output_tokens=args.draft_max_output_tokens),
                evidence_limit=args.evidence_limit,
                policy_documents=policy_documents,
            )
        else:
            graph = build_graph(
                classification_provider=fake_classifier(args.fake_category),
                draft_provider=draft_provider,
                verification_provider=verification_provider,
                fts_searcher=searcher,
                search_filters=FILTERS,
                allowed_info_codes=allowed,
                load_ticket=source.load_ticket,
                record_attempt=source.record_attempt,
                checkpointer=saver,
                allow_tracing=settings.ai_tracing_allowed,
                verification_enabled=settings.cs_draft_verification_enabled,
                invalid_output_retries=args.invalid_output_retries,
                evidence_limit=args.evidence_limit,
                policy_documents=policy_documents,
            )
        if args.batch:
            try:
                report = run_batch(graph, source, batch_ids, args.thread_id, args.out)
            except KeyboardInterrupt:
                print(
                    f"CS_GRAPH_BATCH_INTERRUPTED: {args.out}.jsonl에 끝난 문의 줄이 남아 있습니다.",
                    file=sys.stderr,
                )
                return 130
            report = {"mode": "execute" if args.execute else "fake", **report}
            args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(report, ensure_ascii=False))
            return 0
        try:
            outcome = (
                resume(graph, args.thread_id)
                if args.resume
                else run_ticket(graph, args.thread_id, ticket_id)
            )
        except ValueError as error:
            # 알려진 실행기 코드만 그대로 출력한다. 그 밖의 ValueError(pydantic 검증 등)는 입력값을 담을 수
            # 있어 메시지를 내지 않고 CS_GRAPH_FAILED 한 줄만 낸다. --debug일 때만 그대로 전파한다.
            if args.debug and str(error) not in RUNNER_ERRORS:
                raise
            print(str(error) if str(error) in RUNNER_ERRORS else "CS_GRAPH_FAILED", file=sys.stderr)
            return 1
    summary = {
        "mode": "execute" if args.execute else "fake",
        "thread_id": args.thread_id,
        "resumed": args.resume,
        **asdict(outcome),
    }
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
