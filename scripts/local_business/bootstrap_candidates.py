"""Isolated improvement-candidate step (KAN-162 QA); host proves immutable namespace source.

The provided history already has fault groups past the repeat threshold. The service makes a
candidate draft only when a new resolution record is saved (KAN-206) or when the collect route is
called, so a fresh local environment showed none until someone closed a ticket of that group.
Proposal 4.4 ⑩: a group that reached the threshold has a draft. This runs the same judgement as
the collect route once, as of now, after the history is loaded. Running it again adds nothing.
"""

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from backend.api.cs.improvements.candidates import save_candidates
from scripts.cs_import_provided import Target, validate_target
from scripts.local_business.assumed_units import require

# Recorded as the run's actor and the evidence author. Not a login; the same value every time.
ACTOR = uuid5(NAMESPACE_URL, "team4-local-bootstrap/improvement-candidates")


async def collect(dsn: str) -> dict:
    async with await AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
        # The target check needs the postgres login (same as the other steps). Writes run with the
        # C/S service's own privileges, so the step can do no more than the collect route.
        await conn.execute("SET ROLE cs_app")
        row = await (
            await conn.execute("SELECT current_database() AS db, current_user AS role")
        ).fetchone()
        require(row is not None and row["db"] == "team4" and row["role"] == "cs_app", "DB_ROLE")
        async with conn.transaction():
            result = await save_candidates(conn, ACTOR, datetime.now(UTC))
    return {
        "eligible": result.eligible,
        "created": result.created,
        "already_requested": result.already_requested,
        "skipped": len(result.skipped),
    }


def main():
    require(
        os.environ.get("ENVIRONMENT") == "local" and "K_SERVICE" not in os.environ, "LOCAL_ONLY"
    )
    receipt = json.loads(Path("/receipt/history.json").read_text())
    dsn = os.environ["DATABASE_URL"]
    validate_target(Path("/target"), Target(**receipt["target"]), dsn)
    print(json.dumps(asyncio.run(collect(dsn)), sort_keys=True))


if __name__ == "__main__":
    main()
