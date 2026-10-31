"""KAN-155 로컬 부하 시험 도구의 DB·서버 없이 검사할 수 있는 부분."""

import os
import unittest
from unittest.mock import patch

from scripts import load_run


class AdminUrlTest(unittest.TestCase):
    def test_only_a_loopback_team4_database_is_accepted(self):
        """loopback의 team4만 받고, 원격 주소나 다른 이름은 거부한다(표식 검사는 실행 때 따로 한다)."""
        load_run.check_admin_url("postgresql://postgres:x@127.0.0.1:55434/team4")
        load_run.check_admin_url("host=localhost port=55434 dbname=team4 user=postgres")
        for url in (
            "postgresql://postgres:x@10.0.0.5:5432/team4",
            "postgresql://postgres:x@127.0.0.1:55434/kan77_auth_test",
            "postgresql://postgres:x@db.example.com/team4",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                load_run.check_admin_url(url)


class PercentileTest(unittest.TestCase):
    def test_linear_interpolation_matches_the_evaluation_rule(self):
        """(n-1)q 위치 선형 보간. 빈 목록은 None, 하나면 그 값."""
        self.assertIsNone(load_run.percentile([], 0.5))
        self.assertEqual(load_run.percentile([3.0], 0.95), 3.0)
        self.assertEqual(load_run.percentile([4, 1, 3, 2], 0.5), 2.5)
        self.assertAlmostEqual(load_run.percentile(list(range(1, 101)), 0.95), 95.05)


class JudgeTest(unittest.TestCase):
    def requests(self, read_p95, write_p95, failures):
        return {
            "total.read": {"count": 100, "p95_s": read_p95, "failures": failures},
            "total.write": {"count": 10, "p95_s": write_p95, "failures": 0},
        }

    def test_normal_profile_uses_the_proposed_limits(self):
        """보통 부하: 읽기 0.8초·쓰기 1.5초·실패 0.5% 이하면 통과, 하나라도 넘으면 불합격."""
        ok = load_run.judge(self.requests(0.5, 1.0, 0), "normal")
        self.assertTrue(ok["all_passed"])
        slow = load_run.judge(self.requests(0.9, 1.0, 0), "normal")
        self.assertEqual(
            slow["passed"], {"read_p95": False, "write_p95": True, "failure_rate": True}
        )
        failing = load_run.judge(self.requests(0.5, 1.0, 1), "normal")
        self.assertFalse(failing["passed"]["failure_rate"])  # 1/110 > 0.5%

    def test_missing_measurements_fail_and_limit_profiles_are_not_judged(self):
        """잰 값이 없으면 통과로 치지 않는다. 한계 탐색 단계는 합격선이 없다."""
        self.assertFalse(load_run.judge({}, "triple")["all_passed"])
        self.assertIsNone(load_run.judge({}, "sse-limit"))

    def test_a_blocked_session_check_counts_as_a_failed_operation(self):
        """세션 확인이 503이면 업무 요청을 보내지 않아도 사용자 체감 합계에 실패로 들어간다."""
        import asyncio
        import random

        class Client:
            async def get(self, url, headers):
                return type("R", (), {"status_code": 503})()

        rec = load_run.Recorder()
        state = {
            "users": [
                {"id": "x", "role": r, "token": "t", "dept": None, "managed": []}
                for r in load_run.USERS
            ],
            "tickets": ["t"],
        }
        asyncio.run(load_run.one_action(Client(), {"platform": "p"}, state, rec, random.Random(1)))
        totals = [s for s in rec.samples if s.name.startswith("total.")]
        self.assertEqual([(s.status, s.blocked) for s in totals], [(503, True)])

    def test_a_session_rejected_with_401_is_a_failure(self):
        """세션 확인이 401(만료·비활성)이면 업무 요청을 못 보냈으므로 실패로 센다."""
        rec = load_run.Recorder()
        rec.add("samples", load_run.Sample("total.read", 0.01, 401, False, blocked=True))
        rec.add("samples", load_run.Sample("total.read", 0.01, 404, False))
        report = load_run.summarize(rec, load_run.PROFILES["normal"], opened=0, wall=1.0)
        self.assertEqual(report["requests"]["total.read"]["failures"], 1)


class SummaryTest(unittest.TestCase):
    def test_failures_count_server_errors_rejections_and_connection_errors(self):
        """5xx·429·연결 실패(0)는 실패, 4xx 업무 응답은 실패가 아니다."""
        rec = load_run.Recorder()
        for status in (200, 201, 404, 429, 503, 0):
            rec.add("samples", load_run.Sample("total.read", 0.1, status, False))
        rec.add("db", {"t": 1.0, "open_sse": 4, "platform_app": 3, "cs_app": 1})
        rec.add("db", {"t": 1.5, "open_sse": 10, "platform_app": 5})
        rec.add("sse", {"status": 200, "connect_s": 0.1, "events": 3, "held_s": 50.0})
        rec.add("sse", {"status": 503, "connect_s": 0.01, "events": 0, "held_s": 0.01})
        rec.add("jobs", {"result": "CS_JOB_SUCCEEDED", "seconds": 10.0})
        profile = load_run.Profile(
            "normal", rps=2, sse_streams=10, concurrent_jobs=3, duration_s=10
        )
        report = load_run.summarize(rec, profile, opened=30, wall=10.0)
        self.assertEqual(report["requests"]["total.read"]["failures"], 3)
        self.assertEqual(report["db"]["peak_total"], 5)
        self.assertEqual(report["db"]["peak_by_user"], {"cs_app": 1, "platform_app": 5})
        self.assertEqual(report["db"]["sessions_opened_per_s"], 3.0)
        self.assertEqual((report["db"]["samples"], report["db"]["sampler_error"]), (2, None))
        self.assertEqual(report["jobs"]["results"], {"CS_JOB_SUCCEEDED": 1})
        # 원한 시청 10개 × 10초 중 50초만 열려 있었다.
        self.assertEqual(report["sse"]["coverage"], 0.5)
        self.assertEqual(
            (report["sse"]["open_max"], report["sse"]["statuses"]), (10, {200: 1, 503: 1})
        )


class ScenarioTest(unittest.TestCase):
    def test_every_action_uses_a_real_role_and_a_known_service(self):
        """조작 표의 역할은 DB 역할(또는 any)이고 경로는 세 서비스 중 하나다."""
        for name, role, weight, method, path, write in load_run.ACTIONS:
            with self.subTest(name=name):
                self.assertIn(role, {*load_run.USERS, "any"})
                self.assertGreater(weight, 0)
                self.assertIn(path.split("/")[3], {"cs", "tft", "platform"})
                self.assertEqual(write, method != "GET")

    def test_triple_profile_is_three_times_normal(self):
        normal, triple = load_run.PROFILES["normal"], load_run.PROFILES["triple"]
        self.assertEqual(
            (triple.rps, triple.sse_streams, triple.concurrent_jobs),
            (normal.rps * 3, normal.sse_streams * 3, normal.concurrent_jobs * 3),
        )

    def test_service_processes_do_not_inherit_model_or_cloud_settings(self):
        """서비스 프로세스에는 실행에 필요한 값만 넘겨 실제 모델 키 등이 새지 않는다."""
        with patch.dict(os.environ, {"GEMINI_API_KEY": "real-key", "AI_PROVIDER": "vertex"}):
            for service in ("cs", "tft", "platform"):
                env = load_run.service_env("postgresql://postgres:x@127.0.0.1:55434/team4", service)
                self.assertNotIn("GEMINI_API_KEY", env)
                self.assertNotIn("AI_PROVIDER", env)
                self.assertEqual(env["ENVIRONMENT"], "local")
                self.assertEqual(env["DEV_AUTH_ENABLED"], "false")
                self.assertIn(f"user={service}_app", env["DATABASE_URL"])
        self.assertEqual(env["MICROSOFT_AUTH_APP_ORIGIN"], "https://load.invalid")

    def test_worker_processes_get_only_their_job_and_worker_login(self):
        """worker 프로세스에도 모델 키 등을 넘기지 않고, 작업 ID와 worker 로그인만 준다."""
        from uuid import uuid4

        job = uuid4()
        with patch.dict(os.environ, {"GEMINI_API_KEY": "real-key", "LANGSMITH_API_KEY": "x"}):
            env = load_run.worker_env("postgresql://postgres:x@127.0.0.1:55434/team4", job)
        self.assertEqual(set(env), {"PATH", "HOME", "PYTHONPATH", "CS_JOB_ID", "DATABASE_URL"})
        self.assertEqual(env["CS_JOB_ID"], str(job))
        self.assertIn(f"user={load_run.WORKER_ROLE}", env["DATABASE_URL"])


if __name__ == "__main__":
    unittest.main()
