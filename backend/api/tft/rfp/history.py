"""KAN-226 변경 이력: 요구별·프로젝트별 시간순 이벤트(읽기 전용).

저장 구조는 바꾸지 않는다. 이미 쌓이는 표를 읽어 합친다.
- tft.requirement_versions: AI 판정(ai_draft), 사람 변경(human_input, 제안 출처가 고객 회신이면 회신 반영), 시스템 기록.
- tft.requirement_decisions: 결정 패널 결정, 되돌리기, 책임자 확인·되돌려 보냄(KAN-218~220).
- tft.rfp_audit_events(action='requirement.recompare'): 재비교. requirement_recomparisons는 제안 승인 때
  물려받은 비교 사본도 쌓여 재비교 횟수와 다르고 행위자가 없어 쓰지 않는다.
- tft.question_replies: 회신 기록(회신 원문은 내지 않는다).
기록에 없는 사건은 만들지 않는다. 새 출처(KAN-225 직접 추가·빼기 등)는 SOURCES에 SELECT 하나를 더한다.
행위자 표시 이름은 플랫폼 사용자 조회(/api/v1/platform/users)로 화면이 붙인다(tft는 identity 스키마를 읽지 않는다).
"""

from datetime import datetime
from typing import Any, Literal, get_args
from uuid import UUID

from fastapi import Request

from backend.api.tft.rfp.routes_questions import visible_project
from backend.api.tft.rfp.routes_read import decode_cursor, encode_cursor
from backend.api.tft.rfp.routes_supplement import requirement_project
from backend.api.tft.rfp.schemas import Model
from backend.shared.access import Principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError

HistoryKind = Literal[
    "ai_verdict",
    "human_change",
    "reply_applied",
    "system_record",
    "decision",
    "decision_undone",
    "review_confirmed",
    "review_returned",
    "recompared",
    "reply_recorded",
]

_KIND = "(" + "|".join(get_args(HistoryKind)) + ")"
KIND_PATTERN = f"^{_KIND}(,{_KIND})*$"


def split_kinds(value: str | None) -> list[str] | None:
    """쉼표로 이은 kind query를 목록으로. 값 검사는 경로의 KIND_PATTERN이 한다."""
    return value.split(",") if value else None


class HistoryEvent(Model):
    id: str
    at: datetime
    kind: HistoryKind
    actor_user_id: UUID | None
    requirement_id: UUID | None
    requirement_version: int | None
    summary: str
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


class HistoryPage(Model):
    items: list[HistoryEvent]
    next_cursor: str | None


# 판정·값 칸의 앞 판본 대비 변화만 낸다(같은 칸은 뺀다).
FIELDS = ("status", "value", "unit", "operator", "necessity", "path")

SOURCES = f"""
SELECT 'v:' || v.requirement_id || ':' || v.version AS id, v.created_at AS at,
       CASE WHEN v.provenance_kind = 'ai_draft' THEN 'ai_verdict'
            WHEN v.provenance_kind = 'system_record' THEN 'system_record'
            WHEN p.origin = 'customer_reply' THEN 'reply_applied'
            ELSE 'human_change' END AS kind,
       v.decided_by AS actor_user_id, v.requirement_id, v.version AS requirement_version,
       v.reason AS summary,
       CASE WHEN prev.version IS NULL THEN NULL
            ELSE jsonb_build_object({", ".join(f"'{f}', prev.{f}" for f in FIELDS)}) END AS before,
       jsonb_build_object({", ".join(f"'{f}', v.{f}" for f in FIELDS)}) AS after
  FROM tft.requirement_versions v
  JOIN tft.requirements r ON r.id = v.requirement_id
  LEFT JOIN tft.requirement_versions prev
    ON prev.requirement_id = v.requirement_id AND prev.version = v.version - 1
  LEFT JOIN tft.change_proposals p ON p.id = v.proposal_id
 WHERE r.project_id = %(project)s
UNION ALL
SELECT 'd:' || d.id, d.created_at,
       CASE d.action WHEN 'undo' THEN 'decision_undone'
                     WHEN 'review_confirm' THEN 'review_confirmed'
                     WHEN 'review_return' THEN 'review_returned'
                     ELSE 'decision' END,
       d.actor_user_id, d.requirement_id, d.result_version, d.summary,
       NULL, jsonb_build_object('action', d.action)
  FROM tft.requirement_decisions d
 WHERE d.project_id = %(project)s
UNION ALL
-- CASE: 바깥 요구 필터가 이 SELECT로 내려와도 다른 감사 행의 entity_id를 uuid로 바꾸지 않게.
SELECT 'c:' || a.id, a.at, 'recompared', a.actor_user_id,
       CASE WHEN a.action = 'requirement.recompare' THEN a.entity_id::uuid END, NULL,
       '다시 비교', NULL, NULL
  FROM tft.rfp_audit_events a
 WHERE a.project_id = %(project)s AND a.entity = 'requirement' AND a.action = 'requirement.recompare'
UNION ALL
SELECT 'q:' || qr.id, qr.recorded_at, 'reply_recorded', qr.recorded_by, q.requirement_id, NULL,
       '회신 기록 (' || q.issue_type || ')', NULL, NULL
  FROM tft.question_replies qr
  JOIN tft.customer_questions q ON q.id = qr.question_id
 WHERE q.project_id = %(project)s
"""


def changed(before: dict[str, Any] | None, after: dict[str, Any] | None) -> tuple[Any, Any]:
    """앞 판본과 다른 칸만 남긴다. 앞이 없으면(첫 판본) after 그대로."""
    if before is None or after is None:
        return before, after
    keys = [k for k in after if before.get(k) != after.get(k)]
    return {k: before.get(k) for k in keys}, {k: after.get(k) for k in keys}


async def page(
    conn: Any,
    project_id: UUID,
    *,
    requirement_id: UUID | None,
    kinds: list[str] | None,
    actor: UUID | None,
    limit: int,
    cursor: str | None,
) -> HistoryPage:
    """최신순 한 쪽. 커서는 (시각, id) 키셋이다."""
    after_key = decode_cursor(cursor, str)
    where, params = [], {"project": project_id, "limit": limit + 1}
    if requirement_id is not None:
        where.append("e.requirement_id = %(requirement)s")
        params["requirement"] = requirement_id
    if kinds:
        where.append("e.kind = ANY(%(kinds)s)")
        params["kinds"] = kinds
    if actor is not None:
        where.append("e.actor_user_id = %(actor)s")
        params["actor"] = actor
    if after_key is not None:
        at, _, eid = after_key.partition("|")
        try:
            params["c_at"] = datetime.fromisoformat(at)
        except ValueError:
            raise ApplicationError(
                400, "TFT_INVALID_CURSOR", "The page cursor is invalid."
            ) from None
        params["c_id"] = eid
        where.append("(e.at, e.id) < (%(c_at)s, %(c_id)s)")
    sql = (
        f"SELECT * FROM ({SOURCES}) e"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY e.at DESC, e.id DESC LIMIT %(limit)s"
    )
    cur = await conn.execute(sql, params)
    rows = await cur.fetchall()
    items = []
    for row in rows[:limit]:
        before, after = changed(row["before"], row["after"])
        items.append(HistoryEvent(**{**row, "before": before, "after": after}))
    more = len(rows) > limit
    last = rows[limit - 1] if more else None
    return HistoryPage(
        items=items,
        next_cursor=encode_cursor(f"{last['at'].isoformat()}|{last['id']}") if last else None,
    )


async def project_history(
    request: Request, principal: Principal, project_id: UUID, **filters: Any
) -> HistoryPage:
    async with connection(request.app.state.settings) as conn:
        await visible_project(
            conn, principal, project_id, "TFT_PROJECT_NOT_FOUND", "The project was not found."
        )
        return await page(conn, project_id, requirement_id=None, **filters)


async def requirement_history(
    request: Request, principal: Principal, requirement_id: UUID, **filters: Any
) -> HistoryPage:
    async with connection(request.app.state.settings) as conn:
        project = await requirement_project(conn, principal, requirement_id)
        return await page(conn, project["id"], requirement_id=requirement_id, **filters)
