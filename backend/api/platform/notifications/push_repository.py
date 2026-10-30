"""Session-owned subscriptions and per-generation delivery ledger, platform role only."""

from typing import Any
from uuid import UUID, uuid4

import psycopg

Conn = psycopg.AsyncConnection[dict[str, Any]]


async def subscribe(conn: Conn, user_id: UUID, endpoint: str, p256dh: str, auth: str) -> None:
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (endpoint,))
    # Replace on changed owner/key: cascade removes old-generation deliveries. Unchanged
    # registration preserves created_at, so old events cannot become newly eligible.
    await conn.execute(
        "DELETE FROM notify.push_subscriptions WHERE endpoint=%s "
        "AND (user_id<>%s OR p256dh<>%s OR auth<>%s)",
        (endpoint, user_id, p256dh, auth),
    )
    await conn.execute(
        "INSERT INTO notify.push_subscriptions(id,user_id,endpoint,p256dh,auth) "
        "VALUES (%s,%s,%s,%s,%s) ON CONFLICT(endpoint) DO NOTHING",
        (uuid4(), user_id, endpoint, p256dh, auth),
    )


async def unsubscribe(conn: Conn, user_id: UUID, endpoint: str) -> None:
    await conn.execute(
        "DELETE FROM notify.push_subscriptions WHERE user_id=%s AND endpoint=%s",
        (user_id, endpoint),
    )


async def prepare(conn: Conn) -> None:
    await conn.execute(
        "INSERT INTO notify.push_deliveries(intent_id,subscription_id) "
        "SELECT n.intent_id,s.id FROM notify.user_notifications n "
        "JOIN notify.notification_intents i ON i.id=n.intent_id "
        "JOIN notify.push_subscriptions s ON s.user_id=n.user_id "
        "JOIN identity.users u ON u.id=n.user_id "
        "WHERE n.read_at IS NULL AND u.is_active AND i.created_at>=s.created_at "
        "AND i.created_at>now()-interval '1 hour' ON CONFLICT DO NOTHING"
    )
    # A started attempt may have reached the provider. Never resend it automatically.
    await conn.execute(
        "UPDATE notify.push_deliveries SET status='UNKNOWN' "
        "WHERE status='SENDING' AND started_at<now()-interval '2 minutes'"
    )


async def claim(conn: Conn) -> dict[str, Any] | None:
    cursor = await conn.execute(
        "WITH candidate AS (SELECT d.intent_id,d.subscription_id "
        "FROM notify.push_deliveries d "
        "JOIN notify.notification_intents i ON i.id=d.intent_id "
        "WHERE d.status='PENDING' AND d.next_attempt_at<=now() AND d.attempts<3 "
        "AND i.created_at>now()-interval '1 hour' "
        "ORDER BY d.next_attempt_at,d.intent_id,d.subscription_id "
        "LIMIT 1 FOR UPDATE OF d SKIP LOCKED) "
        "UPDATE notify.push_deliveries d SET status='SENDING',attempts=d.attempts+1,started_at=now() "
        "FROM candidate c WHERE d.intent_id=c.intent_id AND d.subscription_id=c.subscription_id "
        "RETURNING d.intent_id,d.subscription_id,d.attempts"
    )
    return await cursor.fetchone()


async def lock_attempt(conn: Conn, item: dict[str, Any]) -> dict[str, Any] | None:
    # Lock the subscription before sending: owner/key replacement/delete waits for this
    # bounded attempt. A deleted generation can never send to a newly registered owner.
    cursor = await conn.execute(
        "SELECT s.endpoint,s.p256dh,s.auth,i.title,i.link_path,i.id "
        "FROM notify.push_subscriptions s "
        "JOIN notify.push_deliveries d ON d.subscription_id=s.id "
        "JOIN notify.notification_intents i ON i.id=d.intent_id "
        "JOIN notify.user_notifications n ON n.intent_id=i.id AND n.user_id=s.user_id "
        "JOIN identity.users u ON u.id=s.user_id "
        "WHERE d.intent_id=%s AND s.id=%s AND d.status='SENDING' AND d.attempts=%s "
        "AND n.read_at IS NULL AND u.is_active "
        "AND i.created_at>now()-interval '1 hour' FOR UPDATE OF s,d",
        (item["intent_id"], item["subscription_id"], item["attempts"]),
    )
    return await cursor.fetchone()


async def finish(conn: Conn, item: dict[str, Any], status: str, delay: int = 60) -> None:
    await conn.execute(
        "UPDATE notify.push_deliveries SET status=%s,next_attempt_at=now()+(%s * interval '1 second') "
        "WHERE intent_id=%s AND subscription_id=%s AND status='SENDING' AND attempts=%s",
        (status, delay, item["intent_id"], item["subscription_id"], item["attempts"]),
    )
