"""KAN-144 임베딩 후보 비교 실행기. 합성 청크·질의·정답과 fake 임베딩으로 검사한다. 네트워크 없음."""

import contextlib
import io
import json
import math
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import cs_embedding_run

KEY = "fixture-key-must-not-leak"
CHUNKS = {"c1": "Breaker trip curve settings.", "c2": "변압기 탭 조정 방법.", "c3": "Fan control."}
QUERIES = {"q1": "trip curve", "q2": "탭 조정"}
GOLD = {"q1": ["c1"], "q2": ["c2"]}
WORDS = ("trip", "탭", "fan")


def fake_vector(text: str) -> list[float]:
    """단어 포함 여부로 만든 결정적 벡터.

    Args:
        text: 임베딩할 문자열.

    Returns:
        list[float]: 단어별 1/0과 0이 되지 않게 하는 작은 값.
    """
    return [1.0 if word in text.lower() else 0.0 for word in WORDS] + [0.01]


class EmbeddingRunTests(unittest.TestCase):
    def setUp(self):
        """합성 jsonl·후보 파일을 만든다.

        Args:
            없음.

        Returns:
            None: 경로를 준비한다.
        """
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.write_lines("chunks.jsonl", [{"chunk_id": k, "text": v} for k, v in CHUNKS.items()])
        self.write_lines("queries.jsonl", [{"query_id": k, "text": v} for k, v in QUERIES.items()])
        self.write_lines("gold.jsonl", [{"query_id": k, "chunk_ids": v} for k, v in GOLD.items()])
        (self.root / "fts.json").write_text(json.dumps({"q1": ["c3", "c1"], "q2": ["c2"]}))
        (self.root / "cands.json").write_text(
            json.dumps(
                [
                    {"name": "e1", "model": "gemini-embedding-001", "dimension": 4},
                    {"name": "e2", "model": "unpriced-embed", "task_type": "SEMANTIC_SIMILARITY"},
                ]
            )
        )
        self.env = patch.dict(os.environ, {"GEMINI_API_KEY": KEY})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def write_lines(self, name, rows):
        (self.root / name).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))

    def run_main(self, *extra, factory=None):
        """main을 부르고 종료 코드와 출력을 돌려준다.

        Args:
            *extra: 추가 인자.
            factory: 주입할 임베딩 함수.

        Returns:
            tuple[int, str]: 종료 코드와 표준 출력.
        """
        r = self.root
        argv = [
            "--chunks", str(r / "chunks.jsonl"),
            "--queries", str(r / "queries.jsonl"),
            "--gold", str(r / "gold.jsonl"),
            "--candidates", str(r / "cands.json"),
            "--batch-size", "2",
            *extra,
        ]  # fmt: skip
        out = io.StringIO()
        with contextlib.redirect_stdout(out), patch.object(cs_embedding_run, "create_client") as c:
            code = cs_embedding_run.main(argv, factory=factory)
        c.assert_not_called()
        return code, out.getvalue()

    def test_dry_run_separates_batches_from_api_calls_and_costs_input_only(self):
        """dry-run은 하네스 배치 수와 실제 API 호출 수를 나누고 입력 토큰·비용 근사와 상한만 낸다.

        Args:
            없음.

        Returns:
            None: 손계산과 비교한다.
        """
        code, printed = self.run_main()
        self.assertEqual(code, 0)
        report = json.loads(printed.splitlines()[-1])
        texts = [*CHUNKS.values(), *QUERIES.values()]
        chars = sum(len(t) for t in texts)
        upper = sum(len(t.encode("utf-8")) for t in texts)
        priced, unpriced = report["candidates"]
        self.assertEqual((priced["harness_batches"], priced["api_calls"]), (3, 5))
        self.assertEqual(
            (priced["prompt_chars"], priced["approx_input_tokens"], priced["input_tokens_upper"]),
            (chars, math.ceil(chars / 4), upper),
        )
        million = Decimal(1_000_000)
        self.assertEqual(
            Decimal(priced["estimated_input_cost"]),
            Decimal(math.ceil(chars / 4)) * Decimal("0.15") / million,
        )
        self.assertEqual(
            Decimal(priced["total_cost_upper"]), Decimal(upper) * Decimal("0.15") / million
        )
        for key in ("output_cost_upper", "max_output_tokens_per_call", "calls"):
            self.assertNotIn(key, priced)
        self.assertIsNone(unpriced["total_cost_upper"])
        self.assertEqual(
            (unpriced["dimension"], unpriced["task_type"]), (None, "SEMANTIC_SIMILARITY")
        )
        self.assertNotIn("Breaker trip", printed)
        # 배치는 청크·질의를 따로 나눈다(5개를 한 번에 세면 1이 된다).
        (self.root / "cands.json").write_text(
            json.dumps([{"name": "chat", "model": "gemini-3.6-flash"}])
        )
        code, printed = self.run_main("--batch-size", "5")
        [row] = json.loads(printed.splitlines()[-1])["candidates"]
        self.assertEqual((row["harness_batches"], row["api_calls"]), (2, 5))
        # 출력 단가가 있는 모델이어도 임베딩 비용 상한은 입력 상한뿐이다.
        self.assertEqual(row["total_cost_upper"], row["input_cost_upper"])

    def test_execute_writes_harness_rows_fts_and_meta_without_text_or_vectors(self):
        """--execute는 주입한 임베딩으로 하네스를 돌려 후보 행·FTS 행·meta를 쓰고 본문·벡터·키를 넣지 않는다.

        Args:
            없음.

        Returns:
            None: 결과 형식과 비노출을 확인한다.
        """
        seen = []

        def factory(spec, settings):
            def call(batch):
                seen.append((spec["name"], len(batch)))
                return [fake_vector(text) for text in batch]

            return call

        out = self.root / "result.json"
        code, printed = self.run_main(
            "--execute", "--out", str(out), "--fts-ranked", str(self.root / "fts.json"),
            factory=factory,
        )  # fmt: skip
        self.assertEqual(code, 0, printed)
        text = out.read_text(encoding="utf-8")
        result = json.loads(text)
        rows = {row["name"]: row for row in result["result"]["candidates"]}
        self.assertEqual(rows["e1"]["dimension"], 4)
        self.assertEqual(rows["e1"]["mrr"], 1.0)
        self.assertEqual(rows["e1"]["embed_calls"], 3)
        self.assertEqual(result["result"]["fts"]["mrr"], 0.75)
        meta = result["meta"]
        self.assertEqual((meta["chunks"], meta["queries"], meta["k_values"]), (3, 2, [1, 3, 5, 10]))
        self.assertEqual([c["name"] for c in meta["candidates"]], ["e1", "e2"])
        self.assertEqual(len(seen), 6)
        for secret in (KEY, "Breaker trip", "탭 조정", "0.01"):
            self.assertNotIn(secret, text)

    def test_invalid_inputs_exit_with_code_1_before_any_call(self):
        """정답의 모르는 청크·질의 불일치·잘못된 후보 이름·k·--out 없음은 임베딩 호출 전에 종료 코드 1이다.

        Args:
            없음.

        Returns:
            None: 종료 코드와 미호출을 확인한다.
        """
        calls = []

        def factory(spec, settings):
            calls.append(spec)
            return lambda batch: [fake_vector(t) for t in batch]

        self.assertEqual(self.run_main("--execute", factory=factory)[0], 1)
        self.assertEqual(self.run_main("--k", "0,3")[0], 1)
        self.write_lines(
            "gold.jsonl",
            [{"query_id": "q1", "chunk_ids": ["c9"]}, {"query_id": "q2", "chunk_ids": []}],
        )
        self.assertEqual(self.run_main()[0], 1)
        self.write_lines("gold.jsonl", [{"query_id": "q1", "chunk_ids": ["c1"]}])
        self.assertEqual(self.run_main()[0], 1)
        self.write_lines("gold.jsonl", [{"query_id": k, "chunk_ids": v} for k, v in GOLD.items()])
        (self.root / "cands.json").write_text(json.dumps([{"name": "../x", "model": "m"}]))
        code, printed = self.run_main(
            "--execute", "--out", str(self.root / "r.json"), factory=factory
        )
        self.assertEqual((code, calls), (1, []))
        self.assertNotIn(KEY, printed)

    def test_default_factory_calls_ai_embed_once_per_text(self):
        """실제 래퍼는 배치 안 문자열마다 ai.embed를 한 번 부르고 후보의 차원·task_type을 그대로 넘긴다.

        Args:
            없음.

        Returns:
            None: 호출 인자와 반환 벡터를 확인한다.
        """
        calls = []

        def fake_embed(client, model, text, *, dimensions=None, task_type=None):
            calls.append((client, model, text, dimensions, task_type))
            return SimpleNamespace(
                values=fake_vector(text), input_tokens=len(text), billable_characters=None
            )

        settings = cs_embedding_run.AISettings()
        with (
            patch.object(cs_embedding_run, "create_client", return_value="client") as create,
            patch.object(cs_embedding_run, "embed", fake_embed),
        ):
            factory = cs_embedding_run.default_factory(settings)
            spec = {"name": "e", "model": "gemini-embedding-001", "dimension": 4, "task_type": "T"}
            vectors = factory(spec, settings)(["trip a", "탭 b"])
        create.assert_called_once_with(settings, embedding=True)
        self.assertEqual(
            calls,
            [
                ("client", "gemini-embedding-001", "trip a", 4, "T"),
                ("client", "gemini-embedding-001", "탭 b", 4, "T"),
            ],
        )
        self.assertEqual(vectors, [fake_vector("trip a"), fake_vector("탭 b")])
        recorder = factory(spec, settings)
        with patch.object(cs_embedding_run, "embed", fake_embed):
            recorder(["trip a", "탭 b"])
        self.assertEqual(
            recorder.usage(),
            {
                "calls": 2,
                "chars": 9,
                "input_tokens": 9.0,
                "tokens_reported": 2,
                "billable_characters": None,
            },
        )
        cost = cs_embedding_run.usage_cost(
            recorder.usage(), "gemini-embedding-001", cs_embedding_run.load_price_table()
        )
        self.assertEqual(cost["currency"], "USD")
        self.assertEqual(
            cs_embedding_run.Decimal(cost["input_cost"]),
            cs_embedding_run.Decimal(9)
            * cs_embedding_run.load_price_table()["gemini-embedding-001"].input_per_million
            / cs_embedding_run.Decimal(1_000_000),
        )
        self.assertIsNone(cs_embedding_run.usage_cost(None, "gemini-embedding-001", {}))

    def test_execute_records_usage_and_cost_and_drops_partial_tokens(self):
        """--execute는 후보 임베딩의 usage를 행에 남기고 단가표 모델만 비용을 낸다. 토큰을 일부만 알려주면 input_tokens는 None이다.

        Returns:
            None: 후보 행 usage·cost와 per_query를 확인한다.
        """

        class Recorder:
            """토큰을 한 번은 알려주지 않는 가짜 공급자."""

            def __init__(self, partial):
                self.recorder = cs_embedding_run.RecordingEmbed(
                    None, {"model": "m", "dimension": None, "task_type": None}
                )
                self.partial = partial

            def __call__(self, batch):
                def fake(client, model, text, *, dimensions=None, task_type=None):
                    missing = self.partial and text == batch[0]
                    return SimpleNamespace(
                        values=fake_vector(text),
                        input_tokens=None if missing else 2,
                        billable_characters=None,
                    )

                with patch.object(cs_embedding_run, "embed", fake):
                    return self.recorder(batch)

            def usage(self):
                return self.recorder.usage()

        def factory(spec, settings):
            return Recorder(partial=spec["name"] == "e2")

        out = self.root / "usage.json"
        code, printed = self.run_main("--execute", "--out", str(out), factory=factory)
        self.assertEqual(code, 0, printed)
        result = json.loads(out.read_text(encoding="utf-8"))["result"]
        rows = {row["name"]: row for row in result["candidates"]}
        self.assertEqual(rows["e1"]["usage"]["calls"], 5)
        self.assertEqual(rows["e1"]["usage"]["input_tokens"], 10.0)
        self.assertEqual(rows["e1"]["cost"]["currency"], "USD")
        self.assertIsNone(rows["e2"]["usage"]["input_tokens"])
        self.assertIsNone(rows["e2"]["cost"])
        self.assertEqual({item["candidate"] for item in result["per_query"]}, {"e1", "e2"})

    def test_test_in_input_path_prints_a_warning(self):
        """입력 경로에 'test'가 있으면 경고 한 줄을 낸다(실행은 계속한다).

        Args:
            없음.

        Returns:
            None: 경고 문구를 확인한다.
        """
        renamed = self.root / "test-queries.jsonl"
        (self.root / "queries.jsonl").rename(renamed)
        r = self.root
        argv = [
            "--chunks", str(r / "chunks.jsonl"), "--queries", str(renamed),
            "--gold", str(r / "gold.jsonl"), "--candidates", str(r / "cands.json"),
        ]  # fmt: skip
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cs_embedding_run.main(argv), 0)
        self.assertTrue(out.getvalue().startswith("경고:"))


if __name__ == "__main__":
    unittest.main()
