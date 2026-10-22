"""Verify personal-account selection without network, tokens or external dependencies."""

import importlib.util
import io
import json
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

spec = importlib.util.spec_from_file_location(
    "integration_check", Path(__file__).parents[1] / "check_integrations.py"
)
check = importlib.util.module_from_spec(spec)
# These tests supply config directly; the CI runner does not need dotenv or MSAL.
dotenv = types.ModuleType("dotenv")
dotenv.dotenv_values = lambda _path: {}
with patch.dict("sys.modules", {"dotenv": dotenv}):
    spec.loader.exec_module(check)


class MicrosoftPreflightTests(unittest.TestCase):
    values = {
        "MS_TENANT_ID": "11111111-1111-4111-8111-111111111111",
        "MS_CLIENT_ID": "22222222-2222-4222-8222-222222222222",
    }

    def response(self, tenant):
        return io.BytesIO(
            json.dumps({"issuer": f"https://login.microsoftonline.com/{tenant}/v2.0"}).encode()
        )

    def test_personal_probe_selects_consumers_and_keeps_school_configuration(self):
        values = dict(self.values)
        with patch.object(
            check, "urlopen", return_value=self.response("9188040d-6c67-4c5b-b112-36a304b66dad")
        ) as request:
            self.assertEqual(check.microsoft(values, False, personal_account=True), 0)
        self.assertIn("/consumers/v2.0/", request.call_args.args[0])
        self.assertEqual(values, self.values)

    def test_default_still_checks_only_the_configured_organization(self):
        with patch.object(
            check, "urlopen", return_value=self.response(self.values["MS_TENANT_ID"])
        ) as request:
            self.assertEqual(check.microsoft(self.values, False), 0)
        self.assertIn(self.values["MS_TENANT_ID"], request.call_args.args[0])

    def test_personal_probe_rejects_an_unexpected_tenant_issuer(self):
        with patch.object(
            check, "urlopen", return_value=self.response(self.values["MS_TENANT_ID"])
        ):
            with self.assertRaises(ValueError):
                check.microsoft(self.values, False, personal_account=True)

    def test_oauth_diagnostics_preserve_error_number_without_account_or_secret_details(self):
        diagnostic = check.microsoft_error_summary(
            {
                "error": "invalid_request",
                "error_codes": [9002332],
                "error_description": (
                    "AADSTS9002332: fixture-personal-email@example.invalid "
                    "client_secret=fixture-secret code=fixture-code state=fixture-state"
                ),
                "correlation_id": "fixture-correlation-id",
            }
        )
        self.assertEqual(diagnostic, "invalid_request; AADSTS=AADSTS9002332")
        for private in ("fixture", "email", "secret", "state", "correlation"):
            self.assertNotIn(private, diagnostic)

    def test_oauth_diagnostic_uses_only_known_protocol_error_enums(self):
        self.assertEqual(
            check.microsoft_error_summary(
                {"error": "fixture-secret", "error_description": None, "error_codes": None}
            ),
            "oauth_failed; AADSTS=not_provided",
        )

    def test_roundtrip_sends_once_and_requires_both_sent_items_and_own_inbox(self):
        calls = []

        def request(req, **_):
            calls.append(req)
            if req.get_method() == "POST":
                response = io.BytesIO()
                response.status = 202
                return response
            return io.BytesIO(b'{"value":[{"id":"fixture-id-not-for-logs"}]}')

        output = io.StringIO()
        with patch.object(check, "urlopen", side_effect=request), patch("sys.stdout", output):
            self.assertEqual(
                check.mail_roundtrip("fixture-secret-token", "self@example.invalid"), 0
            )
        posts = [r for r in calls if r.get_method() == "POST"]
        self.assertEqual(len(posts), 1)
        payload = json.loads(posts[0].data)
        self.assertEqual(
            payload["message"]["toRecipients"],
            [{"emailAddress": {"address": "self@example.invalid"}}],
        )
        self.assertNotIn("ccRecipients", payload["message"])
        for req in calls[1:]:
            query = parse_qs(urlparse(req.full_url).query)
            self.assertEqual(query["$select"], ["id"])
            self.assertEqual(query["$filter"], [f"subject eq '{payload['message']['subject']}'"])
        self.assertIn('"signed_in_inbox_confirmed": true', output.getvalue())
        self.assertIn('"signed_in_junk_confirmed": false', output.getvalue())
        self.assertEqual(len(calls), 3)
        for sensitive in (
            "fixture-secret-token",
            "fixture-id-not-for-logs",
            "self@example.invalid",
        ):
            self.assertNotIn(sensitive, output.getvalue())

    def test_junk_delivery_counts_as_received_without_claiming_inbox_delivery(self):
        calls = []

        def request(req, **_):
            calls.append(req)
            if req.get_method() == "POST":
                response = io.BytesIO()
                response.status = 202
                return response
            found = "/inbox/" not in req.full_url
            return io.BytesIO(b'{"value":[{"id":"private-id"}]}' if found else b'{"value":[]}')

        output = io.StringIO()
        with patch.object(check, "urlopen", side_effect=request), patch("sys.stdout", output):
            self.assertEqual(check.mail_roundtrip("private-token", "self@example.invalid"), 0)
        result = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["sent_items_confirmed"])
        self.assertTrue(result["signed_in_junk_confirmed"])
        self.assertTrue(result["signed_in_delivery_confirmed"])
        self.assertFalse(result["signed_in_inbox_confirmed"])
        self.assertEqual(sum(req.get_method() == "POST" for req in calls), 1)
        self.assertTrue(all(req.get_method() == "GET" for req in calls[1:]))
        self.assertEqual(len(calls), 4)
        self.assertNotIn("private", output.getvalue())

    def test_junk_delivery_without_sent_items_stays_pending(self):
        now = [0]

        def request(req, **_):
            if req.get_method() == "POST":
                response = io.BytesIO()
                response.status = 202
                return response
            found = "/junkemail/" in req.full_url
            return io.BytesIO(b'{"value":[{"id":"private-id"}]}' if found else b'{"value":[]}')

        def advance(_):
            now[0] = 61

        output = io.StringIO()
        with (
            patch.object(check, "urlopen", side_effect=request),
            patch.object(check.time, "monotonic", side_effect=lambda: now[0]),
            patch.object(check.time, "sleep", side_effect=advance),
            patch("sys.stdout", output),
        ):
            self.assertEqual(check.mail_roundtrip("private-token", "self@example.invalid"), 1)
        result = json.loads(output.getvalue().splitlines()[-1])
        self.assertEqual(result["status"], "PENDING")
        self.assertTrue(result["signed_in_delivery_confirmed"])
        self.assertFalse(result["sent_items_confirmed"])

    def test_accepted_mail_is_not_reported_as_delivered_and_is_never_resent(self):
        response = io.BytesIO()
        response.status = 202
        output = io.StringIO()
        with patch.object(check, "urlopen", return_value=response) as request:
            with patch("sys.stdout", output):
                self.assertEqual(
                    check.mail_roundtrip("fixture-token", "self@example.invalid", wait_seconds=0),
                    1,
                )
        self.assertEqual(request.call_count, 1)
        self.assertIn('"status": "PENDING"', output.getvalue())
        self.assertIn('"signed_in_inbox_confirmed": false', output.getvalue())

    def test_send_failure_is_not_retried(self):
        with patch.object(
            check, "urlopen", side_effect=HTTPError("", 503, "fixture-error", {}, None)
        ) as request:
            with (
                patch("sys.stdout", io.StringIO()),
                self.assertRaisesRegex(check.ConfigurationError, "Graph HTTP 503; phase=send_mail"),
            ):
                check.mail_roundtrip("fixture-token", "self@example.invalid")
        self.assertEqual(request.call_count, 1)

    def test_graph_failure_reports_known_codes_and_phase_without_private_fields(self):
        body = {
            "error": {
                "code": "ErrorInvalidUser",
                "message": "fixture-secret-token self@example.invalid",
                "innerError": {
                    "code": "ErrorItemNotFound",
                    "request-id": "fixture-private-id",
                },
            }
        }
        error = HTTPError(
            "https://graph.microsoft.com/private-fixture",
            404,
            "fixture-private-reason",
            {},
            io.BytesIO(json.dumps(body).encode()),
        )
        output = io.StringIO()
        with patch.object(check, "urlopen", side_effect=error) as request:
            with patch("sys.stdout", output), self.assertRaises(check.ConfigurationError) as raised:
                check.mail_roundtrip("fixture-secret-token", "self@example.invalid")
        self.assertIn("Graph HTTP 404; phase=send_mail", str(raised.exception))
        self.assertIn("code=ErrorInvalidUser,ErrorItemNotFound", str(raised.exception))
        self.assertEqual(request.call_count, 1)
        diagnostic = output.getvalue() + str(raised.exception)
        for private in ("fixture", "self@example.invalid", "https://"):
            self.assertNotIn(private, diagnostic)

    def test_graph_unrecognized_codes_and_malformed_bodies_are_not_echoed(self):
        for body in (
            b'{"error":{"code":"fixture-secret","message":"fixture-private"}}',
            b"fixture-private-not-json",
            b"[]",
        ):
            error = HTTPError("", 404, "private", {}, io.BytesIO(body))
            with patch.object(check, "urlopen", side_effect=error):
                with self.assertRaises(check.ConfigurationError) as raised:
                    check.graph_request(
                        check.Request("https://graph.microsoft.com/v1.0/me/sendMail"),
                        phase="fixture-private",
                        timeout=1,
                    )
            self.assertIn("phase=unknown; code=not_provided_or_unrecognized", str(raised.exception))
            self.assertNotIn("private", str(raised.exception))
            self.assertNotIn("secret", str(raised.exception))

    def test_poll_failure_is_labeled_after_send_acceptance_without_resending(self):
        response = io.BytesIO()
        response.status = 202
        error = HTTPError(
            "", 404, "private", {}, io.BytesIO(b'{"error":{"code":"ErrorItemNotFound"}}')
        )
        output = io.StringIO()
        with patch.object(check, "urlopen", side_effect=[response, error]) as request:
            with (
                patch("sys.stdout", output),
                self.assertRaisesRegex(
                    check.ConfigurationError, "phase=sent_items; code=ErrorItemNotFound"
                ),
            ):
                check.mail_roundtrip("fixture-token", "self@example.invalid")
        self.assertEqual(request.call_count, 2)
        self.assertIn("HTTP 202 accepted", output.getvalue())
        self.assertEqual(
            sum(call.args[0].get_method() == "POST" for call in request.call_args_list), 1
        )

    def test_invalid_or_multiple_recipients_are_rejected_before_network(self):
        for recipient in ("first@example.invalid,second@example.invalid", "one@example.invalid\n"):
            with patch.object(check, "urlopen") as request:
                with self.assertRaises(check.ConfigurationError):
                    check.mail_roundtrip("fixture-token", recipient)
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
