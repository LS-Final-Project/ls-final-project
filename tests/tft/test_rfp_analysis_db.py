"""KAN-232 분석 접수 목록과 다음 처리 방식(전용 PostgreSQL). 행은 모두 합성이다."""

# ruff: noqa: F811

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg

from backend.shared.access import principal_header
from tests.tft.test_rfp_http_base_db import admin, app_dsn, chain  # noqa: F401
from tests.tft.test_rfp_routes_db import OUTSIDER, OWNER, client  # noqa: F401


def overview(client, chain, who=OWNER):
    return client.get(
        f"/api/v1/tft/projects/{chain['project']}/analysis", headers=principal_header(who)
    )


def command(conn, chain, operation, at, result):
    conn.execute(
        "INSERT INTO tft.rfp_commands (actor_user_id, operation, idempotency_key, project_id, "
        "request_hash, status_code, result, created_at) VALUES (%s,%s,%s,%s,%s,202,%s,%s)",
        (OWNER.user_id, operation, uuid4().hex, chain["project"], "0" * 64, json.dumps(result), at),
    )


def test_jobs_newest_first_and_regrade_when_every_spec_version_has_clauses(client, chain, admin):
    now = datetime.now(UTC)
    process, context = uuid4(), uuid4()
    with psycopg.connect(admin) as conn:
        command(
            conn, chain, "project.process", now - timedelta(minutes=5), {"job_id": str(process)}
        )
        command(conn, chain, "context.process", now, {"job_id": str(context)})
        # 처리 외 명령과 job_id 없는 결과는 나오지 않는다.
        command(conn, chain, "question.create", now, {"question_id": str(uuid4())})
    response = overview(client, chain)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert [(j["job_id"], j["kind"]) for j in data["jobs"]] == [
        (str(context), "rfp.context"),
        (str(process), "rfp.process"),
    ]
    assert data["jobs"][0]["actor_user_id"] == str(OWNER.user_id)
    # chain의 규격서 판본 1은 조항이 있으므로 다시 돌리면 모델 없이 재채점한다.
    assert data["process_mode"] == "regrade"


def test_new_latest_version_without_clauses_needs_extraction(client, chain, admin):
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.document_versions (id, document_id, version, mime, storage_key, "
            "sha256, size, edit_trace_support, data_origin, derivation_kind, known_at, "
            "uploaded_by) SELECT %s, document_id, 2, mime, 'k2', %s, size, edit_trace_support, "
            "data_origin, derivation_kind, known_at, uploaded_by FROM tft.document_versions "
            "WHERE id = %s",
            (uuid4(), "1" * 64, chain["docver"]),
        )
        conn.execute(
            "UPDATE tft.documents SET latest_version = 2 WHERE id = %s", (chain["document"],)
        )
    data = overview(client, chain).json()["data"]
    assert (data["jobs"], data["process_mode"]) == ([], "extract")


def test_no_spec_document_and_outsider(client, chain, admin):
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.documents SET kind = 'DATASHEET' WHERE id = %s", (chain["document"],)
        )
    assert overview(client, chain).json()["data"]["process_mode"] is None
    response = overview(client, chain, OUTSIDER)
    assert (response.status_code, response.json()["error"]["code"]) == (
        404,
        "TFT_PROJECT_NOT_FOUND",
    )
