"""Production guards and independently generated API contracts."""

import sys

import pytest
from fastapi.testclient import TestClient

from backend.api.cs.main import create_app as cs_app
from backend.api.platform.main import create_app as platform_app
from backend.api.tft.main import create_app as tft_app
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings


def settings(**values):
    return Settings(_env_file=None, environment="test", **values)


def test_development_endpoints_are_registered_only_when_explicitly_enabled():
    for enabled in (False, True):
        app = platform_app(settings(dev_auth_enabled=enabled))
        paths = set(app.openapi()["paths"])
        assert ("/api/v1/platform/dev/accounts" in paths) is enabled
        assert ("/api/v1/platform/dev/session" in paths) is enabled


def test_unknown_origin_and_client_role_assignment_are_rejected_before_database_access():
    app = platform_app(settings(dev_auth_enabled=True))
    payload = {"user_id": "00000000-0000-4000-8000-000000000001"}
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/v1/platform/dev/session",
                json=payload,
                headers={"Origin": "https://untrusted.invalid"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/platform/dev/session", json=payload | {"role": "manager"}
            ).status_code
            == 422
        )


def test_production_identity_never_accepts_a_development_cookie_or_role_header():
    app = platform_app(Settings(_env_file=None, environment="production", dev_auth_enabled=False))
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/platform/me",
            headers={
                "Cookie": "team4_dev_session=forged",
                "X-User-Role": "manager",
            },
        )
        assert response.status_code == 401
        assert client.get("/api/v1/platform/dev/accounts").status_code == 404
        assert (
            client.post(
                "/api/v1/platform/dev/session",
                json={"user_id": "00000000-0000-4000-8000-000000000001"},
            ).status_code
            == 404
        )


# C/S tickets are implemented by KAN-102 and tested in tests/cs.
@pytest.mark.parametrize(("factory", "path"), ((tft_app, "/api/v1/tft/projects"),))
def test_business_stubs_are_explicit_and_never_return_mock_success(factory, path):
    with TestClient(factory(settings())) as client:
        result = client.get(path)
        assert result.status_code == 501
        assert result.json()["error"]["code"] == "NOT_IMPLEMENTED"
        schema = client.get("/openapi.json").json()
        assert schema["paths"][path]["get"]["x-implementation-status"] == "stub"


def test_one_service_export_does_not_write_the_other_services(monkeypatch, tmp_path):
    from backend.openapi import main

    untouched = tmp_path / "tft.json"
    untouched.write_text("another teammate's generated contract\n")
    monkeypatch.setattr(sys, "argv", ["openapi", "--service", "cs", "--output-dir", str(tmp_path)])
    assert main() == 0
    assert (tmp_path / "cs.json").is_file()
    assert untouched.read_text() == "another teammate's generated contract\n"
    assert not (tmp_path / "platform.json").exists()


def test_feature_errors_extend_the_envelope_without_changing_a_shared_registry():
    app = cs_app(settings())

    @app.get("/_feature_error")
    def feature_error():
        raise ApplicationError(409, "CS_TICKET_VERSION_CONFLICT", "The ticket has changed.")

    with TestClient(app) as client:
        result = client.get("/_feature_error")
        assert result.status_code == 409
        assert result.json()["error"]["code"] == "CS_TICKET_VERSION_CONFLICT"
        assert result.json()["meta"]["request_id"] == result.headers["X-Request-ID"]
