"""CI DB 시험 목록 대조 검사."""

import tempfile
import unittest
from pathlib import Path

from scripts import check_db_tests as check

LINE = "      - run: uv run --frozen pytest -q tests/platform/test_authorization_store.py {}\n"


class CheckDbTestsTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "tests/tft").mkdir(parents=True)
        (self.root / ".github/workflows").mkdir(parents=True)
        (self.root / "tests/platform").mkdir()
        (self.root / "tests/platform/test_authorization_store.py").write_text("")

    def workflow(self, names):
        (self.root / check.WORKFLOW).write_text(LINE.format(" ".join(names)), encoding="utf-8")

    def test_the_repository_list_covers_every_db_test(self):
        self.assertEqual(check.problems(Path(".")), [])

    def test_a_listed_db_test_passes(self):
        (self.root / "tests/tft/test_a_db.py").write_text("")
        self.workflow(["tests/tft/test_a_db.py"])
        self.assertEqual(check.problems(self.root), [])

    def test_a_missing_db_test_is_reported(self):
        (self.root / "tests/tft/test_a_db.py").write_text("")
        self.workflow([])
        self.assertEqual(
            check.problems(self.root), ["auth-database 목록에 없음: tests/tft/test_a_db.py"]
        )

    def test_a_listed_file_that_is_gone_is_reported(self):
        self.workflow(["tests/tft/test_gone_db.py"])
        self.assertEqual(
            check.problems(self.root), ["목록에 있지만 파일이 없음: tests/tft/test_gone_db.py"]
        )

    def test_a_workflow_without_the_command_is_an_error(self):
        (self.root / check.WORKFLOW).write_text("jobs: {}\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            check.problems(self.root)


if __name__ == "__main__":
    unittest.main()
