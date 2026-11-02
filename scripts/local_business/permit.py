"""Local execution permission and charge journal, NOT a second business-job state store.

Reservations are user-selected admission limits, not a guaranteed provider USD cap.
Unknown/failed calls keep their reservation and block further paid work. Runtime DB leases
remain authoritative. No prompt, response, API key or business record is stored here.
"""

import json
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4


class Denied(RuntimeError):
    """A fixed local policy reason; never include provider exceptions or credentials."""


def money(value: str | Decimal) -> Decimal:
    amount = Decimal(value)
    if not amount.is_finite() or amount < 0:
        raise ValueError("INVALID_LOCAL_AMOUNT")
    return amount


class Permit:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = path
        # No business status/lease copy: job IDs here are immutable execution authorizations.
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS driver (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), token TEXT,
                    job_limit INTEGER NOT NULL, call_limit INTEGER NOT NULL,
                    reservation TEXT NOT NULL, budget TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS grants (
                    job_id TEXT PRIMARY KEY, token TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS charges (
                    id TEXT PRIMARY KEY, token TEXT NOT NULL, job_id TEXT NOT NULL,
                    model TEXT NOT NULL, reserved TEXT NOT NULL, actual TEXT,
                    state TEXT NOT NULL CHECK(state IN ('RESERVED','SETTLED','UNKNOWN')),
                    usage TEXT, reason TEXT);
            """)
        path.chmod(0o600)

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, isolation_level=None, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def driver(db, token):
        row = db.execute("SELECT * FROM driver WHERE singleton=1").fetchone()
        if row is None or row["token"] != token:
            raise Denied("LOCAL_DRIVER_NOT_AUTHORIZED")
        return row

    @staticmethod
    def outstanding(db):
        if db.execute("SELECT 1 FROM charges WHERE state!='SETTLED' LIMIT 1").fetchone():
            raise Denied("LOCAL_COST_UNKNOWN_REVIEW_REQUIRED")

    @staticmethod
    def committed(db):
        return sum(
            (money(r[0]) for r in db.execute("SELECT coalesce(actual,reserved) FROM charges")),
            Decimal(0),
        )

    def authorize(
        self, *, max_jobs: int, max_calls: int, reserve_usd: str, budget_usd: str = "1.50"
    ) -> str:
        reserve, budget = money(reserve_usd), money(budget_usd)
        if not 1 <= max_jobs <= 20 or not 1 <= max_calls <= 100:
            raise ValueError("INVALID_LOCAL_LIMIT")
        if not 0 < reserve <= budget < Decimal("3.00"):
            raise ValueError("INVALID_LOCAL_AMOUNT")
        with self.transaction() as db:
            row = db.execute("SELECT token FROM driver WHERE singleton=1").fetchone()
            if row and row[0] is not None:
                raise Denied("LOCAL_DRIVER_ALREADY_AUTHORIZED")
            self.outstanding(db)
            if self.committed(db) + reserve > budget:
                raise Denied("LOCAL_BUDGET_EXHAUSTED")
            token = str(uuid4())
            db.execute(
                "INSERT OR REPLACE INTO driver VALUES(1,?,?,?,?,?)",
                (token, max_jobs, max_calls, str(reserve), str(budget)),
            )
            return token

    def grant(self, token: str, job_id: str):
        if str(UUID(job_id)) != job_id:
            raise ValueError("INVALID_JOB_ID")
        with self.transaction() as db:
            row = self.driver(db, token)
            self.outstanding(db)
            if db.execute("SELECT 1 FROM grants WHERE job_id=?", (job_id,)).fetchone():
                raise Denied("LOCAL_JOB_ALREADY_AUTHORIZED")
            used = db.execute("SELECT count(*) FROM grants WHERE token=?", (token,)).fetchone()[0]
            if used >= row["job_limit"]:
                raise Denied("LOCAL_JOB_LIMIT")
            db.execute("INSERT INTO grants VALUES(?,?)", (job_id, token))

    def reserve(self, token: str, job_id: str, model: str) -> str:
        with self.transaction() as db:
            row = self.driver(db, token)
            self.outstanding(db)
            if not db.execute(
                "SELECT 1 FROM grants WHERE job_id=? AND token=?", (job_id, token)
            ).fetchone():
                raise Denied("LOCAL_JOB_NOT_AUTHORIZED")
            calls = db.execute("SELECT count(*) FROM charges WHERE token=?", (token,)).fetchone()[0]
            if calls >= row["call_limit"]:
                raise Denied("LOCAL_CALL_LIMIT")
            if self.committed(db) + money(row["reservation"]) > money(row["budget"]):
                raise Denied("LOCAL_BUDGET_EXHAUSTED")
            charge = str(uuid4())
            db.execute(
                "INSERT INTO charges VALUES(?,?,?,?,?,NULL,'RESERVED',NULL,NULL)",
                (charge, token, job_id, model, row["reservation"]),
            )
            return charge

    def settle(self, charge: str, *, actual_usd: str | None, usage: dict[str, int] | None = None):
        allowed = {
            "input_tokens",
            "output_tokens",
            "thought_tokens",
            "cached_input_tokens",
            "total_tokens",
        }
        if usage is not None and (
            set(usage) - allowed or any(type(v) is not int or v < 0 for v in usage.values())
        ):
            raise ValueError("INVALID_LOCAL_USAGE")
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM charges WHERE id=? AND state='RESERVED'", (charge,)
            ).fetchone()
            if row is None:
                raise Denied("LOCAL_CHARGE_NOT_RESERVED")
            actual = None if actual_usd is None else money(actual_usd)
            reason = "LOCAL_COST_UNKNOWN" if actual is None else None
            # A known overrun is recorded in full; never truncate recorded spend to a cap.
            if actual is not None and actual > money(row["reserved"]):
                reason = "LOCAL_RESERVATION_EXCEEDED"
            state = "UNKNOWN" if reason else "SETTLED"
            db.execute(
                "UPDATE charges SET actual=?,state=?,usage=?,reason=? WHERE id=?",
                (
                    None if actual is None else str(actual),
                    state,
                    json.dumps(usage) if usage is not None else None,
                    reason,
                    charge,
                ),
            )

    def close_driver(self, token: str):
        with self.transaction() as db:
            self.driver(db, token)
            db.execute("UPDATE driver SET token=NULL WHERE singleton=1")
