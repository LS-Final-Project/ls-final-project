"""C/S 처리 그래프 묶음 실행용 --ticket-file을 개발 분리본에서 만든다(KAN-194 8차).

출력은 scripts/cs_graph_run.py --ticket-file 형식({ticket_id: {record, sensitive_values, product_code, customer_tier,
received_at, required_info_codes, already_asked_codes, required_info_by_category}})이다. 필요 정보 코드는 정답 유형을
쓰지 않는다. 문의 제품의 계열(rules.product_family)에 해당하는 eval/policy/cs_required_info.csv 행을 유형별로
required_info_by_category에 넣고, 그래프 draft 노드가 분류 결과 category_code로 그 행을 고른다. 정책 밖 제품은 빈
지도다. sensitive_values는 {}(이메일·라벨 붙은 시리얼은 build_input 정규식이 가린다), already_asked_codes는 빈
목록이다. 개발 CSV의 사후 열(info_request_sent 등)은 쓰지 않는다.

원문이 들어가므로 출력은 0600이고 저장소 안이면 git이 무시하는 경로(예 artifacts/)만 받는다. test 분할은 거부한다.
"""

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from backend.engine.cs.rules import RuleInputError, product_family
from scripts.cs_candidate_run import SplitBlocked, load_targets, write_private
from scripts.cs_draft_run import load_required
from scripts.cs_evidence_build import load_ticket_ids
from tests.cs.cs_dev_data import load_dev_tickets

REPO = Path(__file__).resolve().parents[1]


def private_path_allowed(path: Path) -> bool:
    """원문 파일을 써도 되는 경로인지 본다(저장소 밖이거나 git이 무시하는 경로).

    Args:
        path: 출력 경로.

    Returns:
        bool: 저장소 밖이면 True, 저장소 안이면 git check-ignore가 무시한다고 할 때만 True.
    """
    target = path.resolve()
    if not target.is_relative_to(REPO):
        return True
    try:
        return (
            subprocess.run(
                ["git", "-C", str(REPO), "check-ignore", "-q", str(target)], check=False
            ).returncode
            == 0
        )
    except OSError:
        return False


def build_inputs(manifest: Path, ids: list[str] | None, limit: int, seed: int) -> dict:
    """개발 분리본에서 그래프 입력을 만든다.

    Args:
        manifest: 개발 manifest(load_targets가 SHA·분할을 검사하고 test 분할을 거부한다).
        ids: 고를 문의 ID. None이면 limit·seed로 결정적 표본을 고른다.
        limit: 표본 상한.
        seed: 표본 시드.

    Returns:
        dict: --ticket-file 내용. manifest에 없는 ID는 CS_GRAPH_INPUTS_UNKNOWN_TICKET ValueError다.
    """
    records = load_targets(manifest, sys.maxsize if ids else limit, 0 if ids else seed)
    chosen = ids or sorted(records)
    if not set(chosen) <= set(records):
        raise ValueError("CS_GRAPH_INPUTS_UNKNOWN_TICKET")
    rows = {row["ticket_id"]: row for row in load_dev_tickets(manifest)}
    required = load_required()
    tickets = {}
    for ticket_id in chosen:
        row = rows[ticket_id]
        try:
            family = product_family(row["product_code"])
        except RuleInputError:
            family = None
        received = datetime.fromisoformat(row["received_at_utc"])
        tickets[ticket_id] = {
            "record": records[ticket_id],
            "sensitive_values": {},
            "product_code": row["product_code"],
            "customer_tier": row["customer_tier"],
            "received_at": (
                received if received.tzinfo else received.replace(tzinfo=UTC)
            ).isoformat(),
            "required_info_codes": [],
            "already_asked_codes": [],
            "required_info_by_category": {
                category: sorted(codes)
                for (row_family, category), codes in sorted(required.items())
                if row_family == family
            },
        }
    return tickets


def main(argv: list[str] | None = None) -> int:
    """인자를 읽어 --ticket-file을 쓴다.

    Args:
        argv: 명령행 인자(테스트 주입용).

    Returns:
        int: 0 성공, 2 test 분할, 1 입력·출력 오류. 원문은 출력하지 않는다.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--ticket-ids", type=Path, help="줄 단위·JSON 배열·라벨 JSON의 키")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.limit < 1 or not private_path_allowed(args.out):
        print(
            "CS_GRAPH_INPUTS_INVALID_ARGS: --limit은 1 이상, --out은 저장소 밖이나 git이 무시하는 경로여야 "
            "합니다(원문 포함).",
            file=sys.stderr,
        )
        return 1
    try:
        ids = load_ticket_ids(args.ticket_ids) if args.ticket_ids else None
        tickets = build_inputs(args.manifest, ids, args.limit, args.seed)
        write_private(args.out, json.dumps(tickets, ensure_ascii=False, indent=2).encode())
    except SplitBlocked:
        print(
            "CS_RUN_TEST_SPLIT: test 분할은 최종 1회 사용 원칙이라 쓰지 않습니다.", file=sys.stderr
        )
        return 2
    except (ValueError, OSError, KeyError, TypeError):
        print("CS_GRAPH_INPUTS_FAILED: manifest·대상 ID·출력 경로를 확인하세요.", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "tickets": len(tickets),
                "unknown_family": sum(not t["required_info_by_category"] for t in tickets.values()),
                "out": str(args.out),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
