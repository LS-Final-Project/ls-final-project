"""KAN-233 업무 통계: SLA·담당자·부서·개선요청 확장 칸을 실제 PostgreSQL에서 확인한다.

시각은 지금(DB now) 기준으로 만든다. 진행 문의의 임박·초과는 조회 시각에서 재기 때문이다.
고객·담당자 ID를 시험마다 새로 만들어 같은 DB의 다른 시험 행이 숫자를 바꾸지 않게 한다.
고객 등급이 없으면 표준 등급으로 계산한다(DEFECT·STANDARD: P1 4h/72h, P3 24h/240h).
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from backend.api.cs.main import create_app
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

SEOUL = ZoneInfo("Asia/Seoul")
CS_MANAGER = Principal(user_id=uuid4(), roles={"manager"}, managed_departments={"GCS_L1"})
LV_MANAGER = Principal(user_id=uuid4(), roles={"manager"}, managed_departments={"TS_LV"})
PATH = "/api/v1/cs/stats"
SAFETY = {
    "category_code": "DEFECT",
    "secondary_category": None,
    "urgency_flag": "SAFETY",
    "provided_info": [],
    "return_subtype": None,
}
CALM = {**SAFETY, "urgency_flag": "NONE"}


@pytest.fixture(scope="module")
def client(cs_database):
    dsn = make_conninfo(cs_database, user="cs_app", password="test-only")
    settings = Settings(_env_file=None, environment="test", database_url=SecretStr(dsn))
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def get(client, path, user, **query):
    response = client.get(f"{PATH}/{path}", params=query, headers=principal_header(user))
    assert response.status_code == 200, response.text
    return response.json()["data"]


class Seed:
    def __init__(self, admin):
        self.admin = admin
        self.customer = f"CUST-{uuid4().hex[:8]}"
        self.owner = uuid4()
        self.now = datetime.now(UTC)

    def ticket(self, hours_ago, state="IN_REVIEW", ai=None, closed_after=None, subject="Leak"):
        ticket_id, received = uuid4(), self.now - timedelta(hours=hours_ago)
        self.admin.execute(
            "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
            "derivation_kind, received_at, known_at, channel, body_language, "
            "customer_external_id, product_code, subject, body) VALUES (%s, 'test', 'kan233', "
            "%s, 'synthetic', 'PROVIDED', %s, %s, 'EMAIL', 'en', %s, 'TRF-OIL-1000', %s, 'B')",
            (ticket_id, f"T-{ticket_id}", received, received, self.customer, subject),
        )
        closed = received + timedelta(hours=closed_after) if closed_after is not None else None
        self.admin.execute(
            "INSERT INTO cs.ticket_workflows (ticket_id, state, owner_user_id, closed_at, "
            "closed_by) VALUES (%s, %s, %s, %s, %s)",
            (ticket_id, state, self.owner, closed, self.owner if closed else None),
        )
        if ai is not None:
            self.admin.execute(
                "INSERT INTO cs.ticket_analyses (id, ticket_id, run_seq, outcome, "
                "classification, input_version) VALUES (%s, %s, 1, 'VALID', %s, 'cs-input-v1')",
                (uuid4(), ticket_id, Jsonb(ai)),
            )
        return ticket_id, received

    def send(self, ticket_id, at):
        self.admin.execute(
            "INSERT INTO cs.ticket_customer_sends (id, ticket_id, kind, workflow_version, "
            "mailbox_id, subject, body_type, body, to_addresses, payload_sha256, approved_by, "
            "approved_at, requested_info_codes, accepted_at, recorded_at) VALUES (%s, %s, "
            "'ANSWER', 1, %s, 'Re', 'text', 'Hi', '[]', %s, %s, %s, '[]', %s, %s)",
            (uuid4(), ticket_id, uuid4(), "0" * 64, uuid4(), at, at, at),
        )


@pytest.fixture
def seed(admin):
    s = Seed(admin)
    s.overdue, _ = s.ticket(80, ai=SAFETY, subject="Overdue")  # P1, 8시간 초과
    s.at_risk, received = s.ticket(60, ai=SAFETY)  # 60/72 = 83% 경과
    s.send(s.at_risk, received + timedelta(hours=2))  # 첫 응답 4시간 안
    # 해결 기한이 1분 뒤: 임박이면서 대개 오늘 기한이다.
    s.due_soon, _ = s.ticket(72 - 1 / 60, ai=SAFETY)
    s.ticket(100, state="CLOSED", ai=SAFETY, closed_after=10)  # 해결 준수
    s.ticket(200, state="CLOSED", ai=SAFETY, closed_after=100)  # 해결 초과
    s.ticket(5, state="RECEIVED")  # 분류 없음 → SLA 미확인
    s.fresh, _ = s.ticket(1, ai=CALM)  # P3, 첫 응답 대기 중
    # AI는 P3이지만 사람이 SAFETY로 고쳐 P1 기한(72h)이 적용된다: 75시간이면 3시간 초과.
    s.corrected, _ = s.ticket(75, ai=CALM, subject="Corrected")
    admin.execute(
        "INSERT INTO cs.ticket_classification_corrections (id, ticket_id, seq, fields, reason, "
        "corrected_by) VALUES (%s, %s, 1, %s, 'phone', %s)",
        (uuid4(), s.corrected, Jsonb({"urgency_flag": "SAFETY"}), s.owner),
    )
    return s


def test_overview_sla_follows_the_rules_and_corrections(client, seed):
    sla = get(client, "overview", CS_MANAGER, customer=seed.customer)["sla"]
    assert sla["first_response"] == {"met": 1, "breached": 3, "pending": 1, "unrecorded": 2}
    assert (sla["resolution"]["met"], sla["resolution"]["breached"]) == (1, 1)
    assert (sla["at_risk"], sla["overdue"], sla["unknown"]) == (2, 2, 1)
    p1 = next(p for p in sla["by_priority"] if p["priority"] == "P1")
    assert p1 == {"priority": "P1", "met": 1, "breached": 1, "at_risk": 2, "overdue": 2}
    assert [p["priority"] for p in sla["by_priority"]] == ["P1", "P2", "P3", "P4"]
    rows = sla["overdue_tickets"]
    assert [r["ticket_id"] for r in rows] == [str(seed.overdue), str(seed.corrected)]
    assert [r["subject"] for r in rows] == ["Overdue", "Corrected"]
    assert 7.9 <= rows[0]["overdue_hours"] <= 8.1 and rows[0]["owner_user_id"] == str(seed.owner)
    data = get(client, "overview", CS_MANAGER, customer=seed.customer)
    assert data["unconfirmed"] == ["RULE_ASSIGNED_DEPT"]


def test_overdue_deadline_matches_the_analysis_view(client, seed):
    sla = get(client, "overview", CS_MANAGER, customer=seed.customer)["sla"]
    [row] = [r for r in sla["overdue_tickets"] if r["ticket_id"] == str(seed.corrected)]
    agent = Principal(user_id=seed.owner, roles={"cs_agent"})
    response = client.get(
        f"/api/v1/cs/tickets/{seed.corrected}/analysis", headers=principal_header(agent)
    )
    assert response.status_code == 200, response.text
    due = response.json()["data"]["rules"]["resolution_due"]
    assert (due["effective"], due["source"]) == (row["resolution_due"], "human_input")


def test_subjects_are_hidden_outside_the_detail_scope(client, seed):
    sla = get(client, "overview", LV_MANAGER, customer=seed.customer)["sla"]
    assert [r["subject"] for r in sla["overdue_tickets"]] == [None, None]
    assert sla["overdue"] == 2


def test_the_period_limits_the_sla_population(client, seed):
    # 최근 3시간에 접수된 것은 P3 대기 문의 하나뿐이다. 종결은 모두 기간 밖이다.
    start = (seed.now - timedelta(hours=3)).isoformat()
    sla = get(client, "overview", CS_MANAGER, customer=seed.customer, start=start)["sla"]
    assert sla["first_response"] == {"met": 0, "breached": 0, "pending": 1, "unrecorded": 0}
    assert (sla["resolution"]["met"], sla["at_risk"], sla["overdue"], sla["unknown"]) == (
        0,
        0,
        0,
        0,
    )


def test_owner_deadlines_and_handle_days(client, admin, seed):
    request = uuid4()
    admin.execute(
        "INSERT INTO cs.improvement_requests (id, request_type, origin, data_origin, "
        "product_code, fault_id, problem_statement, cs_owner) VALUES (%s, 'FAULT', 'MANUAL', "
        "'demo', 'TRF-OIL-1000', 'FLT-067', 'Leaks', %s)",
        (request, seed.owner),
    )
    items = get(client, "owners", CS_MANAGER)["items"]
    [row] = [i for i in items if i["owner_user_id"] == str(seed.owner)]
    assert row["open"] == 6
    assert (row["overdue"], row["sla_unknown"], row["improvements_open"]) == (2, 1, 1)
    # 해결 기한이 12시간 뒤(임박 문의)와 1분 뒤인 두 건 중 서울 날짜로 오늘 안에 끝나는 것.
    midnight = datetime.combine(
        seed.now.astimezone(SEOUL).date() + timedelta(days=1), datetime.min.time(), SEOUL
    )
    dues = [seed.now + timedelta(hours=12), seed.now + timedelta(minutes=1)]
    assert row["due_today"] == sum(due < midnight for due in dues)
    # 종결 두 건: 10시간, 100시간 → 평균 55시간 = 2.3일.
    assert row["avg_handle_days"] == 2.3


def test_department_waiting_and_reply_days(client, admin, seed):
    actor = uuid4()
    admin.execute(
        "INSERT INTO cs.ticket_transfers (id, ticket_id, round, dept_code, status, reason, "
        "transferred_by, transferred_at) VALUES (%s, %s, 1, 'TS_TRF', 'OPEN', 'x', %s, %s)",
        (uuid4(), seed.overdue, actor, seed.now - timedelta(days=10)),
    )
    transfer = uuid4()
    admin.execute(
        "INSERT INTO cs.ticket_transfers (id, ticket_id, round, dept_code, status, reason, "
        "transferred_by, transferred_at, ended_by, ended_at) VALUES (%s, %s, 1, 'TS_TRF', "
        "'RESULT_SUBMITTED', 'x', %s, %s, %s, %s)",
        (transfer, seed.at_risk, actor, seed.now - timedelta(days=3), actor, seed.now),
    )
    admin.execute(
        "INSERT INTO cs.dept_results (id, ticket_id, transfer_id, dept_code, result, "
        "submitted_by, submitted_at) VALUES (%s, %s, %s, 'TS_TRF', 'ok', %s, %s)",
        (uuid4(), seed.at_risk, transfer, actor, seed.now - timedelta(days=1)),
    )
    [dept] = get(client, "overview", CS_MANAGER, customer=seed.customer)["departments"]
    assert dept == {
        "dept_code": "TS_TRF",
        "transferred": 2,
        "results_submitted": 1,
        "results_accepted": 0,
        "returned_or_recalled": 0,
        "waiting": 1,
        "waiting_over_7_days": 1,
        "avg_reply_days": 2.0,
    }
    # 이관된 부서의 관리자는 그 문의 제목을 본다.
    trf = Principal(user_id=uuid4(), roles={"manager"}, managed_departments={"TS_TRF"})
    rows = get(client, "overview", trf, customer=seed.customer)["sla"]["overdue_tickets"]
    assert [r["subject"] for r in rows] == ["Overdue", None]


def request_row(admin, seed, request_type="FAULT", status="SENT", **values):
    request = uuid4()
    fault = ("FLT-067", None) if request_type == "FAULT" else (None, "NOT_FOUND")
    admin.execute(
        "INSERT INTO cs.improvement_requests (id, request_type, origin, data_origin, "
        "product_code, fault_id, doc_signal, problem_statement, status, current_round, sent_at, "
        "completed_at, completed_by) VALUES (%s, %s, 'MANUAL', 'demo', 'TRF-OIL-1000', %s, %s, "
        "'Leaks', %s, 1, %s, %s, %s)",
        (
            request,
            request_type,
            *fault,
            status,
            seed.now - timedelta(days=5),
            seed.now if status == "COMPLETED" else None,
            seed.owner if status == "COMPLETED" else None,
        ),
    )
    admin.execute(
        "INSERT INTO cs.request_evidence (id, request_id, ticket_id) VALUES (%s, %s, %s)",
        (uuid4(), request, seed.overdue),
    )
    for dept, dept_status, due in values.get("depts", []):
        admin.execute(
            "INSERT INTO cs.request_departments (request_id, round, dept_code, status, "
            "reply_due_at) VALUES (%s, 1, %s, %s, %s)",
            (request, dept, dept_status, due),
        )
    return request


def test_improvement_side_counts_and_recurrence(client, admin, seed):
    yesterday = seed.now - timedelta(days=1)
    request_row(
        admin,
        seed,
        depts=[("TS_TRF", "SENT", yesterday), ("QA_CLAIM", "INFO_REQUESTED", None)],
    )
    replied = request_row(admin, seed, depts=[("TS_LV", "ACKNOWLEDGED", yesterday)])
    admin.execute(
        "INSERT INTO cs.dept_replies (id, request_id, round, dept_code, request_version, "
        "reply_type, replied_by) VALUES (%s, %s, 1, 'TS_LV', 1, 'ACKNOWLEDGED', %s)",
        (uuid4(), replied, seed.owner),
    )
    request_row(admin, seed, status="REJECTED", depts=[("FIELD_SVC", "REJECTED", None)])
    request_row(admin, seed, status="COMPLETED")
    request_row(admin, seed, request_type="DOCUMENT", status="COMPLETED")
    improvements = get(client, "overview", CS_MANAGER, customer=seed.customer)["improvements"]
    assert (
        improvements["info_requested"],
        improvements["rejected"],
        improvements["reply_overdue"],
        improvements["completed_in_period"],
    ) == (1, 1, 1, 2)
    # 조치 기록이 없는 완료 요청은 재발 후보가 아니다. 문서 요청은 판정 대상이 아니다.
    assert improvements["recurrence_after_completion"] == {
        "checked": 1,
        "recurrence": 0,
        "not_applicable": 1,
    }
    # 담당자 화면의 SLA는 본인 담당 범위에서만 센다.
    agent = Principal(user_id=seed.owner, roles={"cs_agent"})
    own = get(client, "overview", agent)
    assert own["improvements"] is None and own["sla"]["overdue"] >= 2
