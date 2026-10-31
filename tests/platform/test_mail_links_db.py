"""KAN-75 회신·스레드·업무 연결을 전용 PostgreSQL과 합성 메일로 검증한다.

실제 Graph·사서함에는 연결하지 않는다. 메일 행·원문은 합성이다.
"""

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend.api.platform.auth.http import CSRF_HEADER, SESSION_COOKIE, csrf_token
from backend.api.platform.auth.sessions import issue_session
from backend.api.platform.mail.links import (
    Candidate,
    decide,
    link_origin,
    replies_for,
    reply_headers,
    resolve_reply,
)
from backend.api.platform.mail.send import SendRequest, create_intent, payload_sha256
from backend.api.platform.main import create_app
from backend.shared.errors import ApplicationError
from tests.platform.test_mail_send_db import send_database as send_database
from tests.platform.test_mail_send_db import sender as sender
from tests.platform.test_mail_sync_db import mail_database as mail_database
from tests.platform.test_mail_sync_db import mailbox as mailbox
from tests.platform.test_mail_sync_db import rows
from tests.platform.test_mailbox_http import ORIGIN, configurations

MIGRATIONS = (
    Path("infra/migrations/platform/20261028_KAN-73_mail_triage.sql"),
    Path("infra/migrations/platform/20261029_KAN-75_mail_links.sql"),
)


@pytest.fixture
def links(send_database, sender):
    with psycopg.connect(send_database, autocommit=True) as conn:
        for migration, table in zip(MIGRATIONS, ("mail_triage", "mail_links"), strict=True):
            if (
                conn.execute("SELECT to_regclass(%s)", (f"integration.{table}",)).fetchone()[0]
                is None
            ):
                sql = migration.read_text()
                conn.execute("\n".join(x for x in sql.splitlines() if not x.startswith("\\")))
        run = uuid4()
        conn.execute(
            "INSERT INTO integration.mail_sync_runs (id, mailbox_id, status, lease_expires_at, "
            "finished_at) VALUES (%s, %s, 'SUCCEEDED', now(), now())",
            (run, sender["id"]),
        )
    yield {**sender, "run": run}
    with psycopg.connect(send_database) as conn:
        ids = "(SELECT id FROM integration.mail_messages WHERE mailbox_id=%s)"
        for table in (
            "mail_reply_checks",
            "mail_reply_reviews",
            "mail_links",
            "mail_triage_latest",
            "mail_triage",
        ):
            conn.execute(
                f"DELETE FROM integration.{table} WHERE message_id IN {ids}", (sender["id"],)
            )


def mime(subject="Re: 차단기 트립 문의", in_reply_to=None, references=None, message_id=None):
    lines = [f"Subject: {subject}", f"Message-ID: {message_id or f'<{uuid4()}@customer.invalid>'}"]
    if in_reply_to:
        lines.append(f"In-Reply-To: {in_reply_to}")
    if references:
        lines.append(f"References: {references}")
    return ("\r\n".join(lines) + "\r\n\r\n본문입니다.\r\n").encode()


def add_message(links, raw, *, folder="inbox", internet_id=None, conversation=None, graph_id=None):
    message = uuid4()
    with psycopg.connect(links["dsn"]) as conn:
        conn.execute(
            "INSERT INTO integration.mail_messages (id, mailbox_id, graph_id, folder, "
            "internet_message_id, conversation_id, subject, received_at, has_attachments, "
            "raw_storage_key, raw_sha256, raw_size, first_sync_run_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,'s',now(),false,'k',%s,%s,%s)",
            (
                message,
                links["id"],
                graph_id or f"AAMk-{message}",
                folder,
                internet_id,
                conversation,
                hashlib.sha256(raw).hexdigest(),
                len(raw),
                links["run"],
            ),
        )
    return message


def sent_intent(settings, links, ref, *, internet_id, graph_id):
    """C/S 업무로 보낸 메일(보낸편지함 사본 확인까지)을 만든다."""
    fields = {
        "subject": "Re: 문의",
        "body_type": "text",
        "body": "안내",
        "to": ("c@example.invalid",),
        "cc": (),
    }
    intent = asyncio.run(
        create_intent(
            settings,
            SendRequest(
                mailbox_id=links["id"],
                owner_service="cs",
                business_ref=ref,
                business_version=1,
                approved_by=links["owner"],
                approved_at=datetime.now(UTC),
                approved_payload_sha256=payload_sha256(**fields, attachments=[]),
                **fields,
            ),
        )
    )
    with psycopg.connect(links["dsn"]) as conn:
        conn.execute(
            "UPDATE integration.mail_send_intents SET status='ACCEPTED', attempts=1, "
            "submitted_at=now(), accepted_at=now(), sent_copy_confirmed_at=now(), "
            "sent_graph_id=%s, sent_internet_message_id=%s WHERE id=%s",
            (graph_id, internet_id, intent),
        )
    return intent


def resolve(settings, message, raw):
    return asyncio.run(resolve_reply(settings, message, raw))


def test_reply_headers_are_read_in_order_without_duplicates_or_junk():
    raw = mime(in_reply_to="<a@x>", references="<root@x> junk <a@x>\r\n <b@x>")
    assert reply_headers(raw) == ["<a@x>", "<root@x>", "<b@x>"]
    assert reply_headers(mime()) == []


def test_decision_rules():
    a = Candidate(owner_service="cs", business_ref="cs_ticket:A", method="HEADER")
    b = Candidate(owner_service="cs", business_ref="cs_ticket:B", method="HEADER")
    ta = Candidate(owner_service="cs", business_ref="cs_ticket:A", method="THREAD")
    tb = Candidate(owner_service="cs", business_ref="cs_ticket:B", method="THREAD")
    assert decide([a], [ta], True, None)[0] == a
    assert decide([], [ta], False, "EXISTING_THREAD")[0] == ta
    assert decide([a, b], [], True, None)[1] == "AMBIGUOUS"
    assert decide([], [ta, tb], False, None)[1] == "AMBIGUOUS"
    assert decide([a], [tb], True, None)[1] == "CONFLICT"
    assert decide([], [], True, None)[1] == "UNMATCHED_REPLY"
    assert decide([], [], False, None) == (None, None, [])
    assert decide([a], [], True, "NEW_CS_TICKET")[1] == "TRIAGE_DISAGREES"


def test_reply_to_our_sent_answer_links_by_header(settings, links):
    sent_intent(settings, links, "cs_ticket:TCK-1", internet_id="<ans1@team4>", graph_id="AAMk-s1")
    raw = mime(in_reply_to="<ans1@team4>", references="<orig@customer> <ans1@team4>")
    reply = add_message(links, raw, conversation="conv-1")
    result = resolve(settings, reply, raw)
    assert (result.outcome, result.link.business_ref, result.link.method) == (
        "LINKED",
        "cs_ticket:TCK-1",
        "HEADER",
    )
    # 다시 불러도 같은 결과이고 연결이 늘지 않는다.
    assert resolve(settings, reply, raw) == result
    assert [
        r["message_id"] for r in asyncio.run(replies_for(settings, "cs", "cs_ticket:TCK-1"))
    ] == [reply]


def test_reply_without_headers_links_by_graph_thread(settings, links):
    sent_intent(settings, links, "cs_ticket:TCK-2", internet_id="<ans2@team4>", graph_id="AAMk-s2")
    add_message(
        links,
        mime(),
        folder="sentitems",
        graph_id="AAMk-s2",
        conversation="conv-2",
        internet_id="<ans2@team4>",
    )
    raw = mime()
    reply = add_message(links, raw, conversation="conv-2")
    result = resolve(settings, reply, raw)
    assert (result.outcome, result.link.method) == ("LINKED", "THREAD")


def test_reply_to_the_mail_that_opened_a_ticket_links_to_that_ticket(settings, links):
    origin_raw = mime(subject="Breaker trips", message_id="<orig9@customer>")
    origin = add_message(links, origin_raw, internet_id="<orig9@customer>", conversation="conv-9")
    asyncio.run(
        link_origin(settings, message_id=origin, owner_service="cs", business_ref="cs_ticket:TCK-9")
    )
    asyncio.run(
        link_origin(settings, message_id=origin, owner_service="cs", business_ref="cs_ticket:TCK-9")
    )
    raw = mime(references="<orig9@customer>")
    reply = add_message(links, raw, conversation="conv-9")
    result = resolve(settings, reply, raw)
    assert (result.outcome, result.link.business_ref) == ("LINKED", "cs_ticket:TCK-9")
    assert rows(
        links["dsn"],
        "SELECT kind, method FROM integration.mail_links WHERE message_id=%s",
        origin,
    ) == [("ORIGIN", "BUSINESS")]


def test_headers_pointing_to_two_tickets_go_to_review(settings, links):
    sent_intent(settings, links, "cs_ticket:A", internet_id="<a@team4>", graph_id="AAMk-a")
    sent_intent(settings, links, "cs_ticket:B", internet_id="<b@team4>", graph_id="AAMk-b")
    raw = mime(references="<a@team4> <b@team4>")
    reply = add_message(links, raw)
    result = resolve(settings, reply, raw)
    assert (result.outcome, result.reason) == ("REVIEW", "AMBIGUOUS")
    assert {c.business_ref for c in result.candidates} == {"cs_ticket:A", "cs_ticket:B"}
    assert rows(
        links["dsn"], "SELECT count(*) FROM integration.mail_links WHERE message_id=%s", reply
    ) == [(0,)]


def test_header_and_thread_disagreeing_go_to_review(settings, links):
    sent_intent(settings, links, "cs_ticket:A", internet_id="<a2@team4>", graph_id="AAMk-a2")
    sent_intent(settings, links, "cs_ticket:B", internet_id="<b2@team4>", graph_id="AAMk-b2")
    add_message(links, mime(), folder="sentitems", graph_id="AAMk-b2", conversation="conv-b")
    raw = mime(in_reply_to="<a2@team4>")
    reply = add_message(links, raw, conversation="conv-b")
    result = resolve(settings, reply, raw)
    assert (result.outcome, result.reason) == ("REVIEW", "CONFLICT")


def test_reply_to_unknown_mail_goes_to_review_not_a_guess(settings, links):
    raw = mime(in_reply_to="<someone-else@other.invalid>")
    reply = add_message(links, raw)
    result = resolve(settings, reply, raw)
    assert (result.outcome, result.reason, result.candidates) == ("REVIEW", "UNMATCHED_REPLY", [])


def test_same_subject_alone_never_links(settings, links):
    sent_intent(settings, links, "cs_ticket:TCK-3", internet_id="<ans3@team4>", graph_id="AAMk-s3")
    raw = mime(subject="Re: 문의")
    reply = add_message(links, raw, conversation="conv-unrelated")
    assert resolve(settings, reply, raw).outcome == "NONE"
    assert rows(
        links["dsn"],
        "SELECT count(*) FROM integration.mail_reply_reviews WHERE message_id=%s",
        reply,
    ) == [(0,)]


def test_triage_saying_new_ticket_sends_a_matched_reply_to_review(settings, links):
    sent_intent(settings, links, "cs_ticket:TCK-4", internet_id="<ans4@team4>", graph_id="AAMk-s4")
    raw = mime(in_reply_to="<ans4@team4>")
    reply = add_message(links, raw)
    with psycopg.connect(links["dsn"]) as conn:
        conn.execute(
            "INSERT INTO integration.mail_triage (message_id, version, mail_type, needs_review, "
            "attachments_pending, evidence, link_suggestion, provenance_kind, input_version, "
            "output_version) VALUES (%s, 1, 'CS', false, false, '[]', 'NEW_CS_TICKET', 'ai_draft', 'i', 'o')",
            (reply,),
        )
        conn.execute(
            "INSERT INTO integration.mail_triage_latest (message_id, latest_version) VALUES (%s, 1)",
            (reply,),
        )
    result = resolve(settings, reply, raw)
    assert (result.outcome, result.reason) == ("REVIEW", "TRIAGE_DISAGREES")
    assert result.candidates[0].business_ref == "cs_ticket:TCK-4"


def test_sent_mail_and_tampered_raw_are_not_judged(settings, links):
    raw = mime(in_reply_to="<x@x>")
    sent = add_message(links, raw, folder="sentitems")
    assert resolve(settings, sent, raw).outcome == "NONE"
    reply = add_message(links, raw)
    with pytest.raises(ApplicationError) as error:
        resolve(settings, reply, raw + b"tampered")
    assert error.value.code == "MAIL_LINK_RAW_MISMATCH"


def test_runtime_role_cannot_rewrite_links_or_review_candidates(settings, links):
    raw = mime(in_reply_to="<nobody@x>")
    reply = add_message(links, raw)
    resolve(settings, reply, raw)
    with psycopg.connect(settings.database_url.get_secret_value()) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "UPDATE integration.mail_reply_reviews SET candidates='[]' WHERE message_id=%s",
                (reply,),
            )
    with psycopg.connect(settings.database_url.get_secret_value()) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM integration.mail_reply_reviews WHERE message_id=%s", (reply,))


# --- 메일함 주인의 검토 API ------------------------------------------------------------------


@pytest.fixture
def web(settings, links, registered_user):
    auth, mailbox_settings = configurations()
    app = create_app(settings, auth, mailbox_settings=mailbox_settings)
    session = asyncio.run(issue_session(settings, registered_user[0])).token.get_secret_value()
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        client.cookies.set(SESSION_COOKIE, session)
        yield {"client": client, "headers": {"Origin": ORIGIN, CSRF_HEADER: csrf_token(session)}}


PREFIX = "/api/v1/platform/mail/reply-reviews"


def ambiguous_review(settings, links):
    sent_intent(
        settings,
        links,
        "cs_ticket:A",
        internet_id=f"<a-{uuid4()}@team4>",
        graph_id=f"AAMk-{uuid4()}",
    )
    a = rows(
        links["dsn"],
        "SELECT sent_internet_message_id FROM integration.mail_send_intents WHERE business_ref='cs_ticket:A' AND mailbox_id=%s",
        links["id"],
    )[0][0]
    sent_intent(
        settings,
        links,
        "cs_ticket:B",
        internet_id=f"<b-{uuid4()}@team4>",
        graph_id=f"AAMk-{uuid4()}",
    )
    b = rows(
        links["dsn"],
        "SELECT sent_internet_message_id FROM integration.mail_send_intents WHERE business_ref='cs_ticket:B' AND mailbox_id=%s",
        links["id"],
    )[0][0]
    raw = mime(references=f"{a} {b}")
    reply = add_message(links, raw)
    assert resolve(settings, reply, raw).reason == "AMBIGUOUS"
    return reply


def test_owner_lists_open_reviews_and_reading_does_not_close_them(settings, links, web):
    reply = ambiguous_review(settings, links)
    for _ in range(2):
        items = web["client"].get(PREFIX).json()["data"]["items"]
        assert [i["message_id"] for i in items] == [str(reply)]
    # 본문과 첨부 이름은 내지 않는다.
    assert set(items[0]) == {
        "message_id",
        "received_at",
        "from_address",
        "subject",
        "has_attachments",
        "reason",
        "candidates",
        "created_at",
    }
    assert (items[0]["subject"], items[0]["has_attachments"]) == ("s", False)
    assert items[0]["from_address"] is None
    with psycopg.connect(links["dsn"]) as conn:
        conn.execute(
            "UPDATE integration.mail_messages SET from_address='sender@example.test', "
            "has_attachments=true WHERE id=%s",
            (reply,),
        )
    [item] = web["client"].get(PREFIX).json()["data"]["items"]
    assert (item["from_address"], item["has_attachments"]) == ("sender@example.test", True)
    assert rows(
        links["dsn"], "SELECT status FROM integration.mail_reply_reviews WHERE message_id=%s", reply
    ) == [("OPEN",)]


def test_owner_links_a_review_to_one_of_its_candidates(settings, links, web):
    reply = ambiguous_review(settings, links)
    client, headers = web["client"], web["headers"]
    outside = client.post(
        f"{PREFIX}/{reply}/link",
        json={"owner_service": "cs", "business_ref": "cs_ticket:Z", "note": "확인"},
        headers=headers,
    )
    assert (outside.status_code, outside.json()["error"]["code"]) == (
        422,
        "MAIL_REPLY_NOT_A_CANDIDATE",
    )
    no_csrf = client.post(
        f"{PREFIX}/{reply}/link",
        json={"owner_service": "cs", "business_ref": "cs_ticket:A", "note": "확인"},
        headers={"Origin": ORIGIN},
    )
    assert no_csrf.status_code == 403
    response = client.post(
        f"{PREFIX}/{reply}/link",
        json={"owner_service": "cs", "business_ref": "cs_ticket:A", "note": "본문에 A 문의 번호"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["link"]["method"] == "HUMAN"
    assert rows(
        links["dsn"],
        "SELECT business_ref, method, note FROM integration.mail_links WHERE message_id=%s",
        reply,
    ) == [("cs_ticket:A", "HUMAN", "본문에 A 문의 번호")]
    again = client.post(f"{PREFIX}/{reply}/dismiss", json={"note": "x"}, headers=headers)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "MAIL_REPLY_REVIEW_DECIDED")


def test_owner_dismisses_a_review_with_a_reason(settings, links, web):
    raw = mime(in_reply_to="<nobody2@x>")
    reply = add_message(links, raw)
    resolve(settings, reply, raw)
    assert (
        web["client"]
        .post(f"{PREFIX}/{reply}/dismiss", json={"note": ""}, headers=web["headers"])
        .status_code
        == 422
    )
    response = web["client"].post(
        f"{PREFIX}/{reply}/dismiss", json={"note": "광고 메일"}, headers=web["headers"]
    )
    assert (response.status_code, response.json()["data"]["outcome"]) == (200, "NONE")
    assert resolve(settings, reply, raw).outcome == "NONE"
    assert web["client"].get(PREFIX).json()["data"]["items"] == []


def test_someone_elses_review_is_hidden(settings, links, web):
    reply = ambiguous_review(settings, links)
    with psycopg.connect(links["dsn"]) as conn:
        other = uuid4()
        conn.execute(
            "INSERT INTO identity.users(id,tid,oid,email,display_name,dept) VALUES (%s,%s,%s,%s,%s,%s)",
            (other, uuid4(), uuid4(), "o@example.invalid", "다른 사용자", "TS_AUTO"),
        )
        conn.execute(
            "UPDATE mailbox_auth.connections SET owner_user_id=%s WHERE id=%s", (other, links["id"])
        )
    try:
        assert web["client"].get(PREFIX).json()["data"]["items"] == []
        response = web["client"].post(
            f"{PREFIX}/{reply}/dismiss", json={"note": "x"}, headers=web["headers"]
        )
        assert (response.status_code, response.json()["error"]["code"]) == (
            404,
            "MAIL_REPLY_REVIEW_NOT_FOUND",
        )
    finally:
        with psycopg.connect(links["dsn"]) as conn:
            conn.execute(
                "UPDATE mailbox_auth.connections SET owner_user_id=%s WHERE id=%s",
                (links["owner"], links["id"]),
            )
            conn.execute("DELETE FROM identity.users WHERE id=%s", (other,))


def stored_message(links, raw, **kwargs):
    """원문을 실제로 저장소에 넣고 그 키로 메일 행을 만든다."""
    stored = asyncio.run(links["store"].put(raw, mailbox_id=links["id"]))
    message = add_message(links, raw, **kwargs)
    with psycopg.connect(links["dsn"]) as conn:
        conn.execute(
            "UPDATE integration.mail_messages SET raw_storage_key=%s WHERE id=%s",
            (stored.key, message),
        )
    return message, stored


def test_new_inbox_mail_is_judged_once_after_sync(settings, links):
    from backend.api.platform.mail.links import judge_new_replies

    sent_intent(settings, links, "cs_ticket:TCK-7", internet_id="<ans7@team4>", graph_id="AAMk-s7")
    reply, _ = stored_message(links, mime(in_reply_to="<ans7@team4>"))
    plain, _ = stored_message(links, mime(subject="새 문의"))
    stored_message(links, mime(in_reply_to="<x@x>"), folder="sentitems")
    counts = asyncio.run(judge_new_replies(settings, links["id"], links["store"]))
    assert counts == {"LINKED": 1, "REVIEW": 0, "NONE": 1, "failed": 0}
    assert rows(
        links["dsn"],
        "SELECT outcome FROM integration.mail_reply_checks WHERE message_id=%s",
        reply,
    ) == [("LINKED",)]
    assert rows(
        links["dsn"],
        "SELECT outcome FROM integration.mail_reply_checks WHERE message_id=%s",
        plain,
    ) == [("NONE",)]
    # 이미 판정한 메일은 다시 읽지 않는다.
    again = asyncio.run(judge_new_replies(settings, links["id"], links["store"]))
    assert again == {"LINKED": 0, "REVIEW": 0, "NONE": 0, "failed": 0}


def test_mail_whose_raw_cannot_be_read_is_retried_later(settings, links):
    from backend.api.platform.mail.links import judge_new_replies

    message, stored = stored_message(links, mime(in_reply_to="<nobody3@x>"))
    path = links["store"].root / stored.key
    original = path.read_bytes()
    path.write_bytes(b"damaged")
    first = asyncio.run(judge_new_replies(settings, links["id"], links["store"]))
    assert first["failed"] == 1
    assert rows(
        links["dsn"],
        "SELECT count(*) FROM integration.mail_reply_checks WHERE message_id=%s",
        message,
    ) == [(0,)]
    path.write_bytes(original)
    second = asyncio.run(judge_new_replies(settings, links["id"], links["store"]))
    assert (second["REVIEW"], second["failed"]) == (1, 0)


def test_review_decision_updates_the_judged_marker(settings, links, web):
    reply = ambiguous_review(settings, links)
    web["client"].post(
        f"{PREFIX}/{reply}/dismiss", json={"note": "업무 회신 아님"}, headers=web["headers"]
    )
    assert rows(
        links["dsn"],
        "SELECT outcome FROM integration.mail_reply_checks WHERE message_id=%s",
        reply,
    ) == [("NONE",)]


def test_sync_worker_judges_the_mail_it_just_collected(settings, links):
    from tests.platform.test_mail_worker_db import World

    world = World(settings)
    response = world.sync(links["id"])
    data = response.json()["data"]
    assert (data["outcome"], data["messages_stored"], data["replies_failed"]) == ("SUCCEEDED", 3, 0)
    # 가짜 Graph의 받은편지함 2통은 회신이 아니라 NONE으로 표시되고, 보낸편지함 메일은 판정하지 않는다.
    assert rows(
        links["dsn"],
        "SELECT c.outcome, m.folder FROM integration.mail_reply_checks c "
        "JOIN integration.mail_messages m ON m.id=c.message_id WHERE m.mailbox_id=%s ORDER BY 1, 2",
        links["id"],
    ) == [("NONE", "inbox"), ("NONE", "inbox")]


def test_removed_mail_is_not_judged(settings, links):
    sent_intent(settings, links, "cs_ticket:TCK-8", internet_id="<ans8@team4>", graph_id="AAMk-s8")
    raw = mime(in_reply_to="<ans8@team4>")
    reply = add_message(links, raw)
    with psycopg.connect(links["dsn"]) as conn:
        conn.execute("UPDATE integration.mail_messages SET removed_at=now() WHERE id=%s", (reply,))
    assert resolve(settings, reply, raw).outcome == "NONE"
    assert rows(
        links["dsn"], "SELECT count(*) FROM integration.mail_links WHERE message_id=%s", reply
    ) == [(0,)]
