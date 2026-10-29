"""KAN-110 플랫폼 쪽: 연결된 회신을 C/S에 알리고, 받은 메일을 C/S 문의로 접수한다.

C/S는 가짜 호출 기록기다. 실제 Graph·사서함·C/S에는 연결하지 않는다.
"""

import asyncio
import json
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from backend.api.platform.auth.http import CSRF_HEADER, SESSION_COOKIE, csrf_token
from backend.api.platform.auth.sessions import issue_session
from backend.api.platform.mail.business import FROM_MAIL_PATH, REPLY_PATH, CsCaller
from backend.api.platform.mail.links import notify_cs_replies
from backend.api.platform.mail.storage import LocalObjectStore
from backend.api.platform.main import create_app
from backend.shared.access import PRINCIPAL_HEADER
from backend.shared.service_auth import CALLER_HEADER
from tests.platform.test_mail_links_db import add_message, mime
from tests.platform.test_mail_links_db import links as links
from tests.platform.test_mail_send_db import send_database as send_database
from tests.platform.test_mail_send_db import sender as sender
from tests.platform.test_mail_sync_db import mail_database as mail_database
from tests.platform.test_mail_sync_db import mailbox as mailbox
from tests.platform.test_mail_sync_db import rows
from tests.platform.test_mailbox_http import ORIGIN, configurations

MIGRATION = Path("infra/migrations/platform/20261030_KAN-110_mail_link_notify.sql")
CS_URL = "https://cs.example.invalid"


@pytest.fixture
def notify(links):
    with psycopg.connect(links["dsn"], autocommit=True) as conn:
        conn.execute(
            "\n".join(x for x in MIGRATION.read_text().splitlines() if not x.startswith("\\"))
        )
    return links


class FakeCs:
    """C/S 내부 경로를 흉내 낸다. 받은 호출을 기록하고 정해진 답을 돌려준다."""

    def __init__(self):
        self.calls = []
        self.reply_status = 200
        self.ticket_id = uuid4()
        self.from_mail_status = 201
        self.raise_next = None

    def handler(self, request):
        assert request.url.host == "cs.example.invalid"
        assert request.headers["authorization"] == "Bearer cs-audience-token"
        assert request.headers[CALLER_HEADER] == "cs-audience-token"
        self.calls.append(request)
        if self.raise_next is not None:
            error, self.raise_next = self.raise_next, None
            raise error
        if request.url.path.endswith("/customer-reply"):
            return httpx.Response(
                self.reply_status,
                json={"data": {"state": "IN_REVIEW", "reopened": True, "recorded": True}},
            )
        assert request.url.path == FROM_MAIL_PATH
        assert PRINCIPAL_HEADER in request.headers
        if self.from_mail_status == 201:
            return httpx.Response(201, json={"data": {"id": str(self.ticket_id)}})
        if self.from_mail_status == 409:
            return httpx.Response(
                409,
                json={
                    "error": {
                        "code": "CS_MAIL_ALREADY_REGISTERED",
                        "message": "x",
                        "details": [{"field": "ticket_id", "reason": str(self.ticket_id)}],
                    }
                },
            )
        return httpx.Response(self.from_mail_status, json={"error": {"code": "X"}})

    def caller(self):
        return CsCaller(
            CS_URL,
            lambda audience: "cs-audience-token" if audience == CS_URL else "wrong",
            httpx.MockTransport(self.handler),
        )


def link_reply(notify, ticket_id, method="HEADER"):
    raw = mime()
    message = add_message(notify, raw, conversation=f"conv-{uuid4()}")
    with psycopg.connect(notify["dsn"]) as conn:
        conn.execute(
            "INSERT INTO integration.mail_links (id, message_id, owner_service, business_ref, kind, "
            "method) VALUES (%s, %s, 'cs', %s, 'REPLY', %s)",
            (uuid4(), message, f"cs_ticket:{ticket_id}", method),
        )
        conn.execute(
            "INSERT INTO integration.mail_reply_checks (message_id, outcome) VALUES (%s, 'LINKED')",
            (message,),
        )
    return message


def notified(notify, message):
    [(value,)] = rows(
        notify["dsn"],
        "SELECT business_notified_at FROM integration.mail_links WHERE message_id=%s",
        message,
    )
    return value


def test_linked_replies_are_reported_to_cs_once(settings, notify):
    cs = FakeCs()
    ticket = uuid4()
    message = link_reply(notify, ticket)
    other = link_reply(notify, uuid4(), method="THREAD")
    # 검사용 참조(UUID 아님)는 알릴 곳이 없어 대상이 아니다.
    with psycopg.connect(notify["dsn"]) as conn:
        conn.execute(
            "INSERT INTO integration.mail_links (id, message_id, owner_service, business_ref, kind, "
            "method) VALUES (%s, %s, 'cs', 'cs_ticket:TCK-9', 'REPLY', 'HEADER')",
            (uuid4(), add_message(notify, mime())),
        )
    counts = asyncio.run(notify_cs_replies(settings, cs.caller()))
    assert counts == {"notified": 2, "missing": 0, "failed": 0}
    assert notified(notify, message) is not None and notified(notify, other) is not None
    paths = sorted(c.url.path for c in cs.calls)
    assert REPLY_PATH.format(ticket_id=ticket) in paths
    bodies = [json.loads(c.content) for c in cs.calls]
    assert {b["method"] for b in bodies} == {"HEADER", "THREAD"}
    assert all({"message_id", "method", "received_at"} == set(b) for b in bodies)
    # 다시 불러도 알린 연결은 다시 알리지 않는다.
    assert asyncio.run(notify_cs_replies(settings, cs.caller())) == {
        "notified": 0,
        "missing": 0,
        "failed": 0,
    }
    assert len(cs.calls) == 2


def test_failed_notification_is_left_for_the_next_run(settings, notify):
    cs = FakeCs()
    message = link_reply(notify, uuid4())
    cs.raise_next = httpx.ConnectError("down")
    assert asyncio.run(notify_cs_replies(settings, cs.caller())) == {
        "notified": 0,
        "missing": 0,
        "failed": 1,
    }
    assert notified(notify, message) is None
    cs.reply_status = 503
    assert asyncio.run(notify_cs_replies(settings, cs.caller()))["failed"] == 1
    assert notified(notify, message) is None
    # C/S에 없는 문의는 한 번만 시도한다.
    cs.reply_status = 404
    assert asyncio.run(notify_cs_replies(settings, cs.caller())) == {
        "notified": 0,
        "missing": 1,
        "failed": 0,
    }
    assert notified(notify, message) is not None


# --- 메일 → 문의 접수 --------------------------------------------------------------------------


@pytest.fixture
def web(settings, notify, registered_user, tmp_path):
    auth, mailbox_settings = configurations()
    app = create_app(settings, auth, mailbox_settings=mailbox_settings)
    cs = FakeCs()
    app.state.cs_caller = cs.caller()
    app.state.mail_intake_store = LocalObjectStore(tmp_path)
    session = asyncio.run(issue_session(settings, registered_user[0])).token.get_secret_value()
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        client.cookies.set(SESSION_COOKIE, session)
        yield {
            "client": client,
            "cs": cs,
            "store": LocalObjectStore(tmp_path),
            "headers": {"Origin": ORIGIN, CSRF_HEADER: csrf_token(session)},
        }


def received(web, notify, raw, **changes):
    stored = asyncio.run(web["store"].put(raw, mailbox_id=notify["id"]))
    message = add_message(notify, raw, conversation=f"conv-{uuid4()}", **changes)
    with psycopg.connect(notify["dsn"]) as conn:
        conn.execute(
            "UPDATE integration.mail_messages SET raw_storage_key=%s, from_address=%s WHERE id=%s",
            (stored.key, "plant@customer.invalid", message),
        )
    return message


FIELDS = {
    "customer_external_id": "CUST-0374",
    "product_code": "TRF-OIL-1000",
    "body_language": "en",
}


def register(web, message, **changes):
    return web["client"].post(
        f"/api/v1/platform/mail/messages/{message}/register-ticket",
        json={**FIELDS, **changes},
        headers=web["headers"],
    )


def test_cs_agent_registers_a_received_mail_as_a_ticket_and_links_the_origin(web, notify):
    raw = (
        b"From: Plant <plant@customer.invalid>\r\nSubject: Oil leakage at gasket\r\n"
        b"Message-ID: <orig-110@customer.invalid>\r\n\r\nOil leaks from the gasket.\r\n"
    )
    message = received(web, notify, raw)
    response = register(web, message)
    assert response.status_code == 201, response.text
    assert response.json()["data"] == {
        "message_id": str(message),
        "ticket_id": str(web["cs"].ticket_id),
        "created": True,
    }
    [call] = web["cs"].calls
    body = json.loads(call.content)
    assert (body["subject"], body["body"], body["sender_address"]) == (
        "Oil leakage at gasket",
        "Oil leaks from the gasket.",
        "plant@customer.invalid",
    )
    assert (body["message_id"], body["customer_external_id"], body["product_code"]) == (
        str(message),
        "CUST-0374",
        "TRF-OIL-1000",
    )
    assert UUID(body["mailbox_id"]) == notify["id"] and body["external_id"].startswith("AAMk-")
    principal = json.loads(call.headers[PRINCIPAL_HEADER])
    assert "cs_agent" in principal["roles"]
    assert rows(
        notify["dsn"],
        "SELECT kind, method, business_ref FROM integration.mail_links WHERE message_id=%s",
        message,
    ) == [("ORIGIN", "BUSINESS", f"cs_ticket:{web['cs'].ticket_id}")]

    # 이미 접수된 메일: C/S가 기존 문의를 알려 주고 플랫폼은 연결만 확인한다(두 번 접수하지 않음).
    web["cs"].from_mail_status = 409
    again = register(web, message)
    assert (again.status_code, again.json()["data"]["created"]) == (200, False)
    assert (
        len(
            rows(notify["dsn"], "SELECT 1 FROM integration.mail_links WHERE message_id=%s", message)
        )
        == 1
    )


def test_registration_needs_the_owners_inbox_mail_and_a_body(web, notify):
    sent = received(web, notify, mime(), folder="sentitems")
    assert register(web, sent).status_code == 404
    assert register(web, uuid4()).status_code == 404
    empty = received(web, notify, b"From: a@b.invalid\r\nSubject: s\r\n\r\n")
    assert register(web, empty).json()["error"]["code"] == "MAIL_BODY_EMPTY"
    no_csrf = web["client"].post(
        f"/api/v1/platform/mail/messages/{sent}/register-ticket",
        json=FIELDS,
        headers={"Origin": ORIGIN},
    )
    assert no_csrf.status_code == 403
    bad = register(web, received(web, notify, mime()), customer_external_id="bad id!")
    assert bad.status_code == 422
    web["cs"].raise_next = httpx.ConnectError("down")
    down = register(web, received(web, notify, mime()))
    assert (down.status_code, down.json()["error"]["code"]) == (503, "CS_UNAVAILABLE")


def test_registration_is_closed_without_a_cs_caller(settings, notify, registered_user):
    auth, mailbox_settings = configurations()
    app = create_app(settings, auth, mailbox_settings=mailbox_settings)
    session = asyncio.run(issue_session(settings, registered_user[0])).token.get_secret_value()
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        client.cookies.set(SESSION_COOKIE, session)
        response = client.post(
            f"/api/v1/platform/mail/messages/{uuid4()}/register-ticket",
            json=FIELDS,
            headers={"Origin": ORIGIN, CSRF_HEADER: csrf_token(session)},
        )
    assert response.status_code == 404
