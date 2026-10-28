"""KAN-73 메일 분류 저장·조회·재분류를 실제 PostgreSQL에서: 판본, 소유자 범위, 원문 미노출, 권한.

제공자는 합성 함수다. 실제 메일 본문을 외부 모델로 보내지 않는다.
"""

import asyncio
import hashlib
import json
import os
from email.message import EmailMessage
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend.api.platform.auth.mailbox_settings import MailboxAuthSettings
from backend.api.platform.auth.settings import MicrosoftAuthSettings
from backend.api.platform.mail.triage import classify_message
from backend.api.platform.main import create_app
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings
from tests.platform.test_mail_sync_db import CONSUMER_TID
from tests.platform.test_mail_sync_db import mail_database as mail_database
from tests.platform.test_mailbox_http import context as context

MIGRATION = Path("infra/migrations/platform/20261028_KAN-73_mail_triage.sql")
SUBJECT = "견적 요청 secret-subject-marker"
SENDER = "buyer-marker@example.invalid"
BODY = "새 프로젝트 RFP secret-body-marker 를 보냅니다."


@pytest.fixture(scope="session")
def triage_database(mail_database):
    """KAN-71 수집 표 위에 KAN-73 분류 표를 한 번 적용한다.

    Args:
        mail_database: 수집 마이그레이션까지 적용한 전용 DB.

    Returns:
        str: 관리자 연결 문자열.
    """
    with psycopg.connect(mail_database, autocommit=True) as conn:
        if conn.execute("SELECT to_regclass('integration.mail_triage')").fetchone()[0] is None:
            sql = MIGRATION.read_text()
            conn.execute("\n".join(x for x in sql.splitlines() if not x.startswith("\\")))
    return mail_database


def raw_mail():
    message = EmailMessage()
    message["Subject"] = SUBJECT
    message["From"] = SENDER
    message.set_content(BODY)
    return message.as_bytes()


def add_message(dsn, owner, raw, *, attachments=()):
    """관리자 연결로 사서함·수집 실행·메일(·첨부) 한 통을 만든다.

    Args:
        dsn: 관리자 연결.
        owner: 메일함 소유자 ID.
        raw: 원문 MIME.
        attachments: (kind, content_type, is_inline) 목록.

    Returns:
        tuple: (메일 ID, 사서함 ID).
    """
    mailbox, run, message_id = uuid4(), uuid4(), uuid4()
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "INSERT INTO mailbox_auth.connections (id, owner_user_id, subject_tid, subject_oid, "
            "revision, state, key_version, cache_ciphertext) "
            "VALUES (%s, %s, %s, %s, 1, 'connected', 'test-v1', %s)",
            (mailbox, owner, CONSUMER_TID, uuid4(), os.urandom(64)),
        )
        conn.execute(
            "INSERT INTO integration.mail_sync_runs (id, mailbox_id, lease_expires_at) "
            "VALUES (%s, %s, now())",
            (run, mailbox),
        )
        conn.execute(
            "INSERT INTO integration.mail_messages (id, mailbox_id, graph_id, folder, subject, "
            "from_address, received_at, has_attachments, raw_storage_key, raw_sha256, raw_size, "
            "first_sync_run_id) VALUES (%s, %s, %s, 'inbox', %s, %s, now(), %s, 'k', %s, %s, %s)",
            (
                message_id,
                mailbox,
                f"g-{message_id}",
                SUBJECT,
                SENDER,
                bool(attachments),
                hashlib.sha256(raw).hexdigest(),
                len(raw),
                run,
            ),
        )
        for kind, content_type, inline in attachments:
            conn.execute(
                "INSERT INTO integration.mail_attachments (id, message_id, graph_attachment_id, "
                "kind, content_type, is_inline, storage_key, sha256, size) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    uuid4(),
                    message_id,
                    f"a-{uuid4()}",
                    kind,
                    content_type,
                    inline,
                    None if kind == "reference" else "k",
                    None if kind == "reference" else "0" * 64,
                    None if kind == "reference" else 1,
                ),
            )
    return message_id, mailbox


@pytest.fixture
def owned(triage_database, context):
    """현재 세션 소유자의 메일 한 통(PDF 첨부 1)과 다른 사용자의 메일 한 통."""
    raw = raw_mail()
    mine, my_box = add_message(
        triage_database, context.owner, raw, attachments=[("file", "application/pdf", False)]
    )
    other_user = uuid4()
    with psycopg.connect(triage_database) as conn:
        conn.execute(
            "INSERT INTO identity.users(id,tid,oid,email,display_name,dept) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (other_user, uuid4(), uuid4(), "other@example.invalid", "다른 합성 사용자", "TS_AUTO"),
        )
    other, other_box = add_message(triage_database, other_user, raw)
    yield {"mine": mine, "other": other, "raw": raw, "dsn": triage_database}
    with psycopg.connect(triage_database) as conn:
        for table in ("mail_triage_latest", "mail_triage", "mail_attachments"):
            conn.execute(
                psycopg.sql.SQL("DELETE FROM integration.{} WHERE message_id = ANY(%s)").format(
                    psycopg.sql.Identifier(table)
                ),
                ([mine, other],),
            )
        for box in (my_box, other_box):
            conn.execute("DELETE FROM integration.mail_messages WHERE mailbox_id=%s", (box,))
            conn.execute("DELETE FROM integration.mail_sync_runs WHERE mailbox_id=%s", (box,))
            conn.execute("DELETE FROM mailbox_auth.connections WHERE id=%s", (box,))
        conn.execute("DELETE FROM identity.users WHERE id=%s", (other_user,))


def fake(mail_type="NEW_RFP", link="NEW_PROJECT"):
    body = {
        "mail_type": mail_type,
        "needs_review": False,
        "evidence": [{"source": "subject", "start": 0, "end": 2}],
        "link_suggestion": link,
    }
    return lambda _input: json.dumps(body)


def classify(context, owned, message="mine", **kwargs):
    return asyncio.run(
        classify_message(
            context.app.state.settings,
            message_id=owned[message],
            raw=owned["raw"],
            provider=fake(**kwargs),
        )
    )


def url(message_id):
    return f"/api/v1/platform/mail/messages/{message_id}/triage"


def code(response):
    return response.status_code, response.json()["error"]["code"]


def test_model_triage_is_stored_as_versions_and_read_by_the_owner(context, owned):
    assert classify(context, owned) == 1
    assert classify(context, owned, mail_type="CS", link="NEW_CS_TICKET") == 2
    latest = context.client.get(url(owned["mine"]))
    assert latest.status_code == 200, latest.text
    data = latest.json()["data"]
    assert (data["version"], data["is_latest"], data["mail_type"]) == (2, True, "CS")
    assert (data["provenance_kind"], data["attachments_pending"], data["needs_review"]) == (
        "ai_draft",
        True,
        True,
    )
    assert data["evidence"] == [{"source": "subject", "start": 0, "end": 2}]
    past = context.client.get(url(owned["mine"]), params={"version": 1}).json()["data"]
    assert (past["mail_type"], past["is_latest"], past["latest_version"]) == ("NEW_RFP", False, 2)
    for marker in ("secret-subject-marker", "secret-body-marker", "buyer-marker"):
        assert marker not in latest.text


def test_the_owner_reclassifies_with_a_reason_and_the_read_version(context, owned):
    classify(context, owned)
    headers = context.headers
    body = {
        "version": 1,
        "mail_type": "CS",
        "link_suggestion": "NEW_CS_TICKET",
        "reason": "고객 문의",
    }
    response = context.client.post(url(owned["mine"]), json=body, headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert (data["version"], data["provenance_kind"], data["reason"]) == (
        2,
        "human_input",
        "고객 문의",
    )
    assert data["evidence"] == [] and data["attachments_pending"] is True
    with psycopg.connect(owned["dsn"]) as conn:
        decided_by = conn.execute(
            "SELECT decided_by FROM integration.mail_triage WHERE message_id=%s AND version=2",
            (owned["mine"],),
        ).fetchone()[0]
    assert decided_by == context.owner
    stale = context.client.post(url(owned["mine"]), json=body, headers=headers)
    assert code(stale) == (409, "MAIL_TRIAGE_VERSION_CONFLICT")
    for wrong in (
        body | {"version": 2, "link_suggestion": "NEW_PROJECT"},
        {k: v for k, v in body.items() if k != "reason"} | {"version": 2},
        body | {"version": 2, "mail_type": "SPAM"},
    ):
        assert (
            context.client.post(url(owned["mine"]), json=wrong, headers=headers).status_code == 422
        )
    no_csrf = context.client.post(url(owned["mine"]), json=body | {"version": 2})
    assert no_csrf.status_code == 403


def test_other_mailboxes_and_missing_triage_are_hidden(context, owned):
    classify(context, owned, message="other")
    for response in (
        context.client.get(url(owned["other"])),
        context.client.post(
            url(owned["other"]),
            json={"version": 1, "mail_type": "OTHER", "reason": "x"},
            headers=context.headers,
        ),
        context.client.get(url(uuid4())),
    ):
        assert code(response) == (404, "MAIL_MESSAGE_NOT_FOUND")
    assert code(context.client.get(url(owned["mine"]))) == (404, "MAIL_TRIAGE_NOT_FOUND")
    assert code(context.client.get(url(owned["mine"]), params={"version": 9})) == (
        404,
        "MAIL_TRIAGE_NOT_FOUND",
    )


def test_the_raw_message_must_match_the_stored_hash(context, owned):
    with pytest.raises(ApplicationError) as error:
        asyncio.run(
            classify_message(
                context.app.state.settings,
                message_id=owned["mine"],
                raw=owned["raw"] + b"x",
                provider=fake(),
            )
        )
    assert (error.value.status_code, error.value.code) == (409, "MAIL_TRIAGE_RAW_MISMATCH")


def test_triage_rows_are_append_only_for_the_runtime(context, owned):
    classify(context, owned)
    runtime = context.app.state.settings.database_url.get_secret_value()
    with psycopg.connect(runtime, autocommit=True) as conn:
        for statement in (
            "UPDATE integration.mail_triage SET mail_type='OTHER' WHERE message_id=%s",
            "DELETE FROM integration.mail_triage WHERE message_id=%s",
            "DELETE FROM integration.mail_triage_latest WHERE message_id=%s",
            "UPDATE integration.mail_triage_latest SET message_id=message_id WHERE message_id=%s",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement, (owned["mine"],))
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "INSERT INTO integration.mail_triage (message_id, version, mail_type, "
                "needs_review, attachments_pending, evidence, provenance_kind) "
                "VALUES (%s, 9, 'CS', false, false, '[]', 'human_input')",
                (owned["mine"],),
            )


def test_triage_routes_are_closed_when_mailbox_auth_is_off(triage_database):
    app = create_app(
        Settings(_env_file=None, environment="test"),
        MicrosoftAuthSettings(_env_file=None),
        mailbox_settings=MailboxAuthSettings(_env_file=None),
    )
    with TestClient(app) as client:
        assert client.get(url(uuid4())).status_code == 404
        assert client.post(url(uuid4()), json={}).status_code == 404
