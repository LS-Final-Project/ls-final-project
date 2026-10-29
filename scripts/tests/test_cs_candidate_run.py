"""KAN-144 후보 비교 실행기. 합성 manifest·정답으로 dry-run 계산과 실행 결과 형식을 검사한다. 네트워크 없음."""

import contextlib
import csv
import hashlib
import io
import json
import math
import os
import stat
import tempfile
import unittest
import unittest.mock
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from backend.engine.cs.classification import ClassificationOutput, build_input
from backend.engine.cs.prompt_registry import get_prompt, prompt_fingerprint
from backend.shared.ai import Generation, Usage
from eval.cs_gold_locks import GoldLock
from scripts import cs_candidate_run, cs_draft_run

KEY = "fixture-key-must-not-leak"
CODES = ("TEST_PHOTO", "TEST_SERIAL")
ANSWER = (
    '{"category_code":"RETURN","secondary_category":null,"urgency_flag":"NONE",'
    '"provided_info":[],"return_subtype":null}'
)


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> bytes:
    """합성 CSV를 쓰고 바이트를 돌려준다.

    Args:
        path: 쓸 경로.
        fields: 열 이름.
        rows: 행.

    Returns:
        bytes: 파일 내용.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(buffer.getvalue(), encoding="utf-8")
    return path.read_bytes()


class CandidateRunTests(unittest.TestCase):
    def setUp(self):
        """합성 개발 manifest·정답·코드북·후보 파일을 만든다.

        Args:
            없음.

        Returns:
            None: 경로와 잠금 교체를 준비한다.
        """
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        tickets = [
            {
                "ticket_id": f"SYN-{i}",
                "data_split": "train",
                "channel": "EMAIL",
                "body_language": "en",
                "inquiry_subject": "Return",
                "inquiry_body": f"Please take breaker {i} back." if i != 2 else "   ",
                "product_code": "MCCB-ABS-103c",
            }
            for i in range(4)
        ]
        self.locks = patch.dict(cs_candidate_run.GOLD_LOCKS)
        self.locks.start()
        self.write_sources(tickets)
        write_csv(
            self.root / "codes.csv", ["code", "label"], [{"code": c, "label": c} for c in CODES]
        )
        (self.root / "candidates.json").write_text(
            json.dumps(
                [
                    {"name": "a", "model": "gemini-3.6-flash", "prompt_version": "v1"},
                    {"name": "b", "model": "unpriced-model", "prompt_version": "v1"},
                ]
            )
        )
        self.env = patch.dict(os.environ, {"GEMINI_API_KEY": KEY}, clear=False)
        self.env.start()
        os.environ.pop("GEMINI_MAX_OUTPUT_TOKENS", None)

    def tearDown(self):
        self.locks.stop()
        self.env.stop()
        self.temp.cleanup()

    def write_sources(self, tickets):
        """tickets.csv·manifest·잠긴 정답 두 개를 쓰고 정답 잠금을 그 hash로 바꾼다.

        Args:
            tickets: 합성 문의 행.

        Returns:
            None: 파일과 잠금을 준비한다.
        """
        raw = write_csv(self.root / "tickets.csv", list(tickets[0]), tickets)
        self.write_manifest(["train", "valid"], raw, len(tickets))
        for metric, column, value in (
            ("category", "category_code", "RETURN"),
            ("urgency", "urgency_flag", "NONE"),
        ):
            data = write_csv(
                self.root / f"{metric}.csv",
                ["ticket_id", column],
                [{"ticket_id": t["ticket_id"], column: value} for t in tickets],
            )
            cs_candidate_run.GOLD_LOCKS[metric] = GoldLock(
                "v", hashlib.sha256(data).hexdigest(), ("ticket_id", column), ()
            )

    def write_manifest(self, splits, raw, rows):
        """tickets 항목 하나인 manifest를 쓴다.

        Args:
            splits: 선언 분할.
            raw: tickets.csv 바이트.
            rows: 행 수.

        Returns:
            None: manifest.json을 쓴다.
        """
        (self.root / "manifest.json").write_text(
            json.dumps(
                {
                    "tickets": {
                        "path": "tickets.csv",
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "splits": splits,
                        "rows": rows,
                    }
                }
            )
        )

    def args(self, *extra):
        r = self.root
        return [
            "--manifest", str(r / "manifest.json"),
            "--gold-category", str(r / "category.csv"),
            "--gold-urgency", str(r / "urgency.csv"),
            "--allowed-info-codes", str(r / "codes.csv"),
            "--info-code-column", "code",
            "--candidates", str(r / "candidates.json"),
            "--limit", "10",
            *extra,
        ]  # fmt: skip

    def run_main(self, *extra, factory=None):
        """main을 부르고 종료 코드와 출력을 돌려준다.

        Args:
            *extra: 추가 인자.
            factory: 주입할 공급자 함수.

        Returns:
            tuple[int, str]: 종료 코드와 표준 출력.
        """
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cs_candidate_run.main(self.args(*extra), factory=factory)
        return code, out.getvalue()

    def test_dry_run_reports_counts_tokens_and_cost_upper_bound(self):
        """dry-run은 네트워크 없이 건수·근사 토큰(문자/4)·예상 비용 상한을 손계산과 같게 낸다.

        Args:
            없음.

        Returns:
            None: 후보별 행을 확인한다.
        """
        with patch.object(cs_candidate_run, "create_client") as create:
            code, printed = self.run_main()
        create.assert_not_called()
        self.assertEqual(code, 0)
        report = json.loads(printed.splitlines()[-1])
        self.assertEqual(report["targets"], 4)
        instruction = (
            get_prompt("classification", "v1")
            + json.dumps(
                ClassificationOutput.model_json_schema(), ensure_ascii=False, sort_keys=True
            )
            + "\n허용 정보 코드: "
            + json.dumps(sorted(CODES), ensure_ascii=False)
        )
        prompts = [
            instruction
            + "\n\n입력:\n"
            + build_input(
                {
                    "channel": "EMAIL",
                    "body_language": "en",
                    "subject": "Return",
                    "body": f"Please take breaker {i} back.",
                },
                sensitive_values={},
            ).payload.model_dump_json()
            for i in (0, 1, 3)
        ]
        chars = sum(len(prompt) for prompt in prompts)
        upper = sum(len(prompt.encode("utf-8")) for prompt in prompts)
        tokens = math.ceil(chars / 4)
        priced, unpriced = report["candidates"]
        self.assertEqual((priced["calls"], priced["held"], priced["prompt_chars"]), (3, 1, chars))
        self.assertEqual(
            (priced["approx_input_tokens"], priced["input_tokens_upper"], priced["prompt_bytes"]),
            (tokens, upper, upper),
        )
        million = Decimal(1_000_000)
        input_cost = Decimal(tokens) * Decimal("0.75") / million
        input_upper = Decimal(upper) * Decimal("0.75") / million
        output_cost = Decimal(3 * 1024) * Decimal("3.75") / million
        self.assertEqual(Decimal(priced["estimated_input_cost"]), input_cost)
        self.assertEqual(Decimal(priced["input_cost_upper"]), input_upper)
        self.assertEqual(Decimal(priced["output_cost_upper"]), output_cost)
        self.assertEqual(Decimal(priced["total_cost_upper"]), input_upper + output_cost)
        self.assertGreater(input_upper, input_cost)
        self.assertIn("usage", priced["note"])
        self.assertEqual(priced["currency"], "USD")
        self.assertIsNone(unpriced["total_cost_upper"])
        self.assertNotIn(KEY, printed)

    def test_korean_bodies_make_the_byte_upper_bound_far_above_the_char_estimate(self):
        """한국어 본문은 문자/4 근사보다 실제 토큰이 많을 수 있어 상한(바이트)이 근사의 4배를 넘는다.

        Args:
            없음.

        Returns:
            None: 상한과 근사의 관계를 확인한다.
        """
        body = "차단기가 300 V에서 트립되어 반품을 요청합니다. " * 20
        self.write_sources(
            [
                {
                    "ticket_id": "SYN-KO",
                    "data_split": "train",
                    "channel": "EMAIL",
                    "body_language": "ko",
                    "inquiry_subject": "반품",
                    "inquiry_body": body,
                }
            ]
        )
        code, printed = self.run_main()
        self.assertEqual(code, 0)
        row = json.loads(printed.splitlines()[-1])["candidates"][0]
        self.assertEqual(row["approx_input_tokens"], math.ceil(row["prompt_chars"] / 4))
        self.assertGreater(row["input_tokens_upper"], 4 * row["approx_input_tokens"])
        self.assertGreater(
            Decimal(row["input_cost_upper"]), 4 * Decimal(row["estimated_input_cost"])
        )

    def test_missing_output_directory_stops_before_paid_calls(self):
        """--out 부모 디렉터리가 없으면 공급자를 부르기 전에 종료 코드 1로 끝난다.

        Args:
            없음.

        Returns:
            None: 공급자 미호출을 확인한다.
        """
        factory = unittest.mock.Mock()
        missing = self.root / "no-such-dir" / "result.json"
        code, printed = self.run_main("--execute", "--out", str(missing), factory=factory)
        self.assertEqual(code, 1)
        factory.assert_not_called()
        self.assertIn("CS_RUN_FAILED", printed)

    def test_final_write_failure_is_reported_without_traceback(self):
        """실행 뒤 결과 쓰기가 실패하면 트레이스백 없이 종료 코드 1과 안내 문구만 낸다.

        Args:
            없음.

        Returns:
            None: 종료 코드와 문구를 확인한다.
        """

        def factory(spec, codes, settings):
            return lambda payload: Generation(ANSWER, Usage(), "fake-v", ["STOP"])

        out = self.root / "result.json"
        with patch.object(Path, "write_text", side_effect=OSError("disk full " + KEY)):
            code, printed = self.run_main("--execute", "--out", str(out), factory=factory)
        self.assertEqual(code, 1)
        self.assertIn("CS_RUN_OUTPUT_FAILED", printed)
        self.assertNotIn(KEY, printed)

    def test_candidate_output_limit_drives_the_dry_run_and_is_passed_on(self):
        """후보의 max_output_tokens가 dry-run 출력 상한이 되고(thinking 포함이라 따로 더하지 않음), 실행 때 어댑터·행에 전달된다.

        Args:
            없음.

        Returns:
            None: 출력 상한 비용과 전달된 조절 항목을 확인한다.
        """
        (self.root / "candidates.json").write_text(
            json.dumps(
                [
                    {
                        "name": "a",
                        "model": "gemini-3.6-flash",
                        "prompt_version": "v1",
                        "thinking_budget": 512,
                        "max_output_tokens": 4096,
                    }
                ]
            )
        )
        code, printed = self.run_main()
        self.assertEqual(code, 0)
        [row] = json.loads(printed.splitlines()[-1])["candidates"]
        self.assertEqual(row["max_output_tokens_per_call"], 4096)
        self.assertEqual(
            Decimal(row["output_cost_upper"]),
            Decimal(3 * 4096) * Decimal("3.75") / Decimal(1_000_000),
        )
        self.assertIn("thinking 토큰을 포함", row["note"])
        seen = []

        def factory(spec, codes, settings):
            seen.append(cs_candidate_run.knobs_of(spec))
            return lambda payload: Generation(ANSWER, Usage(), None, [])

        out = self.root / "result.json"
        self.assertEqual(self.run_main("--execute", "--out", str(out), factory=factory)[0], 0)
        self.assertEqual(seen[0].thinking_budget, 512)
        result = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(
            result["result"]["candidates"][0]["knobs"],
            {"thinking_budget": 512, "max_output_tokens": 4096},
        )
        (self.root / "candidates.json").write_text(
            json.dumps(
                [
                    {
                        "name": "a",
                        "model": "m",
                        "prompt_version": "v1",
                        "thinking_budget": 1,
                        "thinking_level": "LOW",
                    }
                ]
            )
        )
        self.assertEqual(self.run_main()[0], 1)

    def test_default_factory_passes_generation_knobs_to_the_adapter(self):
        """실제 공급자 함수는 후보의 생성 조절 항목을 분류 어댑터에 넘긴다(클라이언트·어댑터는 대역).

        Args:
            없음.

        Returns:
            None: 어댑터 인자를 확인한다.
        """
        spec = {"name": "a", "model": "m", "prompt_version": "v1", "thinking_level": "LOW"}
        settings = cs_candidate_run.AISettings()
        with (
            patch.object(cs_candidate_run, "create_client", return_value="client"),
            patch.object(cs_candidate_run, "gemini_classification_provider") as adapter,
        ):
            cs_candidate_run.default_factory(settings)(spec, frozenset(CODES), settings)
        knobs = adapter.call_args.kwargs["generation"]
        self.assertEqual((knobs.thinking_level, knobs.thinking_budget), ("LOW", None))

    def test_test_split_exits_with_code_2(self):
        """manifest가 test 분할을 선언하면 로더 전에 종료 코드 2로 멈춘다.

        Args:
            없음.

        Returns:
            None: 종료 코드와 안내 문구를 확인한다.
        """
        raw = (self.root / "tickets.csv").read_bytes()
        self.write_manifest(["train", "test"], raw, 4)
        code, printed = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn("최종 1회 사용 원칙", printed)

    def test_unlocked_gold_bare_csv_and_missing_out_fail_safely(self):
        """잠금과 다른 정답, manifest가 아닌 CSV, --out 없는 실행은 원문 없이 실패한다.

        Args:
            없음.

        Returns:
            None: 종료 코드 1을 확인한다.
        """
        wrong = self.root / "category-wrong.csv"
        wrong.write_bytes((self.root / "category.csv").read_bytes() + b"SYN-9,QUOTE\n")
        unlocked = self.args()
        unlocked[3] = str(wrong)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cs_candidate_run.main(unlocked), 1)
        bare = self.args()
        bare[1] = str(self.root / "tickets.csv")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cs_candidate_run.main(bare), 1)
        self.assertNotIn("Please take", out.getvalue())
        self.assertEqual(self.run_main("--execute")[0], 1)

    def test_strip_phone_annotation_flag_is_opt_in_and_recorded(self):
        """PHONE 표기는 기본으로 입력에 남고 --strip-phone-annotation일 때만 지워지며 meta에 값·판본이 남는다.

        Args:
            없음.

        Returns:
            None: 공급자 입력 본문과 meta를 확인한다.
        """
        self.write_sources(
            [
                {
                    "ticket_id": f"SYN-{i}",
                    "data_split": "train",
                    "channel": "PHONE",
                    "body_language": "en",
                    "inquiry_subject": "Return",
                    "inquiry_body": f"Urgency: SAFETY\nPlease take breaker {i} back.",
                    "product_code": "MCCB-ABS-103c",
                }
                for i in range(2)
            ]
        )
        for extra, kept, version in (
            ((), True, "cs-classification-input-v4"),
            (("--strip-phone-annotation",), False, "cs-classification-input-v3"),
        ):
            bodies = []

            def factory(spec, codes, settings, bodies=bodies):
                def provider(payload):
                    bodies.append(payload.body)
                    return Generation(ANSWER, Usage(5, 3, None, None, 8), "fake-v", ["STOP"])

                return provider

            out = self.root / f"strip-{bool(extra)}.json"
            code, printed = self.run_main("--execute", "--out", str(out), *extra, factory=factory)
            self.assertEqual(code, 0, printed)
            meta = json.loads(out.read_text(encoding="utf-8"))["meta"]
            self.assertEqual(
                (meta["strip_phone_annotation"], meta["input_version"]), (bool(extra), version)
            )
            self.assertTrue(bodies)
            self.assertTrue(all(("Urgency: SAFETY" in body) is kept for body in bodies))

    def test_execute_writes_harness_result_with_meta_and_no_secrets(self):
        """--execute는 주입한 공급자로 하네스를 돌려 결과·메타를 쓰고 원문·키·표본 ID를 넣지 않는다.

        Args:
            없음.

        Returns:
            None: 결과 JSON 형식을 확인한다.
        """
        calls = []

        def factory(spec, codes, settings):
            def provider(payload):
                calls.append((spec["name"], payload.body))
                return Generation(
                    ANSWER, Usage(5, 3, None, None, 8), "fake-v", ["STOP"], model=spec["model"]
                )

            return provider

        out = self.root / "result.json"
        with patch.object(cs_candidate_run, "create_client") as create:
            code, printed = self.run_main("--execute", "--out", str(out), factory=factory)
        create.assert_not_called()
        self.assertEqual(code, 0, printed)
        text = out.read_text(encoding="utf-8")
        result = json.loads(text)
        self.assertEqual(set(result), {"meta", "result"})
        meta = result["meta"]
        self.assertEqual(meta["targets"], 4)
        expected_ids = "\n".join(f"SYN-{i}" for i in range(4))
        self.assertEqual(
            meta["target_ids_sha256"], hashlib.sha256(expected_ids.encode()).hexdigest()
        )
        self.assertEqual(
            [c["prompt_fingerprint"] for c in meta["candidates"]],
            [prompt_fingerprint("classification", "v1")] * 2,
        )
        self.assertEqual(sorted(result["result"]["ranking"]), ["a", "b"])
        self.assertEqual(len(calls), 6)
        for secret in (KEY, "Please take breaker", "SYN-0", ANSWER):
            self.assertNotIn(secret, text)

    def labelled_run(self, *extra):
        """후보 a는 모두 VALID, 후보 b는 SYN-0만 깨진 응답을 내는 fake로 --execute를 돌린다.

        Args:
            *extra: 추가 인자.

        Returns:
            tuple[int, str, Path]: 종료 코드, 출력, 결과 JSON 경로.
        """

        def factory(spec, codes, settings):
            def provider(payload):
                broken = spec["name"] == "b" and "breaker 0" in payload.body
                return Generation("not json" if broken else ANSWER, Usage(), None, [])

            return provider

        out = self.root / "result.json"
        code, printed = self.run_main("--execute", "--out", str(out), *extra, factory=factory)
        return code, printed, out

    def test_labels_out_writes_private_sorted_labels_with_nulls(self):
        """--labels-out은 후보별 0600 라벨 파일을 쓰고 VALID가 아니거나 보류된 사례는 null이며 meta에 sha256만 남긴다.

        Args:
            없음.

        Returns:
            None: 라벨 형식·권한·meta를 확인한다.
        """
        labels = self.root / "labels"
        labels.mkdir()
        code, printed, out = self.labelled_run("--labels-out", str(labels))
        self.assertEqual(code, 0, printed)
        expected = json.loads(ANSWER)
        files = {}
        for name in ("a", "b"):
            path = labels / f"{name}.labels.json"
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            files[name] = path.read_bytes()
            data = json.loads(files[name])
            self.assertEqual(list(data), ["SYN-0", "SYN-1", "SYN-2", "SYN-3"])
            self.assertIsNone(data["SYN-2"])
            self.assertEqual(data["SYN-1"], expected)
        self.assertEqual(json.loads(files["a"])["SYN-0"], expected)
        self.assertIsNone(json.loads(files["b"])["SYN-0"])
        result = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(
            result["meta"]["label_files"],
            {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        )
        self.assertNotIn("attempts", result["result"])
        self.assertNotIn("SYN-0", out.read_text(encoding="utf-8"))

    def test_no_labels_out_writes_no_label_files_and_bad_requests_stop_early(self):
        """--labels-out이 없으면 라벨 파일·meta 항목이 없고, dry-run과 함께 쓰거나 위험한 후보 이름이면 호출 전에 멈춘다.

        Args:
            없음.

        Returns:
            None: 파일 부재와 종료 코드를 확인한다.
        """
        code, _, out = self.labelled_run()
        self.assertEqual(code, 0)
        self.assertNotIn("label_files", json.loads(out.read_text())["meta"])
        self.assertEqual(list(self.root.rglob("*.labels.json")), [])
        self.assertEqual(self.run_main("--labels-out", str(self.root))[0], 1)
        (self.root / "candidates.json").write_text(
            json.dumps([{"name": "../escape", "model": "m", "prompt_version": "v1"}])
        )
        calls = []
        code, _ = self.run_main(
            "--execute",
            "--out",
            str(self.root / "r.json"),
            "--labels-out",
            str(self.root),
            factory=lambda spec, codes, settings: calls.append(spec) or (lambda p: None),
        )
        self.assertEqual((code, calls), (1, []))
        self.assertFalse((self.root.parent / "escape.labels.json").exists())

    def test_symlinked_label_file_is_refused_before_any_call(self):
        """라벨 파일 자리에 심볼릭 링크가 있으면 따라가지 않고 공급자 호출 전에 종료 코드 1로 멈춘다.

        Args:
            없음.

        Returns:
            None: 링크 대상이 바뀌지 않음과 공급자 미호출을 확인한다.
        """
        labels = self.root / "labels"
        labels.mkdir()
        target = self.root / "shared.txt"
        target.write_text("keep", encoding="utf-8")
        (labels / "a.labels.json").symlink_to(target)
        calls = []
        code, _ = self.run_main(
            "--execute",
            "--out",
            str(self.root / "r.json"),
            "--labels-out",
            str(labels),
            factory=lambda spec, codes, settings: calls.append(spec) or (lambda p: None),
        )
        self.assertEqual((code, calls), (1, []))
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")

    def test_failed_label_write_leaves_no_result_with_hashes(self):
        """라벨을 먼저 쓰므로 라벨 쓰기가 실패하면 sha256을 담은 결과 JSON이 남지 않는다.

        Args:
            없음.

        Returns:
            None: 종료 코드와 빈 결과 파일을 확인한다.
        """
        labels = self.root / "labels"
        labels.mkdir()
        original = cs_candidate_run.write_private

        def failing(path, data):
            if data:
                raise OSError("disk full " + KEY)
            original(path, data)

        with patch.object(cs_candidate_run, "write_private", failing):
            code, printed, out = self.labelled_run("--labels-out", str(labels))
        self.assertEqual(code, 1)
        self.assertIn("CS_RUN_OUTPUT_FAILED", printed)
        self.assertEqual(out.read_text(encoding="utf-8"), "")
        self.assertNotIn(KEY, printed)

    def test_label_file_feeds_the_draft_runner_directly(self):
        """분류 라벨 파일을 초안 실행기의 --classification으로 그대로 넣어 dry-run이 된다(왕복).

        Args:
            없음.

        Returns:
            None: 초안 dry-run의 사례·보류 건수를 확인한다.
        """
        labels = self.root / "labels"
        labels.mkdir()
        self.assertEqual(self.labelled_run("--labels-out", str(labels))[0], 0)
        r = self.root
        argv = [
            "--manifest", str(r / "manifest.json"),
            "--product-column", "product_code",
            "--classification", str(labels / "b.labels.json"),
            "--allowed-info-codes", str(r / "codes.csv"),
            "--info-code-column", "code",
            "--candidates", str(r / "candidates.json"),
        ]  # fmt: skip
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.assertEqual(cs_draft_run.main(argv), 0)
        report = json.loads(printed.getvalue().splitlines()[-1])
        self.assertEqual((report["cases"], report["held_classification"]), (2, 2))
        self.assertEqual(report["no_evidence"], 2)


if __name__ == "__main__":
    unittest.main()
