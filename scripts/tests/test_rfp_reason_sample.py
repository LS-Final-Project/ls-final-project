"""KAN-191 ⓐ 엔진 사유 표본 추출. 시드 고정·문서별 건수·정답 미참조를 검사한다."""

import json
import tempfile
import unittest
from pathlib import Path

from scripts import rfp_reason_sample as rs

ITEMS = [
    {
        "document_number": d,
        "clause_no": f"2.{i}",
        "attribute": "bil" if i % 2 else None,
        "status": "SPEC_GAP",
        "necessity": "MANDATORY",
        "path": None,
        "reason": f"reason {d}-{i} | with bar",
    }
    for d in (10, 12)
    for i in range(15)
]


class ReasonSampleTest(unittest.TestCase):
    def test_same_seed_gives_the_same_sample_per_document(self) -> None:
        a = rs.sample(ITEMS, [10, 12], 10, 20261102)
        b = rs.sample(ITEMS, [10, 12], 10, 20261102)
        self.assertEqual(a, b)
        self.assertEqual([r["document_number"] for r in a].count(10), 10)
        self.assertEqual([r["document_number"] for r in a].count(12), 10)
        self.assertNotEqual(a, rs.sample(ITEMS, [10, 12], 10, 1))

    def test_a_small_document_contributes_everything_it_has(self) -> None:
        rows = rs.sample(ITEMS[:3], [10, 12], 10, 7)
        self.assertEqual(len(rows), 3)

    def test_main_writes_a_table_outside_the_repository_without_gold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pred = Path(tmp) / "pred.json"
            pred.write_text(
                json.dumps({"meta": {"run_label": "FIRST", "git_sha": "abc"}, "items": ITEMS})
            )
            out = Path(tmp) / "sample.md"
            code = rs.main(
                [
                    "--predictions",
                    str(pred),
                    "--documents",
                    "10",
                    "12",
                    "--per-document",
                    "2",
                    "--seed",
                    "3",
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(code, 0)
            text = out.read_text()
            self.assertIn("| 문서 | 조항 | 속성 | 엔진 상태 | 엔진 사유 | 맞음/틀림 | 메모 |", text)
            self.assertEqual(text.count("| 10 | "), 2)
            self.assertIn("\\|", text)  # 사유 안 '|'는 표를 깨지 않게 이스케이프
            self.assertEqual(
                rs.main(
                    [
                        "--predictions",
                        str(pred),
                        "--documents",
                        "10",
                        "--seed",
                        "3",
                        "--out",
                        str(out),
                    ]
                ),
                1,
            )
            inside = rs.ROOT / "eval" / "sample.md"
            self.assertEqual(
                rs.main(
                    [
                        "--predictions",
                        str(pred),
                        "--documents",
                        "10",
                        "--seed",
                        "3",
                        "--out",
                        str(inside),
                    ]
                ),
                1,
            )
            self.assertFalse(inside.exists())


if __name__ == "__main__":
    unittest.main()
