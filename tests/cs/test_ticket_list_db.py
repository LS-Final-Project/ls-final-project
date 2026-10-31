"""문의 목록 서버 필터·정렬·담당자(KAN-131 화면 요청, Drive C/S API 명세 v1.0 7절) — 실제 PostgreSQL.

다른 검사의 문의가 같은 DB에 있으므로, 각 검사는 자기만의 고객 ID를 만들어 그 고객으로 좁혀 본다.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.api.cs.main import create_app
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

AGENT = Principal(user_id=uuid4(), roles={"cs_agent"})
BASE = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def client(cs_database):
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=SecretStr(make_conninfo(cs_database, user="cs_app", password="test-only")),
    )
    with TestClient(create_app(settings), headers=principal_header(AGENT)) as test_client:
        yield test_client


@pytest.fixture
def customer():
    return f"CUST-T{uuid4().hex[:8]}"


def add_ticket(
    admin,
    customer,
    hours,
    *,
    channel="EMAIL",
    product="TRF-OIL-1000",
    subject=None,
    state=None,
    owner=None,
):
    ticket_id = uuid4()
    received = BASE + timedelta(hours=hours)
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, subject, body) VALUES (%s, 'test', 'kan131', %s, 'synthetic', 'PROVIDED', "
        "%s, %s, %s, 'en', %s, %s, %s, 'body')",
        (ticket_id, f"T-{ticket_id}", received, received, channel, customer, product, subject),
    )
    if state is not None:
        admin.execute(
            "INSERT INTO cs.ticket_workflows (ticket_id, state, owner_user_id) VALUES (%s, %s, %s)",
            (ticket_id, state, owner),
        )
    return str(ticket_id)


def ids(client, **params):
    response = client.get("/api/v1/cs/tickets", params=params)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["data"]["items"]]


def test_filters_narrow_the_list_and_combine(client, admin, customer):
    """상태(쉼표로 여러 개)·채널·제품·담당·접수 기간이 각각 좁히고, 함께 주면 모두 만족하는 것만 남는다."""
    owner = uuid4()
    received = add_ticket(admin, customer, 0)
    review = add_ticket(admin, customer, 1, state="IN_REVIEW", owner=owner)
    transferred = add_ticket(admin, customer, 2, channel="CHAT", state="TRANSFERRED", owner=owner)
    other_product = add_ticket(admin, customer, 3, product="UNKNOWN-1", state="IN_REVIEW")
    everything = ids(client, customer=customer)
    assert everything == [other_product, transferred, review, received]
    # 업무 상태가 없는 문의는 접수(RECEIVED)다.
    assert ids(client, customer=customer, state="RECEIVED") == [received]
    assert ids(client, customer=customer, state="IN_REVIEW,TRANSFERRED") == [
        other_product,
        transferred,
        review,
    ]
    assert ids(client, customer=customer, channel="CHAT") == [transferred]
    assert ids(client, customer=customer, product_code="UNKNOWN-1") == [other_product]
    assert ids(client, customer=customer, owner=str(owner)) == [transferred, review]
    # 기간은 시작 포함·끝 제외다.
    window = {
        "received_from": (BASE + timedelta(hours=1)).isoformat(),
        "received_to": (BASE + timedelta(hours=3)).isoformat(),
    }
    assert ids(client, customer=customer, **window) == [transferred, review]
    assert ids(client, customer=customer, state="IN_REVIEW", owner=str(owner), **window) == [review]


def test_the_list_shows_the_owner(client, admin, customer):
    owner = uuid4()
    add_ticket(admin, customer, 0)
    add_ticket(admin, customer, 1, state="IN_REVIEW", owner=owner)
    items = client.get("/api/v1/cs/tickets", params={"customer": customer}).json()["data"]["items"]
    assert [item["owner_user_id"] for item in items] == [str(owner), None]


def test_search_matches_number_subject_or_customer_and_escapes_wildcards(client, admin, customer):
    """검색어는 문의 번호·제목·고객 ID의 부분 문자열이다. %·_는 와일드카드가 아니라 글자다."""
    token = uuid4().hex[:10]
    by_subject = add_ticket(admin, customer, 0, subject=f"Leak at {token.upper()} gasket")
    literal = add_ticket(admin, customer, 1, subject=f"{token}_100% load")
    add_ticket(admin, customer, 2, subject="unrelated")
    assert ids(client, customer=customer, q=token) == [literal, by_subject]
    assert ids(client, customer=customer, q=f"{token}_100%") == [literal]
    assert ids(client, customer=customer, q=f"{token}x100") == []
    # 고객 ID의 일부로도 찾는다(고객 필터 없이 이 고객만 나온다).
    assert set(ids(client, q=customer[5:])) >= {by_subject, literal}


def test_oldest_first_pages_and_cursor_keeps_its_direction(client, admin, customer):
    """오래된 순으로도 빠짐·중복 없이 넘기고, 한 방향의 커서를 다른 방향에 쓰면 400이다."""
    created = [add_ticket(admin, customer, hours) for hours in range(5)]
    seen, cursor = [], None
    while True:
        params = {"customer": customer, "sort": "received_at_asc", "limit": 2}
        if cursor:
            params["cursor"] = cursor
        body = client.get("/api/v1/cs/tickets", params=params).json()["data"]
        seen += [item["id"] for item in body["items"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
        mixed = client.get("/api/v1/cs/tickets", params={"customer": customer, "cursor": cursor})
        assert (mixed.status_code, mixed.json()["error"]["code"]) == (400, "CS_INVALID_CURSOR")
    assert seen == created


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"state": "IN_REVIEW,"}, "CS_INVALID_FILTER"),
        ({"state": "OPEN"}, "CS_INVALID_FILTER"),
        ({"sort": "subject"}, None),
        ({"owner": "not-a-uuid"}, None),
        ({"channel": "FAX"}, None),
    ],
)
def test_bad_filters_are_rejected(client, params, code):
    response = client.get("/api/v1/cs/tickets", params=params)
    assert response.status_code in (400, 422)
    if code:
        assert response.json()["error"]["code"] == code


def test_filters_never_widen_a_department_users_scope(client, admin, customer):
    """부서 사용자는 필터를 줘도 자기 부서로 이관된 문의만 본다."""
    visible = add_ticket(admin, customer, 0, state="TRANSFERRED")
    add_ticket(admin, customer, 1, state="TRANSFERRED")
    admin.execute(
        "INSERT INTO cs.ticket_transfers (id, ticket_id, dept_code, reason, round, status, "
        "transferred_by) VALUES (%s, %s, 'TS_TRF', 'check', 1, 'OPEN', %s)",
        (uuid4(), visible, uuid4()),
    )
    staff = Principal(user_id=uuid4(), roles={"dept_staff"}, dept="TS_TRF")
    response = client.get(
        "/api/v1/cs/tickets",
        params={"customer": customer, "state": "TRANSFERRED"},
        headers=principal_header(staff),
    )
    assert [item["id"] for item in response.json()["data"]["items"]] == [visible]
