"""회신 값 AI 읽기 작업(KAN-224, rfp.reply_read). 모델은 worker에서만 부른다.

모델 키와 비용 원장은 worker에만 있다. 그래서 API는 규칙으로만 읽고, 규칙이 못 읽은 고객 회신이면 요청 행(QUEUED)을
남긴 뒤 다른 모델 작업(처리·맥락·초안 다듬기)과 같은 플랫폼 접수로 worker 작업을 만든다. 접수하지 못하면 UNAVAILABLE을
남기고 화면은 "다시 읽기"를 보인다. worker는 입력 판본(reply_id·request_key의 해시)으로 요청을 찾아 AI로 읽고, 근거 검사
결과를 DONE(또는 모델 실패 FAILED) 행으로 남긴다. 회신 행의 value_candidate(규칙 결과)는 바꾸지 않는다.
"""

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID

from fastapi import Request
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from backend.agent.processor_registry import TftJob
from backend.api.tft.rfp import repository
from backend.engine.rfp.reply_values import ReplyValue, read_reply_value
from backend.shared.access import Principal
from backend.shared.ai import Generation
from backend.shared.database import connection
from backend.shared.platform_jobs import PlatformRejected, PlatformUnknown, PlatformUnreachable

Row = dict[str, Any]
KIND = "rfp.reply_read"
TFT_JOBS = "/internal/v1/platform/jobs/tft"


def input_version(reply_id: UUID, request_key: str) -> str:
    """플랫폼 작업의 동결 입력. worker는 이 값으로 요청 행을 찾는다."""
    encoded = json.dumps(["rfp.reply_read", str(reply_id), request_key], separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


async def reading_inputs(conn: AsyncConnection[Row], question: Mapping[str, Any]) -> Row | None:
    """규칙·AI 읽기에 쓰는 요구 단위·속성·조건 이름. 요구에 이어진 고객 질의가 아니면 None."""
    if question["target"] != "CUSTOMER" or question["requirement_id"] is None:
        return None
    requirement = await repository.latest_requirement_version(
        conn, question["project_id"], question["requirement_id"]
    )
    if requirement is None:
        return None
    current = await repository.judgement_of(conn, requirement)
    return {
        "unit": requirement["unit"],
        "attribute": requirement.get("attribute") or "",
        "question_text": question["question_text"],
        "condition_names": [c["name"] for c in current["conditions"]],
    }


async def _insert(
    conn: AsyncConnection[Row],
    reply_id: UUID,
    request_key: str,
    status: str,
    candidate: ReplyValue | None = None,
) -> None:
    await conn.execute(
        "INSERT INTO tft.reply_value_reads (reply_id, request_key, status, candidate) "
        "VALUES (%s,%s,%s,%s) ON CONFLICT (reply_id, request_key, status) DO NOTHING",
        (
            reply_id,
            request_key,
            status,
            Jsonb(candidate.model_dump(mode="json")) if candidate else None,
        ),
    )


async def enqueue(
    request: Request, principal: Principal, project_id: UUID, reply_id: UUID, request_key: str
) -> str:
    """요청 행을 남기고 worker 작업을 접수한다. 같은 키 재전송은 같은 작업이다.

    Args:
        request: 요청(설정·플랫폼 클라이언트).
        principal: 요청 사용자.
        project_id: 프로젝트 ID(작업 subject).
        reply_id: 회신 ID.
        request_key: 멱등 키(회신 기록 또는 다시 읽기 요청의 Idempotency-Key).

    Returns:
        str: QUEUED(접수함) 또는 UNAVAILABLE(접수 못 함).
    """
    async with connection(request.app.state.settings) as conn:
        # worker가 응답보다 먼저 돌 수 있으므로 접수 전에 남긴다.
        await _insert(conn, reply_id, request_key, "QUEUED")
    client = getattr(request.app.state, "processing_jobs", None)
    status = "UNAVAILABLE"
    if client is not None:
        frozen = input_version(reply_id, request_key)
        try:
            await client.post(
                TFT_JOBS,
                {
                    "actor_id": str(principal.user_id),
                    "kind": KIND,
                    "project_id": str(project_id),
                    "input_version": frozen,
                    "idempotency_key": request_key,
                    "request_hash": frozen,
                },
            )
            status = "QUEUED"
        except (PlatformRejected, PlatformUnreachable, PlatformUnknown):
            # 진행 중인 다른 작업(409)·미연결(503)·응답 모름. 회신 기록은 그대로 두고 다시 읽기로 맡긴다.
            pass
    if status == "UNAVAILABLE":
        async with connection(request.app.state.settings) as conn:
            await _insert(conn, reply_id, request_key, "UNAVAILABLE")
    return status


async def process(
    conn: AsyncConnection[Row], job: TftJob, provider: Callable[[str], Generation]
) -> dict[str, Any]:
    """worker: 요청 행을 찾아 AI로 읽고 결과 행을 남긴다. 이미 결과가 있으면 모델을 부르지 않는다."""
    cursor = await conn.execute(
        "SELECT v.reply_id, v.request_key, r.reply_text, q.* "
        "FROM tft.reply_value_reads v JOIN tft.question_replies r ON r.id = v.reply_id "
        "JOIN tft.customer_questions q ON q.id = r.question_id "
        "WHERE q.project_id = %s AND v.status = 'QUEUED'",
        (job.project_id,),
    )
    # ponytail: 프로젝트의 대기 요청을 다 읽어 해시를 비교한다. 대기 요청이 수천 건이 되면 해시 열을 둔다.
    found = [
        row
        for row in await cursor.fetchall()
        if input_version(row["reply_id"], row["request_key"]) == job.input_version
    ]
    if not found:
        raise ValueError("TFT_REPLY_READ_JOB_MISMATCH")
    row = found[0]
    done = await conn.execute(
        "SELECT 1 FROM tft.reply_value_reads WHERE reply_id = %s AND request_key = %s "
        "AND status IN ('DONE','FAILED')",
        (row["reply_id"], row["request_key"]),
    )
    if await done.fetchone():
        return {}
    inputs = await reading_inputs(conn, row)
    if inputs is None:
        raise ValueError("TFT_REPLY_READ_TARGET_MISSING")
    candidate = await asyncio.to_thread(
        read_reply_value, row["reply_text"], provider=provider, **inputs
    )
    status = "FAILED" if candidate.reason == "AI_FAILED" else "DONE"
    await _insert(conn, row["reply_id"], row["request_key"], status, candidate)
    return {}
