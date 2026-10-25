import logging
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from backend.api.cs.main import create_app as create_cs_app
from backend.api.platform.main import create_app as create_platform_app
from backend.api.tft.main import create_app as create_tft_app
from backend.shared.settings import Settings

FACTORIES = (("cs", create_cs_app), ("tft", create_tft_app), ("platform", create_platform_app))


@pytest.fixture(autouse=True)
def isolated_process_settings(monkeypatch):
    for variable in ("ENVIRONMENT", "DEV_AUTH_ENABLED", "SERVICE_NAME", "PORT", "K_SERVICE"):
        monkeypatch.delenv(variable, raising=False)


def local_settings(**overrides):
    return Settings(_env_file=None, environment="test", **overrides)


@pytest.mark.parametrize(("service", "factory"), FACTORIES)
def test_health_and_openapi_are_service_specific(service, factory):
    app = factory(local_settings())
    assert app.state.settings.service_name == service
    with TestClient(app) as client:
        for path in ("/health", f"/api/v1/{service}/health"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.json()["data"] == {"status": "ok", "service": service}
            assert (
                str(UUID(response.headers["X-Request-ID"])) == response.json()["meta"]["request_id"]
            )
        schema = client.get("/openapi.json").json()
        assert {"/health", f"/api/v1/{service}/health", f"/api/v1/{service}/ready"} <= set(
            schema["paths"]
        )
        assert "ErrorResponse" in schema["components"]["schemas"]
        assert client.get("/docs").status_code == 200
        for other, _ in FACTORIES:
            if other != service:
                assert client.get(f"/api/v1/{other}/health").status_code == 404


def test_request_id_is_preserved_only_when_it_is_a_uuid():
    with TestClient(create_cs_app(local_settings())) as client:
        expected = str(uuid4())
        assert (
            client.get("/health", headers={"X-Request-ID": expected}).headers["X-Request-ID"]
            == expected
        )
        response = client.get("/health", headers={"X-Request-ID": "untrusted-log-content"})
        UUID(response.headers["X-Request-ID"])
        assert response.headers["X-Request-ID"] != "untrusted-log-content"
        assert (
            client.get("/health").headers["X-Request-ID"]
            != client.get("/health").headers["X-Request-ID"]
        )


@pytest.mark.parametrize(("service", "factory"), FACTORIES)
def test_production_hides_docs_and_unimplemented_auth_routes(service, factory):
    settings = Settings(_env_file=None, environment="production", dev_auth_enabled=False)
    with TestClient(factory(settings)) as client:
        assert client.get("/health").status_code == 200
        for path in ("/docs", "/redoc", "/openapi.json", f"/api/v1/{service}/dev/login"):
            assert client.get(path).status_code == 404
        # User-controlled identity headers never turn missing business routes into successes.
        assert client.get(
            f"/api/v1/{service}/me", headers={"X-User-Role": "manager", "X-User-ID": "demo"}
        ).status_code == (401 if service == "platform" else 404)


def test_production_rejects_development_authentication():
    with pytest.raises(ValidationError, match="forbidden in production"):
        Settings(_env_file=None, environment="production", dev_auth_enabled=True)


def test_cloud_run_rejects_implicit_local_configuration(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "team4-cs")
    with pytest.raises(ValidationError, match="must use ENVIRONMENT=production"):
        Settings(_env_file=None)


def test_dotenv_is_loaded_without_importing_unrelated_secret_values(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "SERVICE_NAME=tft\nPORT=8123\nGEMINI_API_KEY=fixture-secret\n", encoding="utf-8"
    )
    settings = Settings(_env_file=dotenv)
    assert settings.service_name == "tft"
    assert settings.port == 8123
    assert "fixture-secret" not in settings.model_dump_json()


def test_invalid_configuration_errors_do_not_echo_input():
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, port="sensitive-fixture-input")
    assert "sensitive-fixture-input" not in str(error.value)


def test_error_envelopes_do_not_expose_exception_or_validation_input(caplog):
    app = create_cs_app(local_settings())

    class Payload(BaseModel):
        count: int

    @app.post("/_validation")
    def validation_route(payload: Payload):
        return payload

    @app.get("/_http")
    def http_error():
        raise HTTPException(403, detail="fixture-private-provider-response")

    @app.get("/_unexpected")
    def unexpected_error():
        raise RuntimeError("fixture-private-provider-response")

    with TestClient(app) as client, caplog.at_level(logging.ERROR):
        responses = (
            client.post("/_validation", json={"count": "fixture-private-provider-response"}),
            client.get("/_http"),
            client.get("/_unexpected"),
            client.post("/health"),
            client.get("/missing"),
        )
    assert [response.status_code for response in responses] == [422, 403, 500, 405, 404]
    assert [response.json()["error"]["code"] for response in responses] == [
        "VALIDATION_ERROR",
        "FORBIDDEN",
        "INTERNAL_ERROR",
        "METHOD_NOT_ALLOWED",
        "NOT_FOUND",
    ]
    for response in responses:
        assert response.json()["meta"]["request_id"] == response.headers["X-Request-ID"]
        assert "fixture-private-provider-response" not in response.text
    assert "fixture-private-provider-response" not in caplog.text


def test_app_instances_do_not_share_router_state():
    first, second = create_cs_app(local_settings()), create_tft_app(local_settings())

    @first.get("/_cs_only")
    def cs_only():
        return {"service": "cs"}

    with TestClient(first) as cs_client, TestClient(second) as tft_client:
        assert cs_client.get("/_cs_only").status_code == 200
        assert tft_client.get("/_cs_only").status_code == 404


def test_entrypoint_selects_only_the_requested_app_and_cloud_run_port(monkeypatch):
    from backend.__main__ import main

    monkeypatch.setenv("SERVICE_NAME", "tft")
    monkeypatch.setenv("PORT", "8124")
    observed = {}
    monkeypatch.setattr(
        "backend.__main__.uvicorn.run",
        lambda target, **kwargs: observed.update(target=target, **kwargs),
    )
    # JSON logging is configured before the server starts (KAN-70).
    monkeypatch.setattr(
        "backend.__main__.configure_logging", lambda: observed.update(logging="json")
    )
    main()
    assert observed == {
        "logging": "json",
        "target": "backend.api.tft.main:create_app",
        "factory": True,
        "host": "0.0.0.0",
        "port": 8124,
        "log_config": None,
        "access_log": False,
    }
