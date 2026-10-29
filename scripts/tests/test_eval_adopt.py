"""KAN-146 채택 기록 CLI. 합성 분류 결과로 파일 쓰기·덮어쓰기 거부·입력 오류를 검사한다."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts import eval_adopt

RESULT = {
    "meta": {
        "candidates": [
            {"name": "a", "model": "m-a", "prompt_version": "v2"},
            {"name": "b", "model": "m-b", "prompt_version": "v1"},
        ],
        "targets": 4,
        "started_at": "2026-10-29T00:00:00+00:00",
        "git_sha": "abc1234",
    },
    "result": {
        "candidates": [
            {
                "name": name,
                "category": {"macro_f1": 0.95},
                "urgency": {"macro_f1": 0.8, "non_none_recall": 0.9, "critical": {"missed": 0}},
                "outcomes": {"VALID": 4, "SCHEMA_VIOLATION": 0},
            }
            for name in ("a", "b")
        ]
    },
}


class EvalAdoptTest(unittest.TestCase):
    """main을 임시 디렉터리에서 실행한다."""

    def setUp(self):
        """결과·사유 파일을 만든다.

        Returns:
            None: self.root에 파일을 둔다.
        """
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "run.json").write_text(json.dumps(RESULT), encoding="utf-8")
        (self.root / "why.txt").write_text("비용 대비 품질이 낫다.\n", encoding="utf-8")

    def tearDown(self):
        """임시 디렉터리를 지운다.

        Returns:
            None: 정리만 한다.
        """
        self.tmp.cleanup()

    def run_main(self, *extra):
        """기본 인자에 extra를 더해 main을 실행한다.

        Args:
            *extra: 추가·덮어쓸 인자.

        Returns:
            tuple[int, str, str]: 종료 코드, 표준 출력, 표준 오류.
        """
        argv = [
            "--task", "classification",
            "--results", str(self.root / "run.json"),
            "--adopt", "a",
            "--decided-by", "평가 담당",
            "--rationale", str(self.root / "why.txt"),
            "--out", str(self.root / "record.json"),
            "--note", str(self.root / "note.md"),
            *extra,
        ]  # fmt: skip
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = eval_adopt.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_writes_proposed_record_and_note(self):
        """기록은 PROPOSED로, 메모는 Markdown으로 쓰고 표준 출력에는 게이트 건수만 낸다.

        Returns:
            None: 파일과 출력을 확인한다.
        """
        code, out, _ = self.run_main("--reject", "b=비용이 두 배다")
        self.assertEqual(code, 0)
        record = json.loads((self.root / "record.json").read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "PROPOSED")
        self.assertEqual(record["adopted"], {"model": "m-a", "prompt_version": "v2"})
        self.assertEqual(record["rejected"], [{"candidate_name": "b", "reason": "비용이 두 배다"}])
        self.assertEqual(record["rationale"], "비용 대비 품질이 낫다.")
        self.assertEqual(
            json.loads(out),
            {"counts": {"PASS": 5, "FAIL": 0, "NOT_EVALUABLE": 0}, "blocker": False},
        )
        note = (self.root / "note.md").read_text(encoding="utf-8")
        self.assertTrue(note.startswith("# 설정 채택 결정 메모: classification · a"))

    def test_existing_output_is_not_overwritten(self):
        """기록이나 메모 파일이 이미 있으면 둘 다 쓰지 않고 종료 코드 1이다.

        Returns:
            None: 기존 파일 보존과 미생성을 확인한다.
        """
        (self.root / "note.md").write_text("기존 메모", encoding="utf-8")
        code, _, err = self.run_main()
        self.assertEqual((code, err.strip()), (1, "EVAL_OUTPUT_EXISTS"))
        self.assertFalse((self.root / "record.json").exists())
        self.assertEqual((self.root / "note.md").read_text(encoding="utf-8"), "기존 메모")

    def test_same_out_and_note_path_is_rejected_without_writing(self):
        """--out과 --note가 같은 경로면 입력 오류이며 빈 파일을 남기지 않는다.

        Returns:
            None: 종료 코드와 미생성을 확인한다.
        """
        code, _, err = self.run_main("--note", str(self.root / "sub" / ".." / "record.json"))
        self.assertEqual((code, err.strip()), (1, "EVAL_INVALID_INPUT"))
        self.assertFalse((self.root / "record.json").exists())

    def test_invalid_inputs_exit_with_code_1(self):
        """모르는 후보·잘못된 --reject·없는 결과 파일은 원문 없이 종료 코드 1이다.

        Returns:
            None: 종료 코드와 오류 코드를 확인한다.
        """
        for extra in (
            ("--adopt", "missing"),
            ("--reject", "no-separator"),
            ("--reject", "b=x", "--reject", "b=y"),
            ("--results", str(self.root / "absent.json")),
        ):
            with self.subTest(extra=extra):
                code, _, err = self.run_main(*extra)
                self.assertEqual((code, err.strip()), (1, "EVAL_INVALID_INPUT"))
                self.assertFalse((self.root / "record.json").exists())


if __name__ == "__main__":
    unittest.main()
