"""KAN-184 seed CLI 안전성. 가짜 연결만 사용하며 DB·운영 명령은 실행하지 않는다."""

import contextlib
import io
import json
import unittest
from unittest import mock

import psycopg

from backend.api.tft.partners.loader import load_profiles
from scripts import rfp_seed_partner_profiles as seed

DSN = "postgresql://tft_app:fake-secret@shared.example.invalid/team4"


class SeedTest(unittest.TestCase):
    def setUp(self):
        self.profiles = load_profiles()[:1]

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = seed.main(list(args))
        self.assertNotIn("fake-secret", out.getvalue() + err.getvalue())
        self.assertNotIn(DSN, out.getvalue() + err.getvalue())
        return result, out.getvalue(), err.getvalue()

    def test_default_and_explicit_dry_run_never_connect_even_with_shared_dsn(self):
        for args in ((), ("--dry-run",)):
            with (
                self.subTest(args=args),
                mock.patch.dict(seed.os.environ, {"DATABASE_URL": DSN}),
                mock.patch.object(seed, "load_profiles", return_value=self.profiles),
                mock.patch.object(seed.psycopg, "connect") as connect,
            ):
                result, out, _ = self.run_main(*args)
                self.assertEqual(result, 0)
                self.assertEqual(
                    json.loads(out.splitlines()[-1]),
                    {"mode": "dry-run", "profiles": 1, "data_origin": "synthetic"},
                )
                self.assertIn("PLANNED", out)
                connect.assert_not_called()

    def test_shared_apply_and_legacy_execute_have_identical_explicit_guards(self):
        for flag in ("--apply", "--execute"):
            with (
                self.subTest(flag=flag),
                mock.patch.dict(seed.os.environ, {"DATABASE_URL": DSN}),
                mock.patch.object(seed, "load_profiles", return_value=self.profiles),
                mock.patch.object(seed.psycopg, "connect") as connect,
                mock.patch.object(seed, "insert", return_value=1) as insert,
                mock.patch.object(seed, "compare", return_value=[("P-A", 1, "SAME")]),
            ):
                conn = connect.return_value.__enter__.return_value
                result, out, _ = self.run_main(flag)
                self.assertEqual(result, 0)
                insert.assert_called_once_with(conn, self.profiles)
                connect.assert_called_once_with(DSN, row_factory=seed.dict_row)
                self.assertEqual(json.loads(out.splitlines()[-1])["inserted"], 1)

    def test_explicit_check_is_read_only_and_reports_drift_without_insert(self):
        with (
            mock.patch.dict(seed.os.environ, {"DATABASE_URL": DSN}),
            mock.patch.object(seed, "load_profiles", return_value=self.profiles),
            mock.patch.object(seed.psycopg, "connect") as connect,
            mock.patch.object(seed, "insert") as insert,
            mock.patch.object(seed, "compare", return_value=[("P-A", 1, "DIFFERENT")]),
        ):
            conn = connect.return_value.__enter__.return_value
            result, out, _ = self.run_main("--check")
            self.assertEqual(result, 1)
            self.assertEqual(json.loads(out.splitlines()[-1])["DIFFERENT"], 1)
            conn.execute.assert_called_once_with("SET TRANSACTION READ ONLY")
            insert.assert_not_called()

    def test_conflicting_modes_exit_before_loading_or_connecting(self):
        for args in (
            ("--check", "--apply"),
            ("--check", "--execute"),
            ("--dry-run", "--apply"),
            ("--dry-run", "--check"),
        ):
            with (
                self.subTest(args=args),
                mock.patch.object(seed, "load_profiles") as load,
                mock.patch.object(seed.psycopg, "connect") as connect,
            ):
                with self.assertRaises(SystemExit) as error:
                    self.run_main(*args)
                self.assertEqual(error.exception.code, 2)
                load.assert_not_called()
                connect.assert_not_called()

    def test_non_synthetic_is_rejected_before_connect_in_all_modes(self):
        for field in ("data_origin", "profile_source"):
            for mode in ((), ("--apply",), ("--execute",), ("--check",)):
                with (
                    self.subTest(field=field, mode=mode),
                    mock.patch.dict(seed.os.environ, {"DATABASE_URL": DSN}),
                    mock.patch.object(
                        seed,
                        "load_profiles",
                        return_value=[self.profiles[0].model_copy(update={field: "real"})],
                    ),
                    mock.patch.object(seed.psycopg, "connect") as connect,
                ):
                    self.assertEqual(self.run_main(*mode)[0], 1)
                    connect.assert_not_called()

    def test_explicit_db_modes_require_dsn_but_default_does_not(self):
        with (
            mock.patch.dict(seed.os.environ, {}, clear=True),
            mock.patch.object(seed, "load_profiles", return_value=self.profiles),
            mock.patch.object(seed.psycopg, "connect") as connect,
        ):
            self.assertEqual(self.run_main()[0], 0)
            for flag in ("--apply", "--check"):
                code, _, err = self.run_main(flag)
                self.assertEqual(code, 1)
                self.assertIn("TFT_SEED_NEEDS_DB", err)
            connect.assert_not_called()

    def test_invalid_file_and_connection_errors_do_not_expose_details(self):
        with (
            mock.patch.object(seed, "load_profiles", side_effect=ValueError("fake-secret")),
            mock.patch.object(seed.psycopg, "connect") as connect,
        ):
            self.assertEqual(self.run_main()[0], 1)
            connect.assert_not_called()
        with (
            mock.patch.dict(seed.os.environ, {"DATABASE_URL": DSN}),
            mock.patch.object(seed, "load_profiles", return_value=self.profiles),
            mock.patch.object(
                seed.psycopg, "connect", side_effect=psycopg.OperationalError("fake-secret")
            ),
        ):
            self.assertEqual(self.run_main("--apply")[0], 1)

    def test_insert_checks_every_profile_before_transaction_and_never_updates(self):
        conn = mock.MagicMock()
        invalid = self.profiles + [self.profiles[0].model_copy(update={"data_origin": "real"})]
        with self.assertRaises(ValueError):
            seed.insert(conn, invalid)
        conn.transaction.assert_not_called()
        conn.execute.return_value.rowcount = 0
        self.assertEqual(seed.insert(conn, self.profiles), 0)
        statement = str(conn.execute.call_args.args[0])
        self.assertIn("ON CONFLICT DO NOTHING", statement)
        self.assertNotIn("UPDATE", statement)
        self.assertNotIn("DELETE", statement)
