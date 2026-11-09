"""KAN-155 원격 부하 도구: 부하 경로가 실제 중계 허용표를 지나고, 쓰기에 CSRF·Origin이 붙는지 본다."""

import unittest
from uuid import uuid4

from backend.api.platform import relay
from backend.api.platform.auth.http import csrf_token
from scripts import load_remote as R


class LoadRemoteTest(unittest.TestCase):
    def test_every_business_action_passes_the_relay_table(self):
        for name, _role, _w, method, path, _write in R.ACTIONS:
            concrete = (
                path.split("?")[0]
                .replace("{ticket}", str(uuid4()))
                .replace("{project}", str(uuid4()))
            )
            if concrete.startswith("/api/v1/platform/"):
                continue  # 플랫폼 경로는 웹이 플랫폼으로 바로 보낸다(server.mjs notificationRoutes).
            self.assertIn(method, relay.allowed_methods(concrete), name)
        for path in (
            f"/api/v1/tft/projects/{uuid4()}/process",
            f"/api/v1/cs/tickets/{uuid4()}/analysis",
            "/api/v1/tft/projects",
            f"/api/v1/tft/projects/{uuid4()}/documents",
        ):
            self.assertIn("POST", relay.allowed_methods(path), path)

    def test_writes_carry_the_session_bound_csrf_and_origin(self):
        user = {"token": "t" * 43}
        read, write = R.headers(user), R.headers(user, write=True)
        self.assertEqual(read, {"Cookie": f"__Host-team4_session={'t' * 43}"})
        self.assertEqual(write["X-CSRF-Token"], csrf_token("t" * 43))
        self.assertEqual(write["Origin"], R.TARGET)
        self.assertIn("-load-", R.TARGET)


if __name__ == "__main__":
    unittest.main()
