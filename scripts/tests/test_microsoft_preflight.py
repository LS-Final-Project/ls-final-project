"""Verify personal-account selection without network, tokens or external dependencies."""

import importlib.util
import io
import json
import types
import unittest
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
