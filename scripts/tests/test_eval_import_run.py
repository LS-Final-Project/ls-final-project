"""KAN-144 실측 결과 반입 스크립트. 합성 결과·라벨·manifest로 변환·검사·dry-run을 본다. DB 없음."""

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from backend.engine.cs.classification import INPUT_VERSION, INPUT_VERSION_STRIPPED
from eval.cs_gold_locks import GoldLock
from scripts import eval_import_run
from scripts.tests.test_cs_draft_run import write_csv

PREDICTION = {
    "category_code": "WARRANTY",
    "secondary_category": None,
    "urgency_flag": "NONE",
    "provided_info": ["serial_no"],
    "return_subtype": None,
}
SECRET = "합성-원문-표식"


def gold_locks(category: bytes, urgency: bytes) -> dict[str, GoldLock]:
    """검사용 잠금표.

    Args:
        category: 분류 정답 바이트.
        urgency: 긴급도 정답 바이트.

    Returns:
        dict: 지표별 GoldLock.
    """
    return {
        metric: GoldLock(
            f"test-{metric}",
            hashlib.sha256(data).hexdigest(),
            ("ticket_id", column),
            (("ALL", 0, 3),),
        )
        for metric, data, column in (
            ("category", category, "category_code"),
            ("urgency", urgency, "urgency_flag"),
        )
    }


class ImportRunTests(unittest.TestCase):
    def setUp(self):
        """합성 manifest(valid 3건)·결과·라벨·정답 파일을 만든다.

        Returns:
            None: self.root에 파일을 둔다.
        """
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        tickets = [
            {
                "ticket_id": ticket_id,
                "data_split": "valid",
                "channel": "EMAIL",
                "body_language": "en",
                "inquiry_subject": "Warranty",
                "inquiry_body": f"{SECRET} {ticket_id}",
            }
            for ticket_id in ("T1", "T2", "T3")
        ]
        self.write_manifest(["valid"], write_csv(self.root / "t.csv", list(tickets[0]), tickets))
        self.write_run({"T1": PREDICTION, "T2": PREDICTION, "T3": None}, {"SCHEMA_VIOLATION": 1})
        self.category = b"ticket_id,category_code\nT1,WARRANTY\nT2,WARRANTY\nT3,RETURN\n"
        self.urgency = b"ticket_id,urgency_flag\nT1,NONE\nT2,NONE\nT3,NONE\n"
        (self.root / "gc.csv").write_bytes(self.category)
        (self.root / "gu.csv").write_bytes(self.urgency)

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
                    },
                    "provenance": {"data_origin": "synthetic"},
                }
            )
        )

    def write_run(self, labels, others, *, label_sha=None, spec=None):
        """라벨 파일과 그에 맞는 결과 JSON을 쓴다.

        Args:
            labels: {ticket_id: 예측 | None}.
            others: 비VALID outcome 건수.
            label_sha: 결과에 적을 라벨 hash(없으면 실제 hash).
            spec: 후보 명세 덮어쓰기.

        Returns:
            None: run.json·labels.json을 쓴다.
        """
        raw = json.dumps(labels, sort_keys=True).encode()
        (self.root / "labels.json").write_bytes(raw)
        ids = sorted(labels)
        outcomes = {"VALID": sum(v is not None for v in labels.values()), "HELD": 0} | others
        run = {
            "meta": {
                "candidates": [
                    {"name": "a", "model": "gemini-x", "prompt_version": "v2"} | (spec or {})
                ],
                "targets": len(ids),
                "target_ids_sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest(),
                "started_at": "2026-10-29T01:00:00+00:00",
                "finished_at": "2026-10-29T01:05:00+00:00",
                "git_sha": "abc1234",
                "label_files": {"a": label_sha or hashlib.sha256(raw).hexdigest()},
            },
            "result": {"candidates": [{"name": "a", "outcomes": outcomes}]},
        }
        (self.root / "run.json").write_text(json.dumps(run))

    def tamper_run(self, key, value):
        """올바른 결과를 쓴 뒤 meta 값 하나를 바꾼다.

        Args:
            key: meta 키.
            value: 새 값.

        Returns:
            None: run.json을 고친다.
        """
        self.write_run({"T1": PREDICTION, "T2": None, "T3": None}, {"TIMEOUT": 2})
        run = json.loads((self.root / "run.json").read_text())
        run["meta"][key] = value
        (self.root / "run.json").write_text(json.dumps(run))

    def run_main(self, *extra, locks=None):
        """main을 부르고 종료 코드와 표준 출력을 돌려준다.

        Args:
            *extra: 추가 인자.
            locks: 잠금표(없으면 합성 정답 hash 잠금표).

        Returns:
            tuple[int, str]: 종료 코드, 표준 출력.
        """
        argv = [
            "--run", str(self.root / "run.json"),
            "--labels", str(self.root / "labels.json"),
            "--candidate", "a",
            "--manifest", str(self.root / "m.json"),
            "--data-split", "valid",
            "--gold-category", str(self.root / "gc.csv"),
            "--gold-urgency", str(self.root / "gu.csv"),
            *extra,
        ]  # fmt: skip
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            code = eval_import_run.main(
                argv, locks=locks or gold_locks(self.category, self.urgency)
            )
        return code, printed.getvalue()

    def test_dry_run_summarises_rows_and_hashes_without_case_data(self):
        """dry-run은 행 수·outcome·hash만 내고 사례 ID·원문·예측·정답을 내지 않는다.

        Returns:
            None: 요약과 비노출을 확인한다.
        """
        self.write_run(
            {"T1": PREDICTION, "T2": PREDICTION, "T3": None},
            {"SCHEMA_VIOLATION": 1},
            spec={"thinking_budget": 512},
        )
        code, printed = self.run_main()
        self.assertEqual(code, 0)
        summary = json.loads(printed)
        plan = eval_import_run.build_plan(
            (self.root / "run.json").read_bytes(),
            (self.root / "labels.json").read_bytes(),
            self.root / "m.json",
            candidate="a",
            data_split="valid",
        )
        self.assertEqual(
            {k: summary[k] for k in ("mode", "experiments", "runs", "cases", "attempts")},
            {"mode": "dry-run", "experiments": 1, "runs": 1, "cases": 3, "attempts": 3},
        )
        self.assertEqual(
            summary["outcomes"], {"VALID": 2, "INVALID_OUTPUT": 1, "FAILED": 0, "HELD": 0}
        )
        self.assertEqual(summary["knobs"], {"thinking_budget": 512})
        self.assertEqual(summary["config_hash"], eval_import_run.hashes(plan.body)["config_hash"])
        for text in ("T1", SECRET, "WARRANTY", "RETURN"):
            self.assertNotIn(text, printed)
        self.assertEqual(
            plan.import_ref,
            hashlib.sha256((self.root / "run.json").read_bytes()).hexdigest()[:12],
        )
        self.assertEqual(plan.attempts["T3"], ("SCHEMA_VIOLATION", None))
        self.assertEqual(plan.attempts["T1"], ("VALID", PREDICTION))
        body = plan.body
        self.assertEqual(
            (body.purpose, body.stage, body.data_split, body.model_id, body.data_origin),
            ("pipeline", "classification", "valid", "gemini-x", "synthetic"),
        )
        self.assertEqual(body.targets[0].attributes, {"channel": "EMAIL"})

    def test_inconsistent_inputs_are_rejected(self):
        """outcome을 하나로 정할 수 없거나 라벨·대상·분할·정답이 결과와 맞지 않으면 종료 코드 1이다.

        Returns:
            None: 종료 코드와 오류 코드를 확인한다.
        """
        labels = {"T1": PREDICTION, "T2": None, "T3": None}
        cases = [
            ("두 종류", lambda: self.write_run(labels, {"SCHEMA_VIOLATION": 1, "TIMEOUT": 1})),
            ("합계 초과", lambda: self.write_run(labels, {"SCHEMA_VIOLATION": 2, "TIMEOUT": 1})),
            ("VALID 불일치", lambda: self.write_run(labels, {"VALID": 2, "TIMEOUT": 1})),
            ("대상 hash", lambda: self.tamper_run("target_ids_sha256", "0" * 64)),
            ("수 불일치", lambda: self.write_run(labels, {"SCHEMA_VIOLATION": 1})),
            ("라벨 hash", lambda: self.write_run(labels, {"TIMEOUT": 2}, label_sha="0" * 64)),
            ("코드 밖 예측", lambda: self.write_run({**labels, "T2": {"x": 1}}, {"TIMEOUT": 1})),
            (
                "모르는 입력 판본",
                lambda: self.tamper_run("input_version", "cs-classification-input-v9"),
            ),
            ("판본·옵션 불일치", lambda: self.tamper_run("strip_phone_annotation", False)),
        ]
        for name, prepare in cases:
            with self.subTest(name):
                prepare()
                code, printed = self.run_main()
                self.assertEqual(code, 1)
                self.assertTrue(printed.startswith("EVAL_IMPORT_FAILED: "))
                self.assertNotIn(SECRET, printed)
        self.write_run(labels, {"TIMEOUT": 2})
        self.assertEqual(self.run_main()[0], 0)
        self.assertEqual(self.run_main("--data-split", "train")[0], 1)
        wrong = gold_locks(b"other", self.urgency)
        self.assertEqual(
            self.run_main(locks=wrong), (1, "EVAL_IMPORT_FAILED: EVAL_IMPORT_GOLD_MISMATCH\n")
        )

    def test_input_builder_version_follows_the_run_meta(self):
        """실험 입력 판본은 결과 meta를 따르고, meta에 판본이 없던 이전 결과는 표기 제거 v3으로 기록한다.

        Returns:
            None: 판본별 등록 요청과 config hash 차이를 확인한다.
        """
        versions = {}
        for name, meta in (
            ("legacy", {}),
            ("v4", {"input_version": INPUT_VERSION, "strip_phone_annotation": False}),
            ("v3", {"input_version": INPUT_VERSION_STRIPPED, "strip_phone_annotation": True}),
        ):
            self.write_run({"T1": PREDICTION, "T2": None, "T3": None}, {"TIMEOUT": 2})
            run = json.loads((self.root / "run.json").read_text())
            run["meta"] |= meta
            (self.root / "run.json").write_text(json.dumps(run))
            plan = eval_import_run.build_plan(
                (self.root / "run.json").read_bytes(),
                (self.root / "labels.json").read_bytes(),
                self.root / "m.json",
                candidate="a",
                data_split="valid",
            )
            versions[name] = (
                plan.body.input_builder_version,
                eval_import_run.hashes(plan.body)["config_hash"],
            )
        self.assertEqual(versions["legacy"], versions["v3"])
        self.assertEqual(versions["v3"][0], INPUT_VERSION_STRIPPED)
        self.assertEqual(versions["v4"][0], INPUT_VERSION)
        self.assertNotEqual(versions["v3"][1], versions["v4"][1])

    def test_test_split_exits_with_code_2(self):
        """test 분할을 선언한 manifest는 종료 코드 2다.

        Returns:
            None: 종료 코드를 확인한다.
        """
        self.write_manifest(["valid", "test"], (self.root / "t.csv").read_bytes())
        self.assertEqual(self.run_main()[0], 2)

    def test_execute_needs_actor_and_a_local_non_proxy_dsn(self):
        """--execute는 --actor가 필요하고, 원격 DSN과 운영자 Proxy 포트 55434는 거부한다.

        Returns:
            None: 종료 코드와 DSN 판정을 확인한다.
        """
        self.assertEqual(
            self.run_main("--execute"), (1, "EVAL_IMPORT_FAILED: EVAL_IMPORT_ACTOR_REQUIRED\n")
        )
        check = eval_import_run.check_dsn
        check("postgresql://u:p@127.0.0.1:55439/db", False)
        for dsn, allow in (
            ("postgresql://u:p@db.example.invalid:5432/db", False),
            ("postgresql://u:p@127.0.0.1:55434/db", False),
            ("postgresql://u:p@127.0.0.1:55434/db", True),
            ("", True),
        ):
            with self.subTest(dsn=dsn, allow=allow), self.assertRaises(ValueError):
                check(dsn, allow)
        check("postgresql://u:p@db.example.invalid:5432/db", True)


if __name__ == "__main__":
    unittest.main()
