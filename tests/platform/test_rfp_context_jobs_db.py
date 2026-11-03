"""Context-only admissions retain caller, idempotency and one-active-project semantics."""

# ruff: noqa: F811
from uuid import uuid4

import psycopg

from backend.agent.tft_worker import event_payload
from backend.api.platform.jobs.events import EventPayload
from tests.platform.test_tft_jobs_db import body, client  # noqa: F401


def test_context_job_is_admitted_once_with_bound_route(client, body, jobs_database):
    body["kind"] = "rfp.context"
    endpoint = "/internal/v1/platform/jobs/tft"
    headers = {"Authorization": "Bearer good-caller"}
    response = client.post(endpoint, json=body, headers=headers)
    assert response.status_code == 201, response.text
    job = response.json()["data"]
    repeated = client.post(endpoint, json=body, headers=headers)
    assert repeated.status_code == 200
    assert repeated.json()["data"]["job_id"] == job["job_id"]
    with psycopg.connect(jobs_database) as conn:
        row = conn.execute(
            "SELECT kind,route,input_version FROM runtime.jobs WHERE id=%s", (job["job_id"],)
        ).fetchone()
    assert row == (
        "rfp.context",
        "/api/v1/tft/projects/{project_id}/context/process",
        body["input_version"],
    )
    conflict = client.post(
        endpoint, json={**body, "idempotency_key": str(uuid4())}, headers=headers
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "TFT_JOB_IN_PROGRESS"


def test_context_progress_has_same_worker_and_sse_contract():
    for step in ("context_map", "context_link", "context_validate"):
        raw = {"step": step, "counts": {"requirements": 3, "proposals": 0}}
        assert EventPayload.model_validate(event_payload(raw)).step == step
