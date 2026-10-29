"""KAN-143 4차: 요구 추출 측정 결과를 채택 기록 입력으로 내고 eval_adopt --task rfp가 읽는지 확인한다.

모델을 부르지 않고 고정한 정답·예측으로 만든다. 원문·정답 파일은 쓰지 않는다.
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.engine.rfp.requirements import ExtractedRequirement
from backend.engine.rfp.scoring import GoldRequirement
from scripts import eval_adopt, rfp_requirements_eval

GOLD = [
    GoldRequirement(clause_no="2.4.1", attribute="BIL", value="95", unit="kV"),
    GoldRequirement(clause_no="2.4.2", attribute="impedance", value="5.75", unit="%"),
    # 필수가 아닌 정답은 정밀도의 정답 집합에는 들어가지만 재현율 분모에는 들어가지 않는다.
    GoldRequirement(clause_no="2.4.3", attribute="cooling", mandatory=False),
]


def predicted(clause_no: str, attribute: str) -> ExtractedRequirement:
    """합성 예측 하나.

    Args:
        clause_no: 조항 번호.
        attribute: 속성.

    Returns:
        ExtractedRequirement: 키만 의미 있는 예측.
    """
    return ExtractedRequirement(
        clause_no=clause_no,
        quote="synthetic",
        kind="SPEC",
        equipment=None,
        task=None,
        attribute=attribute,
        value=None,
        unit=None,
        operator="TEXT",
        standard_system="NONE",
        necessity="MANDATORY",
        designated_model=None,
    )


class AdoptionInputTest(unittest.TestCase):
    """채택 기록 입력 형식과 eval_adopt 판정."""

    def test_adoption_input_is_judged_on_the_two_extraction_gates_only(self):
        """키 기준 재현율·정밀도 2개만 들어가고, eval_adopt는 둘만 판정하며 미측정 blocker로 blocker True다.

        Returns:
            None: 입력 형식과 판정 건수를 확인한다.
        """
        preds = [
            predicted("2.4.1", "BIL"),
            predicted("2.4.2", "impedance"),
            predicted("2.4.3", "cooling"),
        ]
        record = rfp_requirements_eval.adoption_input(
            GOLD,
            preds,
            name="requirements-test-model",
            model="test-model",
            version="v-test",
            started_at="2026-10-30T00:00:00+00:00",
            git_sha="1234abc",
        )
        row = record["result"]["candidates"][0]  # type: ignore[index]
        self.assertEqual(
            row,
            {
                "name": "requirements-test-model",
                "rfp_requirement_recall": 1.0,
                "rfp_requirement_precision": 1.0,
            },
        )
        meta = record["meta"]
        self.assertEqual(
            meta["metric_definition"],  # type: ignore[index]
            "(clause_no, attribute) 키 일치, 상태 미비교, 재현율 분모=필수 정답",
        )
        self.assertEqual(meta["counts"], {"recall": "2/2", "precision": "3/3"})  # type: ignore[index]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "rfp.json").write_text(json.dumps(record), encoding="utf-8")
            (root / "why.txt").write_text("합성 확인\n", encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = eval_adopt.main(
                    [
                        "--task", "rfp",
                        "--results", str(root / "rfp.json"),
                        "--adopt", "requirements-test-model",
                        "--decided-by", "평가 담당",
                        "--rationale", str(root / "why.txt"),
                        "--out", str(root / "record.json"),
                        "--note", str(root / "note.md"),
                    ]
                )  # fmt: skip
            self.assertEqual(code, 0, err.getvalue())
            self.assertEqual(
                json.loads(out.getvalue()),
                {"counts": {"PASS": 2, "FAIL": 0, "NOT_EVALUABLE": 7}, "blocker": True},
            )

    def test_key_match_ignores_values_and_counts_extra_predictions(self):
        """값이 달라도 같은 (조항, 속성)이면 맞고, 재현율은 필수 정답만, 정밀도는 예측 전체로 센다.

        Returns:
            None: 건수 병기를 확인한다.
        """
        preds = [
            predicted("2.4.1", "BIL"),
            predicted("2.4.1", "BIL"),
            predicted("2.4.9", "weight"),
        ]
        record = rfp_requirements_eval.adoption_input(
            GOLD, preds, name="n", model="m", version="v", started_at="t", git_sha="s"
        )
        self.assertEqual(record["meta"]["counts"], {"recall": "1/2", "precision": "1/3"})  # type: ignore[index]

    def test_duplicate_gold_keys_and_unknown_cases_are_rejected(self):
        """정답 키가 겹치거나 정답에 없는 사례의 예측이면 summarize_requirement_extraction처럼 거부한다.

        Returns:
            None: 오류 코드를 확인한다.
        """
        from eval.rfp_metrics import summarize_extraction_keys

        with self.assertRaisesRegex(ValueError, "EVAL_INVALID_INPUT"):
            summarize_extraction_keys({"d": [("2.4.1", "BIL"), ("2.4.1", "BIL")]}, {})
        with self.assertRaisesRegex(ValueError, "EVAL_INVALID_INPUT"):
            summarize_extraction_keys({"d": [("2.4.1", "BIL")]}, {"other": [("2.4.1", "BIL")]})

    def test_bad_adoption_inputs_stop_before_any_model_call(self):
        """중복 정답 키·없는 출력 폴더·이미 있는 출력이면 모델·문서·키를 쓰기 전에 종료 코드 1이다(#295 이연 P3).

        Returns:
            None: 종료 코드·오류 코드·클라이언트 미생성을 확인한다.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gold = root / "gold.jsonl"
            gold.write_text(
                "\n".join(g.model_dump_json() for g in [*GOLD, GOLD[0]]) + "\n", encoding="utf-8"
            )
            (root / "taken.json").write_text("{}", encoding="utf-8")
            base = ["--docx", str(root / "absent.docx"), "--gold", str(gold), "--sections", "2.4"]
            cases = [
                (root / "adopt.json", "EVAL_GOLD_DUPLICATE_KEY"),
                (root / "absent" / "adopt.json", "EVAL_OUTPUT_DIR_MISSING"),
                (root / "taken.json", "EVAL_OUTPUT_EXISTS"),
            ]
            for target, code in cases:
                err = io.StringIO()
                with (
                    patch.object(rfp_requirements_eval, "create_client") as client,
                    patch.object(rfp_requirements_eval, "dotenv_values") as env,
                    contextlib.redirect_stderr(err),
                ):
                    result = rfp_requirements_eval.main(
                        [*base, "--out", str(root / "out"), "--adoption-out", str(target)]
                    )
                self.assertEqual((result, err.getvalue().split(":")[0]), (1, code))
                client.assert_not_called()
                env.assert_not_called()
                self.assertFalse((root / "out").exists())


if __name__ == "__main__":
    unittest.main()
