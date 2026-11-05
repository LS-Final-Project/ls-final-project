"""KAN-73 RFP로 보이는 메일: 규칙 분류(모델 없음), 후보 목록, 첨부 받기, 확인 후 프로젝트 연결.

실제 Graph·사서함·TFT에는 연결하지 않는다. 메일·첨부는 합성이다.
"""

import asyncio
import json
from uuid import uuid4

import psycopg
import pytest

from backend.api.platform.mail.rfp_intake import rule_output, triage_new_rfp_mails
from backend.engine.mail_triage.extract import build_input
from tests.platform.test_mail_intake_db import notify as notify
from tests.platform.test_mail_intake_db import received
from tests.platform.test_mail_intake_db import web as web
from tests.platform.test_mail_links_db import links as links
from tests.platform.test_mail_send_db import send_database as send_database
from tests.platform.test_mail_send_db import sender as sender
from tests.platform.test_mail_sync_db import mail_database as mail_database
from tests.platform.test_mail_sync_db import mailbox as mailbox
from tests.platform.test_mail_sync_db import rows

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
RFP_MAIL = (
    b"From: Buyer <buyer@customer.invalid>\r\nSubject: [RFP-91] B-plant switchgear RFP\r\n"
    b"Message-ID: <rfp-91@customer.invalid>\r\n\r\nPlease find the RFP attached.\r\n"
)


def attach(web, notify, message, *, name="RFP-91_spec.docx", content_type=DOCX, data=b"PK-docx"):
    stored = asyncio.run(web["store"].put(data, mailbox_id=notify["id"]))
    attachment = uuid4()
    with psycopg.connect(notify["dsn"]) as conn:
        conn.execute(
            "INSERT INTO integration.mail_attachments (id, message_id, graph_attachment_id, kind, "
            "name, content_type, is_inline, storage_key, sha256, size) "
            "VALUES (%s,%s,%s,'file',%s,%s,false,%s,%s,%s)",
            (
                attachment,
                message,
                f"att-{attachment}",
                name,
                content_type,
                stored.key,
                stored.sha256,
                stored.size,
            ),
        )
        conn.execute(
            "UPDATE integration.mail_messages SET subject=%s WHERE id=%s",
            ("[RFP-91] B-plant switchgear RFP", message),
        )
    return attachment


def latest_type(notify, message):
    found = rows(
        notify["dsn"],
        "SELECT t.mail_type, t.provenance_kind FROM integration.mail_triage_latest l "
        "JOIN integration.mail_triage t ON t.message_id=l.message_id AND t.version=l.latest_version "
        "WHERE l.message_id=%s",
        message,
    )
    return found[0] if found else None


@pytest.fixture
def sales(web, registered_user, auth_database):
    with psycopg.connect(auth_database) as conn:
        conn.execute(
            "INSERT INTO identity.user_roles(user_id,role) VALUES (%s,'sales') ON CONFLICT DO NOTHING",
            (registered_user[1],),
        )
    return web


def test_rule_always_leaves_a_version_so_skipped_mails_never_block_the_queue():
    first = json.loads(rule_output(build_input(RFP_MAIL, folder="inbox", attachments=())))
    assert first["mail_type"] == "NEW_RFP" and first["evidence"]
    reply = RFP_MAIL.replace(b"Message-ID:", b"In-Reply-To: <x@y>\r\nMessage-ID:")
    assert (
        json.loads(rule_output(build_input(reply, folder="inbox", attachments=())))["mail_type"]
        == "CANDIDATE_REPLY"
    )
    # 표현이 첨부 이름에만 있으면 근거 없이 NEW_RFP(엔진이 사람 검토 필요로 둔다).
    plain = RFP_MAIL.replace(b"RFP", b"Hello")
    only_name = json.loads(rule_output(build_input(plain, folder="inbox", attachments=())))
    assert only_name["mail_type"] == "NEW_RFP" and only_name["evidence"] == []


def test_sync_step_classifies_rfp_mails_once_without_a_model(settings, web, notify):
    rfp = received(web, notify, RFP_MAIL)
    attach(web, notify, rfp)
    no_file = received(web, notify, RFP_MAIL.replace(b"rfp-91@", b"rfp-92@"))
    with psycopg.connect(notify["dsn"]) as conn:
        conn.execute(
            "UPDATE integration.mail_messages SET subject='[RFP-92] spec' WHERE id=%s", (no_file,)
        )
    reply = received(
        web,
        notify,
        RFP_MAIL.replace(b"Message-ID: <rfp-91@", b"In-Reply-To: <x@y>\r\nMessage-ID: <rfp-93@"),
    )
    attach(web, notify, reply)
    counts = asyncio.run(triage_new_rfp_mails(settings, notify["id"], web["store"]))
    assert counts == {"classified": 2, "skipped": 0, "failed": 0}
    assert latest_type(notify, rfp) == ("NEW_RFP", "ai_draft")
    assert latest_type(notify, reply)[0] == "CANDIDATE_REPLY"  # 회신도 판본이 남아 다시 보지 않는다
    assert latest_type(notify, no_file) is None  # DOCX·PDF 첨부가 없으면 보지 않는다
    # 이미 분류가 있으면 다시 보지 않는다(멱등).
    assert (
        asyncio.run(triage_new_rfp_mails(settings, notify["id"], web["store"]))["classified"] == 0
    )


def test_sales_sees_the_candidate_downloads_the_file_and_links_a_project_once(
    settings, sales, notify
):
    web = sales
    message = received(web, notify, RFP_MAIL)
    attachment = attach(web, notify, message)
    other = attach(web, notify, message, name="photo.png", content_type="image/png", data=b"png")
    asyncio.run(triage_new_rfp_mails(settings, notify["id"], web["store"]))
    client, headers = web["client"], web["headers"]

    listed = client.get("/api/v1/platform/mail/rfp-candidates")
    assert listed.status_code == 200, listed.text
    [item] = listed.json()["data"]["items"]
    assert item["message_id"] == str(message) and item["basis"] == "제목: RFP"
    assert [a["name"] for a in item["attachments"]] == ["RFP-91_spec.docx"]

    file = client.get(f"/api/v1/platform/mail/messages/{message}/attachments/{attachment}")
    assert file.status_code == 200 and file.content == b"PK-docx"
    assert file.headers["content-type"] == DOCX
    assert "RFP-91_spec.docx" in file.headers["content-disposition"]
    assert (
        client.get(f"/api/v1/platform/mail/messages/{message}/attachments/{other}").status_code
        == 404
    )

    # 같은 주소와 주고받은 적이 있어 회신 검토(ADDRESS_MATCH)에도 열려 있던 메일이다.
    with psycopg.connect(notify["dsn"]) as conn:
        conn.execute(
            "INSERT INTO integration.mail_reply_reviews (message_id, reason, candidates) "
            "VALUES (%s, 'ADDRESS_MATCH', '[]'::jsonb)",
            (message,),
        )
    project = uuid4()
    path = f"/api/v1/platform/mail/messages/{message}/rfp-project"
    first = client.post(path, json={"project_id": str(project)}, headers=headers)
    assert first.status_code == 201, first.text
    # RFP로 등록하면 열린 회신 검토는 닫힌다(메일함 연결 필요 목록에서 빠진다).
    with psycopg.connect(notify["dsn"]) as conn:
        assert conn.execute(
            "SELECT status, note FROM integration.mail_reply_reviews WHERE message_id=%s",
            (message,),
        ).fetchall() == [("DISMISSED", "새 RFP로 등록")]
    again = client.post(path, json={"project_id": str(project)}, headers=headers)
    assert again.status_code == 200 and again.json()["data"]["created"] is False
    clash = client.post(path, json={"project_id": str(uuid4())}, headers=headers)
    assert clash.status_code == 409
    assert clash.json()["error"]["code"] == "MAIL_RFP_ALREADY_REGISTERED"
    # 연결한 메일은 후보에서 빠진다.
    assert client.get("/api/v1/platform/mail/rfp-candidates").json()["data"]["items"] == []


def test_only_sales_links_a_project(settings, web, notify):
    message = received(web, notify, RFP_MAIL)
    response = web["client"].post(
        f"/api/v1/platform/mail/messages/{message}/rfp-project",
        json={"project_id": str(uuid4())},
        headers=web["headers"],
    )
    assert response.status_code == 403
