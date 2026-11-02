"""Isolated assumed-equipment step; host proves immutable namespace source before entry."""

import json
import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from scripts.cs_import_provided import Target, validate_target
from scripts.local_business.assumed_units import apply, prepare, require


def main():
    require(
        os.environ.get("ENVIRONMENT") == "local" and "K_SERVICE" not in os.environ, "LOCAL_ONLY"
    )
    receipt = json.loads(Path("/receipt/history.json").read_text())
    target = Target(**receipt["target"])
    dsn = os.environ["DATABASE_URL"]
    validate_target(Path("/target"), target, dsn)
    plan = prepare(
        Path("/workspace/eval/customer_history/units_demo.csv"),
        Path("/workspace/eval/customer_history/tickets.csv"),
        receipt,
    )
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT current_database() AS db, session_user AS login, current_user AS role, "
            "host(inet_server_addr()) AS host, inet_server_port() AS port, "
            "(SELECT system_identifier::text FROM pg_control_system()) AS system_id, "
            "(SELECT oid::bigint FROM pg_database WHERE datname=current_database()) AS oid, "
            "current_setting('server_version_num')::int AS version, pg_is_in_recovery() AS recovery"
        ).fetchone()
        require(
            row["db"] == target.db_name == "team4"
            and row["login"] == row["role"] == "postgres"
            and row["host"] == "127.0.0.1"
            and row["port"] == 5432
            and row["system_id"] == target.system_id
            and row["oid"] == target.db_oid
            and 170000 <= row["version"] < 180000
            and row["recovery"] is False,
            "DB_TARGET_MISMATCH",
        )
        result = apply(conn, plan)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
