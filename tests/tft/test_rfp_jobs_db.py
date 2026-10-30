"""경로 11·30 처리 작업 접수(KAN-199·KAN-188)를 가짜 플랫폼과 전용 PostgreSQL(tft_app)로 확인한다.

행은 모두 합성이다. 프로젝트·문서·요구 준비는 tests/tft/test_rfp_http_base_db.py의 고정물을 쓴다.
"""

# ruff: noqa: F811

import json
from uuid import uuid4

import httpx
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

    def handler(self, request):
        assert request.url.path == "/internal/v1/platform/jobs/tft"
        assert request.headers["authorization"] == "Bearer tft-token"
        self.bodies.append(json.loads(request.content))
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
