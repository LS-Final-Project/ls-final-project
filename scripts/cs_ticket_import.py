"""합성 C/S 문의 일괄 등록기(KAN-107). 실제 접수 경로(POST /api/v1/cs/tickets)만 부르고 DB에 직접 쓰지 않는다.

기본은 파일 검사만 한다. --execute일 때만 등록한다. 입력은 문의 객체의 JSON 배열 또는 머리글 있는 CSV이고
칸은 TicketCreate와 같다. 모든 행이 검사를 통과해야 한 건이라도 보낸다(중간까지만 들어가는 일을 막는다).
내용이 완전히 같은 행은 거절한다.
data_origin은 synthetic으로 고정해 보내고, 응답이 synthetic이 아니면 멈춘다.

세션은 환경변수 CS_IMPORT_COOKIE(브라우저의 세션 쿠키)에서만 읽고 출력하지 않는다. CSRF 토큰은 /api/v1/platform/me
응답 머리글에서 받는다. 원장(--ledger, 기본 <입력>.ledger.jsonl)에 끝난 행을 적어 다시 돌려도 중복 등록하지 않는다.
ponytail: 접수 경로에 멱등 키가 없어 원장으로 막는다. 응답을 받기 전에 끊긴 행은 원장에 없어 한 번 더 들어갈 수 있다.
"""

import argparse
import csv
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from backend.api.cs.tickets.schemas import TicketCreate

ORIGIN = "synthetic"
Sender = Callable[[str, str, dict[str, str], bytes | None], tuple[int, Mapping[str, str], bytes]]


def read_rows(path: Path) -> list[dict[str, Any]]:
    """입력 파일을 읽는다.

    Args:
        path: .json(객체 배열) 또는 .csv(머리글 있음).

    Returns:
        list[dict]: 행. CSV의 빈 칸은 없는 값으로 본다.
    """
    if path.suffix.lower() == ".csv":
        # 본문은 등록 뒤 못 고친다. 칸 안의 줄바꿈이 남도록 csv 모듈이 줄을 나누게 둔다.
        with path.open(encoding="utf-8-sig", newline="") as source:
            return [
                {key: value for key, value in row.items() if value not in ("", None)}
                for row in csv.DictReader(source)
            ]
    rows = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(rows, list):
        raise ValueError("JSON 입력은 문의 객체의 배열이어야 합니다.")
    return rows


def check_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """모든 행을 접수 스키마로 검사하고 출처를 synthetic으로 고정한다.

    Args:
        rows: 입력 행.

    Returns:
        tuple: (보낼 본문, 문제 설명). 문제가 하나라도 있으면 보내지 않는다.
    """
    bodies: list[dict[str, Any]] = []
    problems: list[str] = []
    seen: dict[str, int] = {}
    for number, row in enumerate(rows, 1):
        if row.get("data_origin", ORIGIN) != ORIGIN:
            problems.append(f"{number}행: data_origin은 {ORIGIN}만 쓸 수 있습니다.")
            continue
        try:
            ticket = TicketCreate.model_validate({**row, "data_origin": ORIGIN})
        except ValidationError as error:
            fields = sorted({".".join(map(str, e["loc"])) for e in error.errors()})
            problems.append(f"{number}행: {', '.join(fields)} 칸이 맞지 않습니다.")
            continue
        body = ticket.model_dump(exclude_none=True)
        # 원장 키가 같으면 한 건만 들어가야 할지 두 건인지 알 수 없다. 사람이 입력을 고치게 한다.
        if row_key(body) in seen:
            problems.append(f"{number}행: {seen[row_key(body)]}행과 내용이 같습니다.")
            continue
        seen[row_key(body)] = number
        bodies.append(body)
    return bodies, problems


def row_key(body: Mapping[str, Any]) -> str:
    """원장 키. 같은 내용의 행은 같은 키다."""
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def http_send(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, Mapping[str, str], bytes]:
    """HTTP 요청 하나를 보낸다. 오류 상태도 응답으로 돌려준다."""
    request = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request) as response:  # noqa: S310
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


def execute(
    bodies: list[dict[str, Any]], base_url: str, cookie: str, ledger: Path, send: Sender
) -> int:
    """검사를 통과한 행을 등록한다.

    Args:
        bodies: 보낼 본문.
        base_url: 웹 주소.
        cookie: 세션 쿠키.
        ledger: 원장 경로.
        send: HTTP 전송 함수.

    Returns:
        int: 0 전부 끝, 1 도중에 멈춤.
    """
    root = base_url.rstrip("/")
    done = set()
    if ledger.exists():
        done = {json.loads(line)["key"] for line in ledger.read_text(encoding="utf-8").splitlines()}
    status, headers, _ = send("GET", f"{root}/api/v1/platform/me", {"Cookie": cookie}, None)
    token = headers.get("X-CSRF-Token")
    if status != 200 or not token:
        print(f"세션을 확인하지 못했습니다(HTTP {status}). 등록하지 않았습니다.")
        return 1
    created = 0
    for number, body in enumerate(bodies, 1):
        key = row_key(body)
        if key in done:
            continue
        status, _, raw = send(
            "POST",
            f"{root}/api/v1/cs/tickets",
            {"Cookie": cookie, "X-CSRF-Token": token, "Content-Type": "application/json"},
            json.dumps(body, ensure_ascii=False).encode(),
        )
        if status != 201:
            print(f"{number}행에서 멈춤(HTTP {status}). 앞선 {created}건은 등록됐습니다.")
            return 1
        data = json.loads(raw)["data"]
        with ledger.open("a", encoding="utf-8") as file:
            file.write(json.dumps({"key": key, "ticket_id": data["id"]}) + "\n")
        created += 1
        if data.get("data_origin") != ORIGIN:
            print(f"{number}행 응답의 출처가 {ORIGIN}이 아니라 멈춥니다: {data.get('data_origin')}")
            return 1
    print(f"등록 {created}건, 건너뜀 {len(bodies) - created}건")
    return 0


def main(argv: list[str] | None = None, send: Sender = http_send) -> int:
    """인자를 읽어 검사하거나 등록한다.

    Args:
        argv: 명령행 인자(테스트 주입용).
        send: HTTP 전송 함수(테스트 주입용).

    Returns:
        int: 0 성공, 1 실패.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--base-url", default="https://localhost:18443")
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        rows = read_rows(args.file)
    except (OSError, ValueError) as error:
        print(f"입력을 읽지 못했습니다: {error}")
        return 1
    bodies, problems = check_rows(rows)
    if problems:
        print("\n".join(problems))
        print(f"문제 {len(problems)}건, 아무것도 등록하지 않았습니다.")
        return 1
    if not args.execute:
        print(f"검사 통과 {len(bodies)}건. 등록하려면 --execute를 붙이세요.")
        return 0
    cookie = os.environ.get("CS_IMPORT_COOKIE")
    if not cookie:
        print("환경변수 CS_IMPORT_COOKIE가 없습니다.")
        return 1
    ledger = args.ledger or args.file.with_name(f"{args.file.name}.ledger.jsonl")
    return execute(bodies, args.base_url, cookie, ledger, send)


if __name__ == "__main__":
    sys.exit(main())
