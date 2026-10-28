"""KAN-74 승인 메일 발송을 전용 PostgreSQL과 가짜 Graph로 검증한다.

실제 Graph·사서함에는 보내지 않는다. 토큰 함수는 KAN-81 계약 형태의 합성 함수다.
"""

import asyncio
import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest
from pydantic import SecretStr

from backend.api.platform.mail import send
from backend.api.platform.mail.send import (
    INTENT_PROPERTY,
    Attachment,
    SendRequest,
    authorize_resend,
    confirm_sent_copy,
    create_intent,
    expire_submitting,
    payload_sha256,
    submit_intent,
)
from backend.api.platform.mail.storage import LocalObjectStore
from backend.shared.errors import ApplicationError
from tests.platform.test_mail_sync_db import mail_database as mail_database
from tests.platform.test_mail_sync_db import mailbox as mailbox
from tests.platform.test_mail_sync_db import rows

MIGRATION = Path("infra/migrations/platform/20261028_KAN-74_mail_send.sql")
TOKEN, FRESH = "synthetic-send-token", "synthetic-fresh-token"
PDF = b"%PDF-synthetic answer"


@pytest.fixture(scope="session")
def send_database(mail_database):
    with psycopg.connect(mail_database, autocommit=True) as conn:
        if (
            conn.execute("SELECT to_regclass('integration.mail_send_intents')").fetchone()[0]
            is None
        ):
            sql = MIGRATION.read_text()
            conn.execute("\n".join(x for x in sql.splitlines() if not x.startswith("\\")))
    return mail_database


@pytest.fixture
def sender(send_database, mailbox, tmp_path):
    """발신 사서함·저장소와, 검사 뒤 발송 기록 정리."""
    store = LocalObjectStore(tmp_path)
    stored = asyncio.run(store.put(PDF, mailbox_id=mailbox["id"]))
    yield {**mailbox, "store": store, "pdf": stored}
    with psycopg.connect(send_database) as conn:
        conn.execute(
            "DELETE FROM integration.mail_send_events WHERE intent_id IN "
            "(SELECT id FROM integration.mail_send_intents WHERE mailbox_id=%s)",
            (mailbox["id"],),
        )
        conn.execute(
            "DELETE FROM integration.mail_send_intents WHERE mailbox_id=%s", (mailbox["id"],)
        )


def request(sender, **changes):
    values = {
        "mailbox_id": sender["id"],
        "owner_service": "cs",
        "business_ref": "cs_ticket:TCK-000123",
        "business_version": 2,
        "approved_by": sender["owner"],
        "approved_at": datetime(2026, 10, 28, 9, 0, tzinfo=UTC),
        "subject": "Re: 차단기 트립 문의",
        "body_type": "text",
        "body": "안녕하세요. 확인 결과를 안내드립니다.",
        "to": ("customer@example.invalid",),
        "cc": ("lead@example.invalid",),
        "attachments": (Attachment("answer.pdf", "application/pdf", sender["pdf"]),),
    }
    values.update(changes)
    values.setdefault(
        "approved_payload_sha256",
        payload_sha256(
            subject=values["subject"],
            body_type=values["body_type"],
            body=values["body"],
            to=values["to"],
            cc=values["cc"],
            attachments=send.attachment_rows(values["attachments"]),
        ),
    )
    return SendRequest(**values)


class FakeGraph:
    """sendMail과 보낸편지함 조회에 답하는 가짜 Graph."""

    def __init__(self):
        self.replies = []  # sendMail 응답 순서. 비면 202
        self.sent = []
        self.requests = []
        self.sent_items = []

    def handler(self, request):
        self.requests.append(request)
        token = request.headers["authorization"].removeprefix("Bearer ")
        assert token in (TOKEN, FRESH)
        if request.method == "POST" and request.url.path == "/v1.0/me/sendMail":
            reply = self.replies.pop(0) if self.replies else 202
            if isinstance(reply, Exception):
                raise reply
            if reply == 202:
                body = json.loads(request.content)
                self.sent.append(body)
                tag = body["message"]["singleValueExtendedProperties"][0]["value"]
                self.sent_items.append({"id": f"AAMk-{tag}", "internetMessageId": f"<{tag}@x>"})
            return httpx.Response(reply, json={} if reply == 202 else {"error": {"code": "x"}})
        if (
            request.method == "GET"
            and request.url.path == "/v1.0/me/mailFolders/sentitems/messages"
        ):
            flt = request.url.params["$filter"]
            assert INTENT_PROPERTY in flt
            found = [m for m in self.sent_items if m["id"].removeprefix("AAMk-") in flt]
            return httpx.Response(200, json={"value": found})
        raise AssertionError(f"unexpected {request.method} {request.url}")

    def posts(self):
        return [r for r in self.requests if r.method == "POST"]


def token_getter(refresh_error=None):
    calls = []

    async def get(*, mailbox_id, owner_user_id, force_refresh=False):
        calls.append(force_refresh)
        if force_refresh and refresh_error:
            raise ApplicationError(*refresh_error)
        return SecretStr(FRESH if force_refresh else TOKEN)

    get.calls = calls
    return get


def run(coro_fn, graph):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(graph.handler)) as client:
            return await coro_fn(client)

    return asyncio.run(go())


def submit(settings, sender, intent_id, graph, get_token=None):
    return run(
        lambda c: submit_intent(
            settings,
            intent_id,
            get_token=get_token or token_getter(),
            client=c,
            store=sender["store"],
        ),
        graph,
    )


def confirm(settings, intent_id, graph):
    return run(
        lambda c: confirm_sent_copy(settings, intent_id, get_token=token_getter(), client=c),
        graph,
    )


def create(settings, req):
    return asyncio.run(create_intent(settings, req))


def events(dsn, intent_id):
    return [
        r[0]
        for r in rows(
            dsn,
            "SELECT event FROM integration.mail_send_events WHERE intent_id=%s ORDER BY at, event",
            intent_id,
        )
    ]


def state(dsn, intent_id):
    return rows(
        dsn,
        "SELECT status, error_code, attempts, sent_copy_confirmed_at IS NOT NULL "
        "FROM integration.mail_send_intents WHERE id=%s",
        intent_id,
    )[0]


def test_accepted_send_carries_the_approved_content_once(settings, sender):
    intent = create(settings, request(sender))
    graph = FakeGraph()
    result = submit(settings, sender, intent, graph)
    assert (result.status, result.error_code) == ("ACCEPTED", None)
    [body] = graph.sent
    message = body["message"]
    assert message["subject"] == "Re: 차단기 트립 문의"
    assert [r["emailAddress"]["address"] for r in message["toRecipients"]] == [
        "customer@example.invalid"
    ]
    assert [r["emailAddress"]["address"] for r in message["ccRecipients"]] == [
        "lead@example.invalid"
    ]
    assert base64.b64decode(message["attachments"][0]["contentBytes"]) == PDF
    assert message["singleValueExtendedProperties"] == [
        {"id": INTENT_PROPERTY, "value": str(intent)}
    ]
    assert body["saveToSentItems"] is True
    assert state(sender["dsn"], intent) == ("ACCEPTED", None, 1, False)
    assert events(sender["dsn"], intent) == ["QUEUED", "SUBMITTING", "ACCEPTED"]
    # 접수된 의도는 다시 보내지 않는다.
    with pytest.raises(ApplicationError) as error:
        submit(settings, sender, intent, graph)
    assert error.value.code == "MAIL_SEND_NOT_QUEUED"
    assert len(graph.posts()) == 1


def test_accepted_is_not_delivery_until_the_sent_copy_is_found(settings, sender):
    intent = create(settings, request(sender))
    graph = FakeGraph()
    submit(settings, sender, intent, graph)
    assert state(sender["dsn"], intent)[3] is False
    result = confirm(settings, intent, graph)
    assert result.sent_copy_confirmed
    assert state(sender["dsn"], intent) == ("ACCEPTED", None, 1, True)
    assert rows(
        sender["dsn"],
        "SELECT sent_graph_id, sent_internet_message_id FROM integration.mail_send_intents "
        "WHERE id=%s",
        intent,
    ) == [(f"AAMk-{intent}", f"<{intent}@x>")]


def test_same_approval_is_one_intent_and_other_content_conflicts(settings, sender):
    first = create(settings, request(sender))
    assert create(settings, request(sender)) == first
    assert events(sender["dsn"], first) == ["QUEUED"]
    with pytest.raises(ApplicationError) as error:
        create(settings, request(sender, body="다른 본문"))
    assert (error.value.status_code, error.value.code) == (409, "MAIL_SEND_CONFLICT")


def test_content_changed_after_approval_is_refused(settings, sender):
    req = request(sender)
    with pytest.raises(ApplicationError) as error:
        create(
            settings,
            request(
                sender,
                body="승인 뒤 고친 본문",
                approved_payload_sha256=req.approved_payload_sha256,
            ),
        )
    assert error.value.code == "MAIL_APPROVAL_MISMATCH"


def test_stored_row_changed_before_sending_is_not_sent(settings, sender):
    intent = create(settings, request(sender))
    with psycopg.connect(sender["dsn"]) as conn:
        conn.execute(
            "UPDATE integration.mail_send_intents SET body='바뀐 본문' WHERE id=%s", (intent,)
        )
    graph = FakeGraph()
    result = submit(settings, sender, intent, graph)
    assert (result.status, result.error_code) == ("FAILED", "MAIL_APPROVAL_MISMATCH")
    assert graph.posts() == []


def test_changed_attachment_bytes_are_not_sent(settings, sender):
    intent = create(settings, request(sender))
    (sender["store"].root / sender["pdf"].key).write_bytes(b"tampered")
    graph = FakeGraph()
    result = submit(settings, sender, intent, graph)
    assert (result.status, result.error_code) == ("QUEUED", "MAIL_STORAGE_UNAVAILABLE")
    assert graph.posts() == []


@pytest.mark.parametrize(
    "change, code",
    [
        ({"to": ()}, "MAIL_RECIPIENTS_INVALID"),
        ({"to": ("not-an-address",)}, "MAIL_RECIPIENTS_INVALID"),
        (
            {"cc": ("Customer@example.invalid",), "to": ("customer@example.invalid",)},
            "MAIL_RECIPIENTS_INVALID",
        ),
        ({"subject": "a\r\nBcc: x@example.invalid"}, "MAIL_CONTENT_INVALID"),
    ],
)
def test_invalid_mail_is_rejected_before_storing(settings, sender, change, code):
    with pytest.raises(ApplicationError) as error:
        create(settings, request(sender, **change))
    assert error.value.code == code
    assert rows(
        sender["dsn"],
        "SELECT count(*) FROM integration.mail_send_intents WHERE mailbox_id=%s",
        sender["id"],
    ) == [(0,)]


def test_too_large_attachments_are_rejected(settings, sender, monkeypatch):
    monkeypatch.setattr(send, "MAX_ATTACHMENT_BYTES", len(PDF) - 1)
    with pytest.raises(ApplicationError) as error:
        create(settings, request(sender))
    assert error.value.code == "MAIL_ATTACHMENT_TOO_LARGE"


def test_attachment_from_another_mailbox_is_refused(settings, sender):
    other = asyncio.run(sender["store"].put(PDF, mailbox_id=uuid4()))
    with pytest.raises(ApplicationError) as error:
        create(
            settings,
            request(sender, attachments=(Attachment("answer.pdf", "application/pdf", other),)),
        )
    assert error.value.code == "MAIL_ATTACHMENT_INVALID"


def test_body_and_attachments_together_must_fit_one_request(settings, sender, monkeypatch):
    monkeypatch.setattr(send, "MAX_REQUEST_BYTES", send.REQUEST_OVERHEAD + 100)
    with pytest.raises(ApplicationError) as error:
        create(settings, request(sender, body="가" * 40))
    assert error.value.code == "MAIL_TOO_LARGE"


def test_only_the_approvers_connected_mailbox_can_send(settings, sender):
    with pytest.raises(ApplicationError) as error:
        create(settings, request(sender, approved_by=uuid4()))
    assert error.value.status_code == 403
    with psycopg.connect(sender["dsn"]) as conn:
        conn.execute(
            "UPDATE mailbox_auth.connections SET state='disconnected', cache_ciphertext=NULL "
            "WHERE id=%s",
            (sender["id"],),
        )
    with pytest.raises(ApplicationError) as error:
        create(settings, request(sender))
    assert error.value.code == "MAILBOX_NOT_CONNECTED"


def test_no_response_is_unknown_and_never_resent_automatically(settings, sender):
    intent = create(settings, request(sender))
    graph = FakeGraph()
    graph.replies = [httpx.ReadTimeout("slow")]
    result = submit(settings, sender, intent, graph)
    assert (result.status, result.error_code) == ("UNKNOWN", "GRAPH_NO_RESPONSE")
    assert len(graph.posts()) == 1
    with pytest.raises(ApplicationError) as error:
        submit(settings, sender, intent, graph)
    assert error.value.code == "MAIL_SEND_NOT_QUEUED"
    assert len(graph.posts()) == 1


@pytest.mark.parametrize(
    "reply, status, code",
    [
        (500, "UNKNOWN", "GRAPH_UNAVAILABLE"),
        (503, "UNKNOWN", "GRAPH_UNAVAILABLE"),
        (400, "FAILED", "GRAPH_BAD_REQUEST"),
        (403, "FAILED", "GRAPH_FORBIDDEN"),
        (429, "QUEUED", "GRAPH_THROTTLED"),
    ],
)
def test_provider_replies_map_to_fixed_outcomes(settings, sender, reply, status, code):
    intent = create(settings, request(sender))
    graph = FakeGraph()
    graph.replies = [reply]
    result = submit(settings, sender, intent, graph)
    assert (result.status, result.error_code) == (status, code)
    assert len(graph.posts()) == 1


def test_throttled_mail_can_be_sent_later_up_to_three_attempts(settings, sender):
    intent = create(settings, request(sender))
    graph = FakeGraph()
    graph.replies = [429, 429, 429]
    for _ in range(2):
        assert submit(settings, sender, intent, graph).status == "QUEUED"
    # 세 번째도 미뤄지면 대기에 갇히지 않고 실패로 마감한다.
    result = submit(settings, sender, intent, graph)
    assert (result.status, result.error_code) == ("FAILED", "MAIL_SEND_ATTEMPTS_EXHAUSTED")
    with pytest.raises(ApplicationError) as error:
        submit(settings, sender, intent, graph)
    assert error.value.code == "MAIL_SEND_NOT_QUEUED"
    assert len(graph.posts()) == 3


def test_throttled_mail_is_sent_on_a_later_attempt(settings, sender):
    intent = create(settings, request(sender))
    graph = FakeGraph()
    graph.replies = [429, 202]
    assert submit(settings, sender, intent, graph).status == "QUEUED"
    assert submit(settings, sender, intent, graph).status == "ACCEPTED"
    assert state(sender["dsn"], intent) == ("ACCEPTED", None, 2, False)


@pytest.mark.parametrize(
    "change",
    [
        {"body_type": "markdown"},
        {"business_ref": "BAD"},
        {"owner_service": "hr"},
        {"business_version": 0},
    ],
)
def test_bad_business_fields_are_input_errors_not_database_errors(settings, sender, change):
    with pytest.raises(ApplicationError) as error:
        create(settings, request(sender, **change))
    assert error.value.status_code == 422


def test_unauthorized_is_retried_once_with_a_fresh_token(settings, sender):
    intent = create(settings, request(sender))
    graph = FakeGraph()
    graph.replies = [401, 202]
    get_token = token_getter()
    assert submit(settings, sender, intent, graph, get_token).status == "ACCEPTED"
    assert get_token.calls == [False, True]
    other = create(settings, request(sender, business_version=3))
    graph.replies = [401, 401]
    result = submit(settings, sender, other, graph)
    assert (result.status, result.error_code) == ("FAILED", "GRAPH_UNAUTHORIZED")


def test_failed_refresh_after_unauthorized_does_not_send(settings, sender):
    intent = create(settings, request(sender))
    graph = FakeGraph()
    graph.replies = [401]
    get_token = token_getter(refresh_error=(409, "MAILBOX_RECONNECT_REQUIRED", "x"))
    result = submit(settings, sender, intent, graph, get_token)
    assert (result.status, result.error_code) == ("FAILED", "MAILBOX_RECONNECT_REQUIRED")
    assert len(graph.posts()) == 1


def test_unknown_send_found_in_sent_items_becomes_accepted(settings, sender):
    intent = create(settings, request(sender))
    graph = FakeGraph()
    graph.replies = [500]
    submit(settings, sender, intent, graph)
    # Graph는 500을 돌려줬지만 실제로는 보냈다.
    graph.sent_items.append({"id": f"AAMk-{intent}", "internetMessageId": "<m@x>"})
    assert confirm(settings, intent, graph).sent_copy_confirmed
    assert state(sender["dsn"], intent) == ("ACCEPTED", None, 1, True)
    assert events(sender["dsn"], intent)[-1] == "SENT_COPY_CONFIRMED"


def test_resend_needs_a_late_not_found_check_and_the_approvers_reason(settings, sender):
    intent = create(settings, request(sender))
    graph = FakeGraph()
    graph.replies = [httpx.ConnectError("down")]
    submit(settings, sender, intent, graph)
    assert confirm(settings, intent, graph).sent_copy_confirmed is False
    # 전송 직후의 "사본 없음"은 보낸편지함 반영 지연일 수 있어 근거가 되지 않는다.
    with pytest.raises(ApplicationError) as error:
        asyncio.run(
            authorize_resend(settings, intent, actor_user_id=sender["owner"], reason="없음")
        )
    assert error.value.code == "MAIL_RESEND_UNVERIFIED"
    with psycopg.connect(sender["dsn"]) as conn:
        conn.execute(
            "UPDATE integration.mail_send_intents SET submitted_at=now() - interval '11 minutes' "
            "WHERE id=%s",
            (intent,),
        )
    confirm(settings, intent, graph)
    with pytest.raises(ApplicationError) as error:
        asyncio.run(authorize_resend(settings, intent, actor_user_id=uuid4(), reason="확인함"))
    assert error.value.code == "MAIL_RESEND_FORBIDDEN"
    with pytest.raises(ApplicationError) as error:
        asyncio.run(authorize_resend(settings, intent, actor_user_id=sender["owner"], reason=" "))
    assert error.value.code == "MAIL_RESEND_REASON_REQUIRED"
    result = asyncio.run(
        authorize_resend(
            settings, intent, actor_user_id=sender["owner"], reason="보낸편지함에 없음 확인"
        )
    )
    assert result.status == "QUEUED"
    assert rows(
        sender["dsn"],
        "SELECT actor_user_id, note FROM integration.mail_send_events "
        "WHERE intent_id=%s AND event='RESEND_AUTHORIZED'",
        intent,
    ) == [(sender["owner"], "보낸편지함에 없음 확인")]
    assert submit(settings, sender, intent, graph).status == "ACCEPTED"
    assert state(sender["dsn"], intent)[2] == 2


def test_accepted_mail_cannot_be_resent(settings, sender):
    intent = create(settings, request(sender))
    submit(settings, sender, intent, FakeGraph())
    with pytest.raises(ApplicationError) as error:
        asyncio.run(authorize_resend(settings, intent, actor_user_id=sender["owner"], reason="x"))
    assert error.value.code == "MAIL_RESEND_NOT_ALLOWED"


def test_interrupted_submission_becomes_unknown(settings, sender):
    intent = create(settings, request(sender))
    with psycopg.connect(sender["dsn"]) as conn:
        conn.execute(
            "UPDATE integration.mail_send_intents SET status='SUBMITTING', attempts=1, "
            "submitted_at=now() - interval '20 minutes' WHERE id=%s",
            (intent,),
        )
    from datetime import timedelta

    assert asyncio.run(expire_submitting(settings, timedelta(minutes=15))) >= 1
    assert state(sender["dsn"], intent)[:2] == ("UNKNOWN", "MAIL_SUBMIT_INTERRUPTED")


def test_runtime_role_cannot_rewrite_or_delete_history(settings, sender):
    intent = create(settings, request(sender))
    with psycopg.connect(settings.database_url.get_secret_value()) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "UPDATE integration.mail_send_events SET note='x' WHERE intent_id=%s", (intent,)
            )
    with psycopg.connect(settings.database_url.get_secret_value()) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM integration.mail_send_intents WHERE id=%s", (intent,))


def test_payload_hash_ignores_storage_location():
    base = {"name": "a.pdf", "content_type": "application/pdf", "sha256": "0" * 64, "size": 1}
    kwargs = {"subject": "s", "body_type": "text", "body": "b", "to": ["a@b.c"], "cc": []}
    first = payload_sha256(**kwargs, attachments=[{**base, "storage_key": "mail/x"}])
    second = payload_sha256(**kwargs, attachments=[{**base, "storage_key": "mail/y"}])
    assert first == second
    assert first != payload_sha256(**{**kwargs, "to": ["c@b.c"]}, attachments=[base])
