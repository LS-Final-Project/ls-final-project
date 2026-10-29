"""C/S 처리 그래프 로컬 실행(KAN-194 2차). 기본은 fake 공급자로 네트워크를 쓰지 않는다.

시연은 두 번 부른다: 먼저 실행해 사람 확인 직전에 멈추고, 같은 --thread-id로 --resume을 붙여 끝낸다. 별도
프로세스로 재개하려면 체크포인트가 DB에 있어야 하므로 --resume은 DATABASE_URL(cs_app, checkpoint_cs 적용 DB)이
필요하다. --execute는 실제 Gemini 공급자를 쓰며(승인 뒤에만), 키는 환경변수 GEMINI_API_KEY에서만 읽는다. 이
단위의 검색기는 두 모드 모두 메모리 fake다.

--ticket-file은 {ticket_id: {record, sensitive_values, product_code, customer_tier, received_at(ISO, 시간대 포함),
required_info_codes, already_asked_codes}} JSON이다. 원문이 들어 있으므로 저장소에 두지 않는다. --out에는 상태
요약(단계·다음 노드·오류 코드·시도 ID)만 쓴다.
"""

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.agent.cs_runner import (
    InMemoryTicketSource,
    build_cs_runner,
    make_checkpointer,
    resume,
    run_ticket,
)
from backend.engine.cs.graph import TicketInputs, build_graph
from backend.shared.ai import AISettings, create_client
from backend.shared.settings import Settings
from tests.cs.graph_fakes import (
    FILTERS,
    classification_provider,
    draft_provider,
    fts_searcher,
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
            )
            for ticket_id, item in data.items()
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise ValueError("CS_GRAPH_TICKET_FILE") from None
    if not tickets or any(t.received_at.tzinfo is None for t in tickets.values()):
        raise ValueError("CS_GRAPH_TICKET_FILE")
    return tickets


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
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
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
    try:
        tickets = load_tickets(args.ticket_file)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    ticket_id = args.ticket_id or (next(iter(tickets)) if len(tickets) == 1 else None)
    # 재개는 멈춘 thread만 이어 가므로 사례 선택이 필요 없다.
    if not args.resume and ticket_id not in tickets:
        print("CS_GRAPH_TICKET_ID: 파일에 사례가 여럿이면 --ticket-id를 고른다.", file=sys.stderr)
        return 1
    allowed = (
        frozenset(code.strip() for code in args.allowed_codes.split(",") if code.strip())
        if args.allowed_codes
        else frozenset().union(*(t.required_info_codes for t in tickets.values()))
    )
    source = InMemoryTicketSource(tickets)
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
                fts_searcher=fts_searcher,
                search_filters=FILTERS,
                checkpointer=saver,
                model=ai_settings.gemini_model,
                allowed_info_codes=allowed,
                allow_tracing=settings.ai_tracing_allowed,
            )
        else:
            graph = build_graph(
                classification_provider=classification_provider,
                draft_provider=draft_provider,
                fts_searcher=fts_searcher,
                search_filters=FILTERS,
                allowed_info_codes=allowed,
                load_ticket=source.load_ticket,
                record_attempt=source.record_attempt,
                checkpointer=saver,
                allow_tracing=settings.ai_tracing_allowed,
            )
        outcome = (
            resume(graph, args.thread_id)
            if args.resume
            else run_ticket(graph, args.thread_id, ticket_id)
        )
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
