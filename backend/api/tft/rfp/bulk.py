"""일괄 결정과 질의서 발송 기록(KAN-228).

일괄 결정은 단건 결정(decisions.decide_on)을 같은 트랜잭션에서 요구마다 부른다. 규칙은 단건과 같고, 한 건이라도
실패하면 트랜잭션 전체가 롤백된다. 건별 결정 기록은 같은 묶음 ID(detail.batch_id)를 갖고, 묶음 되돌리기는 그 결정들을
최근 것부터 단건 undo로 되돌린다. 같은 사용자·Idempotency-Key 재전송은 건마다 첫 결과를 재생한다.
"""

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from psycopg import AsyncConnection

from backend.api.tft.rfp import commands
from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.decisions import decide_on
from backend.api.tft.rfp.routes_questions import visible_project
from backend.shared.access import Principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError

Row = dict[str, Any]
BATCH_NAMESPACE = UUID("6f1c0d3e-2a7b-4c55-9f0e-6b0a6d2f8a28")
KST = ZoneInfo("Asia/Seoul")
PROJECT_NOT_FOUND = ("TFT_PROJECT_NOT_FOUND", "The project was not found.")


def batch_of(actor: UUID, key: str) -> UUID:
    """같은 사용자·키면 같은 묶음 ID(재전송도 같은 묶음)."""
    return uuid5(BATCH_NAMESPACE, f"{actor}:{key}")


def item_key(key: str, part: str) -> str:
    """건별 결정 기록의 키. 결정 기록의 키 길이(8~128) 안에 든다."""
    return "bulk-" + hashlib.sha256(f"{key}|{part}".encode()).hexdigest()[:48]


async def each(
    conn: AsyncConnection[Row],
    principal: Principal,
    project_id: UUID,
    work: list[tuple[UUID, s.DecisionRequest, str]],
    extra: dict[str, Any],
) -> list[s.DecisionResult]:
    """건마다 decide_on. 실패하면 몇 번째 어느 요구인지 붙여 다시 던진다(트랜잭션은 호출자가 롤백)."""
    out = []
    versions: dict[UUID, int] = {}
    for index, (requirement_id, payload, key) in enumerate(work):
        if payload.action == "review_confirm" and requirement_id in versions:
            # 한 요구에 확인할 결정이 둘이면 앞 확인이 판본을 올린다. 확인 대상은 decision_id가 정하므로
            # 같은 묶음 안에서는 앞 결과의 판본으로 잇는다(결정 행동의 같은 요구 중복은 여전히 판본 409).
            payload = payload.model_copy(update={"version": versions[requirement_id]})
        try:
            result = await decide_on(
                conn, principal, requirement_id, payload, key, project_id=project_id, extra=extra
            )
        except ApplicationError as error:
            raise ApplicationError(
                error.status_code,
                error.code,
                error.message,
                details=[
                    {
                        "index": str(index),
                        "requirement_id": str(requirement_id),
                        "reason": error.code,
                    },
                    *error.details,
                ],
            ) from None
        done = s.DecisionResult.model_validate(result)
        versions[requirement_id] = done.requirement_version
        out.append(done)
    return out


async def bulk_decide(
    request: Request,
    principal: Principal,
    project_id: UUID,
    payload: s.BulkDecisionRequest,
    key: str,
) -> s.BulkDecisionResult:
    """같은 프로젝트의 결정 여러 건을 한 번에 확정한다.

    Returns:
        s.BulkDecisionResult: 묶음 ID와 건별 결과(요청 순서).
    """
    batch_id = batch_of(principal.user_id, key)
    work = [
        (
            item.requirement_id,
            s.DecisionRequest.model_validate(item.model_dump(exclude={"requirement_id"})),
            item_key(key, str(index)),
        )
        for index, item in enumerate(payload.items)
    ]
    async with connection(request.app.state.settings) as conn:
        items = await each(conn, principal, project_id, work, {"batch_id": str(batch_id)})
    return s.BulkDecisionResult(batch_id=batch_id, items=items)


async def bulk_undo(
    request: Request, principal: Principal, project_id: UUID, batch_id: UUID, key: str
) -> s.BulkDecisionResult:
    """묶음의 결정 중 아직 되돌리지 않은 것을 최근 것부터 되돌린다. 한 건이라도 안 되면 모두 그대로다.

    Returns:
        s.BulkDecisionResult: 묶음 ID와 되돌림 결정들. 되돌릴 것이 없으면 404 TFT_BATCH_NOT_FOUND다.
    """
    async with connection(request.app.state.settings) as conn:
        cursor = await conn.execute(
            "SELECT d.id, d.requirement_id, req.latest_version FROM tft.requirement_decisions d "
            "JOIN tft.requirements req ON req.id = d.requirement_id "
            "WHERE d.project_id = %s AND d.detail->>'batch_id' = %s "
            "AND d.action NOT IN ('review_confirm', 'review_return') AND NOT EXISTS "
            "(SELECT 1 FROM tft.requirement_decisions u WHERE u.undoes = d.id) "
            "ORDER BY d.created_at DESC, d.id DESC",
            (project_id, str(batch_id)),
        )
        rows = await cursor.fetchall()
        if not rows:
            # 재전송이면 첫 결과를 재생한다(되돌림 기록의 키가 같다).
            cursor = await conn.execute(
                "SELECT result FROM tft.requirement_decisions WHERE actor_user_id = %s "
                "AND detail->>'undoes_batch' = %s "
                "ORDER BY created_at DESC, id DESC",
                (principal.user_id, str(batch_id)),
            )
            done = [s.DecisionResult.model_validate(r["result"]) for r in await cursor.fetchall()]
            if done:
                return s.BulkDecisionResult(batch_id=batch_id, items=done)
            raise ApplicationError(404, "TFT_BATCH_NOT_FOUND", "Nothing to undo in the batch.")
        work = [
            (
                row["requirement_id"],
                s.DecisionRequest(
                    version=row["latest_version"], action="undo", decision_id=row["id"]
                ),
                item_key(key, f"undo:{row['id']}"),
            )
            for row in rows
        ]
        items = await each(conn, principal, project_id, work, {"undoes_batch": str(batch_id)})
    return s.BulkDecisionResult(batch_id=batch_id, items=items)


async def mark_sent(
    request: Request, principal: Principal, project_id: UUID, payload: s.QuestionsSent
) -> s.QuestionSendList:
    """메일로 보낸 질의서의 고객 질의에 처음 보낸 시각을 남긴다. 이미 남긴 질의는 그대로 둔다(다시 보내도 같다).

    Returns:
        s.QuestionSendList: 요청한 질의들의 발송 기록.
    """
    async with connection(request.app.state.settings) as conn:
        project = await visible_project(conn, principal, project_id, *PROJECT_NOT_FOUND)
        if project["sales_user_id"] != principal.user_id:
            raise HTTPException(403)
        ids = list(dict.fromkeys(payload.question_ids))
        cursor = await conn.execute(
            "SELECT q.id FROM tft.customer_questions q WHERE q.project_id = %s AND q.id = ANY(%s) "
            "AND q.target = 'CUSTOMER' AND q.status IN ('OPEN', 'REPLIED') AND NOT EXISTS "
            "(SELECT 1 FROM tft.question_withdrawals w WHERE w.question_id = q.id)",
            (project_id, ids),
        )
        found = {r["id"] for r in await cursor.fetchall()}
        if missing := [str(i) for i in ids if i not in found]:
            raise ApplicationError(
                422,
                "VALIDATION_ERROR",
                "Only confirmed customer questions of the project can be sent.",
                details=[{"field": "question_ids", "reason": ",".join(missing)}],
            )
        now = datetime.now(UTC)
        for question_id in ids:
            cursor = await conn.execute(
                "INSERT INTO tft.question_sends (question_id, project_id, sent_at, sent_by, mail_ref) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (question_id) DO NOTHING RETURNING question_id",
                (question_id, project_id, now, principal.user_id, payload.mail_ref),
            )
            if await cursor.fetchone():
                await commands.audit(
                    conn,
                    project_id=project_id,
                    entity="question",
                    entity_id=question_id,
                    action="question.send",
                    actor=principal.user_id,
                    detail={"mail_ref": payload.mail_ref},
                )
        sent = await sends(conn, ids)
    return s.QuestionSendList(items=[sent[i] for i in ids])


async def sends(conn: AsyncConnection[Row], question_ids: list[UUID]) -> dict[UUID, s.QuestionSend]:
    """질문 ID → 처음 보낸 기록."""
    cursor = await conn.execute(
        "SELECT question_id, sent_at, sent_by, mail_ref FROM tft.question_sends "
        "WHERE question_id = ANY(%s)",
        (question_ids,),
    )
    return {r["question_id"]: s.QuestionSend.model_validate(r) for r in await cursor.fetchall()}


def waiting_days(question: s.Question, sent: s.QuestionSend | None, now: datetime) -> int | None:
    """보냈고 아직 회신이 없으면 보낸 날부터 지난 날수(한국 날짜 기준)."""
    if sent is None or question.status != "OPEN":
        return None
    return (now.astimezone(KST).date() - sent.sent_at.astimezone(KST).date()).days
