"""Synthetic files and transaction fakes only; real fixture PG is operated by coder2."""

import copy
import csv
import io
import ipaddress
import json
import os
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import psycopg

from scripts import cs_import_provided as loader

SMALL = loader.Counts(1, 1, 1, 1, 1)
NS = "team4-kan159-import-offline"
OWNER = "12345678-1234-4234-8234-123456789abc"
PG = "sha256:" + "a" * 64
RUNTIME = "sha256:" + "b" * 64
TARGET = loader.Target(
    NS, OWNER, PG, RUNTIME, "7703436257927106598", 19676, "kan159_import_fixture_coder2_20261102"
)
DSN = "postgresql://postgres@127.0.0.1:5432/" + TARGET.db_name


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def make_bundle(root: Path):
    """Return six prepare positional args; expected=SMALL is supplied separately.

    All records are authored here. No supplied/raw/evaluation files are consumed.
    """
    root.mkdir(exist_ok=True)
    bundle, ref = root / "bundle", root / "ref"
    bundle.mkdir(exist_ok=True)
    ref.mkdir(exist_ok=True)
    rows = []
    for key, split in [("T1", "train"), ("T2", "valid"), ("T3", "test")]:
        rows.append(
            {
                "ticket_id": key,
                "data_split": split,
                "received_at_utc": "2026-01-01T00:00:00Z",
                "resolved_at_utc": "2026-01-02T00:00:00Z" if key != "T2" else "",
                "resolution_case_id": {"T1": "C1", "T2": "", "T3": "C3"}[key],
                "channel": "EMAIL",
                "body_language": "en",
                "customer_id": "CUST1",
                "product_code": "TRF-DRY-630",
                "inquiry_subject": "offline input",
                "inquiry_body": "HELDOUT_BODY" if split == "test" else "offline inquiry",
                "fw_version_reported": "",
                "error_code_reported": "",
                "customer_tier": "STANDARD",
                "category_code": "GOLD_CANARY",
                "priority": "GOLD_CANARY",
                "status": "CLOSED",
                "final_reply": "GOLD_CANARY",
                "assigned_dept": "GOLD_CANARY",
            }
        )
    source, cases = root / "tickets.csv", root / "resolution_cases.csv"
    write_csv(source, rows)
    write_csv(bundle / "tickets.train.csv", rows[:1])
    write_csv(bundle / "tickets.valid.csv", rows[1:2])
    write_csv(bundle / "tickets.dev.csv", rows[:2])
    case_rows = []
    for key, tid in [("C1", "T1"), ("C3", "T3")]:
        case_rows.append(
            {
                "case_id": key,
                "ticket_id": tid,
                "product_code": "TRF-DRY-630",
                "fault_id": "FLT-001",
                "fw_version_at_issue": "",
                "symptom": "offline symptom",
                "root_cause": "offline cause",
                "root_cause_type": "HARDWARE",
                "action_taken": "offline historical action",
                "onsite_visit": "false",
                "rma_no": "",
                "fw_fixed_version": "",
                "resolved_by_dept": "GCS_L1",
                "recurrence_prevention": "",
                "hypothesis_correct": "GOLD_CANARY",
                "case_summary": "GOLD_CANARY",
            }
        )
    write_csv(cases, case_rows)
    for file, key, value in [
        ("customers", "customer_id", "CUST1"),
        ("product_master", "product_code", "TRF-DRY-630"),
        ("fault_catalog", "fault_id", "FLT-001"),
        ("departments", "dept_code", "GCS_L1"),
    ]:
        write_csv(ref / (file + ".csv"), [{key: value, "ignored": "GOLD_CANARY"}])
    meta = {
        "provenance": {
            "source_sha256": loader.digest(source.read_bytes()),
            "data_origin": "synthetic",
            "procedure": "cs-dev-split-v1",
            "test_rows_discarded": 1,
            "review_status": "pending",
            "purpose": "development_review_and_rules_only",
        },
        "partitions": {
            split: {
                "path": "tickets." + split + ".csv",
                "rows": 1,
                "sha256": loader.digest((bundle / ("tickets." + split + ".csv")).read_bytes()),
            }
            for split in ("train", "valid")
        },
        "tickets": {
            "path": "tickets.dev.csv",
            "rows": 2,
            "splits": ["train", "valid"],
            "sha256": loader.digest((bundle / "tickets.dev.csv").read_bytes()),
        },
    }
    manifest = bundle / "manifest.json"
    manifest.write_bytes(loader.encoded(meta))
    return (
        manifest,
        loader.digest(manifest.read_bytes()),
        source,
        cases,
        loader.digest(cases.read_bytes()),
        ref,
    )


def change_csv(path, change):
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    change(rows)
    write_csv(path, rows)


def refresh(args):
    manifest, _, source, cases, _, ref = args
    meta = json.loads(manifest.read_bytes())
    meta["provenance"]["source_sha256"] = loader.digest(source.read_bytes())
    for entry in [*meta["partitions"].values(), meta["tickets"]]:
        entry["sha256"] = loader.digest((manifest.parent / entry["path"]).read_bytes())
    manifest.write_bytes(loader.encoded(meta))
    return (
        manifest,
        loader.digest(manifest.read_bytes()),
        source,
        cases,
        loader.digest(cases.read_bytes()),
        ref,
    )


class FakeConnection:
    def __init__(self):
        self.rows = {"tickets": [], "resolution_cases": []}
        self.source_records, self.accounts = 0, 4
        self.queries = []
        self.force_case_error = False
        self.identity = {
            "db_name": TARGET.db_name,
            "db_oid": TARGET.db_oid,
            "current_user": "postgres",
            "session_user": "postgres",
            "address": "127.0.0.1",
            "port": 5432,
            "version": 170006,
            "system_id": TARGET.system_id,
            "recovery": False,
            "now": datetime(2026, 11, 2, tzinfo=UTC),
        }

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    @contextmanager
    def transaction(self):
        before = copy.deepcopy(self.rows)
        try:
            yield
        except BaseException:
            self.rows = before
            raise

    def execute(self, sql, params=()):
        self.queries.append(sql)
        if sql.startswith("SELECT current_database()"):
            row = dict(self.identity)
            if "host(inet_server_addr()) AS address" in sql:
                row["address"] = str(ipaddress.ip_interface(row["address"]).ip)
            return SimpleNamespace(fetchone=lambda: row)
        if "count(*) AS n FROM identity.users" in sql:
            return SimpleNamespace(fetchone=lambda: {"n": self.accounts})
        if sql.startswith(("SET LOCAL ROLE", "LOCK TABLE")):
            return None
        if "count(*) AS n FROM cs.ticket_source_records" in sql:
            return SimpleNamespace(fetchone=lambda: {"n": self.source_records})
        table = "resolution_cases" if "cs.resolution_cases" in sql else "tickets"
        if "count(*) AS n" in sql:
            n = sum((r["source"], r["source_instance"]) != params for r in self.rows[table])
            return SimpleNamespace(fetchone=lambda: {"n": n})
        if sql.startswith("SELECT "):
            return SimpleNamespace(fetchall=lambda: copy.deepcopy(self.rows[table]))
        if sql.startswith("INSERT INTO "):
            if table == "resolution_cases" and self.force_case_error:
                raise psycopg.IntegrityError("offline private constraint detail")
            cols = sql.split("(", 1)[1].split(")", 1)[0].split(", ")
            row = dict(zip(cols, params, strict=True))
            if table == "resolution_cases":
                assert row["ticket_id"] in {t["id"] for t in self.rows["tickets"]}
            self.rows[table].append(row)
            return None
        raise AssertionError("Unexpected SQL")


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = make_bundle(self.root / "input")
        self.state = self.root / "target"
        self.state.mkdir()
        (self.state / "settings.json").write_bytes(
            loader.encoded(
                {
                    "namespace": NS,
                    "owner": OWNER,
                    "setup_complete": True,
                    "source_sha": loader.BASE_SOURCE,
                    "images": {"pg": PG, "runtime": RUNTIME},
                    "web_origin": "https://localhost:39443",
                    "ports": {"web": 39443},
                }
            )
        )
        self.env = patch.dict(os.environ, {"ENVIRONMENT": "local", "DATABASE_URL": DSN}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.connect = patch.object(
            loader.psycopg, "connect", side_effect=AssertionError("No real DB")
        )
        self.connect_mock = self.connect.start()
        self.addCleanup(self.connect.stop)

    def plan(self):
        return loader.prepare(*self.args, expected=SMALL)

    def cli(self):
        m, mh, source, cases, ch, ref = self.args
        return [
            "--manifest",
            str(m),
            "--manifest-sha256",
            mh,
            "--source-tickets",
            str(source),
            "--cases",
            str(cases),
            "--cases-sha256",
            ch,
            "--ref",
            str(ref),
        ]

    def apply_cli(self):
        return self.cli() + [
            "--apply",
            "--target-state",
            str(self.state),
            "--namespace",
            NS,
            "--expected-owner",
            OWNER,
            "--expected-pg-image",
            PG,
            "--expected-runtime-image",
            RUNTIME,
            "--expected-system-id",
            TARGET.system_id,
            "--expected-db-oid",
            str(TARGET.db_oid),
            "--expected-db-name",
            TARGET.db_name,
            "--receipt",
            str(self.root / "receipt.json"),
        ]

    def test_whitelist_heldout_and_field_assumptions(self):
        plan = self.plan()
        self.assertEqual((len(plan.tickets), len(plan.cases)), (2, 1))
        data = str(plan.tickets + plan.cases)
        for value in ("GOLD_CANARY", "HELDOUT_BODY", "CLOSED", "category_code", "case_summary"):
            self.assertNotIn(value, data)
        self.assertEqual({r["external_id"] for r in plan.tickets}, {"T1", "T2"})
        self.assertEqual(plan.tickets[0]["received_at"], plan.tickets[0]["known_at"])
        self.assertEqual(plan.cases[0]["resolved_at"], plan.cases[0]["known_at"])
        self.assertEqual(plan.cases[0]["derivation_kind"], "PROVIDED")
        self.assertIsNone(plan.cases[0]["dept_result_id"])
        self.assertEqual(plan.cases[0]["ticket_id"], plan.tickets[0]["id"])
        self.assertEqual(len(plan.summary()["assumptions"]), 3)
        self.connect_mock.assert_not_called()

    def test_hash_tamper_fails_without_connect_or_values(self):
        for index in (0, 2, 3):
            with self.subTest(index=index):
                p = self.args[index]
                original = p.read_bytes()
                p.write_bytes(original + b"\n")
                with self.assertRaisesRegex(loader.Invalid, "IMPORT_HASH_MISMATCH"):
                    self.plan()
                p.write_bytes(original)
        self.connect_mock.assert_not_called()

    def test_partition_hash_tamper_is_not_normalized(self):
        p = self.args[0].parent / "tickets.train.csv"
        p.write_bytes(p.read_bytes() + b"\n")
        with self.assertRaisesRegex(loader.Invalid, "IMPORT_HASH_MISMATCH"):
            self.plan()

    def test_heldout_partition_membership_rejected(self):
        p = self.args[0].parent / "tickets.train.csv"
        change_csv(p, lambda rows: rows[0].update(ticket_id="T3", data_split="test"))
        self.args = refresh(self.args)
        with self.assertRaisesRegex(loader.Invalid, "IMPORT_SPLIT_INVALID"):
            self.plan()

    def test_heldout_case_values_are_not_validated_or_copied(self):
        change_csv(
            self.args[3],
            lambda rows: rows[1].update(
                fault_id="HELDOUT_VALUE", root_cause_type="HELDOUT_VALUE", symptom="HELDOUT_VALUE"
            ),
        )
        self.args = refresh(self.args)
        plan = self.plan()
        self.assertNotIn("HELDOUT_VALUE", str(plan.tickets + plan.cases))
        self.assertEqual(len(plan.cases), 1)

    def test_duplicate_source_and_case_ids_fail_closed(self):
        for mode in ("source", "case"):
            with self.subTest(mode=mode):
                args = make_bundle(self.root / ("duplicate-" + mode))
                if mode == "source":
                    change_csv(args[2], lambda rows: rows[1].update(ticket_id="T1"))
                else:
                    change_csv(args[3], lambda rows: rows[1].update(case_id="C1"))
                with self.assertRaisesRegex(loader.Invalid, "IMPORT_(SOURCE|CASE)_IDS_INVALID"):
                    loader.prepare(*refresh(args), expected=SMALL)

    def test_combined_file_cannot_hide_extra_heldout_rows(self):
        combined = self.args[0].parent / "tickets.dev.csv"
        change_csv(
            combined, lambda rows: rows.append({**rows[0], "ticket_id": "T3", "data_split": "test"})
        )
        self.args = refresh(self.args)
        with self.assertRaisesRegex(loader.Invalid, "IMPORT_COMBINED_INVALID"):
            self.plan()

    def test_unknown_case_is_not_silently_counted_as_heldout(self):
        change_csv(self.args[3], lambda rows: rows[1].update(ticket_id="UNKNOWN"))
        self.args = refresh(self.args)
        with self.assertRaisesRegex(loader.Invalid, "IMPORT_CASE_TICKET_INVALID"):
            self.plan()

    def test_unknown_fks_and_invalid_enums_fail_before_db(self):
        cases = self.args[3]
        original = cases.read_bytes()
        for field, value in [
            ("product_code", "OTHER"),
            ("fault_id", "FLT-999"),
            ("resolved_by_dept", "UNKNOWN"),
            ("root_cause_type", "GOLD_CANARY"),
            ("onsite_visit", "GOLD_CANARY"),
        ]:
            with self.subTest(field=field):
                cases.write_bytes(original)
                change_csv(cases, lambda rows: rows[0].update({field: value}))
                self.args = refresh(self.args)
                with self.assertRaises(loader.Invalid):
                    self.plan()
        self.connect_mock.assert_not_called()

    def test_missing_early_or_naive_resolution_time_rejected(self):
        p = self.args[0].parent / "tickets.train.csv"
        original = p.read_bytes()
        for value in ("", "2025-12-31T00:00:00Z", "2026-01-02T00:00:00"):
            with self.subTest(value=value):
                p.write_bytes(original)
                change_csv(p, lambda rows: rows[0].update(resolved_at_utc=value))
                self.args = refresh(self.args)
                with self.assertRaises(loader.Invalid):
                    self.plan()
        self.connect_mock.assert_not_called()

    def test_manifest_escape_and_duplicate_ids_rejected(self):
        m = json.loads(self.args[0].read_bytes())
        m["partitions"]["train"]["path"] = "../tickets.csv"
        self.args[0].write_bytes(loader.encoded(m))
        self.args = (self.args[0], loader.digest(self.args[0].read_bytes()), *self.args[2:])
        with self.assertRaisesRegex(loader.Invalid, "IMPORT_BUNDLE_PATH_INVALID"):
            self.plan()

    def test_exact_local_target_and_namespace_proof(self):
        loader.validate_target(self.state, TARGET, DSN)
        for dsn in (
            DSN.replace("127.0.0.1", "sql.example.com"),
            DSN.replace("5432", "5433"),
            DSN.replace("postgres@", "cs_app@"),
            DSN + "?options=-c%20role=other",
        ):
            with self.subTest(dsn=dsn), self.assertRaises(loader.Invalid):
                loader.validate_target(self.state, TARGET, dsn)
        cfg = json.loads((self.state / "settings.json").read_bytes())
        for key, value in [("source_sha", "0" * 40), ("owner", "other-owner")]:
            modified = {**cfg, key: value}
            (self.state / "settings.json").write_bytes(loader.encoded(modified))
            with self.assertRaises(loader.Invalid):
                loader.validate_target(self.state, TARGET, DSN)

    def test_actual_sql_shape_idempotency_and_private_metadata_receipt(self):
        plan = self.plan()
        conn = FakeConnection()
        first = loader.apply_plan(conn, plan, TARGET)
        second = loader.apply_plan(conn, plan, TARGET)
        self.assertEqual(
            first,
            {
                "tickets": {"inserted": 2, "skipped": 0},
                "resolution_cases": {"inserted": 1, "skipped": 0},
            },
        )
        self.assertEqual(
            second,
            {
                "tickets": {"inserted": 0, "skipped": 2},
                "resolution_cases": {"inserted": 0, "skipped": 1},
            },
        )
        self.assertTrue(any(q == "SET LOCAL ROLE cs_migrator" for q in conn.queries))
        inserts = [q for q in conn.queries if q.startswith("INSERT")]
        self.assertEqual(len(inserts), 3)
        self.assertTrue(
            all(
                q.startswith(("INSERT INTO cs.tickets ", "INSERT INTO cs.resolution_cases "))
                for q in inserts
            )
        )
        self.assertFalse(any("tft." in q or "runtime.jobs" in q for q in conn.queries))
        receipt = loader.encoded(loader.receipt(plan, TARGET))
        self.assertNotIn(b"GOLD_CANARY", receipt)
        self.assertNotIn(b"offline inquiry", receipt)
        self.assertNotIn(b"offline historical action", receipt)

    def test_late_database_failure_rolls_back_inserted_tickets(self):
        conn = FakeConnection()
        conn.force_case_error = True
        with self.assertRaises(psycopg.IntegrityError):
            loader.apply_plan(conn, self.plan(), TARGET)
        self.assertEqual(conn.rows, {"tickets": [], "resolution_cases": []})
        self.assertTrue(any(q.startswith("INSERT INTO cs.tickets") for q in conn.queries))

    def test_case_conflict_rolls_back_new_ticket_without_overwriting(self):
        plan = self.plan()
        conn = FakeConnection()
        conn.rows["tickets"] = copy.deepcopy(plan.tickets[:1])
        conn.rows["resolution_cases"] = copy.deepcopy(plan.cases)
        conn.rows["resolution_cases"][0]["root_cause"] = "human change"
        before = copy.deepcopy(conn.rows)
        with self.assertRaisesRegex(loader.Invalid, "IMPORT_EXISTING_CONFLICT"):
            loader.apply_plan(conn, plan, TARGET)
        self.assertEqual(conn.rows, before)

    def test_foreign_sources_or_source_records_stop_without_inserts(self):
        plan = self.plan()
        for kind in ("foreign", "gold"):
            conn = FakeConnection()
            if kind == "foreign":
                conn.rows["tickets"] = [{**plan.tickets[0], "source": "manual"}]
            else:
                conn.source_records = 1
            with self.assertRaises(loader.Invalid):
                loader.apply_plan(conn, plan, TARGET)
            self.assertFalse(any(q.startswith("INSERT") for q in conn.queries))

    def test_server_identity_failures_and_future_times_never_insert(self):
        for key, value in [
            ("db_oid", 16384),
            ("system_id", "other"),
            ("port", 55434),
            ("current_user", "cs_app"),
            ("version", 160000),
        ]:
            conn = FakeConnection()
            conn.identity[key] = value
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(loader.Invalid, "IMPORT_SERVER_IDENTITY"),
            ):
                loader.apply_plan(conn, self.plan(), TARGET)
            self.assertFalse(any(q.startswith("INSERT") for q in conn.queries))
        conn = FakeConnection()
        conn.identity["now"] = datetime(2025, 1, 1, tzinfo=UTC)
        with self.assertRaisesRegex(loader.Invalid, "IMPORT_FUTURE_KNOWN_AT"):
            loader.apply_plan(conn, self.plan(), TARGET)

    def test_inet_server_mask_uses_host_normalization_with_exact_ipv4_guard(self):
        conn = FakeConnection()
        conn.identity["address"] = "127.0.0.1/32"
        result = loader.apply_plan(conn, self.plan(), TARGET)
        self.assertEqual(result["tickets"]["inserted"], 2)
        self.assertIn("host(inet_server_addr()) AS address", conn.queries[0])
        for address in ("127.0.0.2/32", "::1/128"):
            conn = FakeConnection()
            conn.identity["address"] = address
            with (
                self.subTest(address=address),
                self.assertRaisesRegex(loader.Invalid, "IMPORT_SERVER_IDENTITY_MISMATCH"),
            ):
                loader.apply_plan(conn, self.plan(), TARGET)
            self.assertFalse(any(q.startswith("INSERT") for q in conn.queries))

    def test_cli_confirmation_is_preconnect_and_production_counts_are_fixed(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(loader.main(self.cli()), 1)  # Small bundle is not production scope.
        self.connect_mock.assert_not_called()
        self.assertNotIn("GOLD_CANARY", out.getvalue())
        out = io.StringIO()
        with patch.object(loader, "prepare", return_value=self.plan()), redirect_stdout(out):
            self.assertEqual(loader.main(self.apply_cli()), 1)
        result = json.loads(out.getvalue())
        self.assertEqual(result["target_sha256"], TARGET.token())
        self.assertEqual(result["code"], "IMPORT_CONFIRM_TARGET_REQUIRED")
        self.connect_mock.assert_not_called()

    def test_apply_cli_receipt_is_0600_metadata_only_and_rerun_stable(self):
        conn, plan = FakeConnection(), self.plan()
        args = self.apply_cli() + ["--confirm-target", TARGET.token()]
        out = io.StringIO()
        with (
            patch.object(loader, "prepare", return_value=plan),
            patch.object(loader.psycopg, "connect", return_value=conn),
            redirect_stdout(out),
        ):
            self.assertEqual(loader.main(args), 0)
            first = (self.root / "receipt.json").read_bytes()
            self.assertEqual(loader.main(args), 0)
        self.assertEqual(first, (self.root / "receipt.json").read_bytes())
        self.assertEqual((self.root / "receipt.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(json.loads(first)["tickets"]), 2)
        for value in ("GOLD_CANARY", "offline inquiry", "T1", "CUST1"):
            self.assertNotIn(value, out.getvalue())

    def test_receipt_cannot_overwrite_input_and_failure_diagnostics_have_no_values(self):
        plan = self.plan()
        with self.assertRaisesRegex(loader.Invalid, "IMPORT_RECEIPT_PATH_INVALID"):
            loader.receipt_path(self.args[3], plan, b"metadata")
        change_csv(
            self.args[0].parent / "tickets.train.csv",
            lambda rows: rows[0].update(channel="GOLD_CANARY"),
        )
        self.args = refresh(self.args)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(loader.main(self.cli()), 1)
        self.assertNotIn("GOLD_CANARY", out.getvalue())
        self.connect_mock.assert_not_called()

    def test_receipt_race_never_overwrites_or_reports_success_after_commit(self):
        path = self.root / "receipt.json"

        class ChangedReceipt(FakeConnection):
            def __exit__(self, *args):
                path.write_bytes(b"other metadata")
                return False

        conn, plan = ChangedReceipt(), self.plan()
        out = io.StringIO()
        with (
            patch.object(loader, "prepare", return_value=plan),
            patch.object(loader.psycopg, "connect", return_value=conn),
            redirect_stdout(out),
        ):
            self.assertEqual(
                loader.main(self.apply_cli() + ["--confirm-target", TARGET.token()]), 1
            )
        result = json.loads(out.getvalue())
        self.assertTrue(result["committed"])
        self.assertEqual(result["code"], "IMPORT_RECEIPT_CONFLICT")
        self.assertEqual(path.read_bytes(), b"other metadata")


if __name__ == "__main__":
    unittest.main()
