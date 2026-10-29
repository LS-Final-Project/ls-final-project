"""KAN-194 2차 C/S 그래프 로컬 실행 스크립트. fake 공급자·메모리 체크포인터로 네트워크·DB 없이 검사한다."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver

from backend.shared.settings import Settings
from scripts import cs_graph_run

TICKET = {
    "t-1": {
        "record": {
            "channel": "EMAIL",
            "body_language": "en",
            "subject": "Breaker trips",
            "body": "Trips at 300 V. serial: ZX-PRIVATE, contact person@example.test",
        },
        "sensitive_values": {},
        "product_code": "TRF-OIL-1000",
        "customer_tier": "GOLD",
        "received_at": "2026-10-29T09:00:00+00:00",
        "required_info_codes": ["TEST_PHOTO"],
        "already_asked_codes": [],
    }
}
TRACING = ("LANGSMITH_TRACING", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2")


class GraphRunTest(unittest.TestCase):
    """스크립트 왕복과 실패 종료."""

    def setUp(self) -> None:
        """임시 사례 파일과 .env를 읽지 않는 설정을 준비한다.

        Returns:
            None: 준비.
        """
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.tickets = Path(self.dir.name) / "tickets.json"
        self.tickets.write_text(json.dumps(TICKET), encoding="utf-8")
        clean = {k: v for k, v in os.environ.items() if k not in (*TRACING, "DATABASE_URL")}
        env = patch.dict(os.environ, clean, clear=True)
        env.start()
        self.addCleanup(env.stop)
        settings = patch.object(cs_graph_run, "Settings", lambda: Settings(_env_file=None))
        settings.start()
        self.addCleanup(settings.stop)

    def run_main(self, *args, **kwargs):
        """main을 부르고 (종료 코드, stdout, stderr)를 돌려준다.

        Args:
            args: 명령행 인자.
            kwargs: main 키워드.

        Returns:
            tuple: (코드, 표준 출력, 표준 오류).
        """
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cs_graph_run.main(["--ticket-file", str(self.tickets), *args], **kwargs)
        return code, out.getvalue(), err.getvalue()

    def test_fake_run_pauses_and_writes_summary_only(self) -> None:
        """fake 실행은 사람 확인 직전에 멈추고 요약만 쓴다(원문 없음).

        Returns:
            None: 종료 코드·요약을 확인한다.
        """
        out = Path(self.dir.name) / "out.json"
        code, _, err = self.run_main("--thread-id", "thread-1", "--out", str(out))
        self.assertEqual(code, 0, err)
        summary = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(summary["mode"], "fake")
        self.assertEqual(summary["stage"], "DRAFTED")
        self.assertEqual(summary["next_nodes"], ["human_review"])
        self.assertEqual(summary["attempt_ids"], ["classification-1", "draft-2"])
        text = out.read_text(encoding="utf-8")
        for secret in ("ZX-PRIVATE", "person@example.test", "Breaker trips"):
            self.assertNotIn(secret, text)

    def test_invalid_output_retries_flag_reaches_the_graph(self) -> None:
        """--invalid-output-retries는 기본 1, 0이면 0으로 그래프에 넘기고 요약에 재호출·회복 수가 있으며 다른 값은 거부한다.

        Returns:
            None: 그래프 인자·요약·종료 코드를 확인한다.
        """
        seen = []
        real = cs_graph_run.build_graph

        def spy(**kwargs):
            """인자를 기록하고 원래 함수를 부른다.

            Args:
                kwargs: build_graph 인자.

            Returns:
                CompiledStateGraph: 그래프.
            """
            seen.append(kwargs["invalid_output_retries"])
            return real(**kwargs)

        out = Path(self.dir.name) / "out.json"
        with patch.object(cs_graph_run, "build_graph", spy):
            self.assertEqual(self.run_main("--thread-id", "t-a", "--out", str(out))[0], 0)
            summary = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual((summary["retried"], summary["recovered"]), (0, 0))
            self.assertEqual([a["attempt_number"] for a in summary["attempts"]], [1, 1])
            code = self.run_main("--thread-id", "t-b", "--invalid-output-retries", "0")[0]
            self.assertEqual(code, 0)
        self.assertEqual(seen, [1, 0])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cs_graph_run.main(
                [
                    "--ticket-file",
                    str(self.tickets),
                    "--thread-id",
                    "t-c",
                    "--invalid-output-retries",
                    "2",
                ]
            )

    def test_evidence_limit_flag_reaches_the_graph_and_bad_values_exit_1(self) -> None:
        """--evidence-limit은 기본 5, 지정하면 그 값으로 그래프에 넘기고 범위 밖 값은 1로 끝난다.

        Returns:
            None: 그래프 인자·근거 수·종료 코드를 확인한다.
        """
        seen = []
        real = cs_graph_run.build_graph

        def spy(**kwargs):
            """인자를 기록하고 원래 함수를 부른다.

            Args:
                kwargs: build_graph 인자.

            Returns:
                CompiledStateGraph: 그래프.
            """
            seen.append(kwargs["evidence_limit"])
            return real(**kwargs)

        with patch.object(cs_graph_run, "build_graph", spy):
            self.assertEqual(self.run_main("--thread-id", "t-e1")[0], 0)
            self.assertEqual(self.run_main("--thread-id", "t-e2", "--evidence-limit", "1")[0], 0)
        self.assertEqual(seen, [5, 1])
        for bad in (("--evidence-limit", "0"), ("--draft-max-output-tokens", "0")):
            code, _, err = self.run_main("--thread-id", "t-bad", *bad)
            self.assertEqual(code, 1)
            self.assertIn("CS_GRAPH_INVALID_ARGS", err)

    def test_resume_needs_database(self) -> None:
        """--resume은 DATABASE_URL이 없으면 안내와 함께 1로 끝난다.

        Returns:
            None: 종료 코드·안내를 확인한다.
        """
        code, _, err = self.run_main("--thread-id", "thread-1", "--resume")
        self.assertEqual(code, 1)
        self.assertIn("CS_GRAPH_RESUME_NEEDS_DB", err)

    def test_execute_without_key_exits_1(self) -> None:
        """--execute는 키가 없으면 네트워크 없이 1로 끝난다.

        Returns:
            None: 종료 코드·안내를 확인한다.
        """
        os.environ.pop("GEMINI_API_KEY", None)
        code, _, err = self.run_main("--thread-id", "thread-1", "--execute")
        self.assertEqual(code, 1)
        self.assertIn("CS_GRAPH_KEY_MISSING", err)

    def test_tracing_environment_is_refused(self) -> None:
        """추적 환경 변수가 있으면 설정 단계에서 1로 끝난다.

        Returns:
            None: 종료 코드·KAN-89 안내를 확인한다.
        """
        os.environ["LANGSMITH_TRACING"] = "true"
        code, _, err = self.run_main("--thread-id", "thread-1")
        self.assertEqual(code, 1)
        self.assertIn("KAN-89", err)

    def test_bad_ticket_file_exits_1(self) -> None:
        """시간대 없는 접수 시각 같은 잘못된 사례 파일은 1로 끝난다.

        Returns:
            None: 종료 코드를 확인한다.
        """
        bad = json.loads(json.dumps(TICKET))
        bad["t-1"]["received_at"] = "2026-10-29T09:00:00"
        self.tickets.write_text(json.dumps(bad), encoding="utf-8")
        code, _, err = self.run_main("--thread-id", "thread-1")
        self.assertEqual(code, 1)
        self.assertIn("CS_GRAPH_TICKET_FILE", err)

    def test_settings_errors_do_not_print_secret_values(self) -> None:
        """설정 형식 오류 출력에 DSN·키 같은 입력값이 없다(hide_input_in_errors 고정).

        Returns:
            None: 종료 코드와 출력·예외 문자열을 확인한다.
        """
        os.environ["PORT"] = "postgresql://user:SECRETPW@db.example/x"
        os.environ["DATABASE_URL"] = "postgresql://user:SECRETPW@db.example/x"
        code, out, err = self.run_main("--thread-id", "thread-1")
        self.assertEqual(code, 1)
        self.assertIn("CS_GRAPH_SETTINGS", err)
        self.assertNotIn("SECRETPW", out + err)
        del os.environ["PORT"], os.environ["DATABASE_URL"]
        os.environ["GEMINI_API_KEY"] = "KEY-SECRET-VALUE"
        os.environ["GEMINI_MODEL"] = "bad model KEY-SECRET-VALUE"
        with self.assertRaises(ValueError) as raised:
            self.run_main("--thread-id", "thread-1", "--execute")
        self.assertNotIn("KEY-SECRET-VALUE", str(raised.exception))

    def test_resume_does_not_need_ticket_id(self) -> None:
        """사례가 여럿이어도 --resume은 --ticket-id 없이 같은 thread를 이어 끝낸다.

        Returns:
            None: 실행(--ticket-id)과 재개(없이)의 종료 코드·단계를 확인한다.
        """
        tickets = dict(TICKET) | {"t-2": TICKET["t-1"]}
        self.tickets.write_text(json.dumps(tickets), encoding="utf-8")
        os.environ["DATABASE_URL"] = "postgresql://unused.example/x"
        saver = InMemorySaver()

        @contextmanager
        def shared(settings):
            """두 실행이 같은 메모리 체크포인터를 쓰게 한다(DB 대신).

            Args:
                settings: 설정.

            Returns:
                Iterator: 공유 체크포인터.
            """
            yield saver

        with patch.object(cs_graph_run, "make_checkpointer", shared):
            code, out, err = self.run_main("--thread-id", "thread-9", "--ticket-id", "t-2")
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)["next_nodes"], ["human_review"])
            code, out, err = self.run_main("--thread-id", "thread-9", "--resume")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["stage"], "HUMAN_REVIEW")

    def test_fts_db_needs_database(self) -> None:
        """--fts-db는 DATABASE_URL이 없으면 1로 끝난다.

        Returns:
            None: 종료 코드·안내를 확인한다.
        """
        code, _, err = self.run_main("--thread-id", "thread-1", "--fts-db")
        self.assertEqual(code, 1)
        self.assertIn("CS_GRAPH_FTS_NEEDS_DB", err)

    def test_rerun_and_bad_resume_exit_1(self) -> None:
        """같은 thread 재실행과 없는 thread 재개는 공급자를 부르지 않고 코드와 함께 1로 끝난다.

        Returns:
            None: 종료 코드·코드 문구를 확인한다.
        """
        os.environ["DATABASE_URL"] = "postgresql://unused.example/x"
        saver = InMemorySaver()

        @contextmanager
        def shared(settings):
            """두 실행이 같은 메모리 체크포인터를 쓰게 한다.

            Args:
                settings: 설정.

            Returns:
                Iterator: 공유 체크포인터.
            """
            yield saver

        with patch.object(cs_graph_run, "make_checkpointer", shared):
            self.assertEqual(self.run_main("--thread-id", "thread-7")[0], 0)
            code, _, err = self.run_main("--thread-id", "thread-7")
            self.assertEqual(code, 1)
            self.assertIn("CS_GRAPH_THREAD_EXISTS", err)
            code, _, err = self.run_main("--thread-id", "typo", "--resume")
            self.assertEqual(code, 1)
            self.assertIn("CS_GRAPH_NOTHING_TO_RESUME", err)

    def test_other_value_errors_print_only_failed_code(self) -> None:
        """가드 코드가 아닌 ValueError는 메시지 없이 CS_GRAPH_FAILED 한 줄과 종료 1이다.

        Returns:
            None: 종료 코드와 원문 비출력을 확인한다.
        """

        def failing(graph, thread_id, ticket_id):
            """원문이 든 검증 오류를 흉내 낸다.

            Args:
                graph: 그래프.
                thread_id: 스레드 ID.
                ticket_id: 문의 ID.

            Returns:
                None: 반환하지 않는다.
            """
            raise ValueError("ZX-PRIVATE body text")

        with patch.object(cs_graph_run, "run_ticket", failing):
            code, out, err = self.run_main("--thread-id", "thread-8")
        self.assertEqual(code, 1)
        self.assertEqual(err.strip(), "CS_GRAPH_FAILED")
        self.assertNotIn("ZX-PRIVATE", out + err)
        with patch.object(cs_graph_run, "run_ticket", failing):
            with self.assertRaises(ValueError):
                self.run_main("--thread-id", "thread-8", "--debug")

    def write_batch(self):
        """세 문의(유형별 필요 코드 지도 포함) 사례 파일을 쓴다.

        Returns:
            list[str]: 문의 ID.
        """
        base = TICKET["t-1"] | {
            "required_info_codes": [],
            "required_info_by_category": {"WARRANTY": ["TEST_PHOTO"], "QUOTE": ["OTHER"]},
        }
        tickets = {f"b-{n}": base for n in range(3)}
        self.tickets.write_text(json.dumps(tickets), encoding="utf-8")
        return list(tickets)

    def test_batch_writes_lines_and_aggregate_without_text(self) -> None:
        """--batch는 문의마다 0600 jsonl 줄을 쓰고(원문·초안 없음) 집계를 --out에 쓴다.

        Returns:
            None: 줄 수·필드·권한·집계를 확인한다.
        """
        ids = self.write_batch()
        out = Path(self.dir.name) / "batch.json"
        code, printed, err = self.run_main("--batch", "--thread-id", "run", "--out", str(out))
        self.assertEqual(code, 0, err)
        lines_path = Path(f"{out}.jsonl")
        self.assertEqual(lines_path.stat().st_mode & 0o777, 0o600)
        lines = [json.loads(text) for text in lines_path.read_text().splitlines()]
        self.assertEqual([line["ticket_id"] for line in lines], ids)
        self.assertEqual({line["thread_id"] for line in lines}, {f"run-{i}" for i in ids})
        first = lines[0]
        self.assertEqual(
            (first["status"], first["stage"], first["evidence"]), ("done", "DRAFTED", 2)
        )
        self.assertEqual(
            [(a["kind"], a["outcome"]) for a in first["attempts"]],
            [("classification", "VALID"), ("draft", "VALID")],
        )
        self.assertEqual(first["usage"]["input_tokens"], 30)
        self.assertEqual((first["cost_usd"], first["cost_unknown_attempts"]), (None, 2))
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(printed), report)
        self.assertEqual(report["tickets"], 3)
        self.assertEqual(report["stages"], {"DRAFTED": 3})
        self.assertEqual((report["classification"], report["draft"]), ({"VALID": 3}, {"VALID": 3}))
        self.assertEqual((report["evidence_mean"], report["retried"]), (2, 0))
        self.assertIsNotNone(report["elapsed_ms"]["p95"])
        text = lines_path.read_text() + out.read_text()
        for secret in ("ZX-PRIVATE", "person@example.test", "Breaker trips", "Synthetic answer"):
            self.assertNotIn(secret, text)

    def test_batch_skips_paused_threads_and_keeps_lines_on_interrupt(self) -> None:
        """같은 체크포인터로 다시 돌리면 멈춘 thread는 skipped이고, 중단하면 끝난 줄만 남고 130이다.

        Returns:
            None: skipped 수·줄 수·종료 코드를 확인한다.
        """
        self.write_batch()
        out = Path(self.dir.name) / "batch.json"
        os.environ["DATABASE_URL"] = "postgresql://unused.example/x"
        saver = InMemorySaver()

        @contextmanager
        def shared(settings):
            """같은 메모리 체크포인터를 돌려준다.

            Args:
                settings: 설정.

            Returns:
                Iterator: 공유 체크포인터.
            """
            yield saver

        with patch.object(cs_graph_run, "make_checkpointer", shared):
            self.assertEqual(
                self.run_main("--batch", "--thread-id", "run", "--out", str(out))[0], 0
            )
            code, printed, err = self.run_main("--batch", "--thread-id", "run", "--out", str(out))
        self.assertEqual(code, 0, err)
        report = json.loads(printed)
        self.assertEqual((report["skipped_this_run"], report["statuses"]), (3, {"done": 3}))
        self.assertEqual(len(Path(f"{out}.jsonl").read_text().splitlines()), 6)
        del os.environ["DATABASE_URL"]
        stopped = Path(self.dir.name) / "stopped.json"
        real = cs_graph_run.run_ticket
        calls = []

        def interrupting(graph, thread_id, ticket_id):
            calls.append(ticket_id)
            if len(calls) == 2:
                raise KeyboardInterrupt
            return real(graph, thread_id, ticket_id)

        with patch.object(cs_graph_run, "run_ticket", interrupting):
            code, _, err = self.run_main("--batch", "--thread-id", "stop", "--out", str(stopped))
        self.assertEqual(code, 130)
        self.assertIn("CS_GRAPH_BATCH_INTERRUPTED", err)
        self.assertEqual(len(Path(f"{stopped}.jsonl").read_text().splitlines()), 1)
        self.assertFalse(stopped.exists())

    def test_thread_interrupted_mid_graph_is_incomplete_not_skipped(self) -> None:
        """초안 단계에서 끊긴 thread는 다시 돌려도 건너뛰지 않고 incomplete로 남긴다(단건 --resume 대상).

        Returns:
            None: 두 번째 실행의 문의별 상태와 집계를 확인한다.
        """
        self.write_batch()
        out = Path(self.dir.name) / "batch.json"
        os.environ["DATABASE_URL"] = "postgresql://unused.example/x"
        saver = InMemorySaver()
        real = cs_graph_run.draft_provider
        calls = []

        def stopping(prompt):
            calls.append(prompt)
            if len(calls) == 2:
                raise KeyboardInterrupt
            return real(prompt)

        @contextmanager
        def shared(settings):
            """같은 메모리 체크포인터를 돌려준다.

            Args:
                settings: 설정.

            Returns:
                Iterator: 공유 체크포인터.
            """
            yield saver

        with patch.object(cs_graph_run, "make_checkpointer", shared):
            with patch.object(cs_graph_run, "draft_provider", stopping):
                code, _, _ = self.run_main("--batch", "--thread-id", "run", "--out", str(out))
            self.assertEqual(code, 130)
            code, printed, err = self.run_main("--batch", "--thread-id", "run", "--out", str(out))
        self.assertEqual(code, 0, err)
        lines = [json.loads(text) for text in Path(f"{out}.jsonl").read_text().splitlines()]
        self.assertEqual(
            [(line["ticket_id"], line["status"]) for line in lines],
            [("b-0", "done"), ("b-0", "skipped"), ("b-1", "incomplete"), ("b-2", "done")],
        )
        report = json.loads(printed)
        self.assertEqual(report["statuses"], {"done": 2, "incomplete": 1})

    def test_percentile_uses_nearest_rank(self) -> None:
        """분위수는 최근접 순위(ceil)다. n=5의 중앙값은 세 번째 값이다.

        Returns:
            None: 값을 확인한다.
        """
        self.assertEqual(cs_graph_run.percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5), 3.0)
        self.assertEqual(cs_graph_run.percentile([float(n) for n in range(1, 31)], 0.95), 29.0)
        self.assertIsNone(cs_graph_run.percentile([], 0.5))

    def test_batch_continues_after_a_ticket_error(self) -> None:
        """문의 하나가 ValueError가 아닌 예외를 내도 failed 줄(코드만)로 남기고 나머지를 돌려 집계를 쓴다.

        Returns:
            None: 줄 상태·코드·집계·원문 비노출을 확인한다.
        """
        self.write_batch()
        out = Path(self.dir.name) / "batch.json"
        real = cs_graph_run.run_ticket

        def flaky(graph, thread_id, ticket_id):
            if ticket_id == "b-1":
                raise RuntimeError("PRIVATE_PROVIDER_DETAIL")
            return real(graph, thread_id, ticket_id)

        with patch.object(cs_graph_run, "run_ticket", flaky):
            code, printed, err = self.run_main("--batch", "--thread-id", "run", "--out", str(out))
        self.assertEqual(code, 0, err)
        lines = [json.loads(text) for text in Path(f"{out}.jsonl").read_text().splitlines()]
        self.assertEqual(
            [(line["ticket_id"], line["status"]) for line in lines],
            [("b-0", "done"), ("b-1", "failed"), ("b-2", "done")],
        )
        self.assertEqual(lines[1]["code"], "CS_GRAPH_FAILED")
        self.assertEqual(json.loads(printed)["statuses"], {"done": 2, "failed": 1})
        self.assertNotIn("PRIVATE_PROVIDER_DETAIL", Path(f"{out}.jsonl").read_text() + printed)

    def test_batch_dry_run_and_argument_errors(self) -> None:
        """--batch --dry-run은 그래프 없이 호출 수·상한을 내고, 잘못된 조합은 1이다.

        Returns:
            None: 보고와 종료 코드를 확인한다.
        """
        self.write_batch()
        with patch.object(cs_graph_run, "build_graph", side_effect=AssertionError("no graph")):
            code, printed, err = self.run_main("--batch", "--dry-run", "--thread-id", "run")
        self.assertEqual(code, 0, err)
        report = json.loads(printed)
        self.assertEqual(
            (report["tickets"], report["calls"], report["max_calls_with_retries"]),
            (3, {"classification": 3, "draft": 3}, 12),
        )
        self.assertEqual(report["draft"]["calls"], 3)
        # 초안 입력 상한은 근거 5건×800자를 넣은 프롬프트다.
        self.assertGreater(report["draft"]["prompt_bytes"], 3 * 5 * 800)
        for args in (
            ("--batch", "--thread-id", "run"),
            ("--batch", "--thread-id", "run", "--out", "x", "--ticket-id", "b-0"),
            ("--dry-run", "--thread-id", "run"),
            ("--batch", "--thread-id", "run", "--out", "x", "--ticket-ids", "b-9"),
        ):
            with self.subTest(args=args):
                self.assertEqual(self.run_main(*args)[0], 1)


if __name__ == "__main__":
    unittest.main()
