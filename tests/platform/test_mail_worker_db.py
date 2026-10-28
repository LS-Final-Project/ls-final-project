"""KAN-72 메일 수집 워커를 전용 PostgreSQL과 가짜 Graph·Cloud Storage·Cloud Tasks로 검증한다.

실제 Scheduler·Tasks·버킷·Graph에는 연결하지 않는다. 호출자 검증기는 합성이다. 이 검사
통과가 운영 큐·IAM·Cloud Run 연결 확인을 뜻하지 않는다.
"""

import base64
import json
import os
import time
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.api.platform.mail.worker import MailSyncSettings, configure_mail_sync
from backend.api.platform.main import create_app
from tests.platform.test_mail_storage_gcs import FakeGcs
from tests.platform.test_mail_sync_db import CONSUMER_TID, TOKEN, FakeGraph, rows
from tests.platform.test_mail_sync_db import mail_database as mail_database
from tests.platform.test_mail_sync_db import mailbox as mailbox

QUEUE = "projects/proj-test-01/locations/asia-northeast3/queues/mail-sync"
SERVICE = "https://team4-platform-test.a.run.app"
INVOKER = "mail-sync-invoker@proj-test-01.iam.gserviceaccount.com"
BUCKET = "proj-test-team4-mail-objects"
GOOD = {"Authorization": "Bearer good-caller"}


def enabled(**changes):
    values = {
        "enabled": True,
        "queue": QUEUE,
        "service_url": SERVICE,
        "invoker_email": INVOKER,
        "MAIL_OBJECT_BUCKET": BUCKET,
        **changes,
    }
    return MailSyncSettings(_env_file=None, **values)


class FakeCaller:
    """지정 호출 계정 토큰만 통과시키는 합성 검증기."""

    def __init__(self):
        self.tokens = []

    def verify(self, token):
        self.tokens.append(token)
        if token != "good-caller":
            raise HTTPException(401)


class FakeTasks:
    """작업 이름이 같으면 409를 돌려주는 가짜 Cloud Tasks."""

    def __init__(self):
        self.tasks = {}
        self.status = None

    def handler(self, request):
        assert request.headers["authorization"] == "Bearer tasks-token"
        assert request.url.path == f"/v2/{QUEUE}/tasks"
        if self.status is not None:
            return httpx.Response(self.status, json={"error": {"code": self.status}})
        task = json.loads(request.content)["task"]
        if task["name"] in self.tasks:
            return httpx.Response(409, json={"error": {"status": "ALREADY_EXISTS"}})
        self.tasks[task["name"]] = task
        return httpx.Response(200, json=task)


class FakeProvider:
    """KAN-81 제공자 호출 형태의 합성 토큰 함수."""

    def __init__(self):
        self.calls = []

    async def get_mailbox_access_token(self, *, mailbox_id, owner_user_id, force_refresh=False):
        self.calls.append((mailbox_id, owner_user_id, force_refresh))
        return SecretStr(TOKEN)


class World:
    """워커 앱과 바깥 서비스 가짜들."""

    def __init__(self, settings, value=None):
        self.graph, self.gcs, self.tasks = FakeGraph(), FakeGcs(), FakeTasks()
        self.caller, self.provider = FakeCaller(), FakeProvider()

        def route(request):
            host = request.url.host
            if host == "graph.microsoft.com":
                return self.graph.handler(request)
            if host == "storage.googleapis.com":
                return self.gcs.handler(request)
            if host == "cloudtasks.googleapis.com":
                return self.tasks.handler(request)
            raise AssertionError(f"unexpected host {host}")

        async def tasks_token():
            return "tasks-token"

        async def storage_token():
            return "gcs-token"

        app = create_app(settings=settings)
        app.state.mailbox_tokens = self.provider
        configure_mail_sync(
            app,
            value or enabled(),
            caller=self.caller,
            tasks_token=tasks_token,
            storage_token=storage_token,
            transport=httpx.MockTransport(route),
        )
        self.client = TestClient(app)

    def sync(self, mailbox_id, headers=GOOD):
        return self.client.post(
            "/internal/v1/platform/mail/sync", json={"mailbox_id": str(mailbox_id)}, headers=headers
        )

    def dispatch(self, headers=GOOD):
        return self.client.post("/internal/v1/platform/mail/dispatch", headers=headers)


def add_mailbox(dsn, owner, state="connected"):
    mailbox_id = uuid4()
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO mailbox_auth.connections (id, owner_user_id, subject_tid, subject_oid, "
            "revision, state, key_version, cache_ciphertext) "
            "VALUES (%s, %s, %s, %s, 1, %s, 'test-v1', %s)",
            (
                mailbox_id,
                owner,
                CONSUMER_TID,
                uuid4(),
                state,
                os.urandom(64) if state == "connected" else None,
            ),
        )
    return mailbox_id


def remove_mailbox(dsn, mailbox_id):
    with psycopg.connect(dsn) as conn:
        conn.execute("DELETE FROM mailbox_auth.connections WHERE id=%s", (mailbox_id,))


def test_worker_routes_are_closed_by_default(settings):
    client = TestClient(create_app(settings=settings))
    assert client.post("/internal/v1/platform/mail/dispatch", headers=GOOD).status_code == 404
    response = client.post(
        "/internal/v1/platform/mail/sync", json={"mailbox_id": str(uuid4())}, headers=GOOD
    )
    assert response.status_code == 404
    assert "/internal/v1/platform/mail/sync" not in client.get("/openapi.json").json()["paths"]


@pytest.mark.parametrize(
    "change",
    [
        {"queue": "queues/mail-sync"},
        {"service_url": "http://team4-platform-test.a.run.app"},
        {"service_url": SERVICE + "/internal"},
        {"service_url": SERVICE + "/"},
        {"invoker_email": "someone@example.com"},
        {"MAIL_OBJECT_BUCKET": ""},
    ],
)
def test_enabled_worker_requires_complete_configuration(change):
    with pytest.raises(ValueError):
        enabled(**change)


def test_enabled_worker_requires_the_mailbox_token_provider(settings):
    app = create_app(settings=settings)
    with pytest.raises(ValueError):
        configure_mail_sync(app, enabled())


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer someone-else"}, {"Authorization": "Basic good-caller"}],
)
def test_wrong_caller_is_rejected_before_any_work(settings, mailbox, headers):
    world = World(settings)
    assert world.dispatch(headers).status_code == 401
    assert world.sync(mailbox["id"], headers).status_code == 401
    assert world.tasks.tasks == {} and world.graph.requests == [] and world.provider.calls == []


def test_dispatch_enqueues_one_task_per_connected_mailbox_once_per_slot(settings, mailbox):
    other = add_mailbox(mailbox["dsn"], mailbox["owner"])
    off = add_mailbox(mailbox["dsn"], mailbox["owner"], state="disconnected")
    try:
        world = World(settings)
        slot = int(time.time()) // 300
        first = world.dispatch()
        assert first.status_code == 200
        names = {f"{QUEUE}/tasks/mail-{m}-{slot}" for m in (mailbox["id"], other)}
        ours = {n: t for n, t in world.tasks.tasks.items() if n in names}
        assert set(ours) == names
        assert not [n for n in world.tasks.tasks if str(off) in n]
        task = ours[f"{QUEUE}/tasks/mail-{mailbox['id']}-{slot}"]["httpRequest"]
        assert task["url"] == f"{SERVICE}/internal/v1/platform/mail/sync"
        assert task["httpMethod"] == "POST"
        assert task["oidcToken"] == {"serviceAccountEmail": INVOKER, "audience": SERVICE}
        assert json.loads(base64.b64decode(task["body"])) == {"mailbox_id": str(mailbox["id"])}
        # 같은 구간에 다시 불려도 작업이 늘지 않는다.
        before = len(world.tasks.tasks)
        second = world.dispatch()
        assert second.status_code == 200
        assert len(world.tasks.tasks) == before
        assert second.json()["data"]["created"] == 0
        assert second.json()["data"]["existing"] == second.json()["data"]["mailboxes"]
    finally:
        remove_mailbox(mailbox["dsn"], other)
        remove_mailbox(mailbox["dsn"], off)


def test_dispatch_failure_asks_the_scheduler_to_retry(settings, mailbox):
    world = World(settings)
    world.tasks.status = 500
    response = world.dispatch()
    assert response.status_code == 503
    assert response.json()["data"]["failed"] >= 1


def test_sync_stores_mail_in_the_bucket_and_repeating_it_stores_nothing_new(settings, mailbox):
    world = World(settings)
    first = world.sync(mailbox["id"])
    assert first.status_code == 200
    assert first.json()["data"] == {
        "mailbox_id": str(mailbox["id"]),
        "outcome": "SUCCEEDED",
        "code": None,
        "messages_stored": 3,
    }
    assert world.provider.calls == [(mailbox["id"], mailbox["owner"], False)]
    assert world.gcs.objects
    assert all(key.startswith(f"mail/{mailbox['id']}/sha256/") for key in world.gcs.objects)
    objects = dict(world.gcs.objects)
    # 같은 메일을 다시 받아도(전체 다시 받기) 메시지·객체가 늘지 않는다.
    with psycopg.connect(mailbox["dsn"]) as conn:
        conn.execute(
            "UPDATE integration.mail_sync_cursors SET next_link=NULL, delta_link=NULL "
            "WHERE mailbox_id=%s",
            (mailbox["id"],),
        )
    second = world.sync(mailbox["id"])
    assert (second.status_code, second.json()["data"]["messages_stored"]) == (200, 0)
    assert world.gcs.objects == objects
    assert rows(
        mailbox["dsn"],
        "SELECT count(*), count(DISTINCT graph_id) FROM integration.mail_messages "
        "WHERE mailbox_id=%s",
        mailbox["id"],
    ) == [(3, 3)]


def test_sync_already_running_is_skipped_without_retry(settings, mailbox):
    with psycopg.connect(mailbox["dsn"]) as conn:
        conn.execute(
            "INSERT INTO integration.mail_sync_runs (id, mailbox_id, lease_expires_at) "
            "VALUES (%s, %s, now() + interval '5 minutes')",
            (uuid4(), mailbox["id"]),
        )
    world = World(settings)
    response = world.sync(mailbox["id"])
    assert response.status_code == 200
    assert response.json()["data"]["outcome"] == "SKIPPED"
    assert response.json()["data"]["code"] == "MAIL_SYNC_IN_PROGRESS"
    assert world.graph.requests == []


def test_sync_of_disconnected_mailbox_is_skipped(settings, mailbox):
    off = add_mailbox(mailbox["dsn"], mailbox["owner"], state="disconnected")
    try:
        response = World(settings).sync(off)
        assert (response.status_code, response.json()["data"]["code"]) == (
            200,
            "MAILBOX_NOT_CONNECTED",
        )
    finally:
        remove_mailbox(mailbox["dsn"], off)


def test_transient_failure_asks_the_queue_to_retry(settings, mailbox):
    world = World(settings)
    world.gcs.upload_status = 503
    response = world.sync(mailbox["id"])
    assert response.status_code == 503
    assert response.json()["data"]["code"] == "MAIL_STORAGE_UNAVAILABLE"


def test_permanent_failure_is_recorded_without_queue_retry(settings, mailbox):
    world = World(settings)
    world.graph.pages[next(iter(world.graph.pages))] = (403, {"error": {"code": "Forbidden"}})
    response = world.sync(mailbox["id"])
    assert response.status_code == 200
    assert response.json()["data"]["outcome"] == "FAILED"
    assert response.json()["data"]["code"] == "GRAPH_FORBIDDEN"


def test_sync_body_must_name_one_mailbox(settings, mailbox):
    world = World(settings)
    for body in ('{"mailbox_id": "nope"}', "not json", "{}"):
        response = world.client.post("/internal/v1/platform/mail/sync", content=body, headers=GOOD)
        assert (response.status_code, response.json()["error"]["code"]) == (
            422,
            "VALIDATION_ERROR",
        )
    assert world.graph.requests == []


def test_bad_body_does_not_reveal_closed_or_unauthenticated_routes(settings, mailbox):
    bad = '{"mailbox_id": "nope"}'
    closed = TestClient(create_app(settings=settings))
    assert closed.post("/internal/v1/platform/mail/sync", content=bad).status_code == 404
    world = World(settings)
    assert world.client.post("/internal/v1/platform/mail/sync", content=bad).status_code == 401
