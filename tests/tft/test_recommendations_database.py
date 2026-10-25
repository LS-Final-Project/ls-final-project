"""일회용 PostgreSQL에서 KAN-123 SQL·동시 접수·롤백을 검증한다.

TFT_TEST_DATABASE_URL은 테스트 전용 team4 DB에만 지정한다. CI에서 독립 서비스로 실행한다.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from backend.api.tft.main import create_app
from backend.api.tft.recommendations import repository
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

DSN = os.getenv("TFT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="requires dedicated TFT_TEST_DATABASE_URL")


@pytest.fixture(scope="module")
def database():
    assert DSN
    with psycopg.connect(DSN, autocommit=True) as conn:
        assert conn.execute("SELECT current_database()").fetchone()[0] == "team4"
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'tft_app'").fetchone():
            apply(conn, Path("infra/sql/001_service_roles.sql"))
        for path in sorted(Path("infra/migrations/tft").glob("*.sql")):
            apply(conn, path)
        # The new migration is repeatable without deleting records.
        apply(conn, Path("infra/migrations/tft/20261025_KAN-123_recommendation_commands.sql"))
        apply(conn, Path("infra/migrations/tft/20261025_KAN-123_competency_rechecks.sql"))
    yield DSN


def apply(conn, path):
    source = "\n".join(line for line in path.read_text().splitlines() if not line.startswith("\\"))
    conn.execute(source)


@pytest.fixture
def scenario(database):
    request, role, org, candidate, user, project = [uuid4() for _ in range(6)]
    dept = "TEST_" + uuid4().hex.upper()
    with psycopg.connect(database) as conn:
        conn.execute(
            "INSERT INTO tft.organizations (id, source, source_instance, data_origin, derivation_kind, company_code, name, dept_code) VALUES (%s,'test','test','synthetic','ASSUMED','HOST','Test department',%s)",
            (org, dept),
        )
        conn.execute(
            "INSERT INTO tft.candidates (id, source, source_instance, data_origin, derivation_kind, organization_id, display_name) VALUES (%s,'test','test','synthetic','ASSUMED',%s,'Test candidate')",
            (candidate, org),
        )
        conn.execute(
            "INSERT INTO tft.projects (id,source,source_instance,data_origin,derivation_kind,known_at,company_code,name,status) VALUES (%s,'test','test','synthetic','ASSUMED',now(),'HOST','Test project','STAFFING')",
            (project,),
        )
        conn.execute(
            "INSERT INTO tft.required_roles (id,project_id,data_origin,derivation_kind,role_code,title,status,reviewed_by_user_id,reviewed_at) VALUES (%s,%s,'synthetic','ASSUMED','TEST','Test role','ISSUED',%s,now())",
            (role, project, user),
        )
        conn.execute(
            "INSERT INTO tft.staffing_requests (id,project_id,required_role_id,organization_id,data_origin,derivation_kind,status,issued_by_user_id,issued_at,due_undecided) VALUES (%s,%s,%s,%s,'synthetic','ASSUMED','ISSUED',%s,now(),true)",
            (request, project, role, org, user),
        )
    settings = Settings(
        service_name="tft",
        environment="test",
        database_url=make_conninfo(database, options="-c role=tft_app"),
    )
    headers = principal_header(Principal(user_id=user, roles={"dept_staff"}, dept=dept))
    with TestClient(create_app(settings)) as client:
        yield client, request, candidate, headers, database, role, org


def recommend(scenario, key="one", rationale="실제 수행 업무", version=1):
    client, request, candidate, headers, *_ = scenario
    return client.post(
        f"/api/v1/tft/staffing-requests/{request}/recommendations",
        headers={**headers, "Idempotency-Key": key, "If-Match": f'"{version}"'},
        json={"candidate_id": str(candidate), "rationale": rationale},
    )


def test_database_concurrent_same_key_is_one_atomic_recommendation(scenario):
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: recommend(scenario), range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    client, request, candidate, headers, database, role, org = scenario
    with psycopg.connect(database) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.candidate_recommendations WHERE staffing_request_id=%s",
                (request,),
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.recommendation_commands WHERE request_id=%s", (request,)
            ).fetchone()[0]
            == 1
        )
        assert conn.execute(
            "SELECT version,status FROM tft.staffing_requests WHERE id=%s", (request,)
        ).fetchone() == (2, "ANSWERED")
        assert (
            conn.execute(
                "SELECT action FROM tft.staffing_request_revisions WHERE staffing_request_id=%s",
                (request,),
            ).fetchone()[0]
            == "RECOMMEND"
        )
        assert (
            conn.execute("SELECT status FROM tft.required_roles WHERE id=%s", (role,)).fetchone()[0]
            == "RECOMMENDATIONS_RECEIVED"
        )
    assert recommend(scenario, rationale="changed").status_code == 409
    listed = client.get(f"/api/v1/tft/staffing-requests/{request}/recommendations", headers=headers)
    assert listed.status_code == 200 and len(listed.json()["data"]["items"]) == 1
    candidates = client.get(f"/api/v1/tft/staffing-requests/{request}/candidates", headers=headers)
    assert candidates.status_code == 200 and candidates.json()["data"]["items"][0]["id"] == str(
        candidate
    )
    inbox = client.get("/api/v1/tft/department-requests", headers=headers)
    assert inbox.status_code == 200 and inbox.json()["data"]["items"][0]["id"] == str(request)


def test_database_append_verification_and_no_false_final_approval(scenario):
    result = recommend(scenario)
    assert result.status_code == 200
    rec = result.json()["data"]["id"]
    client, request, _, headers, database, _, _ = scenario
    for version, kind, body in [
        (
            2,
            "experience",
            {
                "summary": "프로젝트에서 실제 담당한 시공 업무",
                "status": "CONFIRMED",
                "basis": "업무 기록",
            },
        ),
        (
            3,
            "availability",
            {
                "status": "CONFIRMED",
                "basis": "부서 확인",
                "period_start": "2027-01-01",
                "period_end": "2027-02-01",
                "allocation_rate": 50,
            },
        ),
    ]:
        response = client.post(
            f"/api/v1/tft/staffing-requests/{request}/recommendations/{rec}/{kind}",
            headers={**headers, "Idempotency-Key": kind, "If-Match": f'"{version}"'},
            json=body,
        )
        assert response.status_code == 200, response.text
    listed = client.get(
        f"/api/v1/tft/staffing-requests/{request}/recommendations", headers=headers
    ).json()["data"]["items"][0]
    assert len(listed["experience_evidence"]) == len(listed["availability_checks"]) == 1
    assert listed["availability_checks"][0]["allocation_rate"] == 50
    with psycopg.connect(database) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.final_confirmations WHERE project_id=(SELECT project_id FROM tft.staffing_requests WHERE id=%s)",
                (request,),
            ).fetchone()[0]
            == 0
        )


def test_database_receipt_failure_rolls_back_business_rows(scenario, monkeypatch):
    async def fail(conn, *args):
        await conn.execute("SELECT 1/0")

    monkeypatch.setattr(repository, "remember", fail)
    assert recommend(scenario).status_code == 503
    _, request, _, _, database, role, _ = scenario
    with psycopg.connect(database) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.candidate_recommendations WHERE staffing_request_id=%s",
                (request,),
            ).fetchone()[0]
            == 0
        )
        assert conn.execute(
            "SELECT version,status FROM tft.staffing_requests WHERE id=%s", (request,)
        ).fetchone() == (1, "ISSUED")
        assert (
            conn.execute("SELECT status FROM tft.required_roles WHERE id=%s", (role,)).fetchone()[0]
            == "ISSUED"
        )


def test_database_other_department_is_hidden_and_receipts_are_append_only(scenario):
    client, request, _, _, database, _, _ = scenario
    other = principal_header(Principal(user_id=uuid4(), roles={"dept_staff"}, dept="OTHER"))
    response = client.get(f"/api/v1/tft/staffing-requests/{request}/candidates", headers=other)
    assert response.status_code == 404
    with psycopg.connect(database) as conn:
        assert (
            conn.execute(
                "SELECT has_table_privilege('tft_app','tft.recommendation_commands','UPDATE')"
            ).fetchone()[0]
            is False
        )


def test_department_project_projection_hides_other_requests_and_unlinked_rfp(scenario):
    client, request, _, headers, database, role, org = scenario
    secret_org, secret_request, requirement = uuid4(), uuid4(), uuid4()
    with psycopg.connect(database) as conn:
        project = conn.execute(
            "SELECT project_id FROM tft.staffing_requests WHERE id=%s", (request,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO tft.organizations (id,source,source_instance,data_origin,derivation_kind,company_code,name,dept_code) VALUES (%s,'test','test','synthetic','ASSUMED','HOST','Other department',%s)",
            (secret_org, "OTHER_" + uuid4().hex.upper()),
        )
        conn.execute(
            "INSERT INTO tft.staffing_requests (id,project_id,required_role_id,organization_id,data_origin,derivation_kind) VALUES (%s,%s,%s,%s,'synthetic','ASSUMED')",
            (secret_request, project, role, secret_org),
        )
        conn.execute(
            "INSERT INTO tft.rfp_requirements (id,project_id,data_origin,derivation_kind,requirement_code,title) VALUES (%s,%s,'synthetic','ASSUMED','SECRET','unlinked private requirement')",
            (requirement, project),
        )
    response = client.get(f"/api/v1/tft/projects/{project}", headers=headers)
    assert response.status_code == 200, response.text
    detail = response.json()["data"]
    assert [item["id"] for item in detail["staffing_requests"]] == [str(request)]
    assert detail["rfp_requirements"] == []
    assert detail["current_composition"] is None and detail["final_confirmation"] is None
    listed = client.get("/api/v1/tft/projects", headers=headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["data"]["items"]] == [str(project)]
    other = principal_header(Principal(user_id=uuid4(), roles={"dept_staff"}, dept="NO_REQUEST"))
    assert client.get(f"/api/v1/tft/projects/{project}", headers=other).status_code == 404


def test_database_competency_must_belong_to_requested_role(scenario):
    client, request, _, headers, database, role, _ = scenario
    rec = recommend(scenario).json()["data"]["id"]
    competency = uuid4()
    with psycopg.connect(database) as conn:
        conn.execute(
            "INSERT INTO tft.role_competencies (id,required_role_id,kind,description) VALUES (%s,%s,'REQUIRED','시공 경험')",
            (competency, role),
        )
    path = f"/api/v1/tft/staffing-requests/{request}/recommendations/{rec}/competency"
    auth = {**headers, "If-Match": '"2"', "Idempotency-Key": "check"}
    assert (
        client.post(
            path,
            headers=auth,
            json={"competency_id": str(uuid4()), "status": "CONFIRMED", "basis": "기록"},
        ).status_code
        == 422
    )
    response = client.post(
        path,
        headers=auth,
        json={"competency_id": str(competency), "status": "CONFIRMED", "basis": "기록"},
    )
    assert response.status_code == 200, response.text
    data = client.get(
        f"/api/v1/tft/staffing-requests/{request}/recommendations", headers=headers
    ).json()["data"]["items"][0]
    assert data["competency_checks"][0]["status"] == "CONFIRMED"


def test_database_competency_recheck_preserves_history_and_replays_once(scenario):
    client, request, _, headers, database, role, _ = scenario
    rec = recommend(scenario).json()["data"]["id"]
    competency = uuid4()
    with psycopg.connect(database) as conn:
        conn.execute(
            "INSERT INTO tft.role_competencies (id,required_role_id,kind,description) VALUES (%s,%s,'REQUIRED','재검증 대상')",
            (competency, role),
        )
    path = f"/api/v1/tft/staffing-requests/{request}/recommendations/{rec}/competency"
    first = client.post(
        path,
        headers={**headers, "If-Match": '"2"', "Idempotency-Key": "first-check"},
        json={"competency_id": str(competency), "status": "CONFIRMED", "basis": "최초 확인"},
    )
    assert first.status_code == 200, first.text
    auth = {**headers, "If-Match": '"3"', "Idempotency-Key": "recheck"}
    payload = {"competency_id": str(competency), "status": "UNMET", "basis": "변경 근거"}
    second = client.post(path, headers=auth, json=payload)
    assert second.status_code == 200, second.text
    replay = client.post(path, headers=auth, json=payload)
    assert replay.status_code == 200
    assert replay.json() == second.json()
    checks = client.get(
        f"/api/v1/tft/staffing-requests/{request}/recommendations", headers=headers
    ).json()["data"]["items"][0]["competency_checks"]
    assert len(checks) == 2
    assert {record["basis"] for record in checks} == {"최초 확인", "변경 근거"}
    assert (
        next(record for record in checks if record["id"] == first.json()["data"]["id"])["status"]
        == "CONFIRMED"
    )
    assert (
        next(record for record in checks if record["id"] == second.json()["data"]["id"])["status"]
        == "UNMET"
    )
