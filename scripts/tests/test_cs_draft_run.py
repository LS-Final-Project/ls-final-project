"""KAN-144 초안 후보 비교 실행기. 합성 manifest·분류·근거로 dry-run과 실행 결과 형식을 검사한다. 네트워크 없음."""

import contextlib
import csv
import hashlib
import io
import json
import os
import stat
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from backend.engine.cs.classification import ClassificationOutput
from backend.engine.cs.drafting import (
    DRAFT_INPUT_VERSION,
    EvidenceSnippet,
    build_draft_input,
    render_prompt,
)
from backend.engine.cs.prompt_registry import prompt_fingerprint
from backend.shared.ai import Generation, Usage
from scripts import cs_draft_run

KEY = "fixture-key-must-not-leak"
CODES = ("serial_no", "purchase_doc", "photos")
WARRANTY = {
    "category_code": "WARRANTY",
    "secondary_category": None,
    "urgency_flag": "NONE",
    "provided_info": ["serial_no"],
    "return_subtype": None,
}
SNIPPET = {
    "chunk_id": "c1",
    "snippet": "Warranty period is 18 months.",
    "section_path": ["Warranty"],
    "document_sha256": "a" * 64,
}


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


class DraftRunTests(unittest.TestCase):
    def setUp(self):
        """합성 manifest·분류·근거·코드북·후보 파일을 만든다.

        Args:
            없음.

        Returns:
            None: 경로를 준비한다.
        """
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        products = {"T1": "MCCB-ABS-103c", "T2": "MCCB-ABS-103c", "T3": "MCCB-ABS-103c"}
        products |= {"T4": "MCCB-ABS-103c", "T5": "NOPE-1"}
        tickets = [
            {
                "ticket_id": ticket_id,
                "data_split": "train",
                "channel": "EMAIL",
                "body_language": "en",
                "inquiry_subject": "Warranty",
                "inquiry_body": f"Breaker {ticket_id} tripped, serial: ZX-{ticket_id}.",
                "product_code": product,
            }
            for ticket_id, product in products.items()
        ]
        self.write_manifest(["train"], write_csv(self.root / "t.csv", list(tickets[0]), tickets))
        (self.root / "cls.json").write_text(
            json.dumps(
                {
                    "T1": WARRANTY,
                    "T2": None,
                    "T3": WARRANTY | {"provided_info": ["bogus"]},
                    "T4": WARRANTY,
                    "T5": WARRANTY,
                }
            )
        )
        (self.root / "ev.json").write_text(json.dumps({"T1": [SNIPPET]}))
        write_csv(self.root / "codes.csv", ["code"], [{"code": c} for c in CODES])
        (self.root / "cands.json").write_text(
            json.dumps(
                [
                    {"name": "a", "model": "gemini-3.6-flash", "prompt_version": "v1"},
                    {"name": "b", "model": "unpriced-model", "prompt_version": "v1"},
                ]
            )
        )
        self.env = patch.dict(os.environ, {"GEMINI_API_KEY": KEY})
        self.env.start()
        os.environ.pop("GEMINI_MAX_OUTPUT_TOKENS", None)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def write_manifest(self, splits, raw):
        (self.root / "m.json").write_text(
            json.dumps(
                {
                    "tickets": {
                        "path": "t.csv",
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "splits": splits,
                        "rows": 5,
                    }
                }
            )
        )

    def run_main(self, *extra, factory=None):
        """main을 부르고 종료 코드와 출력을 돌려준다.

        Args:
            *extra: 추가 인자.
            factory: 주입할 공급자 함수.

        Returns:
            tuple[int, str]: 종료 코드와 표준 출력.
        """
        r = self.root
        argv = [
            "--manifest", str(r / "m.json"),
            "--product-column", "product_code",
            "--classification", str(r / "cls.json"),
            "--evidence", str(r / "ev.json"),
            "--allowed-info-codes", str(r / "codes.csv"),
            "--info-code-column", "code",
            "--candidates", str(r / "cands.json"),
            *extra,
        ]  # fmt: skip
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch.object(cs_draft_run, "create_client") as c:
            code = cs_draft_run.main(argv, factory=factory)
        c.assert_not_called()
        return code, out.getvalue()

    def test_dry_run_counts_skips_and_estimates_from_rendered_prompts(self):
        """VALID 분류만 쓰고(null·검사 실패 보류), 정책 없는 제품은 빼며, 근거 없음을 세고 비용을 손계산과 맞춘다.

        Args:
            없음.

        Returns:
            None: 건수와 추정 값을 확인한다.
        """
        code, printed = self.run_main()
        self.assertEqual(code, 0)
        report = json.loads(printed.splitlines()[-1])
        self.assertEqual(
            {
                k: report[k]
                for k in ("cases", "held_classification", "missing_policy", "no_evidence")
            },
            {"cases": 2, "held_classification": 2, "missing_policy": 1, "no_evidence": 1},
        )
        self.assertIn("근거 없는 사례", printed)
        required = frozenset(
            "serial_no;purchase_doc;install_date;failure_description;photos".split(";")
        )
        prompts = []
        for ticket_id, evidence in (("T1", [EvidenceSnippet(**SNIPPET)]), ("T4", [])):
            payload = build_draft_input(
                {
                    "channel": "EMAIL",
                    "body_language": "en",
                    "subject": "Warranty",
                    "body": f"Breaker {ticket_id} tripped, serial: ZX-{ticket_id}.",
                },
                classification=ClassificationOutput(**WARRANTY),
                required_info_codes=required,
                already_asked_codes=frozenset(),
                evidence=evidence,
                sensitive_values={},
            ).payload
            prompts.append(render_prompt(payload, prompt_version="v1"))
        priced, unpriced = report["candidates"]
        chars = sum(len(p) for p in prompts)
        upper = sum(len(p.encode("utf-8")) for p in prompts)
        self.assertEqual((priced["calls"], priced["held"], priced["prompt_chars"]), (2, 0, chars))
        self.assertEqual(priced["input_tokens_upper"], upper)
        output = Decimal(2 * 1024) * Decimal("3.75") / Decimal(1_000_000)
        upper_cost = Decimal(upper) * Decimal("0.75") / Decimal(1_000_000)
        self.assertEqual(Decimal(priced["total_cost_upper"]), upper_cost + output)
        self.assertIsNone(unpriced["total_cost_upper"])
        self.assertNotIn("ZX-T1", printed)

    def test_execute_writes_result_with_rule_based_expectations_and_no_text(self):
        """--execute는 주입한 공급자로 하네스를 돌려 meta(기대값 규칙·건수·지문)와 결과를 쓰고 원문·키·ID를 넣지 않는다.

        Args:
            없음.

        Returns:
            None: 결과 형식과 비노출을 확인한다.
        """
        prompts = []

        def factory(spec, settings):
            def provider(prompt):
                prompts.append(prompt)
                return Generation("not json", Usage(5, 3, None, None, 8), "fake-v", ["STOP"])

            return provider

        out = self.root / "result.json"
        code, printed = self.run_main("--execute", "--out", str(out), factory=factory)
        self.assertEqual(code, 0, printed)
        text = out.read_text(encoding="utf-8")
        result = json.loads(text)
        meta = result["meta"]
        self.assertEqual((meta["cases"], meta["no_evidence"]), (2, 1))
        self.assertIn("정답 아님", meta["expected_values"])
        self.assertEqual(meta["case_ids_sha256"], hashlib.sha256(b"T1\nT4").hexdigest())
        self.assertEqual(
            [c["prompt_fingerprint"] for c in meta["candidates"]],
            [prompt_fingerprint("draft", "v1")] * 2,
        )
        self.assertEqual(sorted(result["result"]["ranking"]), ["a", "b"])
        # 모든 응답이 형식 위반이라 기본 재호출(attempt 2)까지 사례·후보마다 두 번 부른다.
        self.assertEqual(len(prompts), 8)
        self.assertEqual(meta["invalid_output_retries"], 1)
        for secret in (KEY, "Breaker T1", "ZX-T1", "not json", "Warranty period"):
            self.assertNotIn(secret, text)

    def test_audit_out_writes_private_case_audit_without_draft_text(self):
        """--audit-out은 후보별 0600 사례 감사를 실행 사례만 쓰고 meta에 sha256을 남기며, 초안·근거·원문은 넣지 않는다.

        Args:
            없음.

        Returns:
            None: 항목 값·권한·meta·부분집합·비노출을 확인한다.
        """

        def factory(spec, settings):
            def provider(prompt):
                return Generation(
                    "not json",
                    Usage(1000, 200, 50, None, 1250),
                    "flash-0429",
                    ["STOP"],
                    model="gemini-3.6-flash",
                    attempts=2,
                )

            return provider

        audit = self.root / "audit"
        audit.mkdir()
        out = self.root / "result.json"
        code, printed = self.run_main(
            "--execute", "--out", str(out), "--audit-out", str(audit), "--ticket-ids", "T4",
            factory=factory,
        )  # fmt: skip
        self.assertEqual(code, 0, printed)
        files = {}
        for name in ("a", "b"):
            path = audit / f"{name}.audit.json"
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            files[name] = path.read_bytes()
            data = json.loads(files[name])
            self.assertEqual(list(data), ["T4"])
            entry = data["T4"]
            self.assertEqual(
                {k: entry[k] for k in ("outcome", "model", "model_version", "attempts")},
                {"outcome": "SCHEMA_VIOLATION", "model": "gemini-3.6-flash"}
                | {"model_version": "flash-0429", "attempts": 2},
            )
            self.assertEqual(entry["input_version"], DRAFT_INPUT_VERSION)
            self.assertRegex(entry["input_sha256"], "^[0-9a-f]{64}$")
            self.assertEqual(entry["usage"]["total_tokens"], 1250)
            self.assertTrue(entry["pricing_version"].startswith("gemini-3.6-flash@"))
        result = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(
            result["meta"]["audit_files"],
            {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
        )
        self.assertNotIn("attempts", result["result"])
        # 모든 응답이 형식 위반이라 T4를 attempt 2로 한 번 더 불렀고, 그 감사는 retry-audit에 있다.
        retried = {name: (audit / f"{name}.retry-audit.json").read_bytes() for name in ("a", "b")}
        for data in retried.values():
            self.assertEqual(json.loads(data)["T4"]["outcome"], "SCHEMA_VIOLATION")
        self.assertEqual(
            result["meta"]["retry_audit_files"],
            {name: hashlib.sha256(data).hexdigest() for name, data in retried.items()},
        )
        text = b"".join([*files.values(), *retried.values()]).decode()
        for secret in (KEY, "Breaker T4", "not json", "Warranty period", "request_codes"):
            self.assertNotIn(secret, text)
        self.assertEqual(self.run_main("--audit-out", str(audit))[0], 1)
        plain = self.root / "plain.json"
        self.assertEqual(self.run_main("--execute", "--out", str(plain), factory=factory)[0], 0)
        self.assertNotIn("audit_files", json.loads(plain.read_text())["meta"])

    def draft_factory(self, calls, stop_after=None):
        """입력의 근거로 유효한 세 선택지를 답하고(근거 없으면 UNCONFIRMED) a의 T1 첫 호출만 형식 위반을 내는
        공급자 함수. stop_after번 호출한 뒤 다음 호출에서 KeyboardInterrupt를 낸다.

        Args:
            calls: 호출을 쌓을 목록.
            stop_after: 중단 기준 호출 수(None이면 끝까지).

        Returns:
            Callable: 공급자 함수.
        """

        def factory(spec, settings):
            def provider(prompt):
                if stop_after is not None and len(calls) >= stop_after:
                    raise KeyboardInterrupt
                payload = json.loads(prompt.split("\n\n입력:\n", 1)[1])
                first = (spec["name"], prompt) not in calls
                calls.append((spec["name"], prompt))
                chunks = [item["chunk_id"] for item in payload["evidence"]]
                if spec["name"] == "a" and chunks and first:
                    return Generation("not json", Usage(5, 3, None, None, 8), "fake-v", ["STOP"])
                sentence = {
                    "text": "Synthetic answer.",
                    "kind": "EVIDENCE" if chunks else "UNCONFIRMED",
                    "evidence_chunk_ids": chunks[:1],
                }
                options = [
                    {
                        "label": label,
                        "sentences": [sentence],
                        "info_requests": [
                            {"info_code": code, "question": "Please send it."}
                            for code in payload["request_info_codes"]
                        ]
                        if label == "INFO_FIRST"
                        else [],
                    }
                    for label in ("CONCISE", "DETAILED", "INFO_FIRST")
                ]
                return Generation(
                    json.dumps({"options": options}),
                    Usage(5, 3, None, None, 8),
                    "fake-v",
                    ["STOP"],
                    model="gemini-3.6-flash",
                )

            return provider

        return factory

    def test_interrupted_draft_run_resumes_with_same_result(self):
        """초안 실행도 중단하면 진행 파일이 남고(초안 문장은 예측 진행 파일에만), --resume은 남은 사례만 불러 전체
        실행과 같은 결과를 낸다. 시도 2 재호출은 사례 단위로 함께 기록된다.

        Args:
            없음.

        Returns:
            None: 종료 코드·진행 파일·호출 수·결과 동일·불일치 거부를 확인한다.
        """
        full_calls = []
        full = self.root / "full.json"
        code, printed = self.run_main(
            "--execute", "--out", str(full), factory=self.draft_factory(full_calls)
        )
        self.assertEqual(code, 0, printed)
        expected = json.loads(full.read_text(encoding="utf-8"))
        self.assertEqual(expected["result"]["candidates"][0]["final"]["recovered"], 1)
        out = self.root / "result.json"
        progress = self.root / "result.json.progress.jsonl"
        labels = self.root / "result.json.progress-labels.jsonl"
        stopped = []
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code, _ = self.run_main(
                "--execute", "--out", str(out), factory=self.draft_factory(stopped, stop_after=3)
            )
        # a는 T1(형식 위반 → 시도 2)·T4를 끝냈고 b의 T1 호출에서 멈췄다.
        self.assertEqual((code, err.getvalue().strip()), (130, "EVAL_RUN_INTERRUPTED completed=2"))
        text = progress.read_text(encoding="utf-8")
        for secret in ("Synthetic answer", "T1", "Breaker", "not json"):
            self.assertNotIn(secret, text)
        self.assertIn("Synthetic answer", labels.read_text(encoding="utf-8"))
        mismatch = []
        code, printed = self.run_main(
            "--execute", "--out", str(out), "--resume", str(progress), "--seed", "1",
            factory=self.draft_factory(mismatch),
        )  # fmt: skip
        self.assertEqual((code, mismatch), (1, []))
        self.assertTrue(printed.startswith("EVAL_RESUME_MISMATCH"))
        with patch.object(cs_draft_run, "prompt_fingerprint", return_value="0" * 64):
            code, printed = self.run_main(
                "--execute", "--out", str(out), "--resume", str(progress),
                factory=self.draft_factory(mismatch),
            )  # fmt: skip
        self.assertEqual((code, mismatch), (1, []))
        resumed_calls = []
        code, printed = self.run_main(
            "--execute", "--out", str(out), "--resume", str(progress),
            factory=self.draft_factory(resumed_calls),
        )  # fmt: skip
        self.assertEqual(code, 0, printed)
        self.assertEqual(len(resumed_calls), len(full_calls) - len(stopped))
        result = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(result["meta"]["resumed_cases"], 2)
        for data in (result, expected):
            for row in data["result"]["candidates"]:
                row.pop("latency_ms")
        self.assertEqual(result["result"], expected["result"])
        self.assertFalse(progress.exists() or labels.exists())

    def test_strip_phone_annotation_flag_is_opt_in_and_recorded(self):
        """PHONE 표기는 기본으로 초안 입력에 남고 --strip-phone-annotation일 때만 지워지며 meta에 값·판본이 남는다.

        Args:
            없음.

        Returns:
            None: 프롬프트와 meta를 확인한다.
        """
        tickets = [
            {
                "ticket_id": ticket_id,
                "data_split": "train",
                "channel": "PHONE",
                "body_language": "en",
                "inquiry_subject": "Warranty",
                "inquiry_body": f"Urgency: SAFETY\nBreaker {ticket_id} tripped.",
                "product_code": "MCCB-ABS-103c",
            }
            for ticket_id in ("T1", "T2", "T3", "T4", "T5")
        ]
        self.write_manifest(["train"], write_csv(self.root / "t.csv", list(tickets[0]), tickets))
        for extra, kept, version in (
            ((), True, "cs-draft-input-v2"),
            (("--strip-phone-annotation",), False, "cs-draft-input-v1"),
        ):
            prompts = []

            def factory(spec, settings, prompts=prompts):
                def provider(prompt):
                    prompts.append(prompt)
                    return Generation("not json", Usage(5, 3, None, None, 8), "fake-v", ["STOP"])

                return provider

            out = self.root / f"strip-{bool(extra)}.json"
            code, printed = self.run_main("--execute", "--out", str(out), *extra, factory=factory)
            self.assertEqual(code, 0, printed)
            meta = json.loads(out.read_text(encoding="utf-8"))["meta"]
            self.assertEqual(
                (meta["strip_phone_annotation"], meta["input_version"]), (bool(extra), version)
            )
            self.assertTrue(prompts)
            self.assertTrue(all(("Urgency: SAFETY" in prompt) is kept for prompt in prompts))

    def test_ticket_ids_rerun_only_that_subset(self):
        """--ticket-ids는 그 사례만 재실행하고 부분 집합 표시를 단다.

        Args:
            없음.

        Returns:
            None: 재실행 수를 확인한다.
        """
        factory = Mock(
            side_effect=lambda spec, settings: lambda prompt: Generation("x", Usage(), None, [])
        )
        out = self.root / "subset.json"
        code, _ = self.run_main(
            "--execute", "--out", str(out), "--ticket-ids", "T4", factory=factory
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.read_text())["result"]["subset"], {"rerun": 1, "total": 2})

    def test_rule_based_expectations_follow_the_stated_rule(self):
        """기대값은 근거 ID 전부, 필요 − 제공 요청 코드, 근거 유무로 답변 가능 여부다.

        Args:
            없음.

        Returns:
            None: build_cases 결과를 확인한다.
        """
        record = {"channel": "EMAIL", "body_language": "en", "subject": None, "body": "Help."}
        required = {("LV_BREAKER", "WARRANTY"): frozenset({"serial_no", "photos"})}
        cases, gold, counts = cs_draft_run.build_cases(
            {"T1": record, "T4": record},
            {"T1": "MCCB-ABS-103c", "T4": "MCCB-ABS-103c"},
            {"T1": WARRANTY, "T4": WARRANTY},
            {"T1": [SNIPPET]},
            required,
            frozenset(CODES),
            {},
        )
        self.assertEqual(
            (gold["T1"].request_codes, gold["T1"].relevant_chunk_ids, gold["T1"].answerable),
            (frozenset({"photos"}), frozenset({"c1"}), True),
        )
        self.assertEqual((gold["T4"].input_chunk_ids, gold["T4"].answerable), (frozenset(), False))
        self.assertEqual(cases["T1"].already_asked_codes, frozenset())
        self.assertEqual(counts["no_evidence"], 1)

    def test_dry_run_renders_each_candidate_version(self):
        """dry-run은 후보의 판본으로 프롬프트를 만들므로 판본이 바뀌면 크기도 바뀐다.

        Args:
            없음.

        Returns:
            None: 판본별 문자 수 차이를 확인한다.
        """
        from backend.engine.cs import prompt_registry

        (self.root / "cands.json").write_text(
            json.dumps(
                [
                    {"name": "a", "model": "m", "prompt_version": "v1"},
                    {"name": "b", "model": "m", "prompt_version": "test-v2"},
                ]
            )
        )
        with patch.dict(prompt_registry.PROMPTS["draft"], {"test-v2": "짧은 판본.\n"}):
            code, printed = self.run_main()
        self.assertEqual(code, 0)
        first, second = json.loads(printed.splitlines()[-1])["candidates"]
        self.assertGreater(first["prompt_chars"], second["prompt_chars"])

    def test_unknown_version_stops_before_any_paid_call(self):
        """모르는 판본 후보가 뒤에 있어도 --execute는 공급자를 부르기 전에 종료 코드 1로 멈춘다.

        Args:
            없음.

        Returns:
            None: 공급자 미호출을 확인한다.
        """
        (self.root / "cands.json").write_text(
            json.dumps(
                [
                    {"name": "a", "model": "m", "prompt_version": "v1"},
                    {"name": "b", "model": "m", "prompt_version": "v9"},
                ]
            )
        )
        calls = []

        def factory(spec, settings):
            def provider(prompt):
                calls.append(spec["name"])
                return Generation("x", Usage(), None, [])

            return provider

        code, _ = self.run_main("--execute", "--out", str(self.root / "r.json"), factory=factory)
        self.assertEqual(code, 1)
        self.assertEqual(calls, [])

    def test_default_factory_passes_generation_knobs_to_the_text_adapter(self):
        """실제 공급자 함수는 후보의 생성 조절 항목을 텍스트 어댑터에 넘긴다(클라이언트·어댑터는 대역).

        Args:
            없음.

        Returns:
            None: 어댑터 인자를 확인한다.
        """
        spec = {"name": "a", "model": "m", "prompt_version": "v1", "max_output_tokens": 4096}
        settings = cs_draft_run.AISettings()
        with (
            patch.object(cs_draft_run, "create_client", return_value="client"),
            patch.object(cs_draft_run, "gemini_text_provider") as adapter,
        ):
            cs_draft_run.default_factory(settings)(spec, settings)
        self.assertEqual(adapter.call_args.kwargs["generation"].max_output_tokens, 4096)

    def test_invalid_inputs_exit_with_codes(self):
        """test 분할은 2, 잘못된 분류 형식·모르는 재실행 ID·--out 없는 실행·출력 디렉터리 없음은 1이다.

        Args:
            없음.

        Returns:
            None: 종료 코드와 공급자 미호출을 확인한다.
        """
        factory = Mock()
        self.assertEqual(self.run_main("--execute", factory=factory)[0], 1)
        missing = str(self.root / "no-dir" / "r.json")
        self.assertEqual(self.run_main("--execute", "--out", missing, factory=factory)[0], 1)
        self.assertEqual(self.run_main("--ticket-ids", "T9")[0], 1)
        factory.assert_not_called()
        (self.root / "cls.json").write_text(json.dumps([WARRANTY]))
        code, printed = self.run_main()
        self.assertEqual(code, 1)
        self.assertNotIn("ZX-", printed)
        self.write_manifest(["train", "test"], (self.root / "t.csv").read_bytes())
        self.assertEqual(self.run_main()[0], 2)


if __name__ == "__main__":
    unittest.main()
