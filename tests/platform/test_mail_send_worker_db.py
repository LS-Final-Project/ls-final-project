"""KAN-74 발송 입구·실행을 전용 PostgreSQL과 가짜 Graph·Cloud Storage·Cloud Tasks로 검증한다.

실제 Graph·버킷·큐에는 연결하지 않는다. 호출자 검증기는 합성이다.
"""

import base64
import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.api.platform.mail.send import payload_sha256
from backend.api.platform.mail.send_worker import MailSendSettings, configure_mail_send
from backend.api.platform.mail.storage import content_key
from backend.api.platform.main import create_app
from tests.platform.test_mail_send_db import FakeGraph
from tests.platform.test_mail_send_db import send_database as send_database
from tests.platform.test_mail_send_db import sender as sender
from tests.platform.test_mail_storage_gcs import FakeGcs
from tests.platform.test_mail_sync_db import mail_database as mail_database
from tests.platform.test_mail_sync_db import mailbox as mailbox
from tests.platform.test_mail_sync_db import rows

QUEUE = "projects/proj-test-01/locations/asia-northeast3/queues/mail-send"
SERVICE = "https://team4-platform-test.a.run.app"
INVOKER = "mail-sync-invoker@proj-test-01.iam.gserviceaccount.com"
CS = "cs-runtime@proj-test-01.iam.gserviceaccount.com"
BUCKET = "proj-test-team4-mail-objects"
AS_CS = {"Authorization": "Bearer cs-caller"}
AS_TASKS = {"Authorization": "Bearer tasks-caller"}
FILE = b"%PDF-synthetic answer"


def enabled(**changes):
    values = {
        "enabled": True,
        "queue": QUEUE,
        "service_url": SERVICE,
        "invoker_email": INVOKER,
        "cs_caller_email": CS,
        "MAIL_OBJECT_BUCKET": BUCKET,
        **changes,
    }
    return MailSendSettings(_env_file=None, **values)


class Caller:
    def __init__(self, good):
        self.good = good

    def verify(self, token):
        if token != self.good:
            raise HTTPException(401)


class FakeTasks:
    def __init__(self):
        self.tasks = {}
        self.status = None

    def handler(self, request):
        assert request.headers["authorization"] == "Bearer tasks-token"
        if self.status is not None:
            return httpx.Response(self.status, json={})
        task = json.loads(request.content)["task"]
        if task["name"] in self.tasks:
            return httpx.Response(409, json={})
        self.tasks[task["name"]] = task
        return httpx.Response(200, json=task)

    def named(self, prefix):
        return {n.rsplit("/", 1)[1]: t for n, t in self.tasks.items() if prefix in n}


class Provider:
    async def get_mailbox_access_token(self, *, mailbox_id, owner_user_id, force_refresh=False):
        return SecretStr("synthetic-send-token")


class World:
    def __init__(self, settings, value=None):
        self.graph, self.gcs, self.tasks = FakeGraph(), FakeGcs(), FakeTasks()

        def route(request):
            host = request.url.host
            if host == "graph.microsoft.com":
                return self.graph.handler(request)
            if host == "storage.googleapis.com":
                return self.gcs.handler(request)
            if host == "cloudtasks.googleapis.com":
                return self.tasks.handler(request)
            raise AssertionError(host)

        async def tasks_token():
            return "tasks-token"

        async def storage_token():
            return "gcs-token"

        app = create_app(settings=settings)
        app.state.mailbox_tokens = Provider()
        configure_mail_send(
            app,
            value or enabled(),
            invoker=Caller("tasks-caller"),
            cs_caller=Caller("cs-caller"),
            tasks_token=tasks_token,
            storage_token=storage_token,
            transport=httpx.MockTransport(route),
        )
        self.client = TestClient(app)

    def post(self, path, body, headers):
        return self.client.post(f"/internal/v1/platform/mail/{path}", json=body, headers=headers)


def payload(sender, world, **changes):
    key = content_key(sender["id"], hashlib.sha256(FILE).hexdigest())
    world.gcs.objects[key] = FILE
    body = {
        "mailbox_id": str(sender["id"]),
        "business_ref": "cs_ticket:TCK-000123",
        "business_version": 2,
        "approved_by": str(sender["owner"]),
        "approved_at": datetime(2026, 10, 28, 9, 0, tzinfo=UTC).isoformat(),
        "subject": "Re: 차단기 트립 문의",
        "body_type": "text",
        "body": "확인 결과를 안내드립니다.",
        "to": ["customer@example.invalid"],
        "cc": [],
        "attachments": [
            {
                "name": "answer.pdf",
                "content_type": "application/pdf",
                "storage_key": key,
                "sha256": hashlib.sha256(FILE).hexdigest(),
                "size": len(FILE),
            }
        ],
        **changes,
    }
    body.setdefault(
        "approved_payload_sha256",
        payload_sha256(
            subject=body["subject"],
            body_type=body["body_type"],
            body=body["body"],
            to=body["to"],
            cc=body["cc"],
            attachments=body["attachments"],
        ),
    )
    return body


def created(world, sender):
    response = world.post("send-intents", payload(sender, world), AS_CS)
    assert response.status_code == 202, response.text
    return response.json()["data"]["intent_id"]


def test_routes_are_closed_by_default(settings, sender):
    client = TestClient(create_app(settings=settings))
    for path in ("send-intents", "send", "send-confirm", "send-sweep"):
        assert client.post(f"/internal/v1/platform/mail/{path}", json={}).status_code == 404
    assert client.get(f"/internal/v1/platform/mail/send-intents/{uuid4()}").status_code == 404


@pytest.mark.parametrize(
    "change",
    [
        {"cs_caller_email": ""},
        {"cs_caller_email": INVOKER},
        {"queue": "q"},
        {"MAIL_OBJECT_BUCKET": ""},
        {"service_url": SERVICE + "/x"},
    ],
)
def test_enabled_sending_requires_complete_configuration(change):
    with pytest.raises(ValueError):
        enabled(**change)


def test_each_route_accepts_only_its_caller(settings, sender):
    world = World(settings)
    assert world.post("send-intents", payload(sender, world), AS_TASKS).status_code == 401
    for path in ("send", "send-confirm", "send-sweep"):
        assert world.post(path, {"intent_id": str(uuid4())}, AS_CS).status_code == 401
    assert world.tasks.tasks == {} and world.graph.requests == []


def test_request_creates_one_intent_and_one_send_task(settings, sender):
    world = World(settings)
    first = world.post("send-intents", payload(sender, world), AS_CS)
    assert first.status_code == 202
    data = first.json()["data"]
    assert (data["status"], data["task_registered"]) == ("QUEUED", True)
    intent = data["intent_id"]
    [(name, task)] = world.tasks.named("mailsend-").items()
    assert name == f"mailsend-{intent}-a0"
    assert task["httpRequest"]["url"] == f"{SERVICE}/internal/v1/platform/mail/send"
    assert task["httpRequest"]["oidcToken"] == {"serviceAccountEmail": INVOKER, "audience": SERVICE}
    assert json.loads(base64.b64decode(task["httpRequest"]["body"])) == {"intent_id": intent}
    assert rows(
        sender["dsn"],
        "SELECT owner_service FROM integration.mail_send_intents WHERE id=%s",
        intent,
    ) == [("cs",)]
    # 같은 승인을 다시 보내도 의도·작업이 늘지 않는다.
    again = world.post("send-intents", payload(sender, world), AS_CS)
    assert again.json()["data"]["intent_id"] == intent
    assert len(world.tasks.named("mailsend-")) == 1


@pytest.mark.parametrize(
    "change, status, code",
    [
        ({"approved_payload_sha256": "nothex"}, 422, "VALIDATION_ERROR"),
        ({"approved_payload_sha256": "0" * 64}, 409, "MAIL_APPROVAL_MISMATCH"),
        ({"owner_service": "platform"}, 202, None),
    ],
)
def test_request_validation(settings, sender, change, status, code):
    world = World(settings)
    response = world.post("send-intents", payload(sender, world, **change), AS_CS)
    assert response.status_code == status
    if code:
        assert response.json()["error"]["code"] == code
    else:
        # 본문으로 서비스를 고를 수 없다. 검증된 호출 계정이 정한다.
        intent = response.json()["data"]["intent_id"]
        assert rows(
            sender["dsn"],
            "SELECT owner_service FROM integration.mail_send_intents WHERE id=%s",
            intent,
        ) == [("cs",)]


def test_status_shows_progress_without_content(settings, sender):
    world = World(settings)
    intent = created(world, sender)
    response = world.client.get(f"/internal/v1/platform/mail/send-intents/{intent}", headers=AS_CS)
    data = response.json()["data"]
    assert (data["status"], data["attempts"]) == ("QUEUED", 0)
    assert [e["event"] for e in data["events"]] == ["QUEUED"]
    assert "body" not in data and "recipients" not in data


def test_status_of_another_service_is_hidden(settings, sender):
    import asyncio

    from backend.api.platform.mail.send import Attachment, SendRequest, create_intent
    from backend.api.platform.mail.storage import StoredObject

    world = World(settings)
    body = payload(sender, world)
    a = body["attachments"][0]
    intent = asyncio.run(
        create_intent(
            settings,
            SendRequest(
                mailbox_id=sender["id"],
                owner_service="platform",
                business_ref="notice:N-1",
                business_version=1,
                approved_by=sender["owner"],
                approved_at=datetime.now(UTC),
                subject=body["subject"],
                body_type="text",
                body=body["body"],
                to=tuple(body["to"]),
                attachments=(
                    Attachment(
                        a["name"],
                        a["content_type"],
                        StoredObject(a["storage_key"], a["sha256"], a["size"]),
                    ),
                ),
                approved_payload_sha256=body["approved_payload_sha256"],
            ),
        )
    )
    response = world.client.get(f"/internal/v1/platform/mail/send-intents/{intent}", headers=AS_CS)
    assert response.status_code == 404


def test_send_task_sends_once_and_schedules_the_copy_check(settings, sender):
    world = World(settings)
    intent = created(world, sender)
    response = world.post("send", {"intent_id": intent}, AS_TASKS)
    assert (response.status_code, response.json()["data"]["status"]) == (200, "ACCEPTED")
    [sent] = world.graph.sent
    assert base64.b64decode(sent["message"]["attachments"][0]["contentBytes"]) == FILE
    [(name, task)] = world.tasks.named("mailconfirm-").items()
    assert name == f"mailconfirm-{intent}-s1"
    assert task["httpRequest"]["url"].endswith("/send-confirm")
    assert "scheduleTime" in task
    # 같은 작업이 다시 와도 두 번 보내지 않는다.
    again = world.post("send", {"intent_id": intent}, AS_TASKS)
    assert (again.status_code, again.json()["data"]["status"]) == (200, "SKIPPED")
    assert len(world.graph.posts()) == 1


def test_deferred_send_asks_the_queue_to_retry(settings, sender):
    world = World(settings)
    intent = created(world, sender)
    world.graph.replies = [429]
    response = world.post("send", {"intent_id": intent}, AS_TASKS)
    assert (response.status_code, response.json()["data"]["status"]) == (503, "QUEUED")


def test_unknown_send_is_not_retried_by_the_queue(settings, sender):
    world = World(settings)
    intent = created(world, sender)
    world.graph.replies = [httpx.ReadTimeout("slow")]
    response = world.post("send", {"intent_id": intent}, AS_TASKS)
    assert (response.status_code, response.json()["data"]["status"]) == (200, "UNKNOWN")


def test_disconnected_mailbox_fails_the_waiting_intent(settings, sender):
    world = World(settings)
    intent = created(world, sender)
    with psycopg.connect(sender["dsn"]) as conn:
        conn.execute(
            "UPDATE mailbox_auth.connections SET state='disconnected', cache_ciphertext=NULL "
            "WHERE id=%s",
            (sender["id"],),
        )
    response = world.post("send", {"intent_id": intent}, AS_TASKS)
    assert response.json()["data"] == {
        "intent_id": intent,
        "status": "SKIPPED",
        "code": "MAILBOX_NOT_CONNECTED",
    }
    assert rows(
        sender["dsn"],
        "SELECT status, error_code FROM integration.mail_send_intents WHERE id=%s",
        intent,
    ) == [("FAILED", "MAILBOX_NOT_CONNECTED")]


def test_copy_check_retries_until_the_sent_copy_appears(settings, sender):
    world = World(settings)
    intent = created(world, sender)
    world.post("send", {"intent_id": intent}, AS_TASKS)
    copies = list(world.graph.sent_items)
    world.graph.sent_items.clear()
    first = world.post("send-confirm", {"intent_id": intent}, AS_TASKS)
    assert first.status_code == 503
    world.graph.sent_items.extend(copies)
    second = world.post("send-confirm", {"intent_id": intent}, AS_TASKS)
    assert (second.status_code, second.json()["data"]["code"]) == (200, None)


def test_sweep_recovers_stuck_and_unregistered_work(settings, sender):
    world = World(settings)
    stuck = created(world, sender)
    waiting = world.post("send-intents", payload(sender, world, business_version=3), AS_CS).json()[
        "data"
    ]["intent_id"]
    unknown = world.post("send-intents", payload(sender, world, business_version=4), AS_CS).json()[
        "data"
    ]["intent_id"]
    world.tasks.tasks.clear()
    with psycopg.connect(sender["dsn"]) as conn:
        conn.execute(
            "UPDATE integration.mail_send_intents SET status='SUBMITTING', attempts=1, "
            "submitted_at=now() - interval '20 minutes' WHERE id=%s",
            (stuck,),
        )
        conn.execute(
            "UPDATE integration.mail_send_intents SET updated_at=now() - interval '5 minutes' "
            "WHERE id=%s",
            (waiting,),
        )
        conn.execute(
            "UPDATE integration.mail_send_intents SET status='UNKNOWN', attempts=1, "
            "submitted_at=now() - interval '11 minutes' WHERE id=%s",
            (unknown,),
        )
    response = world.post("send-sweep", {}, AS_TASKS)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["interrupted"] >= 1 and data["failed"] == 0
    names = set(world.tasks.named("mail"))
    assert f"mailsend-{waiting}-a0" in names
    assert f"mailconfirm-{unknown}-u1" in names
    # 멈췄던 전송은 결과 불명이 되고 유예가 지나 대조 대상이 된다.
    assert f"mailconfirm-{stuck}-u1" in names
    assert rows(
        sender["dsn"],
        "SELECT status FROM integration.mail_send_intents WHERE id=%s",
        stuck,
    ) == [("UNKNOWN",)]


def test_sweep_registration_failure_asks_the_scheduler_to_retry(settings, sender):
    world = World(settings)
    waiting = created(world, sender)
    with psycopg.connect(sender["dsn"]) as conn:
        conn.execute(
            "UPDATE integration.mail_send_intents SET updated_at=now() - interval '5 minutes' "
            "WHERE id=%s",
            (waiting,),
        )
    world.tasks.status = 500
    assert world.post("send-sweep", {}, AS_TASKS).status_code == 503
