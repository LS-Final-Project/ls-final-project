"""KAN-101 2차 검색 hit@5 측정. 합성 색인·근거로 역매핑과 집계를 검사한다. DB·실제 자료 없음."""

import json
import tempfile
import unittest
from pathlib import Path

from eval.search_expected import load_policy
from scripts import cs_search_eval

INDEX = {
    "DS-A": {"doc_type": "DS", "product_code": "A", "family": "PLC"},
    "TG-A": {"doc_type": "TG", "product_code": "A", "family": "PLC"},
    "POL-RETURN": {"doc_type": "POL", "product_code": "", "family": ""},
}


class SearchEvalTest(unittest.TestCase):
    def test_reverse_maps_evidence_and_aggregates_without_text(self):
        """근거 sha → doc_id(README는 None)로 되돌려 유형별 hit@5·top1을 내고 원문을 담지 않는다."""
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "index.json"
            manifest.write_text(
                json.dumps(
                    {
                        "documents": [
                            {"doc_id": "DS-A", "sha256": "a" * 64, "reason": None},
                            {"doc_id": "TG-A", "sha256": "b" * 64, "reason": None},
                            {"doc_id": None, "sha256": "c" * 64, "reason": None},
                            {"doc_id": "POL-RETURN", "sha256": "d" * 64, "reason": "SKIPPED"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            doc_ids = cs_search_eval.document_ids(manifest)
        self.assertEqual(doc_ids, {"a" * 64: "DS-A", "b" * 64: "TG-A", "c" * 64: None})
        row = lambda sha: {"document_sha256": sha, "snippet": "SECRET BODY"}  # noqa: E731
        result = cs_search_eval.evaluate(
            ["t1", "t2", "t3"],
            {"t1": "A", "t2": "A", "t3": "A"},
            {"t1": {"category_code": "DEFECT"}, "t2": {"category_code": "TECH_SPEC"}, "t3": None},
            {"t1": [row("c" * 64), row("b" * 64)], "t2": [row("a" * 64)], "t3": [row("a" * 64)]},
            doc_ids,
            INDEX,
            load_policy(),
        )
        self.assertEqual(result["basis"], "RULE_EXPECTATION_NOT_GOLD")
        self.assertEqual(result["expected_version"], "search-expected-v1")
        metrics = result["metrics"]
        self.assertEqual(
            (metrics["n"], metrics["no_label"], metrics["hit_at_5"], metrics["top1"]),
            (2, 1, 1.0, 0.5),
        )
        self.assertEqual(metrics["by_category"]["DEFECT"], {"n": 1, "hit_at_5": 1.0, "top1": 0.0})
        self.assertNotIn("SECRET", json.dumps(result))
        with self.assertRaises(ValueError):
            cs_search_eval.evaluate(
                ["t1"],
                {"t1": "A"},
                {"t1": {"category_code": "DEFECT"}},
                {"t1": [row("e" * 64)]},
                doc_ids,
                INDEX,
                load_policy(),
            )


if __name__ == "__main__":
    unittest.main()
