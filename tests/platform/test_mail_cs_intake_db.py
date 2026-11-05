"""KAN-237 문의로 보이는 새 메일: 후보 목록, 본문 확인, 확인 후 문의 등록, 문의 아님.
KAN-238 메일함의 받은 메일 목록과 연결 상태, 역할과 무관한 본문 보기.

C/S는 가짜 호출 기록기다(고객 연락처 답 포함). 실제 Graph·사서함·C/S에는 연결하지 않는다. 메일은 합성이다.
"""

import asyncio
import json
from uuid import uuid4

import httpx
import psycopg

from backend.api.platform.mail.business import SENDER_CUSTOMERS_PATH
from backend.api.platform.mail.links import judge_new_replies
from tests.platform.test_mail_intake_db import FIELDS, FakeCs, received, register
from tests.platform.test_mail_intake_db import notify as notify
from tests.platform.test_mail_intake_db import web as web
from tests.platform.test_mail_links_db import links as links
from tests.platform.test_mail_send_db import send_database as send_database
from tests.platform.test_mail_send_db import sender as sender
from tests.platform.test_mail_sync_db import mail_database as mail_database
from tests.platform.test_mail_sync_db import mailbox as mailbox
from tests.platform.test_mail_sync_db import rows

CUSTOMER = "plant@customer.invalid"
NEW_MAIL = (
    b"From: Plant <plant@customer.invalid>\r\nSubject: Oil leakage at gasket\r\n"
    b"Message-ID: <orig-237@customer.invalid>\r\n\r\nOil leaks from the gasket.\r\n"
)
CANDIDATES = "/api/v1/platform/mail/cs-candidates"
MESSAGES = "/api/v1/platform/mail/messages"


class ContactCs(FakeCs):
    """고객 연락처 질의에도 답하는 가짜 C/S."""

    def __init__(self, contacts):
        super().__init__()
        self.contacts = contacts

    def handler(self, request):
        if request.url.path != SENDER_CUSTOMERS_PATH:
            return super().handler(request)
        assert request.headers["authorization"] == "Bearer cs-audience-token"
        self.calls.append(request)
        if self.raise_next is not None:
            error, self.raise_next = self.raise_next, None
            raise error
        addresses = json.loads(request.content)["addresses"]
        items = [
            {"address": a, "customer_external_ids": self.contacts[a]}
            for a in addresses
            if a in self.contacts
        ]
        return httpx.Response(200, json={"data": {"items": items}})


def with_contacts(web, contacts=None):
    cs = ContactCs({CUSTOMER: ["CUST-0374"]} if contacts is None else contacts)
    web["client"].app.state.cs_caller = cs.caller()
    web["cs"] = cs
    return cs


def judge(settings, web, notify):
    asyncio.run(judge_new_replies(settings, notify["id"], web["store"]))


def listed(web):
    response = web["client"].get(CANDIDATES)
    assert response.status_code == 200, response.text
    return {i["message_id"]: i for i in response.json()["data"]["items"]}


def test_new_customer_mail_is_a_candidate_until_registered_and_replies_attach(
    settings, web, notify
):
    with_contacts(web)
    new = received(web, notify, NEW_MAIL, internet_id="<orig-237@customer.invalid>")
    stranger = received(web, notify, NEW_MAIL.replace(b"orig-237", b"other-237"))
    with psycopg.connect(notify["dsn"]) as conn:
        conn.execute(
            "UPDATE integration.mail_messages SET from_address='x@nowhere.invalid' WHERE id=%s",
            (stranger,),
        )
    assert listed(web) == {}  # 회신 판정 전에는 보이지 않는다
    judge(settings, web, notify)
    found = listed(web)
    assert list(found) == [str(new)]  # 연락처에 없는 주소는 빠진다
    item = found[str(new)]
    assert (item["from_address"], item["customer_external_ids"], item["triage_version"]) == (
        CUSTOMER,
        ["CUST-0374"],
        0,
    )

    content = web["client"].get(f"/api/v1/platform/mail/messages/{new}/content")
    assert content.status_code == 200, content.text
    data = content.json()["data"]
    assert (data["subject"], data["body"], data["truncated"]) == (
        "Oil leakage at gasket",
        "Oil leaks from the gasket.",
        False,
    )

    first = register(web, new, body_language="ko")
    assert first.status_code == 201, first.text
    ticket = web["cs"].ticket_id
    assert listed(web) == {}  # 등록한 메일은 후보에서 빠진다
    web["cs"].from_mail_status = 409  # 같은 메일을 다시 등록하면 같은 문의로 200
    again = register(web, new)
    assert (again.status_code, again.json()["data"]) == (
        200,
        {"message_id": str(new), "ticket_id": str(ticket), "created": False},
    )

    # 우리가 보낸 메일이나 원 메일을 가리키는 고객 회신은 회신 흐름으로 그 문의에 붙고 후보가 아니다.
    reply = received(
        web,
        notify,
        b"From: Plant <plant@customer.invalid>\r\nSubject: Re: Oil leakage at gasket\r\n"
        b"In-Reply-To: <orig-237@customer.invalid>\r\nMessage-ID: <r-237@customer.invalid>\r\n"
        b"\r\nPhotos attached.\r\n",
    )
    judge(settings, web, notify)
    assert rows(
        notify["dsn"],
        "SELECT kind, method, business_ref FROM integration.mail_links WHERE message_id=%s",
        reply,
    ) == [("REPLY", "HEADER", f"cs_ticket:{ticket}")]
    assert listed(web) == {}


def test_a_new_mail_from_a_known_address_is_shown_and_its_review_closes_on_register(
    settings, web, notify
):
    with_contacts(web)
    first = received(web, notify, NEW_MAIL)
    judge(settings, web, notify)
    assert register(web, first).status_code == 201
    # 회신 표시 없이 같은 주소에서 온 새 메일: 회신 검토(ADDRESS_MATCH)에도 있고 후보에도 보인다.
    later = received(web, notify, NEW_MAIL.replace(b"orig-237", b"next-237"))
    judge(settings, web, notify)
    assert rows(
        notify["dsn"],
        "SELECT reason, status FROM integration.mail_reply_reviews WHERE message_id=%s",
        later,
    ) == [("ADDRESS_MATCH", "OPEN")]
    assert list(listed(web)) == [str(later)]
    assert register(web, later).status_code == 201
    assert rows(
        notify["dsn"],
        "SELECT status, note FROM integration.mail_reply_reviews WHERE message_id=%s",
        later,
    ) == [("DISMISSED", "새 문의로 등록")]


def test_not_an_inquiry_and_mails_linked_elsewhere_leave_the_list(settings, web, notify):
    with_contacts(web)
    other = received(web, notify, NEW_MAIL)
    rfp = received(web, notify, NEW_MAIL.replace(b"orig-237", b"rfp-237"))
    judge(settings, web, notify)
    assert set(listed(web)) == {str(other), str(rfp)}
    # [문의 아님]: 분류가 없던 메일에 판본 0으로 OTHER를 처음 남긴다.
    path = f"/api/v1/platform/mail/messages/{other}/triage"
    body = {"version": 0, "mail_type": "OTHER", "reason": "C/S 담당자 확인: 문의 아님"}
    marked = web["client"].post(path, json=body, headers=web["headers"])
    assert marked.status_code == 200, marked.text
    assert web["client"].post(path, json=body, headers=web["headers"]).status_code == 409
    # RFP 프로젝트로 연결된 메일은 후보가 아니고 문의로 등록하면 409다.
    with psycopg.connect(notify["dsn"]) as conn:
        conn.execute(
            "INSERT INTO integration.mail_links (id, message_id, owner_service, business_ref, "
            "kind, method) VALUES (gen_random_uuid(), %s, 'platform', "
            "'rfp_project:00000000-0000-4000-8000-000000000001', 'ORIGIN', 'BUSINESS')",
            (rfp,),
        )
    assert listed(web) == {}
    clash = register(web, rfp)
    assert (clash.status_code, clash.json()["error"]["code"]) == (409, "MAIL_ALREADY_LINKED")


def test_only_a_cs_agent_sees_and_registers(settings, web, notify, registered_user, auth_database):
    with_contacts(web)
    message = received(web, notify, NEW_MAIL)
    judge(settings, web, notify)
    # 영업(sales)만 남긴다. 역할이 하나도 없으면 운영 세션이 아니라 401이다.
    with psycopg.connect(auth_database) as conn:
        conn.execute(
            "INSERT INTO identity.user_roles(user_id,role) VALUES (%s,'sales')",
            (registered_user[1],),
        )
        conn.execute(
            "DELETE FROM identity.user_roles WHERE user_id=%s AND role='cs_agent'",
            (registered_user[1],),
        )
    client = web["client"]
    assert client.get(CANDIDATES).status_code == 403
    assert register(web, message, **FIELDS).status_code == 403
    # KAN-238: 본문 보기와 받은 메일 목록은 메일함 주인이면 역할과 무관하다(영업이 RFP 메일을 본다).
    assert client.get(f"/api/v1/platform/mail/messages/{message}/content").status_code == 200
    assert [i["message_id"] for i in client.get(MESSAGES).json()["data"]["items"]] == [str(message)]


def test_candidates_are_unavailable_when_cs_does_not_answer(settings, web, notify):
    cs = with_contacts(web)
    received(web, notify, NEW_MAIL)
    judge(settings, web, notify)
    cs.raise_next = httpx.ConnectError("down")
    response = web["client"].get(CANDIDATES)
    assert (response.status_code, response.json()["error"]["code"]) == (503, "CS_UNAVAILABLE")


def received_at(web, notify, hour):
    message = received(web, notify, NEW_MAIL)
    with psycopg.connect(notify["dsn"]) as conn:
        conn.execute(
            "UPDATE integration.mail_messages SET received_at=%s WHERE id=%s",
            (f"2026-11-05T0{hour}:00:00+00:00", message),
        )
    return message


def test_inbox_lists_own_received_mail_newest_first_with_link_state(settings, web, notify):
    ticket = received_at(web, notify, 1)
    rfp = received_at(web, notify, 2)
    review = received_at(web, notify, 3)
    looks = received_at(web, notify, 4)
    plain = received_at(web, notify, 5)
    sent = received(web, notify, NEW_MAIL, folder="sentitems")
    project = "rfp_project:00000000-0000-4000-8000-000000000001"
    with psycopg.connect(notify["dsn"]) as conn:
        for message, service, ref in (
            (ticket, "cs", "cs_ticket:00000000-0000-4000-8000-000000000002"),
            (rfp, "platform", project),
        ):
            conn.execute(
                "INSERT INTO integration.mail_links (id, message_id, owner_service, business_ref, "
                "kind, method) VALUES (gen_random_uuid(), %s, %s, %s, 'ORIGIN', 'BUSINESS')",
                (message, service, ref),
            )
        conn.execute(
            "INSERT INTO integration.mail_reply_reviews (message_id, reason, candidates) "
            "VALUES (%s, 'AMBIGUOUS', '[]')",
            (review,),
        )
    marked = web["client"].post(
        f"{MESSAGES}/{looks}/triage",
        json={"version": 0, "mail_type": "NEW_RFP", "reason": "영업 확인"},
        headers=web["headers"],
    )
    assert marked.status_code == 200, marked.text

    items = web["client"].get(MESSAGES).json()["data"]["items"]
    state = [(i["message_id"], i["link"], i["reply_review_open"], i["mail_type"]) for i in items]
    assert state == [  # 최근 순, 보낸편지함 제외
        (str(plain), None, False, None),
        (str(looks), None, False, "NEW_RFP"),
        (str(review), None, True, None),
        (str(rfp), {"owner_service": "platform", "business_ref": project}, False, None),
        (
            str(ticket),
            {
                "owner_service": "cs",
                "business_ref": "cs_ticket:00000000-0000-4000-8000-000000000002",
            },
            False,
            None,
        ),
    ]
    assert str(sent) not in {i["message_id"] for i in items}
    assert (items[0]["from_address"], items[0]["subject"]) == (CUSTOMER, "s")  # DB 메타데이터
    assert "body" not in items[0]

    # 다른 사람 메일함이 되면 목록·본문 모두 보이지 않는다.
    other = uuid4()
    with psycopg.connect(notify["dsn"]) as conn:
        conn.execute(
            "INSERT INTO identity.users(id,tid,oid,email,display_name,dept) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (other, uuid4(), uuid4(), "o@example.invalid", "다른 사용자", "TS_AUTO"),
        )
        conn.execute(
            "UPDATE mailbox_auth.connections SET owner_user_id=%s WHERE id=%s",
            (other, notify["id"]),
        )
    try:
        assert web["client"].get(MESSAGES).json()["data"]["items"] == []
        assert web["client"].get(f"{MESSAGES}/{plain}/content").status_code == 404
    finally:
        with psycopg.connect(notify["dsn"]) as conn:
            conn.execute(
                "UPDATE mailbox_auth.connections SET owner_user_id=%s WHERE id=%s",
                (notify["owner"], notify["id"]),
            )
            conn.execute("DELETE FROM identity.users WHERE id=%s", (other,))
