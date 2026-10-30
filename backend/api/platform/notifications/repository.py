"""잠근 의도를 활성 사용자로 펼치고 본인 알림만 읽는다. commit은 호출자가 맡는다."""

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

Row = dict[str, Any]
RECIPIENTS = """
INSERT INTO notify.user_notifications(intent_id,user_id)
SELECT DISTINCT i.id,u.id
FROM notify.notification_intents i
CROSS JOIN LATERAL jsonb_array_elements(i.audience) s
JOIN identity.users u ON u.is_active AND (
    u.id::text=s->>'user'
    OR (s->>'role'='cs_agent' AND EXISTS (
        SELECT 1 FROM identity.user_roles r WHERE r.user_id=u.id AND r.role='cs_agent'))
    OR (u.dept=s->>'dept' AND EXISTS (
        SELECT 1 FROM identity.user_roles r WHERE r.user_id=u.id AND r.role='dept_staff'))
    OR (EXISTS (SELECT 1 FROM identity.user_roles r WHERE r.user_id=u.id AND r.role='manager')
        AND EXISTS (SELECT 1 FROM identity.manager_departments m
                    WHERE m.user_id=u.id AND m.dept_code=s->>'manager_dept'))
)
WHERE i.id=%s AND (i.actor_id IS NULL OR u.id<>i.actor_id)
ON CONFLICT DO NOTHING
"""


async def expand_pending(conn: AsyncConnection[Row], batch_size: int = 100) -> int:
    if type(batch_size) is not int or not 1 <= batch_size <= 100:
        raise ValueError("NOTIFICATION_INVALID_BATCH")
    cursor = await conn.execute(
        "SELECT id FROM notify.notification_intents WHERE expanded_at IS NULL "
        "ORDER BY created_at,id LIMIT %s FOR UPDATE SKIP LOCKED",
        (batch_size,),
    )
    rows = await cursor.fetchall()
    for row in rows:
        await conn.execute(RECIPIENTS, (row["id"],))
        await conn.execute(
            "UPDATE notify.notification_intents SET expanded_at=clock_timestamp() WHERE id=%s",
            (row["id"],),
        )
    return len(rows)


async def list_mine(
    conn: AsyncConnection[Row], user_id: UUID, limit: int, offset: int
) -> list[Row]:
    cursor = await conn.execute(
        "SELECT i.id,i.kind,i.title,i.link_path,i.created_at,n.read_at "
        "FROM notify.user_notifications n JOIN notify.notification_intents i ON i.id=n.intent_id "
        "WHERE n.user_id=%s ORDER BY i.created_at DESC,i.id DESC LIMIT %s OFFSET %s",
        (user_id, limit, offset),
    )
    return await cursor.fetchall()


async def unread_count(conn: AsyncConnection[Row], user_id: UUID) -> int:
    cursor = await conn.execute(
        "SELECT count(*) AS count FROM notify.user_notifications WHERE user_id=%s AND read_at IS NULL",
        (user_id,),
    )
    row = await cursor.fetchone()
    assert row is not None
    return int(row["count"])


async def mark_read(conn: AsyncConnection[Row], user_id: UUID, intent_id: UUID) -> bool:
    cursor = await conn.execute(
        "UPDATE notify.user_notifications SET read_at=coalesce(read_at,clock_timestamp()) "
        "WHERE user_id=%s AND intent_id=%s",
        (user_id, intent_id),
    )
    return cursor.rowcount == 1
