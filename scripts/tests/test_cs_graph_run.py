"""KAN-194 2차 C/S 그래프 로컬 실행 스크립트. fake 공급자·메모리 체크포인터로 네트워크·DB 없이 검사한다."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
