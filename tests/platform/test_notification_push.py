"""Web Push has no real HTTP calls: session policy and explicit fake senders only."""

import base64
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from backend.api.platform.notifications import push_dispatch, push_routes
from backend.api.platform.notifications.push_settings import HOST, PushSettings, endpoint
from tests.platform.test_notifications import USER, setup

PUBLIC = (
    base64.urlsafe_b64encode(
        ec.derive_private_key(17, ec.SECP256R1())
        .public_key()
        .public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    )
    .rstrip(b"=")
    .decode()
)
AUTH = base64.urlsafe_b64encode(b"a" * 16).rstrip(b"=").decode()
ENDPOINT = f"https://{HOST}/fcm/send/secret-token"


def settings():
    return PushSettings(
        _env_file=None,
        enabled=True,
        public_key=PUBLIC,
        service_url="https://team4-platform-test.a.run.app",
        invoker_email="push-invoker@example-project.iam.gserviceaccount.com",
        project_number="123456789",
        job="notification-push",
    )


@pytest.mark.parametrize(
    "value",
    [
        "http://fcm.googleapis.com/fcm/send/token",
        "https://evil.test/fcm/send/token",
        "https://fcm.googleapis.com.evil.test/fcm/send/token",
        "https://fcm.googleapis.com:443/fcm/send/token",
        "https://u@fcm.googleapis.com/fcm/send/token",
        "https://fcm.googleapis.com/fcm/send/token?secret=1",
        "https://fcm.googleapis.com/fcm/send/token#x",
        "https://fcm.googleapis.com/fcm/send/../token",
        "https://127.0.0.1/fcm/send/token",
    ],
)
def test_endpoint_allowlist(value):
    with pytest.raises(ValueError):
        endpoint(value)


def test_subscription_is_current_user_only_and_no_secret_response(monkeypatch):
    client = setup(monkeypatch)[0]
    push_dispatch.configure_push(client.app, settings(), caller=Mock())
    conn = AsyncMock()

    @asynccontextmanager
    async def connection(_):
        yield conn

    monkeypatch.setattr(push_routes, "connection", connection)
    subscribe = AsyncMock()
    unsubscribe = AsyncMock()
    monkeypatch.setattr(push_routes.repository, "subscribe", subscribe)
    monkeypatch.setattr(push_routes.repository, "unsubscribe", unsubscribe)
    assert client.get("/api/v1/platform/push/public-key").json()["data"] == {"public_key": PUBLIC}
    payload = {"endpoint": ENDPOINT, "keys": {"p256dh": PUBLIC, "auth": AUTH}}
    result = client.put(
        "/api/v1/platform/push/subscription",
        json=payload,
        headers={"Origin": "http://localhost:5173"},
    )
    assert result.status_code == 204 and result.content == b""
    subscribe.assert_awaited_once_with(conn, USER, ENDPOINT, PUBLIC, AUTH)
    result = client.request(
        "DELETE", "/api/v1/platform/push/subscription", json={"endpoint": ENDPOINT}
    )
    assert result.status_code == 204
    unsubscribe.assert_awaited_once_with(conn, USER, ENDPOINT)
    for invalid in (
        {**payload, "user_id": str(uuid4())},
        {**payload, "keys": {"p256dh": "bad", "auth": AUTH}},
    ):
        assert client.put("/api/v1/platform/push/subscription", json=invalid).status_code == 422
    assert (
        client.put(
            "/api/v1/platform/push/subscription",
            json=payload,
            headers={"Origin": "https://evil.test"},
        ).status_code
        == 403
    )
    assert subscribe.await_count == 1


def test_disabled_and_missing_session(monkeypatch):
    client = setup(monkeypatch)[0]
    assert client.get("/api/v1/platform/push/public-key").status_code == 404
    client.app.dependency_overrides.clear()
    assert client.get("/api/v1/platform/push/public-key").status_code == 401


def test_internal_only_starts_fixed_job_no_browser_send(monkeypatch):
    client = setup(monkeypatch)[0]
    seen = []

    def transport(request):
        seen.append(request)
        assert (
            str(request.url)
            == "https://run.googleapis.com/v2/projects/123456789/locations/asia-northeast3/jobs/notification-push:run"
        )
        assert request.content == b"{}"
        return httpx.Response(200, json={})

    caller = Mock()
    token = AsyncMock(return_value="fake-access-token")
    push_dispatch.configure_push(
        client.app, settings(), caller=caller, token=token, transport=httpx.MockTransport(transport)
    )
    assert client.post("/internal/v1/platform/notifications/push").status_code == 401
    assert (
        client.post(
            "/internal/v1/platform/notifications/push", headers={"Authorization": "Bearer fake"}
        ).status_code
        == 202
    )
    caller.verify.assert_called_once_with("fake")
    assert len(seen) == 1
    assert (
        client.post(
            "/internal/v1/platform/notifications/push",
            json={"job": "evil"},
            headers={"Authorization": "Bearer fake"},
        ).status_code
        == 400
    )
    assert len(seen) == 1
    assert "/internal/v1/platform/notifications/push" not in client.app.openapi()["paths"]


def test_operational_subscription_requires_existing_csrf_policy(monkeypatch):
    from backend.api.platform.auth.http import SESSION_COOKIE, csrf_token

    client = setup(monkeypatch, enabled=True, dev=False)[0]
    push_dispatch.configure_push(client.app, settings(), caller=Mock())
    client.cookies.set(SESSION_COOKIE, "test-session")
    payload = {"endpoint": ENDPOINT, "keys": {"p256dh": PUBLIC, "auth": AUTH}}
    mutation = AsyncMock()
    monkeypatch.setattr(push_routes.repository, "subscribe", mutation)
    for headers in (
        {},
        {"Origin": "https://evil.test", "X-CSRF-Token": csrf_token("test-session")},
        {"Origin": "https://app.example.invalid", "X-CSRF-Token": "bad"},
    ):
        assert (
            client.put(
                "/api/v1/platform/push/subscription", json=payload, headers=headers
            ).status_code
            == 403
        )
    mutation.assert_not_awaited()

    @asynccontextmanager
    async def connection(_):
        yield AsyncMock()

    monkeypatch.setattr(push_routes, "connection", connection)
    assert (
        client.put(
            "/api/v1/platform/push/subscription",
            json=payload,
            headers={
                "Origin": "https://app.example.invalid",
                "X-CSRF-Token": csrf_token("test-session"),
            },
        ).status_code
        == 204
    )
    mutation.assert_awaited_once()


def test_configuration_and_public_registration_never_discover_adc(monkeypatch):
    provider = Mock(side_effect=AssertionError("ADC must be lazy"))
    monkeypatch.setattr(push_dispatch, "GoogleAccessToken", provider)
    client = setup(monkeypatch)[0]
    push_dispatch.configure_push(client.app, settings(), caller=Mock())
    assert client.get("/api/v1/platform/push/public-key").status_code == 200
    provider.assert_not_called()
