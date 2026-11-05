"""KAN-224: 회신 값 AI 읽기는 API가 아니라 worker 작업(rfp.reply_read)이 한다. 가짜 플랫폼·가짜 공급자로 확인한다."""

# ruff: noqa: F811

import asyncio
import json
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from backend.agent.processor_registry import TftJob
from backend.api.tft.main import create_app
from backend.api.tft.rfp import reply_read
from backend.api.tft.rfp.jobs import TftProcessingSettings, configure_processing_jobs
from backend.api.tft.rfp.routes_questions import TftReplySettings
from backend.shared.ai import Generation, Usage
from backend.shared.settings import Settings
from tests.tft.test_rfp_http_base_db import admin, app_dsn, chain  # noqa: F401
from tests.tft.test_rfp_jobs_db import FakePlatform
from tests.tft.test_rfp_routes_db import OWNER, answer, ask, call, gap  # noqa: F401
from tests.tft.test_worker_processor_db import worker_dsn  # noqa: F401

PLATFORM = "https://platform.example.test"
VAGUE = "BIL은 125 kV가 아니라 150 kV로 적용 바랍니다."


def make_client(app_dsn, platform, enabled=True):
    app = create_app(
        Settings(_env_file=None, environment="test", database_url=app_dsn),
        reply=TftReplySettings(_env_file=None, enabled=enabled),
    )
    configure_processing_jobs(
        app,
        TftProcessingSettings(_env_file=None, enabled=True, platform_url=PLATFORM),
        token=lambda audience: "tft-token",
        transport=httpx.MockTransport(platform.handler),
    )
    return TestClient(app)


def customer_reply(client, gap, text):
    asked = ask(client, gap, issue_type="EMPTY_VALUE", target="CUSTOMER", target_dept=None)
    return answer(client, asked.json()["data"], OWNER, reply_text=text).json()["data"]


def ai_answer(value, quote, unit="kV"):
    text = json.dumps({"value": value, "unit": unit, "quote": quote, "conditions": []})
    return lambda _prompt: Generation(
        text=text, usage=Usage(), model_version=None, finish_reasons=[]
    )


def run_worker(worker_dsn, project_id, body, provider):
    job = TftJob(
        job_id=uuid4(),
        kind=reply_read.KIND,
        project_id=UUID(project_id),
        input_version=body["input_version"],
        actor_id=UUID(body["actor_id"]),
    )

    async def go():
        async with await psycopg.AsyncConnection.connect(
            worker_dsn, row_factory=dict_row, autocommit=True
        ) as conn:
            return await reply_read.process(conn, job, provider)

    return asyncio.run(go())


def candidate(client, gap, question_id):
    listed = call(client, "GET", f"/projects/{gap['project']}/questions", OWNER).json()["data"]
    (row,) = [q for q in listed["items"] if q["question_id"] == question_id]
    return row["value_candidate"]


def test_rule_reads_without_a_worker_job(app_dsn, gap):
    platform = FakePlatform()
    client = make_client(app_dsn, platform)
    data = customer_reply(client, gap, "BIL은 125 kV로 적용 바랍니다.")
    assert data["value_candidate"]["method"] == "rule"
    assert data["value_candidate"]["ai"] is None
    assert platform.bodies == []


def test_unread_reply_becomes_a_worker_job_and_the_worker_stores_a_grounded_value(
    app_dsn, gap, worker_dsn
):
    platform = FakePlatform()
    client = make_client(app_dsn, platform)
    data = customer_reply(client, gap, VAGUE)
    assert data["value_candidate"]["method"] == "none"
    assert data["value_candidate"]["reason"] == "CORRECTION"
    assert data["value_candidate"]["ai"] == "QUEUED"
    (body,) = platform.bodies
    assert body["kind"] == "rfp.reply_read" and body["project_id"] == str(gap["project"])
    # API 프로세스는 모델을 부르지 않는다. worker가 가짜 공급자로 읽는다.
    run_worker(worker_dsn, str(gap["project"]), body, ai_answer("150", VAGUE))
    got = candidate(client, gap, data["question_id"])
    assert got["method"] == "ai" and got["value"] == "150" and got["ai"] == "DONE"
    assert got["quote"] == VAGUE
    # 끝난 작업을 다시 돌려도 모델을 부르지 않는다.
    run_worker(worker_dsn, str(gap["project"]), body, lambda _p: pytest.fail("called again"))


def test_ungrounded_ai_answer_is_none_and_can_be_read_again(app_dsn, gap, worker_dsn):
    platform = FakePlatform()
    client = make_client(app_dsn, platform)
    data = customer_reply(client, gap, VAGUE)
    (body,) = platform.bodies
    run_worker(worker_dsn, str(gap["project"]), body, ai_answer("200", "회신에 없는 문장 200 kV"))
    got = candidate(client, gap, data["question_id"])
    assert (got["method"], got["reason"], got["ai"]) == ("none", "AI_UNGROUNDED", "DONE")
    path = f"/questions/{data['question_id']}/reply/read"
    again = call(client, "POST", path, OWNER, None, uuid4().hex)
    assert again.status_code == 200, again.text
    assert again.json()["data"]["value_candidate"]["ai"] == "QUEUED"
    assert (
        len(platform.bodies) == 2 and platform.bodies[1]["input_version"] != body["input_version"]
    )


def test_refused_admission_is_unavailable_and_model_failure_is_failed(app_dsn, gap, worker_dsn):
    platform = FakePlatform()
    platform.answer = (409, {"error": {"code": "TFT_JOB_IN_PROGRESS", "message": "busy"}})
    client = make_client(app_dsn, platform)
    data = customer_reply(client, gap, VAGUE)
    assert data["value_candidate"]["ai"] == "UNAVAILABLE"
    platform.answer = (201, {"data": {"job_id": str(uuid4()), "status": "QUEUED"}})
    path = f"/questions/{data['question_id']}/reply/read"
    assert call(client, "POST", path, OWNER, None, uuid4().hex).status_code == 200

    def broken(_prompt):
        raise RuntimeError("model down")

    run_worker(worker_dsn, str(gap["project"]), platform.bodies[-1], broken)
    got = candidate(client, gap, data["question_id"])
    assert (got["method"], got["reason"], got["ai"]) == ("none", "AI_FAILED", "FAILED")


def test_disabled_ai_does_not_admit_a_job(app_dsn, gap):
    platform = FakePlatform()
    client = make_client(app_dsn, platform, enabled=False)
    data = customer_reply(client, gap, VAGUE)
    assert data["value_candidate"]["method"] == "none" and data["value_candidate"]["ai"] is None
    assert platform.bodies == []
    path = f"/questions/{data['question_id']}/reply/read"
    response = call(client, "POST", path, OWNER, None, uuid4().hex)
    assert response.status_code == 503
