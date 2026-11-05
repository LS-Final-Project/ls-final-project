"""KAN-152 C/S 문의·개선 전체 흐름을 HTTP API만으로 한 번에 잇는다(전용 PostgreSQL, 가짜 메일 플랫폼).

단계별 검사(test_workflow_db·test_customer_mail_db·test_units_db·test_improvement_followups_db)는 단계마다 상태를
직접 만든다. 여기서는 앞 단계 결과를 뒤 단계가 그대로 이어받는지 본다. DB에 직접 넣는 것은 합성 설치 장비 1대뿐이고,
문의·발송·회신·장비 확인·부서 결과·종결·해결 기록·개선요청·차수·부서 동작은 모두 API로 만든다.

흐름: 접수 → 장비 확인 → 정보 요청 발송(플랫폼 접수) → 고객 회신으로 다시 열림 → 부서 이관·결과 채택 → 종결 →
해결 기록 → 개선요청 발송 → 새 문의 3건 → 재요청 차수 → 부서 접수·회신·조치·완료 → 구형(미적용) 장비 기록은
재발로 세지 않고 업데이트 안내 대상 → 조치 적용 장비 재발 1건 → 완료된 요청 다시 열림.
"""

import random
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.api.cs.main import create_app
from backend.api.cs.workflow.mail import CustomerMailSettings, configure_customer_mail
from backend.shared.access import principal_header
from backend.shared.settings import Settings
from tests.cs.test_customer_mail_db import PLATFORM, FakePlatform, last_send, send
from tests.cs.test_improvement_transitions_db import PATH, QA_STAFF
from tests.cs.test_workflow_db import AGENT, TRF_STAFF, act, ok, state

PRODUCT = "TRF-OIL-1000"
OLD_FW, FIXED_FW = "1.60", "1.72"


@pytest.fixture
def platform():
    return FakePlatform()


@pytest.fixture
def client(cs_database, platform):
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=SecretStr(make_conninfo(cs_database, user="cs_app", password="test-only")),
    )
    app = create_app(settings)
    configure_customer_mail(
        app,
        CustomerMailSettings(_env_file=None, enabled=True, platform_url=PLATFORM),
        token=lambda audience: "cs-token",
        transport=httpx.MockTransport(platform.handler),
    )
    with TestClient(app, headers=principal_header(AGENT)) as test_client:
        yield test_client


@pytest.fixture
def fault(admin):
    """검사 DB에 아직 없는 고장 ID(재요청·재발 신호는 제품·고장 ID로 묶이므로 다른 검사와 섞이지 않게)."""
    used = {row[0] for row in admin.execute("SELECT DISTINCT fault_id FROM cs.resolution_cases")}
    free = [f"FLT-{n}" for n in range(900, 1000) if f"FLT-{n}" not in used]
    return random.choice(free)


def create_ticket(client, customer, body="Oil leakage at gasket."):
    response = client.post(
        "/api/v1/cs/tickets",
        json={
            "channel": "EMAIL",
            "body_language": "en",
            "customer_external_id": customer,
            "product_code": PRODUCT,
            "body": body,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def through_department(client, ticket, version):
    """이관 → 부서 결과 → 채택. 종결 조건(채택된 부서 결과)을 API로 채운다. 마지막 업무 판본을 돌려준다."""
    data = ok(act(client, ticket, "transfer", version=version, dept_code="TS_TRF", reason="Gasket"))
    data = ok(
        act(client, ticket, "dept-result", TRF_STAFF, version=data["version"], result="Replaced")
    )
    [result] = [r for r in data["dept_results"] if r["review_status"] == "SUBMITTED"]
    review = f"dept-results/{result['id']}/review"
    data = ok(
        act(client, ticket, review, version=data["version"], result_version=1, decision="ACCEPT")
    )
    return data["version"], result["id"]


def register_case(client, ticket, version, fault, fw, dept_result_id=None):
    response = client.post(
        f"/api/v1/cs/tickets/{ticket}/resolution-case",
        json={
            "version": version,
            "fault_id": fault,
            "fw_version_at_issue": fw,
            "symptom": "Oil leakage",
            "root_cause": "Gasket aging under firmware fan control",
            "root_cause_type": "FIRMWARE",
            "action_taken": "Gasket replaced, firmware checked",
            "onsite_visit": True,
            "resolved_by_dept": "TS_TRF",
            "dept_result_id": dept_result_id,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def resolved(client, fault, fw, customer=None):
    """새 문의 하나를 접수부터 해결 기록까지 API로 끝낸다. (문의 ID, 해결 기록 ID)."""
    ticket = create_ticket(client, customer or f"CUST-{uuid4().hex[:6]}")
    version = ok(act(client, ticket, "start-review", version=0))["version"]
    version, result_id = through_department(client, ticket, version)
    closed = ok(act(client, ticket, "close", version=version))
    return ticket, register_case(client, ticket, closed["version"], fault, fw, result_id)


def improvement(client, rid, step, user=AGENT, /, **body):
    """개선요청 동작 하나. 위치 전용 인자라 본문의 'action' 칸(부서 조치 내용)과 겹치지 않는다."""
    return client.post(f"{PATH}/{rid}/{step}", json=body, headers=principal_header(user))


def signals(client, rid):
    response = client.get(f"{PATH}/{rid}/followup-signals")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_a_ticket_and_its_improvement_run_end_to_end_through_the_api(
    client, admin, platform, fault
):
    customer = f"CUST-{uuid4().hex[:6]}"
    unit = uuid4()
    # 합성 설치 장비 1대(장비 마스터는 적재 자료라 API가 없다).
    admin.execute(
        "INSERT INTO cs.installed_units (id, source, source_instance, data_origin, known_at, "
        "serial_no, customer_external_id, product_code) VALUES (%s, 'test', 'kan152', 'demo', "
        "%s, %s, %s, %s)",
        (unit, datetime(2026, 1, 1, tzinfo=UTC), f"SN-{unit.hex[:10]}", customer, PRODUCT),
    )

    # 1. 접수 → 장비 확인
    ticket = create_ticket(client, customer)
    assert state(client, ticket)["state"] == "RECEIVED"
    link = client.put(
        f"/api/v1/cs/tickets/{ticket}/units/{unit}",
        json={"link_status": "CONFIRMED", "link_basis": "SERIAL_PROVIDED", "version": None},
    )
    assert link.status_code == 200, link.text
    assert link.json()["data"]["link"]["link_status"] == "CONFIRMED"

    # 2. 정보 요청 발송 → 플랫폼 접수 → 고객 대기 → 회신으로 다시 열림
    version = ok(act(client, ticket, "start-review", version=0))["version"]
    sent = last_send(ok(send(client, ticket, version, kind="INFO_REQUEST")))
    platform.settle("ACCEPTED")
    data = ok(act(client, ticket, f"sends/{sent['id']}/refresh"))
    assert data["state"] == "WAITING_CUSTOMER"
    reply = client.post(
        f"/internal/v1/cs/tickets/{ticket}/customer-reply",
        json={
            "message_id": str(uuid4()),
            "method": "HEADER",
            "received_at": datetime.now(UTC).isoformat(),
        },
    )
    assert reply.status_code == 200, reply.text
    assert (reply.json()["data"]["reopened"], reply.json()["data"]["state"]) == (True, "IN_REVIEW")

    # 3. 부서 이관·결과 채택 → 종결 → 해결 기록(구형 펌웨어에서 난 고장)
    version, result_id = through_department(client, ticket, state(client, ticket)["version"])
    closed = ok(act(client, ticket, "close", version=version))
    assert closed["state"] == "CLOSED"
    first_case = register_case(client, ticket, closed["version"], fault, OLD_FW, result_id)
    detail = client.get(f"/api/v1/cs/tickets/{ticket}").json()["data"]
    assert detail["workflow_state"] == "CLOSED"

    # 4. 같은 고장 3건을 근거로 개선요청을 만들고 보낸다(이 문의가 첫 근거).
    evidence = [(ticket, first_case), *(resolved(client, fault, OLD_FW) for _ in range(2))]
    created = client.post(
        PATH,
        json={
            "request_type": "FAULT",
            "product_code": PRODUCT,
            "fault_id": fault,
            "problem_statement": "Same gasket leakage at three customers.",
            "proposed_depts": ["QA_CLAIM"],
            "evidence": [{"ticket_id": str(t), "case_id": str(c)} for t, c in evidence],
        },
    )

    assert created.status_code == 201, created.text
    draft = created.json()["data"]["request"]
    rid = draft["id"]
    reviewed = ok(
        improvement(client, rid, "review", version=draft["version"], proposed_depts=["QA_CLAIM"])
    )
    sent_request = ok(improvement(client, rid, "send", version=reviewed["request"]["version"]))
    assert sent_request["request"]["status"] == "SENT"
    before = signals(client, rid)
    assert (before["phase"], before["decision"]) == ("BEFORE_ACTION", "NONE")

    # 5. 보낸 뒤 새 문의 3건(접수부터 해결 기록까지 API) → 재요청 차수
    new = [resolved(client, fault, OLD_FW) for _ in range(3)]
    signal = signals(client, rid)
    assert (signal["decision"], signal["counts"]["new_since_sent"]) == ("REQUEST_AGAIN", 3)
    opened = ok(
        improvement(
            client,
            rid,
            "rounds",
            version=sent_request["request"]["version"],
            kind="REQUEST_AGAIN",
            as_of=signal["as_of"],
        )
    )
    assert opened["request"]["current_round"] == 2
    active = {e["ticket_id"] for e in opened["evidence"] if e["removed_at"] is None}
    assert {str(t) for t, _ in new} <= active

    # 6. 부서 접수·회신·조치(펌웨어 1.72 이상 적용)·추적 계획·완료. 부서 경로는 KAN-116 #310이 중계를 고쳤다.
    v = opened["request"]["version"]
    v = ok(improvement(client, rid, "departments/QA_CLAIM/acknowledge", QA_STAFF, version=v))[
        "request"
    ]["version"]
    v = ok(
        improvement(
            client, rid, "departments/QA_CLAIM/reply", QA_STAFF, version=v, reply_type="ADOPTED"
        )
    )["request"]["version"]
    acted = ok(
        improvement(
            client,
            rid,
            "departments/QA_CLAIM/action",
            QA_STAFF,
            version=v,
            action="Release firmware 1.72 with fan control fix",
            action_scope="Units on 1.72 or later",
            action_date="2025-12-01",
            action_evidence="Release note",
            scope_kind="FIRMWARE_VERSION",
            scope_value=FIXED_FW,
        )
    )
    v = acted["request"]["version"]
    v = ok(improvement(client, rid, "followup-plan", version=v, followup_plan="Watch 90 days"))[
        "request"
    ]["version"]
    done = ok(improvement(client, rid, "complete", version=v, followup_result="Quiet 90 days"))
    assert done["request"]["status"] == "COMPLETED"
    v = done["request"]["version"]

    # 7. 구형(미적용) 장비 기록: 재발이 아니라 따로 세고, 그 고객은 업데이트 안내 대상
    old_customer = f"CUST-OLD-{uuid4().hex[:4]}"
    resolved(client, fault, OLD_FW, customer=old_customer)
    old_only = signals(client, rid)
    assert (old_only["phase"], old_only["decision"]) == ("AFTER_ACTION", "NONE")
    assert old_customer in old_only["update_notice_customers"]
    assert old_only["counts"]["recurrence"] == 0 and old_only["counts"]["pre_action_unit"] == 1

    # 8. 조치 적용 장비에서 재발 1건 → 완료된 요청이 다시 열린다(앞 완료 기록은 남는다).
    recurred, _ = resolved(client, fault, FIXED_FW)
    signal = signals(client, rid)
    assert (signal["decision"], signal["counts"]["recurrence"]) == ("RECURRENCE", 1)
    reopened = ok(
        improvement(client, rid, "rounds", version=v, kind="RECURRENCE", as_of=signal["as_of"])
    )
    request = reopened["request"]
    assert (request["status"], request["current_round"], request["completed_at"]) == (
        "SENT",
        3,
        None,
    )
    assert reopened["rounds"][-1]["previous_completion"]["followup_result"] == "Quiet 90 days"
    assert str(recurred) in {e["ticket_id"] for e in reopened["evidence"]}

    # 끝까지 이어진 뒤에도 기록이 빠지지 않았다: 첫 문의의 업무 전이와 개선요청 차수가 감사에 모두 남는다.
    transitions = [
        row[0]
        for row in admin.execute(
            "SELECT changes->'action'->>'to' FROM cs.audit_events WHERE entity_type='ticket_workflow' "
            "AND entity_id=%s ORDER BY version",
            (ticket,),
        )
    ]
    assert transitions[0] == "start_review" and transitions[-1] == "close"
    assert {"transfer", "dept_result", "review_dept_result"} <= set(transitions)
    rounds = [
        row[0]
        for row in admin.execute(
            "SELECT changes->'kind'->>'to' FROM cs.audit_events WHERE entity_type='improvement_request' "
            "AND entity_id=%s AND changes->'action'->>'to'='open_round' ORDER BY version",
            (rid,),
        )
    ]
    assert rounds == ["REQUEST_AGAIN", "RECURRENCE"]
