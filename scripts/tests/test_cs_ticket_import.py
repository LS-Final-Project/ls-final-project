import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import cs_ticket_import as tool

ROW = {
    "channel": "EMAIL",
    "body_language": "ko",
    "customer_external_id": "SYN-CUST-1",
    "product_code": "TRF-OIL-1000",
    "body": "합성 문의입니다",
}


class Server:
    """접수 경로를 흉내 낸다. 받은 요청을 적고 정한 응답을 준다."""

    def __init__(self, origin="synthetic", status=201, me=200, fail_at=None):
        self.origin, self.status, self.me, self.fail_at = origin, status, me, fail_at
        self.posts = []

    def __call__(self, method, url, headers, body):
        if method == "GET":
            return self.me, {"X-CSRF-Token": "csrf-1"}, b"{}"
        if headers.get("Origin") != "https://localhost:18443":
            return 403, {}, b"{}"
        self.posts.append((url, headers, json.loads(body)))
        if self.fail_at == len(self.posts):
            return 503, {}, b"{}"
        data = {"id": f"t{len(self.posts)}", "data_origin": self.origin}
        return self.status, {}, json.dumps({"data": data}).encode()


class ImportTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        patcher = patch.dict(os.environ, {"CS_IMPORT_COOKIE": "session=secret-value"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, rows, name="tickets.json"):
        path = self.folder / name
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        return path

    def run_tool(self, path, server, *extra):
        return tool.main(["--file", str(path), *extra], send=server)

    def test_default_only_checks_and_sends_nothing(self):
        server = Server()
        self.assertEqual(self.run_tool(self.write([ROW]), server), 0)
        self.assertEqual(server.posts, [])

    def test_real_route_with_session_csrf_and_forced_origin(self):
        server = Server()
        self.assertEqual(
            self.run_tool(self.write([{**ROW, "customer_tier": "GOLD"}]), server, "--execute"), 0
        )
        url, headers, body = server.posts[0]
        self.assertTrue(url.endswith("/api/v1/cs/tickets"))
        self.assertEqual(headers["X-CSRF-Token"], "csrf-1")
        self.assertEqual(headers["Cookie"], "session=secret-value")
        self.assertEqual((body["data_origin"], body["customer_tier"]), ("synthetic", "GOLD"))

    def test_any_bad_row_stops_everything_before_a_write(self):
        for bad in (
            {**ROW, "data_origin": "real"},
            {**ROW, "channel": "FAX"},
            {**ROW, "customer_tier": "VIP"},
        ):
            server = Server()
            self.assertEqual(self.run_tool(self.write([ROW, bad]), server, "--execute"), 1)
            self.assertEqual(server.posts, [])

    def test_a_product_outside_the_policy_table_stops_everything_before_a_write(self):
        server = Server()
        path = self.write([ROW, {**ROW, "product_code": "TRF-NONE-1", "body": "둘째"}])
        with patch("builtins.print") as printed:
            self.assertEqual(self.run_tool(path, server, "--execute"), 1)
        self.assertEqual(server.posts, [])
        self.assertIn("TRF-NONE-1", " ".join(str(c) for c in printed.call_args_list))

    def test_identical_rows_stop_everything_before_a_write(self):
        server = Server()
        path = self.write([ROW, {**ROW, "body": "둘째"}, dict(ROW)])
        self.assertEqual(self.run_tool(path, server, "--execute"), 1)
        self.assertEqual(server.posts, [])

    def test_stops_when_the_server_did_not_keep_it_synthetic(self):
        server = Server(origin="demo")
        self.assertEqual(
            self.run_tool(self.write([ROW, {**ROW, "body": "둘째"}]), server, "--execute"), 1
        )
        self.assertEqual(len(server.posts), 1)

    def test_rerun_skips_finished_rows_and_finishes_the_rest(self):
        path = self.write([ROW, {**ROW, "body": "둘째"}, {**ROW, "body": "셋째"}])
        first = Server(fail_at=2)
        self.assertEqual(self.run_tool(path, first, "--execute"), 1)
        second = Server()
        self.assertEqual(self.run_tool(path, second, "--execute"), 0)
        self.assertEqual([p[2]["body"] for p in second.posts], ["둘째", "셋째"])
        third = Server()
        self.assertEqual(self.run_tool(path, third, "--execute"), 0)
        self.assertEqual(third.posts, [])

    def test_no_session_means_no_write(self):
        server = Server(me=401)
        self.assertEqual(self.run_tool(self.write([ROW]), server, "--execute"), 1)
        self.assertEqual(server.posts, [])

    def test_missing_cookie_setting_means_no_write(self):
        server = Server()
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.run_tool(self.write([ROW]), server, "--execute"), 1)
        self.assertEqual(server.posts, [])

    def test_csv_blank_cells_are_absent(self):
        path = self.folder / "tickets.csv"
        path.write_text(
            "channel,body_language,customer_external_id,product_code,body,customer_tier\n"
            "EMAIL,ko,SYN-CUST-1,TRF-OIL-1000,합성 문의,\n",
            encoding="utf-8",
        )
        server = Server()
        self.assertEqual(self.run_tool(path, server, "--execute"), 0)
        self.assertNotIn("customer_tier", server.posts[0][2])

    def test_csv_keeps_line_breaks_inside_a_cell(self):
        path = self.folder / "tickets.csv"
        path.write_bytes(
            "channel,body_language,customer_external_id,product_code,body\r\n"
            'EMAIL,ko,SYN-CUST-1,TRF-OIL-1000,"첫 줄\r\n둘째 줄\n셋째 줄"\r\n'.encode()
        )
        server = Server()
        self.assertEqual(self.run_tool(path, server, "--execute"), 0)
        self.assertEqual(server.posts[0][2]["body"], "첫 줄\r\n둘째 줄\n셋째 줄")

    def test_the_cookie_is_never_printed(self):
        with patch("builtins.print") as printed:
            self.run_tool(self.write([ROW]), Server(status=500), "--execute")
        self.assertNotIn("secret-value", " ".join(str(c) for c in printed.call_args_list))


if __name__ == "__main__":
    unittest.main()
