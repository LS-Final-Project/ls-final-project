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


if __name__ == "__main__":
    unittest.main()
