"""Finite Cloud Run Job: never imported/called by the HTTP dispatch route."""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from backend.api.platform.notifications import push_repository as repository
from backend.api.platform.notifications import repository as notifications
from backend.api.platform.notifications.push_send import send
from backend.api.platform.notifications.push_settings import PushWorkerSettings
from backend.shared.database import connection
from backend.shared.settings import Settings

logger = logging.getLogger(__name__)
Sender = Callable[[dict[str, Any], PushWorkerSettings], tuple[str, int]]


async def run(settings: Settings, push: PushWorkerSettings, *, sender: Sender = send) -> int:
    if not push.enabled:
        return 0
    async with connection(settings) as conn:
        await notifications.expand_pending(conn)
        await repository.prepare(conn)
    completed = 0
    for _ in range(push.batch_size):
        async with connection(settings) as conn:
            item = await repository.claim(conn)
        # SENDING and attempts are committed before the first provider byte.
        if item is None:
            break
        async with connection(settings) as conn:
            row = await repository.lock_attempt(conn, item)
            if row is None:
                await repository.finish(conn, item, "SKIPPED")
                continue
            # This synchronous bounded child wait is intentional in a dedicated CLI:
            # no async cancellation releases the generation lock while a send continues.
            status, delay = sender(row, push)
            if status == "GONE":
                await conn.execute(
                    "DELETE FROM notify.push_subscriptions WHERE id=%s", (item["subscription_id"],)
                )
            else:
                if status == "PENDING" and item["attempts"] >= 3:
                    status = "FAILED"
                await repository.finish(conn, item, status, delay)
        completed += 1
    return completed


def main() -> int:
    try:
        settings = Settings()
        push = PushWorkerSettings()
        if not push.enabled:
            logger.info("NOTIFICATION_PUSH_DISABLED")
            return 0
        asyncio.run(run(settings, push))
    except Exception:
        logger.error("NOTIFICATION_PUSH_FAILED")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
