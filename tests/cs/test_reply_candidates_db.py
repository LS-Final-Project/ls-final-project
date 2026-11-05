"""KAN-235 회신 후보와 근거(C/S 내부 경로)를 전용 PostgreSQL과 합성 문의로 검증한다.

보낸 주소와 주고받은 문의, 고객 연락처(C33)로 찾은 같은 고객의 진행 중 문의, 후보마다의 근거(고객 회신 대기,
본문의 제품·오류 코드 일치)를 본다. 읽기만 하고 아무것도 바꾸지 않는다.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from backend.api.cs.workflow.reply_candidates import address_parts, mentions
from tests.cs.test_customer_mail_db import client as client
from tests.cs.test_customer_mail_db import platform as platform

PATH = "/internal/v1/cs/tickets/reply-candidates"


def from_mail(client, *, sender, customer, product, subject="합성 문의"):
    response = client.post(
        "/internal/v1/cs/tickets/from-mail",
        json={
            "message_id": str(uuid4()),
            "mailbox_id": str(uuid4()),
            "external_id": f"AAMk-{uuid4()}",
            "sender_address": sender,
            "received_at": datetime(2026, 11, 5, 9, tzinfo=UTC).isoformat(),
            "subject": subject,
            "body": f"Synthetic inquiry about {product}.",
            "body_language": "en",
            "customer_external_id": customer,
            "product_code": product,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def candidates(client, **body):
    response = client.post(PATH, json=body)
    assert response.status_code == 200, response.text
    return {i["ticket_id"]: i for i in response.json()["data"]["items"]}


def test_code_and_address_helpers():
    assert mentions("Error E-21 again on TRF-OIL-1000.", "trf-oil-1000")
    assert not mentions("TRF-OIL-10000 is a different model", "TRF-OIL-1000")
    assert not mentions("", "E-21") and not mentions("E-21", None)
    assert address_parts(" Plant@Customer.Invalid ") == (
        "plant@customer.invalid",
        "customer.invalid",
    )
    assert address_parts("no-at-sign") == (None, None)


def test_sender_address_and_customer_contact_find_tickets_with_evidence(client, admin):
    tag = uuid4().hex[:8]
    customer = f"CUST-{tag}"
    domain = f"plant-{tag}.invalid"
    mine = from_mail(client, sender=f"buyer@{domain}", customer=customer, product="TRF-OIL-1000")
    colleague = from_mail(
        client, sender=f"other@{tag}.example.invalid", customer=customer, product="UPS-LSU-10K"
    )
    stranger = from_mail(
        client, sender=f"x@{tag}.nowhere.invalid", customer=f"CUST-X{tag}", product="UPS-LSU-10K"
    )
    admin.execute(
        "UPDATE cs.tickets SET error_code_reported='E-21' WHERE id=%s", (UUID(colleague),)
    )
    text = "Photos of the TRF-OIL-1000 nameplate attached. The display shows E-21."
    # 연락처가 없으면 그 주소로 들어온 문의만 후보다.
    found = candidates(client, from_address=f"Buyer@{domain.upper()}", text=text)
    assert list(found) == [mine]
    assert found[mine]["evidence"] == ["ORIGIN_ADDRESS", "PRODUCT_MATCH"]
    assert (found[mine]["given"], found[mine]["state"]) == (False, "RECEIVED")
    # 회사 도메인을 고객 연락처로 넣으면 같은 고객의 진행 중 문의가 근거와 함께 더해진다.
    admin.execute(
        "INSERT INTO cs.customer_contacts (id, customer_external_id, domain, data_origin) "
        "VALUES (%s, %s, %s, 'demo')",
        (uuid4(), customer, domain),
    )
    found = candidates(client, from_address=f"buyer@{domain}", text=text)
    assert set(found) == {mine, colleague}
    assert found[mine]["evidence"] == ["ORIGIN_ADDRESS", "CUSTOMER_CONTACT", "PRODUCT_MATCH"]
    assert found[colleague]["evidence"] == ["CUSTOMER_CONTACT", "ERROR_CODE_MATCH"]
    assert stranger not in found
    # 꺼진 연락처는 후보를 만들지 않는다.
    admin.execute(
        "UPDATE cs.customer_contacts SET active=false WHERE customer_external_id=%s", (customer,)
    )
    assert set(candidates(client, from_address=f"buyer@{domain}", text=text)) == {mine}


def test_given_candidates_come_first_and_unknown_tickets_drop(client, admin):
    tag = uuid4().hex[:8]
    given = from_mail(
        client, sender=f"a@{tag}.invalid", customer=f"CUST-{tag}", product="UPS-LSU-10K"
    )
    admin.execute(
        "INSERT INTO cs.ticket_workflows (ticket_id, state) VALUES (%s, 'WAITING_CUSTOMER') "
        "ON CONFLICT (ticket_id) DO UPDATE SET state='WAITING_CUSTOMER'",
        (UUID(given),),
    )
    response = client.post(PATH, json={"ticket_ids": [given, str(uuid4())]})
    [item] = response.json()["data"]["items"]
    assert (item["ticket_id"], item["given"], item["evidence"]) == (
        given,
        True,
        ["WAITING_CUSTOMER"],
    )
    assert candidates(client, from_address=f"nobody-{tag}@nowhere.invalid") == {}
    assert client.post(PATH, json={"from_address": "a@b.c", "unknown": 1}).status_code == 422


def test_sender_customers_match_active_contacts_address_first(client, admin):
    """KAN-237: 보낸 주소의 고객은 활성 연락처로 찾는다. 주소 일치 먼저, 도메인 다음, 없으면 빠진다."""
    tag = uuid4().hex[:8]
    domain = f"plant-{tag}.invalid"
    exact, by_domain, off = f"CUST-E{tag}", f"CUST-D{tag}", f"CUST-O{tag}"
    admin.execute(
        "INSERT INTO cs.customer_contacts (id, customer_external_id, email, domain, active, "
        "data_origin) VALUES (%s,%s,%s,NULL,true,'demo'), (%s,%s,NULL,%s,true,'demo'), "
        "(%s,%s,%s,NULL,false,'demo')",
        (
            uuid4(),
            exact,
            f"buyer@{domain}",
            uuid4(),
            by_domain,
            domain,
            uuid4(),
            off,
            f"x@{domain}",
        ),
    )
    path = "/internal/v1/cs/tickets/sender-customers"
    response = client.post(
        path, json={"addresses": [f"Buyer@{domain.upper()}", f"x@{domain}", "nobody@none.invalid"]}
    )
    assert response.status_code == 200, response.text
    found = {i["address"]: i["customer_external_ids"] for i in response.json()["data"]["items"]}
    assert found == {f"buyer@{domain}": [exact, by_domain], f"x@{domain}": [by_domain]}
    assert client.post(path, json={"addresses": ["a@b.c"] * 101}).status_code == 422
