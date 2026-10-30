"""Build boundaries, shared impact and rename/deletion safety."""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("changes", Path(__file__).parents[1] / "changes.py")
changes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(changes)


class ChangeTests(unittest.TestCase):
    def test_cs_engine_selects_only_its_consumers_including_runtime_markdown(self):
        """C/S 전용 엔진의 검사·배포와 평가 소비자를 선택한다.

        Args:
            없음.

        Returns:
            None: 코드·초기화·런타임 문서 모두 같은 소유 경계를 따름을 확인한다.
        """
        for path in (
            "backend/engine/cs/classification.py",
            "backend/engine/cs/__init__.py",
            "backend/engine/cs/prompt.md",
        ):
            with self.subTest(path=path):
                result = changes.classify([path])
                self.assertEqual(result["services"], ["cs", "platform"])
                self.assertEqual(result["backends"], ["cs", "platform"])
                self.assertTrue(result["evaluation"])
                self.assertFalse(result["infrastructure"])

    def test_shared_or_unknown_engine_keeps_both_consumers(self):
        """공통·미분류 경로 및 C/S 경로 밖 이동은 보수적으로 검사한다.

        Args:
            없음.

        Returns:
            None: 삭제·추가 경로의 합집합과 공통 영향 범위를 확인한다.
        """
        for path in (
            "backend/engine/__init__.py",
            "backend/engine/search.py",
            "backend/engine/new_domain/adapter.py",
            "backend/engine/cs_extra/adapter.py",
            "backend/agent/cs/prompts/classify.md",
        ):
            with self.subTest(path=path):
                result = changes.classify(["backend/engine/cs/adapter.py", path])
                self.assertEqual(result["services"], ["cs", "tft", "platform"])
                self.assertEqual(result["backends"], ["cs", "tft", "platform"])
                self.assertTrue(result["evaluation"])

    def test_collaboration_consumers_run_only_with_infrastructure_inputs(self):
        """협업 검사의 입력과 일반 업무 변경을 구분한다.

        Args:
            없음.

        Returns:
            None: 협업 입력·실행 환경 변경은 선택하고 무관한 경로는 생략한다.
        """
        for path in (
            "scripts/dev.py",
            "scripts/tests/test_dev.py",
            "scripts/README.md",
            ".github/scripts/pr_metadata.cjs",
            ".github/tests/ai_review.test.cjs",
            ".github/workflows/collaboration-checks.yml",
            ".github/pull_request_template.md",
            "infra/local/init.sh",
            "pyproject.toml",
            "uv.lock",
            "new-runtime/handler.py",
        ):
            with self.subTest(path=path):
                self.assertTrue(changes.classify([path])["infrastructure"])
        for path in (
            "docs/team-start.md",
            "frontend/src/App.tsx",
            "backend/api/cs/workflow/routes.py",
            "tests/cs/test_workflow.py",
            "eval/smoke.py",
        ):
            with self.subTest(path=path):
                self.assertFalse(changes.classify([path])["infrastructure"])

    def test_main_compares_against_successful_ci_not_a_failed_previous_commit(self):
        with (
            patch.object(
                changes.subprocess,
                "check_output",
                return_value='[{"headSha":"failed-branch"},{"headSha":"passed-ancestor"}]',
            ),
            patch.object(
                changes.subprocess,
                "run",
                side_effect=[
                    subprocess.CompletedProcess([], 1),
                    subprocess.CompletedProcess([], 0),
                ],
            ),
        ):
            self.assertEqual(changes.successful_branch_base("current"), "passed-ancestor")

    def test_dev_ci_history_is_separate_from_main(self):
        with patch.object(changes.subprocess, "check_output", return_value="[]") as command:
            self.assertEqual(changes.successful_branch_base("current", "dev"), "0" * 40)
        args = command.call_args.args[0]
        self.assertEqual(args[args.index("--branch") + 1], "dev")
        with self.assertRaises(ValueError):
            changes.successful_branch_base("current", "feature")

    def test_frontend_does_not_build_backends(self):
        result = changes.classify(["frontend/src/App.tsx"])
        self.assertEqual(result["services"], ["web"])
        self.assertEqual(result["backends"], [])

    def test_business_change_checks_frontend_contract_without_deploying_web(self):
        result = changes.classify(["backend/api/cs/tickets/schemas.py", "docs/openapi/cs.json"])
        self.assertEqual(result["services"], ["cs"])
        self.assertEqual(result["backends"], ["cs"])
        self.assertTrue(result["frontend"])

    def test_mock_fixture_is_not_a_production_web_release(self):
        result = changes.classify(["frontend/src/mocks/generated/cs.json"])
        self.assertTrue(result["frontend"])
        self.assertEqual(result["services"], [])
        self.assertEqual(result["backends"], ["cs"])

    def test_compose_changes_are_checked_without_releasing_cloud_services(self):
        result = changes.classify(["compose.yaml"])
        self.assertTrue(result["infrastructure"])
        self.assertEqual(result["services"], [])

    def test_platform_migration_runs_platform_checks_without_a_release(self):
        """플랫폼 SQL만 바뀌어도 인증 DB 검사를 포함한 플랫폼 검사를 선택한다.

        Args:
            없음.

        Returns:
            None: 인프라와 플랫폼 검사 선택 및 배포 대상 부재를 검증한다.
        """
        result = changes.classify(["infra/migrations/platform/20261023_KAN-77_oauth.sql"])
        self.assertTrue(result["infrastructure"])
        self.assertEqual(result["backends"], ["platform"])
        self.assertEqual(result["services"], [])
        self.assertFalse(result["frontend"])

    def test_runtime_markdown_prompts_are_not_skipped_as_documentation(self):
        result = changes.classify(["backend/agent/cs/prompts/classification.md"])
        self.assertEqual(result["services"], ["cs", "tft", "platform"])
        self.assertEqual(result["backends"], ["cs", "tft", "platform"])

    def test_shared_dependencies_expand_to_all_backends(self):
        for path in ("backend/shared/models.py", "uv.lock", "backend/api/common/application.py"):
            with self.subTest(path=path):
                result = changes.classify([path])
                self.assertEqual(result["services"], list(changes.BACKENDS))
                self.assertTrue(result["frontend"])
                self.assertTrue(result["evaluation"])

    def test_shared_ai_and_business_modules_check_evaluation_consumers(self):
        for path in (
            "backend/shared/ai.py",
            "backend/agent/cs/prompts/classify.md",
            "backend/engine/search.py",
        ):
            with self.subTest(path=path):
                result = changes.classify([path])
                self.assertTrue(result["evaluation"])
                self.assertIn("cs", result["backends"])
                self.assertIn("tft", result["backends"])

    def test_new_evaluation_tests_run_without_releasing_an_api(self):
        result = changes.classify(["tests/eval/retrieval/test_metrics.py"])
        self.assertTrue(result["evaluation"])
        self.assertEqual(result["backends"], [])
        self.assertEqual(result["services"], [])

    def test_deploy_model_changes_run_the_promotion_check(self):
        result = changes.classify(["infra/deploy_private.py"])
        self.assertTrue(result["evaluation"])
        self.assertTrue(result["infrastructure"])

    def test_evaluation_spec_runs_evaluation_without_releasing_an_api(self):
        result = changes.classify(["docs/evaluation.md"])
        self.assertTrue(result["evaluation"])
        self.assertEqual(result["backends"], [])
        self.assertEqual(result["services"], [])

    def test_unknown_shared_test_directories_check_all_consumers_without_a_release(self):
        for path in (
            "tests/agent/test_classification.py",
            "tests/engine/test_search.py",
            "tests/new_shared/test_contract.py",
        ):
            with self.subTest(path=path):
                result = changes.classify([path])
                self.assertEqual(result["backends"], list(changes.BACKENDS))
                self.assertTrue(result["evaluation"])
                self.assertEqual(result["services"], [])

    def test_docs_are_skipped_but_unknown_code_is_not(self):
        self.assertEqual(changes.classify(["docs/team-start.md"])["services"], [])
        self.assertEqual(
            changes.classify(["new-runtime/handler.py"])["services"], list(changes.SERVICES)
        )
        self.assertEqual(changes.classify(["infra/migrations/cs/new.sql"])["services"], [])
        self.assertTrue(changes.classify(["infra/migrations/cs/new.sql"])["infrastructure"])

    def test_moving_between_services_includes_old_and_new_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.check_output(["git", "-C", directory, *args], text=True).strip()

            git("init", "-q")
            git("config", "user.email", "fixture@example.invalid")
            git("config", "user.name", "fixture")
            source = root / "backend/api/cs/feature.py"
            source.parent.mkdir(parents=True)
            source.write_text("fixture\n")
            git("add", ".")
            git("commit", "-qm", "base")
            base = git("rev-parse", "HEAD")
            destination = root / "backend/api/tft/feature.py"
            destination.parent.mkdir(parents=True)
            source.rename(destination)
            git("add", "-A")
            git("commit", "-qm", "move")
            paths = subprocess.check_output(
                ["git", "-C", directory, "diff", "--no-renames", "--name-only", base, "HEAD"],
                text=True,
            ).splitlines()
            self.assertEqual(changes.classify(paths)["services"], ["cs", "tft"])


if __name__ == "__main__":
    unittest.main()
