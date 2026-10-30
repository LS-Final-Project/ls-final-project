"""RFP 쓰기 요청의 멱등 처리(tft.rfp_commands, KAN-189 PR-A).

같은 사용자·연산·Idempotency-Key로 같은 요청을 다시 보내면 저장한 결과를 그대로 돌려준다. 같은 키로 다른 요청을
보내면 409 IDEMPOTENCY_CONFLICT다. 결과 기록은 쓰기와 같은 트랜잭션에서 한다(쓰기만 남고 기록이 없는 일이 없게).
"""

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from backend.shared.errors import ApplicationError

Operation = Literal["supplement.select", "question.create", "question.reply"]
OPERATIONS: frozenset[str] = frozenset(("supplement.select", "question.create", "question.reply"))


def request_hash(path_ids: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    """경로 ID와 본문을 정렬 JSON으로 묶어 해시한다.

    Args:
        path_ids: 경로 변수(project_id·requirement_id·question_id 등).
        payload: 검증을 마친 본문(model_dump(mode="json")).

    Returns:
        str: SHA-256 16진 문자열.
    """
    body = {"path": {k: str(v) for k, v in path_ids.items()}, "body": payload}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _check(operation: str) -> None:
    if operation not in OPERATIONS:
        raise ValueError("Unknown RFP command operation")


async def replay(
    conn: AsyncConnection[dict[str, Any]],
    *,
    actor: UUID,
    operation: Operation,
    key: str,
    digest: str,
) -> tuple[int, dict[str, Any]] | None:
    """같은 키의 기록이 있으면 저장한 응답을 돌려준다.

    Args:
        conn: 진행 중 트랜잭션.
        actor: 요청자.
        operation: 연산.
        key: Idempotency-Key.
        digest: request_hash 결과.

    Returns:
        tuple | None: (status_code, result). 기록이 없으면 None. 같은 키에 다른 요청이면 409 IDEMPOTENCY_CONFLICT다.
    """
    _check(operation)
    cursor = await conn.execute(
        "SELECT request_hash, status_code, result FROM tft.rfp_commands "
        "WHERE actor_user_id = %s AND operation = %s AND idempotency_key = %s",
        (actor, operation, key),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    if row["request_hash"] != digest:
        raise ApplicationError(409, "IDEMPOTENCY_CONFLICT", "The request key is already used.")
    return row["status_code"], row["result"]


async def remember(
    conn: AsyncConnection[dict[str, Any]],
    *,
    actor: UUID,
    operation: Operation,
    key: str,
    digest: str,
    project_id: UUID | None,
    status_code: int,
    result: Mapping[str, Any],
) -> None:
    """처리 결과를 저장한다. 같은 키가 동시에 들어오면 기본 키 충돌로 한쪽이 실패한다.

    Args:
        conn: 쓰기와 같은 트랜잭션.
        actor: 요청자.
        operation: 연산.
        key: Idempotency-Key.
        digest: request_hash 결과.
        project_id: 대상 프로젝트.
        status_code: 응답 상태(2xx).
        result: 응답 data(JSON 객체).

    Returns:
        None.
    """
    _check(operation)
    await conn.execute(
        "INSERT INTO tft.rfp_commands (actor_user_id, operation, idempotency_key, project_id, "
        "request_hash, status_code, result) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (actor, operation, key, project_id, digest, status_code, Jsonb(dict(result))),
    )


async def run_command(
    conn: AsyncConnection[dict[str, Any]],
    *,
    actor: UUID,
    operation: Operation,
    key: str,
    digest: str,
    project_id: UUID | None,
    status_code: int,
    work: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """기록이 있으면 재생하고, 없으면 work를 실행해 결과를 같은 트랜잭션에 기록한다.

    상태에 따른 검사(판본 409 등)는 work 안에 둔다. 재전송은 상태가 바뀐 뒤에도 첫 결과를 받아야 한다.

    Args:
        conn: 진행 중 트랜잭션(커밋은 호출자의 connection이 한다).
        actor: 요청자.
        operation: 연산.
        key: Idempotency-Key.
        digest: request_hash 결과.
        project_id: 대상 프로젝트.
        status_code: 처음 처리의 응답 상태.
        work: 쓰기를 하고 응답 data(JSON 객체)를 돌려주는 함수.

    Returns:
        dict: 응답 data. 같은 키가 동시에 처리되면 늦은 쪽은 409 IDEMPOTENCY_CONFLICT다(트랜잭션이 중단돼 같은
            트랜잭션에서 다시 읽을 수 없다. 다시 보내면 먼저 끝난 결과를 재생한다).
    """
    stored = await replay(conn, actor=actor, operation=operation, key=key, digest=digest)
    if stored is not None:
        return stored[1]
    result = await work()
    try:
        await remember(
            conn,
            actor=actor,
            operation=operation,
            key=key,
            digest=digest,
            project_id=project_id,
            status_code=status_code,
            result=result,
        )
    except UniqueViolation:
        raise ApplicationError(
            409, "IDEMPOTENCY_CONFLICT", "The same request key is in progress. Retry later."
        ) from None
    return result
