"""KAN-148 저장 수치 대조 CLI. DB 대신 읽기 함수를 주입해 단계 대조 경로를 검사한다."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from scripts import eval_reconcile

URL = "postgresql://secret-user:secret-pass@db.invalid/eval"
RUN = "00000000-0000-0000-0000-000000000001"
KEYS = "category_macro_f1,urgency_macro_f1,critical_miss_count,hit_at_5"


def summaries(status="SUCCEEDED", not_started=0, critical=0):
    """RAW 재계산 결과 모양의 합성 집계.

    Args:
        status: 실행 상태.
        not_started: 시도가 없는 대상 수.
        critical: 치명 긴급도 누락 수.

    Returns:
        dict: category·urgency 집계.
    """
    return {
        "category": {
            "run_status": status,
            "target_count": 4,
            "report": {
                "overall": {
                    "total": 4,
                    "accuracy": 0.75,
                    "macro_f1": 0.5,
                    "outcomes": {"NOT_STARTED": not_started, "VALID": 4 - not_started},
                }
            },
        },
        "urgency": {
            "run_status": status,
            "target_count": 4,
            "report": {
                "overall": {
                    "total": 4,
                    "macro_f1": 0.25,
                    "non_none": {"recall": 1.0},
                    "critical": {"missed": critical},
                }
            },
        },
    }


def row(key, value, version="v1", hour=1, denominator=None):
    return {
        "metric_key": key,
        "value": None if value is None else Decimal(str(value)),
        "denominator": denominator,
        "scoring_version": version,
        "created_at": datetime(2026, 10, 29, hour, tzinfo=UTC),
    }


def stored(category_f1=0.5, critical=0):
    """RAW와 같은 값을 담은 저장 행(필요하면 일부만 바꾼다).

    Args:
        category_f1: 주분류 macro-F1 저장 값.
        critical: 치명 누락 저장 값. None이면 행을 넣지 않는다.

    Returns:
        list[dict]: metrics 행.
    """
    rows = [
        row("cs_locked_category_first_macro_f1", category_f1),
        row("cs_locked_category_first_accuracy", 0.75, denominator=4),
        row("cs_locked_urgency_first_macro_f1", 0.25),
    ]
    if critical is not None:
        rows.append(row("cs_locked_urgency_first_critical_missed", critical))
    return rows


class ReconcileCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ("cat.csv", "urg.csv"):
            (self.root / name).write_text("ticket_id,x\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def run_main(self, data, *extra):
        """fake 읽기 함수로 main을 돌린다.

        Args:
            data: (집계, 저장 행).
            *extra: 추가 인자.

        Returns:
            tuple[int, str, dict | None]: 종료 코드, 출력, 결과 JSON.
        """
        out = self.root / "out.json"
        argv = [
            "--database-url", URL, "--run-id", RUN,
            "--gold-category", str(self.root / "cat.csv"),
            "--gold-urgency", str(self.root / "urg.csv"),
            "--metric-keys", KEYS, "--out", str(out), *extra,
        ]  # fmt: skip
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            code = eval_reconcile.main(argv, reader=lambda *args: data)
        result = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
        return code, printed.getvalue(), result

    def test_matching_stages_are_consistent_and_unmapped_keys_stay_uncomputed(self):
        """RAW·DB가 같으면 MATCH이고 매핑 없는 키(hit_at_5)는 전 단계 None이라 일관성을 깨지 않는다.

        Args:
            없음.

        Returns:
            None: 상태·meta를 확인한다.
        """
        code, printed, result = self.run_main((summaries(), stored()))
        self.assertEqual(code, 0, printed)
        metrics = result["result"]["metrics"]
        self.assertEqual(metrics["category_macro_f1"]["status"], "MATCH")
        self.assertEqual(metrics["critical_miss_count"]["values"], {"RAW": 0, "DB": 0.0})
        self.assertEqual(metrics["hit_at_5"]["status"], "MISSING")
        self.assertEqual(result["result"]["case_count"]["status"], "MATCH")
        self.assertTrue(result["result"]["consistent"])
        self.assertEqual(result["meta"]["unmapped_keys"], ["hit_at_5"])
        self.assertEqual(result["meta"]["stages"], ["RAW", "DB"])
        self.assertNotIn("secret-pass", printed + json.dumps(result))

    def test_mismatch_zero_for_missing_and_latest_version(self):
        """DB 값이 다르면 MISMATCH, RAW 0인데 DB 행이 없으면 ZERO_FOR_MISSING, DB는 가장 늦은 판본만 쓴다.

        Args:
            없음.

        Returns:
            None: 상태와 고른 판본을 확인한다.
        """
        rows = stored(category_f1=0.4, critical=None) + [
            row("cs_locked_category_first_macro_f1", 0.5, version="v2", hour=2)
        ]
        _, _, result = self.run_main((summaries(), rows))
        metrics = result["result"]["metrics"]
        self.assertEqual(metrics["category_macro_f1"]["status"], "MATCH")
        self.assertEqual(
            result["meta"]["db_scoring_versions"]["cs_locked_category_first_macro_f1"], "v2"
        )
        self.assertEqual(metrics["critical_miss_count"]["status"], "ZERO_FOR_MISSING")
        self.assertFalse(result["result"]["consistent"])
        _, _, result = self.run_main((summaries(), stored(category_f1=0.4)))
        self.assertEqual(result["result"]["metrics"]["category_macro_f1"]["status"], "MISMATCH")
        self.assertEqual(result["result"]["metrics"]["category_macro_f1"]["diff_stages"], ["DB"])

    def test_default_tolerance_absorbs_numeric_rounding_but_zero_does_not(self):
        """DB numeric의 15자리 반올림(1/3 → 0.333333333333333)은 기본 허용 오차로 MATCH, 0이면 MISMATCH다.

        Args:
            없음.

        Returns:
            None: 두 허용 오차의 상태를 확인한다.
        """
        data = summaries()
        data["category"]["report"]["overall"]["accuracy"] = 1 / 3
        rows = stored() + [
            row("cs_locked_category_first_accuracy", "0.333333333333333", hour=3, denominator=4)
        ]
        keys = ("--metric-keys", "category_micro_accuracy")
        _, _, result = self.run_main((data, rows), *keys)
        self.assertEqual(result["result"]["metrics"]["category_micro_accuracy"]["status"], "MATCH")
        _, _, result = self.run_main((data, rows), *keys, "--tolerance", "0")
        self.assertEqual(
            result["result"]["metrics"]["category_micro_accuracy"]["status"], "MISMATCH"
        )

    def test_partial_runs_and_unstarted_targets_are_marked_partial(self):
        """PARTIAL 실행과 시도 없는 대상이 있는 실행은 부분 결과로 표시하고 일관 판정을 내리지 않는다.

        Args:
            없음.

        Returns:
            None: partial·coverage를 확인한다.
        """
        _, _, result = self.run_main((summaries(status="PARTIAL"), stored()))
        self.assertTrue(result["result"]["partial"])
        self.assertEqual(result["result"]["run_status"], "PARTIAL")
        _, _, result = self.run_main((summaries(status="TIMED_OUT", not_started=1), stored()))
        self.assertEqual(result["result"]["run_status"], "ABORTED")
        self.assertEqual(result["result"]["coverage"]["incomplete_targets"], 1)
        self.assertFalse(result["result"]["consistent"])

    def test_screen_and_csv_stages_join_the_comparison(self):
        """화면 DTO 경로와 CSV 열 합계가 SCREEN·CSV 단계로 들어간다.

        Args:
            없음.

        Returns:
            None: 단계 값과 상태를 확인한다.
        """
        (self.root / "screen.json").write_text(json.dumps({"quality": {"f1": 0.5}}))
        (self.root / "paths.json").write_text(json.dumps({"category_macro_f1": ["quality", "f1"]}))
        (self.root / "export.csv").write_text("f1\n0.2\n0.3\n", encoding="utf-8")
        (self.root / "cols.json").write_text(json.dumps({"category_macro_f1": "f1"}))
        _, _, result = self.run_main(
            (summaries(), stored()),
            "--screen-json", str(self.root / "screen.json"),
            "--screen-paths", str(self.root / "paths.json"),
            "--csv", str(self.root / "export.csv"),
            "--csv-columns", str(self.root / "cols.json"),
        )  # fmt: skip
        self.assertEqual(result["meta"]["stages"], ["RAW", "DB", "SCREEN", "CSV"])
        values = result["result"]["metrics"]["category_macro_f1"]["values"]
        self.assertEqual(values, {"RAW": 0.5, "DB": 0.5, "SCREEN": 0.5, "CSV": 0.5})
        self.assertEqual(result["result"]["metrics"]["category_macro_f1"]["status"], "MATCH")

    def test_non_object_mapping_files_exit_with_code_1(self):
        """--screen-paths·--csv-columns가 JSON 객체가 아니면 트레이스백 없이 종료 코드 1이다.

        Args:
            없음.

        Returns:
            None: 종료 코드와 안내 문구를 확인한다.
        """
        (self.root / "screen.json").write_text("{}")
        (self.root / "list.json").write_text("[]")
        (self.root / "export.csv").write_text("f1\n0.5\n", encoding="utf-8")
        data = (summaries(), stored())
        for extra in (
            (
                "--screen-json",
                str(self.root / "screen.json"),
                "--screen-paths",
                str(self.root / "list.json"),
            ),
            ("--csv", str(self.root / "export.csv"), "--csv-columns", str(self.root / "list.json")),
        ):
            code, printed, _ = self.run_main(data, *extra)
            self.assertEqual(code, 1)
            self.assertIn("CS_RECONCILE_FAILED", printed)

    def test_duplicate_metric_keys_are_removed(self):
        """--metric-keys의 중복 키는 한 번만 대조하고 미매핑 목록에도 한 번만 남는다.

        Args:
            없음.

        Returns:
            None: 결과 키와 meta를 확인한다.
        """
        out = self.root / "dup.json"
        argv = [
            "--database-url", URL, "--run-id", RUN,
            "--gold-category", str(self.root / "cat.csv"),
            "--gold-urgency", str(self.root / "urg.csv"),
            "--metric-keys", "hit_at_5,category_macro_f1,hit_at_5", "--out", str(out),
        ]  # fmt: skip
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                eval_reconcile.main(argv, reader=lambda *a: (summaries(), stored())), 0
            )
        result = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(result["meta"]["unmapped_keys"], ["hit_at_5"])
        self.assertEqual(sorted(result["result"]["metrics"]), ["category_macro_f1", "hit_at_5"])

    def test_invalid_arguments_and_reader_errors_exit_with_code_1(self):
        """DB 연결 없음·음수 허용 오차·짝 없는 화면/CSV 인자·잘못된 실행 ID·읽기 오류는 원문 없이 1이다.

        Args:
            없음.

        Returns:
            None: 종료 코드와 비노출을 확인한다.
        """
        data = (summaries(), stored())
        self.assertEqual(self.run_main(data, "--tolerance", "-1")[0], 1)
        (self.root / "x.csv").write_text("f1\n0.5\n", encoding="utf-8")
        self.assertEqual(self.run_main(data, "--csv", str(self.root / "x.csv"))[0], 1)
        out = self.root / "o.json"
        base = [
            "--run-id", RUN, "--gold-category", "a", "--gold-urgency", "b", "--out", str(out),
        ]  # fmt: skip
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(eval_reconcile.main(base, reader=lambda *a: data), 1)
        bad = base[:1] + ["not-a-uuid"] + base[2:] + ["--database-url", URL]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(eval_reconcile.main(bad, reader=lambda *a: data), 1)

        def failing(*args):
            raise ValueError("CS_STORED_METRICS_INVALID_INPUT " + URL)

        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(eval_reconcile.main(base + ["--database-url", URL], reader=failing), 1)
        self.assertNotIn("secret-pass", printed.getvalue())
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
