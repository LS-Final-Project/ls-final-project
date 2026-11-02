"""Explicit local PROVIDED history import, never workflow/result/gold replay (KAN-159).

Host prerequisite: owned(state) + no_active_driver(config). Mount only settings.json,
inputs, this fixed tool and a private receipt directory; never keys or the Docker socket.
No SDK, HTTP, DDL, automatic closing or improvement judgement is invoked here.
"""

import argparse
import csv
import hashlib
import io
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from pydantic import ValidationError

from backend.api.cs.tickets.schemas import TicketCreate
from backend.api.cs.workflow.schemas import ResolutionCaseCreate
from backend.engine.cs.rules import load_policy
from scripts.local_business.config import ROOT, settings

BASE_SOURCE = "a4ce3dc0bceb5e707606189a5b35ba5d40be6ff4"
SOURCE = "training_csv"
ASSUMPTIONS = {
    "tickets.known_at": "ASSUMED from source received_at_utc; no explicit known_at column",
    "cases.resolved_at": "historical time from linked development ticket resolved_at_utc",
    "cases.known_at": "ASSUMED equal to historical resolved_at; not explicit in cases CSV",
}
TICKET_INPUT = (
    "channel",
    "body_language",
    "customer_id",
    "product_code",
    "inquiry_subject",
    "inquiry_body",
    "fw_version_reported",
    "error_code_reported",
    "customer_tier",
)
TICKET_META = (
    "ticket_id",
    "data_split",
    "received_at_utc",
    "resolved_at_utc",
    "resolution_case_id",
)
CASE_INPUT = (
    "product_code",
    "fault_id",
    "fw_version_at_issue",
    "symptom",
    "root_cause",
    "root_cause_type",
    "action_taken",
    "onsite_visit",
    "rma_no",
    "fw_fixed_version",
    "resolved_by_dept",
    "recurrence_prevention",
)


class Invalid(ValueError):
    """Only fixed codes and file/count diagnostics, never raw values or validation errors."""

    def __init__(self, code, file=None, count=1):
        super().__init__(code)
        self.code, self.file, self.count = code, file, count


def require(ok, code, file=None):
    if not ok:
        raise Invalid(code, file)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def read(path, expected=None):
    data = path.read_bytes()
    require(expected is None or digest(data) == expected, "IMPORT_HASH_MISMATCH", path.name)
    return data


def csv_rows(data, required, name):
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
    headers = reader.fieldnames or []
    require(
        len(headers) == len(set(headers)) and set(required) <= set(headers),
        "IMPORT_HEADERS_INVALID",
        name,
    )
    for row in reader:
        require(
            None not in row and all(row[k] is not None for k in required),
            "IMPORT_ROW_INVALID",
            name,
        )
        yield row


def bundle_file(root, value):
    require(
        isinstance(value, str)
        and value
        and not Path(value).is_absolute()
        and ".." not in Path(value).parts,
        "IMPORT_BUNDLE_PATH_INVALID",
    )
    path = (root / value).resolve()
    require(path.is_relative_to(root.resolve()) and path.is_file(), "IMPORT_BUNDLE_PATH_INVALID")
    return path


def timestamp(value, name):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(result.tzinfo is not None, "IMPORT_TIME_INVALID", name)
        return result.astimezone(UTC)
    except (ValueError, AttributeError):
        raise Invalid("IMPORT_TIME_INVALID", name) from None


@dataclass(frozen=True)
class Counts:
    train: int = 2800
    valid: int = 603
    test: int = 597
    cases: int = 696
    excluded_cases: int = 104


@dataclass
class Plan:
    source_instance: str
    tickets: list[dict]
    cases: list[dict]
    hashes: dict[str, str]
    counts: Counts
    input_roots: tuple[Path, ...]

    def summary(self):
        return {"counts": asdict(self.counts), "hashes": self.hashes, "assumptions": ASSUMPTIONS}


def prepare(
    manifest: Path,
    manifest_sha256: str,
    source_tickets: Path,
    cases: Path,
    cases_sha256: str,
    ref: Path,
    *,
    expected=Counts(),
) -> Plan:
    """Validate pinned inputs, project before modelling and never consume heldout bodies.

    Non-default Counts exists only for isolated fixture callers; CLI never exposes it.
    Source CSV contributes only ID/split metadata. Gold columns are allowed but discarded.
    """
    mdata = read(manifest, manifest_sha256)
    meta = json.loads(mdata)
    provenance = meta["provenance"]
    require(
        set(meta["partitions"]) == {"train", "valid"}
        and provenance["data_origin"] == "synthetic"
        and provenance["procedure"] == "cs-dev-split-v1"
        and provenance["test_rows_discarded"] == expected.test,
        "IMPORT_MANIFEST_INVALID",
        manifest.name,
    )
    require(
        re.fullmatch(r"[0-9a-f]{64}", provenance["source_sha256"]) is not None,
        "IMPORT_MANIFEST_INVALID",
        manifest.name,
    )
    hashes = {"manifest": digest(mdata)}
    original = read(source_tickets, provenance["source_sha256"])
    hashes["source_tickets"] = digest(original)
    ids = {s: set() for s in ("train", "valid", "test")}
    seen = set()
    for row in csv_rows(original, ("ticket_id", "data_split"), source_tickets.name):
        key, split = row["ticket_id"], row["data_split"]
        require(
            key and key not in seen and split in ids,
            "IMPORT_SOURCE_IDS_INVALID",
            source_tickets.name,
        )
        seen.add(key)
        ids[split].add(key)
    require(
        all(len(ids[s]) == getattr(expected, s) for s in ids),
        "IMPORT_COUNTS_INVALID",
        source_tickets.name,
    )
    selected = {}
    ticket_files = {}
    for split in ("train", "valid"):
        entry = meta["partitions"][split]
        path = bundle_file(manifest.parent, entry["path"])
        data = read(path, entry["sha256"])
        hashes[split] = digest(data)
        rows = []
        for raw in csv_rows(data, TICKET_INPUT + TICKET_META, path.name):
            row = {k: raw[k] for k in TICKET_INPUT + TICKET_META}
            require(
                row["data_split"] == split
                and row["ticket_id"] in ids[split]
                and row["ticket_id"] not in selected,
                "IMPORT_SPLIT_INVALID",
                path.name,
            )
            selected[row["ticket_id"]] = row
            ticket_files[row["ticket_id"]] = path.name
            rows.append(row)
        require(
            len(rows) == entry["rows"] == getattr(expected, split)
            and {r["ticket_id"] for r in rows} == ids[split],
            "IMPORT_COUNTS_INVALID",
            path.name,
        )
    combined = meta["tickets"]
    require(
        combined["splits"] == ["train", "valid"]
        and combined["rows"] == expected.train + expected.valid,
        "IMPORT_MANIFEST_INVALID",
    )
    path = bundle_file(manifest.parent, combined["path"])
    data = read(path, combined["sha256"])
    hashes["development_combined"] = digest(data)
    combined_ids = []
    for row in csv_rows(data, ("ticket_id", "data_split"), path.name):
        require(
            row["data_split"] in {"train", "valid"} and row["ticket_id"] in ids[row["data_split"]],
            "IMPORT_COMBINED_INVALID",
            path.name,
        )
        combined_ids.append(row["ticket_id"])
    require(
        len(combined_ids) == len(selected)
        and len(set(combined_ids)) == len(selected)
        and set(combined_ids) == set(selected),
        "IMPORT_COMBINED_INVALID",
        path.name,
    )
    reference = {}
    for file, key in (
        ("customers", "customer_id"),
        ("product_master", "product_code"),
        ("fault_catalog", "fault_id"),
        ("departments", "dept_code"),
    ):
        path = ref / (file + ".csv")
        data = read(path)
        hashes["ref/" + path.name] = digest(data)
        keys = [r[key] for r in csv_rows(data, (key,), path.name)]
        require(all(keys) and len(keys) == len(set(keys)), "IMPORT_REF_INVALID", path.name)
        reference[file] = set(keys)
    products = set(load_policy().families)
    source_instance = "cs-development-v1:" + provenance["source_sha256"]

    def identity(kind, external):
        return uuid5(NAMESPACE_URL, f"{SOURCE}/{source_instance}/{kind}/{external}")

    tickets = []
    by_external = {}
    for external, row in selected.items():
        require(
            row["customer_id"] in reference["customers"]
            and row["product_code"] in reference["product_master"] & products,
            "IMPORT_TICKET_FK_INVALID",
        )
        payload = {
            "channel": row["channel"],
            "body_language": row["body_language"],
            "customer_external_id": row["customer_id"],
            "product_code": row["product_code"],
            "subject": row["inquiry_subject"] or None,
            "body": row["inquiry_body"],
            "fw_version_reported": row["fw_version_reported"] or None,
            "error_code_reported": row["error_code_reported"] or None,
            "customer_tier": row["customer_tier"] or None,
            "data_origin": "synthetic",
        }
        try:
            payload = TicketCreate.model_validate(payload).model_dump()
        except ValidationError:
            raise Invalid("IMPORT_TICKET_SCHEMA_INVALID") from None
        received = timestamp(row["received_at_utc"], ticket_files[external])
        ticket = {
            "id": identity("ticket", external),
            "source": SOURCE,
            "source_instance": source_instance,
            "external_id": external,
            "derivation_kind": "PROVIDED",
            "received_at": received,
            "known_at": received,
            **payload,
        }
        tickets.append(ticket)
        by_external[external] = ticket
    cdata = read(cases, cases_sha256)
    hashes["resolution_cases"] = digest(cdata)
    case_rows, seen_cases, seen_tickets = [], set(), set()
    excluded = 0
    for raw in csv_rows(cdata, ("case_id", "ticket_id") + CASE_INPUT, cases.name):
        external, ticket_id = raw["case_id"], raw["ticket_id"]
        require(
            external and external not in seen_cases and ticket_id not in seen_tickets,
            "IMPORT_CASE_IDS_INVALID",
            cases.name,
        )
        seen_cases.add(external)
        seen_tickets.add(ticket_id)
        if ticket_id in ids["test"]:
            excluded += 1
            continue
        require(ticket_id in selected, "IMPORT_CASE_TICKET_INVALID", cases.name)
        row = {k: raw[k] for k in CASE_INPUT}
        ticket = by_external[ticket_id]
        require(
            row["product_code"] == ticket["product_code"]
            and row["fault_id"] in reference["fault_catalog"]
            and row["resolved_by_dept"] in reference["departments"]
            and selected[ticket_id]["resolution_case_id"] == external,
            "IMPORT_CASE_FK_INVALID",
            cases.name,
        )
        resolved = timestamp(selected[ticket_id]["resolved_at_utc"], ticket_files[ticket_id])
        require(resolved >= ticket["received_at"], "IMPORT_CASE_TIME_ORDER", cases.name)
        try:
            payload = ResolutionCaseCreate.model_validate(
                {"version": 1, **{k: (v or None) for k, v in row.items() if k != "product_code"}}
            ).model_dump(exclude={"version"})
        except ValidationError:
            raise Invalid("IMPORT_CASE_SCHEMA_INVALID", cases.name) from None
        case_rows.append(
            {
                "id": identity("case", external),
                "ticket_id": ticket["id"],
                "source": SOURCE,
                "source_instance": source_instance,
                "external_id": external,
                "data_origin": "synthetic",
                "derivation_kind": "PROVIDED",
                "resolved_at": resolved,
                "known_at": resolved,
                "product_code": ticket["product_code"],
                "registered_by": None,
                **payload,
            }
        )
    require(
        len(case_rows) == expected.cases and excluded == expected.excluded_cases,
        "IMPORT_CASE_COUNTS_INVALID",
        cases.name,
    )
    require(
        {r["ticket_id"] for r in case_rows}
        == {by_external[k]["id"] for k, row in selected.items() if row["resolution_case_id"]},
        "IMPORT_CASE_SET_INVALID",
        cases.name,
    )
    return Plan(
        source_instance,
        tickets,
        case_rows,
        hashes,
        expected,
        (
            manifest.parent.resolve(),
            source_tickets.parent.resolve(),
            cases.parent.resolve(),
            ref.resolve(),
        ),
    )


@dataclass(frozen=True)
class Target:
    namespace: str
    owner: str
    pg_image: str
    runtime_image: str
    system_id: str
    db_oid: int
    db_name: str = "team4"

    def token(self):
        return digest(encoded(asdict(self)))


def validate_target(state: Path, target: Target, dsn: str):
    cfg = settings(state)
    require(
        cfg.get("setup_complete") is True
        and cfg["namespace"] == target.namespace
        and cfg.get("source_sha") == BASE_SOURCE
        and cfg.get("owner") == target.owner
        and cfg["images"].get("pg") == target.pg_image
        and cfg["images"].get("runtime") == target.runtime_image,
        "IMPORT_NAMESPACE_PROOF_MISMATCH",
    )
    require(
        str(UUID(target.owner)) == target.owner
        and target.system_id.isdecimal()
        and target.db_oid > 0
        and (
            target.db_name == "team4"
            or re.fullmatch(r"kan159_import_fixture_[a-z0-9_]+", target.db_name)
        )
        and all(
            re.fullmatch(r"sha256:[0-9a-f]{64}", value)
            for value in (target.pg_image, target.runtime_image)
        ),
        "IMPORT_TARGET_INVALID",
    )
    try:
        info = conninfo_to_dict(dsn)
    except psycopg.Error:
        raise Invalid("IMPORT_LOCAL_TARGET_REQUIRED") from None
    require(
        set(info) <= {"host", "port", "dbname", "user"}
        and info.get("host") == "127.0.0.1"
        and info.get("port") == "5432"
        and info.get("dbname") == target.db_name
        and info.get("user") == "postgres"
        and not any(
            os.environ.get(k)
            for k in (
                "PGSERVICE",
                "PGHOSTADDR",
                "PGOPTIONS",
                "PGPASSFILE",
                "PGSERVICEFILE",
                "PGPASSWORD",
            )
        ),
        "IMPORT_LOCAL_TARGET_REQUIRED",
    )


def apply_plan(conn, plan: Plan, target: Target):
    """One transaction; exact matching rows skip, any conflict rolls everything back."""
    counts = {}
    with conn.transaction():
        row = conn.execute(
            "SELECT current_database() AS db_name, (SELECT oid::bigint FROM pg_database "
            "WHERE datname=current_database()) AS db_oid, current_user AS current_user, "
            "session_user AS session_user, inet_server_addr()::text AS address, "
            "inet_server_port() AS port, current_setting('server_version_num')::int AS version, "
            "(SELECT system_identifier::text FROM pg_control_system()) AS system_id, "
            "pg_is_in_recovery() AS recovery, clock_timestamp() AS now"
        ).fetchone()
        require(
            row
            and row["db_name"] == target.db_name
            and row["db_oid"] == target.db_oid
            and row["current_user"] == row["session_user"] == "postgres"
            and row["address"] == "127.0.0.1"
            and row["port"] == 5432
            and 170000 <= row["version"] < 180000
            and row["system_id"] == target.system_id
            and row["recovery"] is False,
            "IMPORT_SERVER_IDENTITY_MISMATCH",
        )
        require(
            all(r["known_at"] <= row["now"] for r in plan.tickets + plan.cases),
            "IMPORT_FUTURE_KNOWN_AT",
        )
        require(
            conn.execute("SELECT count(*) AS n FROM identity.users").fetchone()["n"] == 4,
            "IMPORT_IDENTITY_BASELINE_INVALID",
        )
        conn.execute("SET LOCAL ROLE cs_migrator")
        conn.execute(
            "LOCK TABLE cs.tickets, cs.resolution_cases, cs.ticket_source_records "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
        require(
            conn.execute("SELECT count(*) AS n FROM cs.ticket_source_records").fetchone()["n"] == 0,
            "IMPORT_SOURCE_RECORDS_NOT_EMPTY",
        )
        for table in ("tickets", "resolution_cases"):
            n = conn.execute(
                f"SELECT count(*) AS n FROM cs.{table} WHERE source IS DISTINCT FROM %s "
                "OR source_instance IS DISTINCT FROM %s",
                (SOURCE, plan.source_instance),
            ).fetchone()["n"]
            require(n == 0, "IMPORT_FOREIGN_SOURCE_PRESENT")
        for table, wanted in (("tickets", plan.tickets), ("resolution_cases", plan.cases)):
            columns = tuple(wanted[0])
            existing = conn.execute(f"SELECT {', '.join(columns)} FROM cs.{table}").fetchall()
            by_id = {r["external_id"]: r for r in existing}
            require(
                set(by_id) <= {r["external_id"] for r in wanted}, "IMPORT_UNEXPECTED_EXISTING_IDS"
            )
            inserted = skipped = 0
            for record in wanted:
                old = by_id.get(record["external_id"])
                if old is not None:
                    require(all(old[k] == record[k] for k in columns), "IMPORT_EXISTING_CONFLICT")
                    skipped += 1
                else:
                    conn.execute(
                        f"INSERT INTO cs.{table} ({', '.join(columns)}) VALUES "
                        f"({', '.join(['%s'] * len(columns))})",
                        tuple(record.values()),
                    )
                    inserted += 1
            counts[table] = {"inserted": inserted, "skipped": skipped}
    return counts


def receipt(plan: Plan, target: Target):
    return {
        "committed": True,
        "target": asdict(target),
        **plan.summary(),
        "source": SOURCE,
        "source_instance": plan.source_instance,
        "tickets": [
            {
                "external_id": r["external_id"],
                "id": str(r["id"]),
                "customer_external_id": r["customer_external_id"],
            }
            for r in plan.tickets
        ],
        "cases": [
            {"external_id": r["external_id"], "id": str(r["id"]), "ticket_id": str(r["ticket_id"])}
            for r in plan.cases
        ],
    }


def receipt_path(path: Path, plan: Plan, data: bytes):
    resolved = path.resolve()
    require(
        not path.is_symlink()
        and not any(resolved.is_relative_to(p) for p in plan.input_roots)
        and not resolved.is_relative_to(ROOT.resolve() / "data"),
        "IMPORT_RECEIPT_PATH_INVALID",
    )
    if path.exists():
        require(
            path.stat().st_mode & 0o077 == 0 and path.read_bytes() == data,
            "IMPORT_RECEIPT_CONFLICT",
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "source-tickets", "cases", "ref"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("manifest-sha256", "cases-sha256"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-target")
    parser.add_argument("--target-state", type=Path)
    parser.add_argument("--namespace")
    parser.add_argument("--expected-owner")
    parser.add_argument("--expected-pg-image")
    parser.add_argument("--expected-runtime-image")
    parser.add_argument("--expected-system-id")
    parser.add_argument("--expected-db-oid", type=int)
    parser.add_argument("--expected-db-name", default="team4")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    committed = False
    try:
        require(
            os.environ.get("ENVIRONMENT") == "local" and "K_SERVICE" not in os.environ,
            "IMPORT_LOCAL_ENV_REQUIRED",
        )
        plan = prepare(
            args.manifest,
            args.manifest_sha256,
            args.source_tickets,
            args.cases,
            args.cases_sha256,
            args.ref,
        )
        report = plan.summary()
        if not args.apply:
            print(json.dumps({"mode": "dry-run", **report}, sort_keys=True))
            return 0
        require(
            all(
                v is not None
                for v in (
                    args.target_state,
                    args.namespace,
                    args.expected_owner,
                    args.expected_pg_image,
                    args.expected_runtime_image,
                    args.expected_system_id,
                    args.expected_db_oid,
                    args.receipt,
                )
            ),
            "IMPORT_TARGET_ARGUMENTS_REQUIRED",
        )
        target = Target(
            args.namespace,
            args.expected_owner,
            args.expected_pg_image,
            args.expected_runtime_image,
            args.expected_system_id,
            args.expected_db_oid,
            args.expected_db_name,
        )
        validate_target(args.target_state, target, os.environ.get("DATABASE_URL", ""))
        require(args.confirm_target == target.token(), "IMPORT_CONFIRM_TARGET_REQUIRED")
        data = encoded(receipt(plan, target)) + b"\n"
        receipt_path(args.receipt, plan, data)
        with psycopg.connect(
            os.environ["DATABASE_URL"], autocommit=True, row_factory=dict_row
        ) as conn:
            applied = apply_plan(conn, plan, target)
        committed = True
        try:
            fd = os.open(args.receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            receipt_path(args.receipt, plan, data)
        else:
            with os.fdopen(fd, "wb") as out:
                out.write(data)
        print(
            json.dumps(
                {
                    "mode": "applied",
                    **report,
                    "applied": applied,
                    "target_sha256": target.token(),
                    "receipt_sha256": digest(data),
                },
                sort_keys=True,
            )
        )
        return 0
    except Invalid as error:
        out = {"code": error.code, "file": error.file, "count": error.count, "committed": committed}
        if error.code == "IMPORT_CONFIRM_TARGET_REQUIRED":
            out["target_sha256"] = target.token()
        print(json.dumps(out, sort_keys=True))
    except Exception:
        print(
            json.dumps(
                {
                    "code": "IMPORT_COMMITTED_RECEIPT_FAILED" if committed else "IMPORT_FAILED",
                    "committed": committed,
                }
            )
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
