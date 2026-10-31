"""KAN-157 이전 앱 호환 검사에서 DB·git 없이 검사할 수 있는 부분."""

import unittest

from scripts import compat_check

BASE = {"cs.tickets", "runtime.jobs", "evaluation.admission_receipts"}
ROLES = {"cs_app", "platform_app", "tft_app"}


def reasons(sql: str) -> list[tuple[str, str, str]]:
    return [
        (f.level, f.table, f.reason)
        for f in compat_check.check_statements("x.sql", sql, BASE, ROLES)
    ]


class FileChangeTest(unittest.TestCase):
    def test_only_added_migration_files_are_allowed(self):
        """공용 DB는 적용한 파일을 다시 실행하지 않으므로 고치거나 지우거나 옮긴 파일은 막는다."""
        found = compat_check.classify_file_changes(
            "A\tinfra/migrations/cs/new.sql\n"
            "M\tinfra/migrations/cs/old.sql\n"
            "D\tinfra/migrations/tft/gone.sql\n"
            "R100\tinfra/migrations/cs/a.sql\tinfra/migrations/cs/b.sql\n"
        )
        self.assertEqual(
            [(f.level, f.file) for f in found],
            [
                ("BLOCK", "infra/migrations/cs/old.sql"),
                ("BLOCK", "infra/migrations/tft/gone.sql"),
                ("BLOCK", "infra/migrations/cs/b.sql"),
            ],
        )


class StatementTest(unittest.TestCase):
    def test_breaking_changes_on_previous_tables_block(self):
        """이전 표에 열 삭제·이름 변경·형 변경·NOT NULL 강제·표 삭제를 하면 막는다."""
        self.assertEqual(
            reasons(
                "ALTER TABLE cs.tickets DROP COLUMN subject;"
                "ALTER TABLE cs.tickets RENAME COLUMN body TO text;"
                "ALTER TABLE runtime.jobs ALTER COLUMN state TYPE varchar(20);"
                "ALTER TABLE runtime.jobs ALTER COLUMN owner SET NOT NULL;"
                "DROP TABLE IF EXISTS cs.tickets;"
            ),
            [
                ("BLOCK", "cs.tickets", "DROP COLUMN"),
                ("BLOCK", "cs.tickets", "RENAME"),
                ("BLOCK", "runtime.jobs", "ALTER COLUMN TYPE"),
                ("BLOCK", "runtime.jobs", "SET NOT NULL"),
                ("BLOCK", "cs.tickets", "DROP TABLE"),
            ],
        )

    def test_new_not_null_columns_need_a_default(self):
        """이전 코드의 INSERT는 새 열을 모르므로 DEFAULT 없는 NOT NULL 열만 막는다. CHECK의 IS NOT NULL은 열 제약이 아니다."""
        self.assertEqual(
            reasons(
                "ALTER TABLE evaluation.admission_receipts\n"
                "  ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT 'ACCEPT',\n"
                "  ADD COLUMN IF NOT EXISTS import_ref text CONSTRAINT c CHECK ((import_ref IS NOT NULL)),\n"
                "  ADD COLUMN owner text NOT NULL;"
            ),
            [("BLOCK", "evaluation.admission_receipts", "ADD COLUMN NOT NULL without DEFAULT")],
        )

    def test_new_tables_and_comments_are_ignored(self):
        """새 표에는 무엇을 해도 이전 코드와 상관없고, 주석 안의 DROP은 문장이 아니다."""
        self.assertEqual(
            reasons(
                "-- ALTER TABLE cs.tickets DROP COLUMN subject;\n"
                "CREATE TABLE cs.new_table (id int);\n"
                "ALTER TABLE cs.new_table DROP COLUMN id;\n"
                "DROP TABLE cs.new_table;"
            ),
            [],
        )

    def test_constraint_drops_and_revokes_from_previous_roles_need_review(self):
        """제약 삭제(넓히는지 좁히는지)와 이전 앱 역할의 권한 회수는 실행 검사로 확인한다. 새 역할의 회수는 보지 않는다."""
        self.assertEqual(
            reasons(
                "DO $$ BEGIN ALTER TABLE runtime.jobs DROP CONSTRAINT IF EXISTS jobs_kind_check; END $$;"
                "REVOKE DELETE, TRUNCATE ON cs.tickets FROM cs_app;"
                "REVOKE ALL ON ALL TABLES IN SCHEMA cs FROM cs_worker;"
                "REVOKE UPDATE ON ALL TABLES IN SCHEMA runtime FROM platform_app;"
            ),
            [
                ("REVIEW", "runtime.jobs", "DROP CONSTRAINT"),
                ("REVIEW", "cs.tickets", "REVOKE FROM cs_app"),
                ("REVIEW", "runtime.jobs", "REVOKE FROM platform_app"),
            ],
        )


class MoreStatementTest(unittest.TestCase):
    def test_default_removal_schema_moves_and_columns_without_keyword_block(self):
        """DEFAULT 삭제·스키마 이동, COLUMN 키워드 없는 NOT NULL 열 추가도 이전 코드를 깬다. 블록 주석은 문장이 아니다."""
        self.assertEqual(
            reasons(
                "/* ALTER TABLE cs.tickets DROP COLUMN body; */"
                "ALTER TABLE cs.tickets ALTER COLUMN status DROP DEFAULT;"
                "ALTER TABLE runtime.jobs SET SCHEMA archive;"
                "ALTER TABLE cs.tickets ADD owner text NOT NULL;"
            ),
            [
                ("BLOCK", "cs.tickets", "DROP DEFAULT"),
                ("BLOCK", "runtime.jobs", "SET SCHEMA"),
                ("BLOCK", "cs.tickets", "ADD COLUMN NOT NULL without DEFAULT"),
            ],
        )

    def test_new_constraints_security_and_object_drops_need_review(self):
        """제약 추가는 이전 코드의 쓰기를 거절할 수 있고, 행 보안·인덱스·타입·이전 함수 변경도 확인 대상이다."""
        found = [
            (f.level, f.table, f.reason)
            for f in compat_check.check_statements(
                "x.sql",
                "ALTER TABLE cs.tickets ADD CONSTRAINT t_ok CHECK (status <> '');"
                "ALTER TABLE cs.tickets VALIDATE CONSTRAINT t_ok;"
                "ALTER TABLE runtime.jobs ENABLE ROW LEVEL SECURITY;"
                "CREATE POLICY p ON runtime.jobs USING (true);"
                "DROP INDEX IF EXISTS cs.tickets_unique;"
                "ALTER TYPE cs.state RENAME VALUE 'A' TO 'B';"
                "CREATE OR REPLACE FUNCTION runtime.claim() RETURNS void AS $$ SELECT 1 $$ LANGUAGE sql;"
                "CREATE FUNCTION cs.new_one() RETURNS void AS $$ SELECT 1 $$ LANGUAGE sql;",
                BASE,
                ROLES,
                frozenset({"runtime.claim"}),
            )
        ]
        self.assertEqual(
            found,
            [
                ("REVIEW", "cs.tickets", "ADD CONSTRAINT"),
                ("REVIEW", "cs.tickets", "VALIDATE CONSTRAINT"),
                ("REVIEW", "runtime.jobs", "ROW LEVEL SECURITY"),
                ("REVIEW", "runtime.jobs", "POLICY"),
                ("REVIEW", "cs.tickets_unique", "DROP INDEX"),
                ("REVIEW", "cs.state", "ALTER TYPE"),
                ("REVIEW", "runtime.claim", "FUNCTION REPLACED"),
            ],
        )

    def test_revokes_on_schemas_functions_and_sequences_need_review(self):
        """표가 아닌 대상의 권한도 이전 앱 역할에서 회수하면 확인한다."""
        self.assertEqual(
            reasons(
                "REVOKE USAGE ON SCHEMA runtime FROM cs_app, tft_app;"
                "REVOKE EXECUTE ON FUNCTION runtime.claim() FROM platform_app;"
                "REVOKE USAGE ON ALL SEQUENCES IN SCHEMA cs FROM cs_app;"
                "REVOKE USAGE ON SCHEMA notify FROM cs_worker;"
            ),
            [
                ("REVIEW", "schema runtime", "REVOKE FROM cs_app,tft_app"),
                ("REVIEW", "function runtime.claim()", "REVOKE FROM platform_app"),
                ("REVIEW", "all sequences in schema cs", "REVOKE FROM cs_app"),
            ],
        )


class EnvTest(unittest.TestCase):
    def test_tests_get_the_database_but_no_secrets_or_other_environments(self):
        """이전 리비전 검사가 HEAD의 가상 환경이나 셸의 키를 쓰지 않게 한다."""
        env = compat_check.test_env(
            "db",
            {
                "PATH": "/bin",
                "GEMINI_API_KEY": "k",
                "UV_PROJECT_ENVIRONMENT": "/x",
                "VIRTUAL_ENV": "/y",
                "PGPASSWORD": "p",
            },
        )
        self.assertEqual(env, {"PATH": "/bin", "AUTH_TEST_DATABASE_URL": "db"})


class RoleAndTableTest(unittest.TestCase):
    def test_previous_tables_and_roles_are_read_from_create_statements(self):
        """표는 CREATE TABLE/VIEW에서, 앱 역할은 CREATE ROLE에서 읽고 마이그레이션 역할은 뺀다."""
        sql = (
            "CREATE TABLE IF NOT EXISTS cs.tickets (id int);\n"
            "CREATE VIEW cs.latest AS SELECT 1;\n"
            "CREATE ROLE cs_app LOGIN;\nCREATE ROLE cs_migrator NOLOGIN;\n"
            "EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', mapping.schema_name, mapping.app_role);"
        )
        self.assertEqual(compat_check.created_tables(sql), {"cs.tickets", "cs.latest"})
        self.assertEqual(compat_check.created_roles(sql), {"cs_app"})
        self.assertEqual(
            compat_check.created_functions(
                "CREATE OR REPLACE FUNCTION runtime.claim() RETURNS int"
            ),
            {"runtime.claim"},
        )


class RunGuardTest(unittest.TestCase):
    def test_only_the_loopback_test_database_is_accepted(self):
        compat_check.check_db_url("postgresql://postgres:x@127.0.0.1:55433/kan77_auth_test")
        for url in (
            "postgresql://postgres:x@10.0.0.5:5432/kan77_auth_test",
            "postgresql://postgres:x@127.0.0.1:5433/team4",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                compat_check.check_db_url(url)

    def test_the_db_test_list_comes_from_the_ci_workflow(self):
        workflow = "run: |\n  pytest -q tests/platform/test_authorization_store.py tests/cs/a.py\n"
        self.assertEqual(
            compat_check.db_test_list(workflow),
            ["tests/platform/test_authorization_store.py", "tests/cs/a.py"],
        )
        with self.assertRaises(ValueError):
            compat_check.db_test_list("pytest -q tests")

    def test_every_migration_is_applied_by_some_test_setup(self):
        """실행 검사는 CI 검사 준비로 마이그레이션을 적용하므로, 빠진 파일이 없어야 결과를 믿을 수 있다."""
        self.assertEqual(compat_check.unapplied_migrations(), [])

    def test_a_platform_migration_missing_from_the_setup_is_reported(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder in ("cs", "platform"):
                (root / "infra/migrations" / folder).mkdir(parents=True)
            (root / "infra/migrations/cs/1_a.sql").write_text("")
            (root / "infra/migrations/platform/1_b.sql").write_text("")
            (root / "infra/migrations/platform/2_c.sql").write_text("")
            (root / "tests").mkdir()
            (root / "tests/conftest.py").write_text(
                'M = Path("infra/migrations/cs").glob("*.sql")\nF = "1_b.sql"\n'
            )
            self.assertEqual(
                compat_check.unapplied_migrations(root), ["infra/migrations/platform/2_c.sql"]
            )

    def test_uncommitted_inputs_stop_the_run(self):
        """실행 검사는 작업 트리를 읽으므로 마이그레이션·검사·코드에 커밋하지 않은 변경이 있으면 멈춘다."""
        porcelain = (
            " M infra/migrations/cs/new.sql\n"
            "?? tests/cs/test_x.py\n"
            "R  backend/a.py -> backend/b.py\n"
            "?? 2026-10-29-notes.txt\n"
            " M docs/release-runbook.md\n"
        )
        self.assertEqual(
            compat_check.dirty_inputs(porcelain),
            ["infra/migrations/cs/new.sql", "tests/cs/test_x.py", "backend/b.py"],
        )

    def test_snapshot_diff_reports_both_directions(self):
        """이전 검사 준비가 옛 권한을 다시 주면 added에, 무언가 지우면 removed에 나온다."""
        self.assertEqual(
            compat_check.snapshot_diff(["a", "b"], ["b", "c"]),
            {"removed": ["a"], "added": ["c"]},
        )


if __name__ == "__main__":
    unittest.main()
