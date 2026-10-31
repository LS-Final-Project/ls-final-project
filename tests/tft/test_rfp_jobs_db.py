"""경로 11·30 처리 작업 접수(KAN-199·KAN-188)를 가짜 플랫폼과 전용 PostgreSQL(tft_app)로 확인한다.

행은 모두 합성이다. 프로젝트·문서·요구 준비는 tests/tft/test_rfp_http_base_db.py의 고정물을 쓴다.
"""

# ruff: noqa: F811

import json
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from backend.api.tft.main import create_app
from backend.api.tft.rfp.jobs import (
    TftProcessingSettings,
    configure_processing_jobs,
    input_version,
)
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

# 고정물을 가져와 쓴다(pytest가 이름으로 찾는다).
from tests.tft.test_rfp_http_base_db import SALES, admin, app_dsn, chain  # noqa: F401

PLATFORM = "https://platform.example.test"
OWNER = Principal(user_id=SALES, roles=frozenset({"sales"}))
REVIEWER = Principal(user_id=uuid4(), roles=frozenset({"dept_staff"}), dept="RND_FW")
OUTSIDER = Principal(user_id=uuid4(), roles=frozenset({"sales"}))


class FakePlatform:
    """받은 본문을 남기고 정한 답을 돌려준다."""

    def __init__(self):
        self.bodies = []
        self.answer = (201, {"data": {"job_id": str(uuid4()), "status": "QUEUED"}})
        self.during = None

    def handler(self, request):
        assert request.url.path == "/internal/v1/platform/jobs/tft"
        assert request.headers["authorization"] == "Bearer tft-token"
        self.bodies.append(json.loads(request.content))
        if self.during is not None:
            self.during()
        if isinstance(self.answer, Exception):
            raise self.answer
        status, payload = self.answer
        if "data" in payload:
            data = {**payload["data"]}
            data["status_url"] = f"/api/v1/platform/jobs/{data['job_id']}"
            payload = {"data": data}
        return httpx.Response(status, json=payload)


@pytest.fixture
def platform():
    return FakePlatform()


@pytest.fixture
def client(app_dsn, platform):
    app = create_app(Settings(_env_file=None, environment="test", database_url=app_dsn))
    configure_processing_jobs(
        app,
        TftProcessingSettings(_env_file=None, enabled=True, platform_url=PLATFORM),
        token=lambda audience: "tft-token",
        transport=httpx.MockTransport(platform.handler),
    )
    return TestClient(app)


def process(client, chain, who=OWNER, key=None, **body):
    return client.post(
        f"/api/v1/tft/projects/{chain['project']}/process",
        json=body,
        headers={**principal_header(who), "Idempotency-Key": key or uuid4().hex},
    )


def error(response):
    return response.status_code, response.json()["error"]["code"]


def test_owner_starts_processing_and_the_platform_gets_frozen_inputs(client, platform, chain):
    """담당 sales 접수는 202이고 플랫폼에 동결 입력을 보낸다. 재전송의 기존 작업 상태도 202로 옮긴다."""
    response = process(client, chain, key="process-0001", reason="재처리")
    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert data["status"] == "QUEUED" and data["status_url"].endswith(data["job_id"])
    [body] = platform.bodies
    assert body == {
        "actor_id": str(SALES),
        "kind": "rfp.process",
        "project_id": str(chain["project"]),
        "input_version": input_version([chain["docver"]]),
        "idempotency_key": "process-0001",
        "request_hash": body["request_hash"],
    }
    assert len(body["request_hash"]) == 64
    platform.answer = (200, {"data": {"job_id": data["job_id"], "status": "RUNNING"}})
    again = process(client, chain, key="process-0001", reason="재처리")
    assert (again.status_code, again.json()["data"]["status"]) == (202, "RUNNING")


def test_scope_permission_and_inputs_are_checked_before_the_platform(client, platform, chain):
    """범위 밖 404, 검토 부서는 처리 403·메모 해석 202, 요구가 있으면 사유 422, 다른 프로젝트 판본 422."""
    assert error(process(client, chain, who=OUTSIDER, reason="x")) == (
        404,
        "TFT_PROJECT_NOT_FOUND",
    )
    assert process(client, chain, who=REVIEWER, reason="x").status_code == 403
    assert error(process(client, chain)) == (422, "TFT_REASON_REQUIRED")
    foreign = process(
        client, chain, reason="x", document_version_ids=[str(chain["docver"]), str(uuid4())]
    )
    assert error(foreign) == (422, "VALIDATION_ERROR")
    assert platform.bodies == []
    annotate = client.post(
        f"/api/v1/tft/projects/{chain['project']}/annotations/process",
        json={
            "document_version_id": str(chain["docver"]),
            "base_document_version_id": str(chain["docver"]),
        },
        headers={**principal_header(REVIEWER), "Idempotency-Key": uuid4().hex},
    )
    assert annotate.status_code == 202, annotate.text
    assert platform.bodies[-1]["kind"] == "rfp.annotate"


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ((409, {"error": {"code": "TFT_JOB_IN_PROGRESS"}}), (409, "TFT_JOB_IN_PROGRESS")),
        ((409, {"error": {"code": "IDEMPOTENCY_CONFLICT"}}), (409, "IDEMPOTENCY_CONFLICT")),
        (
            (503, {"error": {"code": "TFT_PROCESSOR_NOT_CONNECTED"}}),
            (503, "TFT_PROCESSOR_NOT_CONNECTED"),
        ),
        ((422, {"error": {"code": "JOB_ACTOR_UNKNOWN"}}), (503, "DEPENDENCY_UNAVAILABLE")),
        ((500, {"error": {"code": "INTERNAL"}}), (503, "DEPENDENCY_UNAVAILABLE")),
        (httpx.ConnectError("down"), (503, "DEPENDENCY_UNAVAILABLE")),
    ],
)
def test_platform_refusals_are_passed_through_or_unavailable(
    client, platform, chain, answer, expected
):
    """플랫폼 409 두 코드·미연결 503은 그대로, 그 밖의 거절·연결 실패는 503 DEPENDENCY_UNAVAILABLE."""
    platform.answer = answer
    assert error(process(client, chain, reason="재처리")) == expected


def test_processing_switched_off_is_not_connected(app_dsn, platform, chain):
    """tft 설정이 꺼져 있으면 플랫폼을 부르지 않고 503 TFT_PROCESSOR_NOT_CONNECTED."""
    off = TestClient(
        create_app(
            Settings(_env_file=None, environment="test", database_url=app_dsn),
            TftProcessingSettings(_env_file=None, enabled=False),
        )
    )
    assert error(process(off, chain, reason="재처리")) == (503, "TFT_PROCESSOR_NOT_CONNECTED")
    assert platform.bodies == []


@pytest.fixture
def fresh(admin):
    """요구가 아직 없는 프로젝트와 문서 판본 하나."""
    ids = {k: uuid4() for k in ("project", "document", "docver")}
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.projects (id, source, source_instance, data_origin, derivation_kind, "
            "known_at, company_code, name, sales_user_id, responsible_user_id, review_departments) "
            "VALUES (%s,'test','kan199','synthetic','PROVIDED',now(),'HOST','합성',%s,%s,%s)",
            (ids["project"], SALES, uuid4(), ["RND_FW"]),
        )
        conn.execute(
            "INSERT INTO tft.documents (id, project_id, kind, title, data_origin, derivation_kind, "
            "known_at, created_by) VALUES (%s,%s,'RFP_SPEC','합성 규격서','synthetic','PROVIDED',now(),%s)",
            (ids["document"], ids["project"], uuid4()),
        )
        conn.execute(
            "INSERT INTO tft.document_versions (id, document_id, version, mime, storage_key, sha256, "
            "size, edit_trace_support, data_origin, derivation_kind, known_at, uploaded_by) "
            "VALUES (%s,%s,1,'text/markdown','k',%s,10,false,'synthetic','PROVIDED',now(),%s)",
            (ids["docver"], ids["document"], "0" * 64, uuid4()),
        )
    return ids


def test_a_resend_replays_even_after_requirements_appear(client, platform, fresh, admin):
    """첫 접수(사유 없음) 뒤 요구가 생겨도 같은 키 재전송은 사유 검사 없이 기존 작업 202, 다른 본문은 409."""
    first = process(client, fresh, key="process-fresh-1")
    assert first.status_code == 202, first.text
    job_id = first.json()["data"]["job_id"]
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.requirements (id, project_id, code, data_origin, derivation_kind, "
            "known_at) VALUES (%s,%s,'R-1','synthetic','DERIVED',now())",
            (uuid4(), fresh["project"]),
        )
    assert error(process(client, fresh)) == (422, "TFT_REASON_REQUIRED")
    platform.answer = (200, {"data": {"job_id": job_id, "status": "SUCCEEDED"}})
    again = process(client, fresh, key="process-fresh-1")
    assert (again.status_code, again.json()["data"]["status"]) == (202, "SUCCEEDED")
    assert platform.bodies[-1]["input_version"] == platform.bodies[0]["input_version"]
    calls = len(platform.bodies)
    changed = process(client, fresh, key="process-fresh-1", reason="본문이 다르다")
    assert error(changed) == (409, "IDEMPOTENCY_CONFLICT")
    assert len(platform.bodies) == calls


def test_an_unreadable_platform_answer_is_unavailable(client, platform, chain):
    """플랫폼 2xx 본문이 접수 결과로 읽히지 않으면 500이 아니라 503 DEPENDENCY_UNAVAILABLE."""
    platform.answer = (201, {"data": {"job_id": str(uuid4()), "status": "SOMETHING"}})
    assert error(process(client, chain, reason="재처리")) == (503, "DEPENDENCY_UNAVAILABLE")


def detail(client, chain):
    response = client.get(
        f"/api/v1/tft/projects/{chain['project']}", headers=principal_header(OWNER)
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_accepting_processing_shows_on_the_project(client, platform, chain):
    """접수하면 ANALYZING이고 최신 작업 ID가 보인다. 상태는 플랫폼 소유라 비어 있다."""
    before = detail(client, chain)
    assert before["output_status"] == "INTAKE" and before["latest_job"] is None
    first = process(client, chain, reason="재처리").json()["data"]["job_id"]
    shown = detail(client, chain)
    assert shown["output_status"] == "ANALYZING"
    assert shown["latest_job"] == {"job_id": first, "status": None, "step": None}
    # 프로젝트 판본은 사람이 고친 것이 아니라 오르지 않는다.
    assert shown["version"] == before["version"]
    second = str(uuid4())
    platform.answer = (201, {"data": {"job_id": second, "status": "QUEUED"}})
    process(client, chain, reason="재처리")
    assert detail(client, chain)["latest_job"]["job_id"] == second


def test_a_refused_job_leaves_the_project_as_it_was(client, platform, chain):
    """플랫폼이 거절하면 상태도 작업도 바뀌지 않는다."""
    platform.answer = (409, {"error": {"code": "TFT_JOB_IN_PROGRESS"}})
    assert process(client, chain, reason="재처리").status_code == 409
    shown = detail(client, chain)
    assert shown["output_status"] == "INTAKE" and shown["latest_job"] is None


def test_a_job_that_fails_before_the_answer_is_not_overwritten(client, platform, chain, admin):
    """worker가 접수 응답보다 먼저 실패해 되돌려도 다시 ANALYZING이 되지 않는다."""
    seen = []

    def fast_failure():
        with psycopg.connect(admin) as conn:
            seen.append(
                conn.execute(
                    "UPDATE tft.projects SET output_status='INTAKE' WHERE id=%s "
                    "AND output_status='ANALYZING' RETURNING id",
                    (chain["project"],),
                ).fetchone()
            )

    platform.during = fast_failure
    assert process(client, chain, reason="재처리").status_code == 202
    assert seen == [(chain["project"],)]
    assert detail(client, chain)["output_status"] == "INTAKE"


@pytest.mark.parametrize(
    "answer",
    [
        (409, {"error": {"code": "TFT_JOB_IN_PROGRESS"}}),
        (500, {"error": {"code": "BOOM"}}),
        httpx.ConnectError("synthetic"),
    ],
    ids=["refused", "server-error", "unreachable"],
)
def test_a_job_that_was_not_accepted_restores_the_previous_status(
    client, platform, chain, admin, answer
):
    """접수가 안 되면 이전 값으로 돌아간다. 검토본 상태도 잃지 않는다."""
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.projects SET output_status='REVIEW_DRAFT' WHERE id=%s", (chain["project"],)
        )
    platform.answer = answer
    assert process(client, chain, reason="재처리").status_code >= 400
    assert detail(client, chain)["output_status"] == "REVIEW_DRAFT"


def test_a_resend_of_an_accepted_job_does_not_mark_again(client, platform, chain, admin):
    """같은 키 재전송은 표시를 다시 쓰지 않는다(그사이 끝난 작업을 되살리지 않는다)."""
    assert process(client, chain, key="resend-0001", reason="재처리").status_code == 202
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.projects SET output_status='INTAKE' WHERE id=%s", (chain["project"],)
        )
    assert process(client, chain, key="resend-0001", reason="재처리").status_code == 202
    assert detail(client, chain)["output_status"] == "INTAKE"


def test_an_unexpected_failure_while_reading_the_answer_restores_the_status(
    client, platform, chain, admin
):
    """처리하지 않는 예외여도 접수를 확인하지 못했으면 분석 중으로 남기지 않는다."""
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.projects SET output_status='REVIEW_DRAFT' WHERE id=%s", (chain["project"],)
        )

    async def broken(path, body):
        raise RuntimeError("synthetic")

    client.app.state.processing_jobs.post = broken
    assert process(client, chain, reason="재처리").status_code == 500
    assert detail(client, chain)["output_status"] == "REVIEW_DRAFT"


def test_explicit_draft_rewrite_version_is_authorized_idempotent_and_stale_safe(
    client, app_dsn, chain, platform
):
    from tests.tft.test_rfp_draft_routes_db import generate

    generate(app_dsn, chain, uuid4())
    url = f"/api/v1/tft/projects/{chain['project']}/draft/rewrite"
    key = uuid4().hex
    headers = {**principal_header(OWNER), "Idempotency-Key": key}
    accepted = client.post(url, json={"version": 1}, headers=headers)
    assert accepted.status_code == 202, accepted.text
    assert platform.bodies[-1]["kind"] == "rfp.draft_rewrite"
    replay = client.post(url, json={"version": 1}, headers=headers)
    assert replay.status_code == 202 and replay.json()["data"] == accepted.json()["data"]
    calls = len(platform.bodies)
    changed = client.post(url, json={"version": 2}, headers=headers)
    assert changed.status_code == 409 and len(platform.bodies) == calls
    stale = client.post(
        url,
        json={"version": 2},
        headers={**principal_header(OWNER), "Idempotency-Key": uuid4().hex},
    )
    assert stale.status_code == 409 and len(platform.bodies) == calls
    for who, status in ((REVIEWER, 403), (OUTSIDER, 404)):
        denied = client.post(
            url,
            json={"version": 1},
            headers={**principal_header(who), "Idempotency-Key": uuid4().hex},
        )
        assert denied.status_code == status
    assert len(platform.bodies) == calls
