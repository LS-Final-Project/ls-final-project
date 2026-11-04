"""Local real-Outlook mail mode: gating, secrets, allow list and the local Tasks/Storage stand-in.

Offline: Microsoft and Graph are MockTransport or never reached.
"""

import asyncio
import base64
import json
from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from backend.api.platform.mail.storage import GcsObjectStore
from scripts.local_business import mail, runtime
from scripts.local_business.auth import PLATFORM_ORIGIN, PLATFORM_SA, LocalLogin

CLIENT = {"client_id": str(uuid4()), "client_secret": "teammate-secret-value"}
PORTS = {"web": 38443, "cs": 38081, "tft": 38082, "platform": 38083}


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.setenv("TFT_DOCUMENT_ROOT", str(tmp_path / "files"))
    (tmp_path / "auth").mkdir()
    (tmp_path / "control").mkdir()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for name, data in {
        "caller-key.pem": key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        "caller-public.pem": key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ),
    }.items():
        (tmp_path / "auth" / name).write_bytes(data)
        (tmp_path / "auth" / name).chmod(0o600)
    (tmp_path / "settings.json").write_text(
        json.dumps(
            {
                "namespace": "team4-kan159-mail",
                "ports": PORTS,
                "web_origin": "https://localhost:38443",
            }
        )
    )
    return tmp_path


def test_mail_is_off_without_mail_on(state):
    platform = runtime.app_for("platform", state)
    assert platform.state.mailbox_auth.enabled is False
    assert platform.state.mail_sync.enabled is False
    assert platform.state.mail_send.enabled is False
    assert runtime.app_for("cs", state).state.platform_mail is None


def test_client_app_requires_id_and_secret(tmp_path):
    with pytest.raises(ValueError, match="LOCAL_MAIL_CLIENT_REQUIRED"):
        mail.client_app(None, {})
    with pytest.raises(ValueError, match="LOCAL_MAIL_CLIENT_REQUIRED"):
        mail.client_app(None, {"LOCAL_MAIL_CLIENT_ID": CLIENT["client_id"]})
    env = {"LOCAL_MAIL_CLIENT_ID": CLIENT["client_id"], "LOCAL_MAIL_CLIENT_SECRET": "s"}
    assert mail.client_app(None, env)["client_secret"] == "s"
    secret = tmp_path / "ms-app.json"
    secret.write_text(json.dumps(CLIENT))
    secret.chmod(0o644)
    with pytest.raises(ValueError, match="LOCAL_MAIL_SECRET_FILE_PERMISSIONS"):
        mail.client_app(str(secret), {})
    secret.chmod(0o600)
    assert mail.client_app(str(secret), {}) == CLIENT


def test_write_validates_modes_and_keeps_the_mailbox_key(state):
    with pytest.raises(ValueError, match="LOCAL_MAIL_MODE_REQUIRED"):
        mail.write(state, app=CLIENT, sync=False, send=False, allow=[], interval=60)
    with pytest.raises(ValueError, match="LOCAL_MAIL_ALLOW_LIST_INVALID"):
        mail.write(state, app=CLIENT, sync=False, send=True, allow=[], interval=60)
    with pytest.raises(ValueError, match="LOCAL_MAIL_INTERVAL_INVALID"):
        mail.write(state, app=CLIENT, sync=True, send=False, allow=[], interval=10)
    first = mail.write(state, app=CLIENT, sync=True, send=False, allow=["@x.kr"], interval=60)
    path = state / "auth" / "mail.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert first["allow"] == []  # no allow list without --send
    second = mail.write(state, app=CLIENT, sync=True, send=True, allow=["@Outlook.KR"], interval=60)
    assert second["encryption_key"] == first["encryption_key"]
    assert mail.load(state)["allow"] == ["@outlook.kr"]
    path.chmod(0o644)
    with pytest.raises(ValueError, match="LOCAL_MAIL_SECRET_FILE_PERMISSIONS"):
        mail.load(state)


def test_allow_list_matches_exact_address_or_exact_domain():
    allow = ["@outlook.kr", "boss@example.com"]
    assert mail.allowed("CSDesk-01@outlook.kr", allow)
    assert mail.allowed("boss@example.com", allow)
    assert not mail.allowed("x@outlook.kr.evil.com", allow)
    assert not mail.allowed("x@sub.outlook.kr", allow)
    assert not mail.allowed("other@example.com", allow)
    assert not mail.allowed("outlook.kr", allow)


@pytest.mark.parametrize("sync,send", [(True, False), (False, True), (True, True)])
def test_mail_on_turns_on_only_what_was_asked(state, sync, send):
    mail.write(state, app=CLIENT, sync=sync, send=send, allow=mail.DEFAULT_ALLOW, interval=60)
    platform = runtime.app_for("platform", state)
    assert platform.state.mailbox_auth.enabled is True
    assert platform.state.mailbox_auth.redirect_uri == (
        "https://localhost:38443/api/v1/platform/auth/mailbox/callback"
    )
    # Sign-in stays synthetic; the mailbox consent uses the teammate's app.
    assert isinstance(platform.state.microsoft_oauth, LocalLogin)
    assert UUID(platform.state.mailbox_oauth._client_id) == UUID(CLIENT["client_id"])
    assert platform.state.mail_sync.enabled is sync
    assert platform.state.mail_send.enabled is send
    assert (runtime.app_for("cs", state).state.platform_mail is not None) is send
    with TestClient(platform):
        pass  # the scheduler starts and stops with the app


def transport(tmp_path, seen, allow=mail.DEFAULT_ALLOW):
    def real(request):
        seen.append(request)
        return httpx.Response(202)

    return mail.LocalMailTransport(
        ports=PORTS,
        token=lambda audience, email: f"token-for-{email}",
        objects=tmp_path / "objects",
        allow=allow,
        real=httpx.MockTransport(real),
    )


def send_body(*to, cc=()):
    return {
        "message": {
            "toRecipients": [{"emailAddress": {"address": a}} for a in to],
            "ccRecipients": [{"emailAddress": {"address": a}} for a in cc],
        }
    }


def test_send_outside_the_allow_list_never_reaches_graph(tmp_path):
    asyncio.run(_send_outside_the_allow_list_never_reaches_graph(tmp_path))


async def _send_outside_the_allow_list_never_reaches_graph(tmp_path):
    seen = []
    local = transport(tmp_path, seen)
    url = "https://graph.microsoft.com/v1.0/me/sendMail"
    async with httpx.AsyncClient(transport=local) as client:
        ok = await client.post(url, json=send_body("csdesk-01@outlook.kr"))
        to_outside = await client.post(url, json=send_body("customer@gmail.com"))
        cc_outside = await client.post(url, json=send_body("csdesk-01@outlook.kr", cc=["x@y.com"]))
        nobody = await client.post(url, json=send_body())
        other = client.get("https://example.com/")
        with pytest.raises(httpx.ConnectError):
            await other
    assert ok.status_code == 202
    assert [r.status_code for r in (to_outside, cc_outside, nobody)] == [403, 403, 403]
    assert [str(r.url) for r in seen] == [url]


def test_cloud_task_becomes_a_local_platform_call(tmp_path):
    asyncio.run(_cloud_task_becomes_a_local_platform_call(tmp_path))


async def _cloud_task_becomes_a_local_platform_call(tmp_path):
    seen = []
    local = transport(tmp_path, seen)
    body = base64.b64encode(b'{"intent_id": "x"}').decode()
    task = {
        "name": "projects/p/locations/l/queues/q/tasks/send-1",
        "httpRequest": {
            "url": PLATFORM_ORIGIN + "/internal/v1/platform/mail/send",
            "body": body,
            "oidcToken": {"serviceAccountEmail": PLATFORM_SA, "audience": PLATFORM_ORIGIN},
        },
    }
    async with httpx.AsyncClient(transport=local) as client:
        url = "https://cloudtasks.googleapis.com/v2/projects/p/locations/l/queues/q/tasks"
        first = await client.post(url, json={"task": task})
        again = await client.post(url, json={"task": task})
        await asyncio.gather(*local.running)
    assert (first.status_code, again.status_code) == (200, 409)
    [call] = seen
    assert str(call.url) == "http://127.0.0.1:38083/internal/v1/platform/mail/send"
    assert call.headers["authorization"] == f"Bearer token-for-{PLATFORM_SA}"
    assert call.content == b'{"intent_id": "x"}'
    with pytest.raises(ValueError, match="LOCAL_MAIL_TASK_TARGET_INVALID"):
        await local.call({**task["httpRequest"], "url": "https://elsewhere.invalid/x"})


def test_storage_round_trip_with_the_real_object_store(tmp_path):
    asyncio.run(_storage_round_trip_with_the_real_object_store(tmp_path))


async def _storage_round_trip_with_the_real_object_store(tmp_path):
    local = transport(tmp_path, [])

    async def token():
        return "local"

    mailbox = uuid4()
    async with httpx.AsyncClient(transport=local) as client:
        store = GcsObjectStore(client, mail.BUCKET, token)
        stored = await store.put(b"raw mime", mailbox_id=mailbox)
        assert await store.put(b"raw mime", mailbox_id=mailbox) == stored
        assert await store.get(stored) == b"raw mime"
        escape = await client.post(
            "https://storage.googleapis.com/upload/storage/v1/b/local-mail/o",
            params={"name": "../../escape"},
            content=b"x",
        )
    assert escape.status_code == 400
    assert (tmp_path / "objects" / stored.key).read_bytes() == b"raw mime"
