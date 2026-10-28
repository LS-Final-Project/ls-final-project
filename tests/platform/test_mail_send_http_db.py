"""KAN-74 승인자 발송 결과·재발송 허가 사용자 API를 전용 PostgreSQL과 가짜 Graph·Tasks로 검증한다.

실제 Graph·큐에는 연결하지 않는다. 세션은 실제 발급 함수로, 사서함 토큰은 합성으로 만든다.
"""

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from backend.api.platform.auth.http import CSRF_HEADER, SESSION_COOKIE, csrf_token
from backend.api.platform.auth.sessions import issue_session
from backend.api.platform.mail.send import (
    Attachment,
    SendRequest,
    attachment_rows,
    create_intent,
    payload_sha256,
    submit_intent,
)
from backend.api.platform.mail.send_worker import configure_mail_send
from backend.api.platform.main import create_app
from tests.platform.test_mail_send_db import FakeGraph, token_getter
from tests.platform.test_mail_send_db import send_database as send_database
from tests.platform.test_mail_send_db import sender as sender
from tests.platform.test_mail_send_worker_db import Caller, FakeTasks, Provider, enabled
from tests.platform.test_mail_sync_db import CONSUMER_TID, rows
from tests.platform.test_mail_sync_db import mail_database as mail_database
from tests.platform.test_mail_sync_db import mailbox as mailbox
from tests.platform.test_mailbox_http import ORIGIN, configurations

PREFIX = "/api/v1/platform/mail/send-intents"


@pytest.fixture
def web(settings, sender, registered_user):
    auth, mailbox_settings = configurations()
    app = create_app(settings, auth, mailbox_settings=mailbox_settings)
    graph, tasks = FakeGraph(), FakeTasks()

    def route(request):
        if request.url.host == "graph.microsoft.com":
            return graph.handler(request)
        if request.url.host == "cloudtasks.googleapis.com":
            return tasks.handler(request)
        raise AssertionError(request.url.host)

    async def tasks_token():
        return "tasks-token"

    app.state.mailbox_tokens = Provider()
    configure_mail_send(
        app,
        enabled(),
        invoker=Caller("tasks-caller"),
        cs_caller=Caller("cs-caller"),
        tasks_token=tasks_token,
        storage_token=tasks_token,
        transport=httpx.MockTransport(route),
    )
    session = asyncio.run(issue_session(settings, registered_user[0])).token.get_secret_value()
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        client.cookies.set(SESSION_COOKIE, session)
        yield {
            "client": client,
            "graph": graph,
            "tasks": tasks,
            "headers": {"Origin": ORIGIN, CSRF_HEADER: csrf_token(session)},
        }


def intent_for(settings, sender, mailbox_id=None, approver=None, version=2):
    fields = {
        "subject": "Re: 차단기 트립 문의",
        "body_type": "text",
        "body": "안내드립니다.",
        "to": ("customer@example.invalid",),
        "cc": (),
    }
    attachments = (Attachment("answer.pdf", "application/pdf", sender["pdf"]),)
    if mailbox_id is not None:
        attachments = ()
    return asyncio.run(
        create_intent(
            settings,
            SendRequest(
                mailbox_id=mailbox_id or sender["id"],
                owner_service="cs",
                business_ref="cs_ticket:TCK-000123",
                business_version=version,
                approved_by=approver or sender["owner"],
                approved_at=datetime.now(UTC),
                attachments=attachments,
                approved_payload_sha256=payload_sha256(
                    **fields, attachments=attachment_rows(attachments)
                ),
                **fields,
            ),
        )
    )


def make_unknown(settings, sender, intent):
    graph = FakeGraph()
    graph.replies = [httpx.ConnectError("down")]

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(graph.handler)) as client:
            return await submit_intent(
                settings, intent, get_token=token_getter(), client=client, store=sender["store"]
            )

    assert asyncio.run(go()).status == "UNKNOWN"


@pytest.fixture
def stranger(sender):
    """다른 사용자와 그 사용자의 연결 사서함."""
    user, box = uuid4(), uuid4()
    with psycopg.connect(sender["dsn"]) as conn:
        conn.execute(
            "INSERT INTO identity.users(id,tid,oid,email,display_name,dept) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (user, uuid4(), uuid4(), "other@example.invalid", "다른 합성 사용자", "TS_AUTO"),
        )
        conn.execute(
            "INSERT INTO identity.user_roles(user_id,role) VALUES (%s,'cs_agent')", (user,)
        )
        conn.execute(
            "INSERT INTO mailbox_auth.connections (id, owner_user_id, subject_tid, subject_oid, "
            "revision, state, key_version, cache_ciphertext) "
            "VALUES (%s, %s, %s, %s, 1, 'connected', 'test-v1', %s)",
            (box, user, CONSUMER_TID, uuid4(), os.urandom(64)),
        )
    yield {"user": user, "mailbox": box}
    with psycopg.connect(sender["dsn"]) as conn:
        conn.execute(
            "DELETE FROM integration.mail_send_events WHERE intent_id IN "
            "(SELECT id FROM integration.mail_send_intents WHERE mailbox_id=%s)",
            (box,),
        )
        conn.execute("DELETE FROM integration.mail_send_intents WHERE mailbox_id=%s", (box,))
        conn.execute("DELETE FROM mailbox_auth.connections WHERE id=%s", (box,))
        conn.execute("DELETE FROM identity.user_roles WHERE user_id=%s", (user,))
        conn.execute("DELETE FROM identity.users WHERE id=%s", (user,))


def test_routes_are_closed_when_sending_is_off(settings, sender, registered_user):
    auth, mailbox_settings = configurations()
    app = create_app(settings, auth, mailbox_settings=mailbox_settings)
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get(PREFIX).status_code == 404
        assert client.post(f"{PREFIX}/{uuid4()}/resend", json={"reason": "x"}).status_code == 404


def test_list_shows_only_my_sends_without_content(settings, sender, stranger, web):
    mine = intent_for(settings, sender)
    intent_for(
        settings, sender, mailbox_id=stranger["mailbox"], approver=stranger["user"], version=9
    )
    response = web["client"].get(PREFIX)
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert [i["intent_id"] for i in items] == [str(mine)]
    assert items[0]["status"] == "QUEUED" and items[0]["resend_ready"] is False
    assert "body" not in items[0] and "recipients" not in items[0]
    assert web["client"].get(PREFIX, params={"status": "UNKNOWN"}).json()["data"]["items"] == []


def test_list_requires_a_current_session(settings, sender, web):
    web["client"].cookies.clear()
    assert web["client"].get(PREFIX).status_code == 401


@pytest.mark.parametrize("bad", ["no-origin", "foreign-origin", "no-csrf"])
def test_changes_require_same_origin_and_csrf(settings, sender, web, bad):
    intent = intent_for(settings, sender)
    headers = dict(web["headers"])
    if bad == "no-origin":
        headers.pop("Origin")
    elif bad == "foreign-origin":
        headers["Origin"] = "https://evil.example.invalid"
    else:
        headers.pop(CSRF_HEADER)
    for path in ("check-sent-copy", "resend"):
        response = web["client"].post(
            f"{PREFIX}/{intent}/{path}", json={"reason": "확인함"}, headers=headers
        )
        assert response.status_code == 403
    assert web["graph"].requests == []


def test_someone_elses_send_is_hidden(settings, sender, stranger, web):
    theirs = intent_for(
        settings, sender, mailbox_id=stranger["mailbox"], approver=stranger["user"], version=9
    )
    for path, body in (("check-sent-copy", None), ("resend", {"reason": "확인함"})):
        response = web["client"].post(
            f"{PREFIX}/{theirs}/{path}", json=body, headers=web["headers"]
        )
        assert (response.status_code, response.json()["error"]["code"]) == (
            404,
            "MAIL_SEND_NOT_FOUND",
        )


def test_unknown_send_is_checked_then_resent_with_a_reason(settings, sender, web):
    intent = intent_for(settings, sender)
    make_unknown(settings, sender, intent)
    client, headers = web["client"], web["headers"]
    # 유예 전에는 "사본 없음"이 재발송 근거가 되지 않는다.
    check = client.post(f"{PREFIX}/{intent}/check-sent-copy", headers=headers)
    assert check.json()["data"] == {
        "intent_id": str(intent),
        "status": "UNKNOWN",
        "sent_copy_confirmed": False,
    }
    early = client.post(f"{PREFIX}/{intent}/resend", json={"reason": "없음"}, headers=headers)
    assert (early.status_code, early.json()["error"]["code"]) == (409, "MAIL_RESEND_UNVERIFIED")
    with psycopg.connect(sender["dsn"]) as conn:
        conn.execute(
            "UPDATE integration.mail_send_intents SET submitted_at=now() - interval '11 minutes' "
            "WHERE id=%s",
            (intent,),
        )
    client.post(f"{PREFIX}/{intent}/check-sent-copy", headers=headers)
    [item] = client.get(PREFIX, params={"status": "UNKNOWN"}).json()["data"]["items"]
    assert item["resend_ready"] is True
    assert (
        client.post(f"{PREFIX}/{intent}/resend", json={"reason": ""}, headers=headers).status_code
        == 422
    )
    response = client.post(
        f"{PREFIX}/{intent}/resend", json={"reason": "보낸편지함에 없음 확인"}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["data"] == {
        "intent_id": str(intent),
        "status": "QUEUED",
        "task_registered": True,
    }
    assert f"mailsend-{intent}-a1" in web["tasks"].named("mailsend-")
    assert rows(
        sender["dsn"],
        "SELECT note FROM integration.mail_send_events WHERE intent_id=%s "
        "AND event='RESEND_AUTHORIZED'",
        intent,
    ) == [("보낸편지함에 없음 확인",)]


def test_sent_copy_found_for_an_unknown_send_settles_it(settings, sender, web):
    intent = intent_for(settings, sender)
    make_unknown(settings, sender, intent)
    web["graph"].sent_items.append({"id": f"AAMk-{intent}", "internetMessageId": "<m@x>"})
    response = web["client"].post(f"{PREFIX}/{intent}/check-sent-copy", headers=web["headers"])
    assert response.json()["data"] == {
        "intent_id": str(intent),
        "status": "ACCEPTED",
        "sent_copy_confirmed": True,
    }
    again = web["client"].post(
        f"{PREFIX}/{intent}/resend", json={"reason": "x"}, headers=web["headers"]
    )
    assert (again.status_code, again.json()["error"]["code"]) == (409, "MAIL_RESEND_NOT_ALLOWED")
