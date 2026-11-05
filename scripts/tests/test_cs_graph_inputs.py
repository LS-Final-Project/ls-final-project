"""KAN-194 8차 그래프 입력 생성기. 합성 개발 CSV·manifest로 형식·필요 코드·경로·분할 규칙을 검사한다."""

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts import cs_graph_inputs, cs_graph_run
from scripts.cs_draft_run import load_required
from scripts.tests.test_cs_draft_run import write_csv


class GraphInputsTest(unittest.TestCase):
    def setUp(self):
        """합성 개발 CSV(정답 category 열 포함)와 manifest를 만든다.

        Returns:
            None: self.root에 파일을 둔다.
        """
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        tickets = [
            {
                "ticket_id": ticket_id,
                "data_split": "valid",
                "received_at_utc": "2026-10-01T09:00:00Z",
                "channel": "EMAIL",
                "body_language": "en",
                "customer_tier": "GOLD",
                "product_code": product,
                "category_code": category,
                "inquiry_subject": "Trip",
                "inquiry_body": f"Unit {ticket_id} trips. contact person@example.test",
            }
            for ticket_id, product, category in (
                ("G1", "TRF-OIL-1000", "WARRANTY"),
                ("G2", "TRF-OIL-1000", "QUOTE"),
                ("G3", "NOPE-1", "WARRANTY"),
            )
        ]
        self.write_manifest(["valid"], write_csv(self.root / "t.csv", list(tickets[0]), tickets))

    def tearDown(self):
        """임시 디렉터리를 지운다.

        Returns:
            None: 정리만 한다.
        """
        self.temp.cleanup()

    def write_manifest(self, splits, raw):
        """manifest를 쓴다.

        Args:
            splits: 선언 분할.
            raw: tickets CSV 바이트.

        Returns:
            None: m.json을 쓴다.
        """
        (self.root / "m.json").write_text(
            json.dumps(
                {
                    "tickets": {
                        "path": "t.csv",
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "splits": splits,
                        "rows": 3,
                    }
                }
            )
        )

    def run_main(self, *extra, out=None):
        """main을 부르고 (종료 코드, 표준 출력)을 돌려준다.

        Args:
            *extra: 추가 인자.
            out: 출력 경로(없으면 임시 디렉터리).

        Returns:
            tuple[int, str]: 종료 코드, 표준 출력.
        """
        argv = [
            "--manifest", str(self.root / "m.json"),
            "--out", str(out or self.root / "tickets.json"),
            *extra,
        ]  # fmt: skip
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(io.StringIO()):
            code = cs_graph_inputs.main(argv)
        return code, printed.getvalue()

    def test_writes_graph_ticket_file_with_family_rows_not_gold_category(self):
        """출력은 cs_graph_run이 읽는 형식이고, 필요 코드는 정답 유형이 아니라 제품 계열의 유형별 행이다.

        Returns:
            None: 형식·필요 코드·시간대·권한·요약을 확인한다.
        """
        code, printed = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(printed)["unknown_family"], 1)
        path = self.root / "tickets.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        data = json.loads(path.read_text(encoding="utf-8"))
        family_rows = {
            category: sorted(codes)
            for (family, category), codes in load_required().items()
            if family == "TRANSFORMER"
        }
        # 정답 유형이 다른 두 문의(WARRANTY·QUOTE)가 같은 지도를 받는다.
        self.assertEqual(data["G1"]["required_info_by_category"], family_rows)
        self.assertEqual(data["G2"]["required_info_by_category"], family_rows)
        self.assertEqual(data["G3"]["required_info_by_category"], {})
        self.assertEqual(data["G1"]["received_at"], "2026-10-01T09:00:00+00:00")
        self.assertEqual(
            (data["G1"]["sensitive_values"], data["G1"]["already_asked_codes"]), ({}, [])
        )
        self.assertNotIn("category_code", json.dumps(data))
        tickets = cs_graph_run.load_tickets(path)
        self.assertEqual(tickets["G1"].required_for("WARRANTY"), frozenset(family_rows["WARRANTY"]))
        self.assertEqual(tickets["G3"].required_for("WARRANTY"), frozenset())

    def test_ticket_ids_paths_and_test_split(self):
        """--ticket-ids로 고르고, 모르는 ID·저장소 안 추적 경로는 1, test 분할은 2다.

        Returns:
            None: 종료 코드와 키를 확인한다.
        """
        (self.root / "ids.txt").write_text("G2\n", encoding="utf-8")
        self.assertEqual(self.run_main("--ticket-ids", str(self.root / "ids.txt"))[0], 0)
        self.assertEqual(
            list(json.loads((self.root / "tickets.json").read_text(encoding="utf-8"))), ["G2"]
        )
        (self.root / "ids.txt").write_text("G9\n", encoding="utf-8")
        self.assertEqual(
            self.run_main("--ticket-ids", str(self.root / "ids.txt"), out=self.root / "x.json")[0],
            1,
        )
        tracked = cs_graph_inputs.REPO / "scripts" / "graph-inputs-test.json"
        self.assertEqual(self.run_main(out=tracked)[0], 1)
        self.assertFalse(tracked.exists())
        self.assertTrue(cs_graph_inputs.private_path_allowed(cs_graph_inputs.REPO / ".env"))
        self.write_manifest(["valid", "test"], (self.root / "t.csv").read_bytes())
        self.assertEqual(self.run_main(out=self.root / "y.json")[0], 2)


if __name__ == "__main__":
    unittest.main()
