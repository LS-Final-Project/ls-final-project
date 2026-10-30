"""실제 전용 PostgreSQL에서 초안 판본·인가·검토본·예약·DOCX worker를 확인한다. 로컬 DB 없으면 CI 위임."""

# ruff: noqa: F811

import asyncio
import hashlib
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from backend.agent.processor_registry import TftJob
from backend.api.tft.main import create_app
from backend.api.tft.rfp import draft_exports, drafts
from backend.api.tft.rfp.storage import LocalDocumentStore
from backend.engine.rfp.draft import DISCLAIMER
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings
from tests.tft.test_rfp_http_base_db import SALES, admin, app_dsn, chain  # noqa: F401
from tests.tft.test_worker_processor_db import worker_dsn  # noqa: F401

OWNER = Principal(user_id=SALES, roles=frozenset({"sales"}))
OUTSIDER = Principal(user_id=uuid4(), roles=frozenset({"sales"}))
REVIEWER = Principal(user_id=uuid4(), roles=frozenset({"dept_staff"}), dept="TS_TRF")


@pytest.fixture
def client(app_dsn, monkeypatch, tmp_path):
    monkeypatch.setenv("TFT_DOCUMENT_ROOT", str(tmp_path))
    monkeypatch.setenv("TFT_DOCUMENT_BUCKET", "")
    with TestClient(
        create_app(Settings(_env_file=None, environment="test", database_url=app_dsn))
    ) as result:
        yield result


def generate(app_dsn, chain, job_id):
    async def work():
        async with await psycopg.AsyncConnection.connect(app_dsn, row_factory=dict_row) as conn:
            return await drafts.generate(conn, chain["project"], SALES, job_id)

    return asyncio.run(work())


def call(client, method, chain, suffix="", body=None, key=None, user=OWNER):
    headers = principal_header(user)
    if key:
        headers["Idempotency-Key"] = key
    return client.request(
        method, f"/api/v1/tft/projects/{chain['project']}/draft{suffix}", json=body, headers=headers
    )


def first_sentence(data):
    return next(row for part in data["sections"] for row in part["sentences"])


def test_generated_job_retry_keeps_human_head_and_history(client, app_dsn, chain, admin):
    job_id = uuid4()
    first = generate(app_dsn, chain, job_id)
    old = call(client, "GET", chain).json()["data"]
    row = first_sentence(old)
    body = {
        "version": 1,
        "edits": [
            {
                "sentence_id": row["sentence_id"],
                "text": "사람이 수정한 검토 문구",
                "reason": "원문 확인",
                "evidence": row["evidence"],
            }
        ],
    }
    edited = call(client, "PATCH", chain, body=body)
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"]["version"] == 2
    replay = generate(app_dsn, chain, job_id)
    assert replay.version == first.version == 1
    assert call(client, "GET", chain).json()["data"] == edited.json()["data"]
    assert call(client, "GET", chain, suffix="?version=1").json()["data"] == old
    assert call(client, "PATCH", chain, body=body).status_code == 409
    with psycopg.connect(admin) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.draft_versions WHERE project_id=%s", (chain["project"],)
            ).fetchone()[0]
            == 2
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.draft_versions WHERE source_job_id=%s", (job_id,)
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.rfp_audit_events WHERE project_id=%s AND action='draft.edit'",
                (chain["project"],),
            ).fetchone()[0]
            == 1
        )


def test_scope_readonly_and_review_snapshot_are_enforced(client, app_dsn, chain, admin):
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.requirement_versions SET needs_review=true, unresolved=%s, "
            "next_owner_field='TECH' WHERE requirement_id=%s",
            ('["원문 확인"]', chain["requirement"]),
        )
    generate(app_dsn, chain, uuid4())
    read = call(client, "GET", chain)
    row = first_sentence(read.json()["data"])
    assert row["needs_rereview"] is True
    assert call(client, "GET", chain, user=OUTSIDER).status_code == 404
    assert call(client, "GET", chain, user=REVIEWER).status_code == 200
    payload = {
        "version": 1,
        "edits": [
            {
                "sentence_id": row["sentence_id"],
                "text": "사람 검토 문구",
                "reason": "문구 수정",
                "evidence": row["evidence"],
            }
        ],
    }
    assert call(client, "PATCH", chain, body=payload, user=REVIEWER).status_code == 403
    changed = call(client, "PATCH", chain, body=payload)
    assert changed.status_code == 200, changed.text
    changed_row = next(
        r
        for p in changed.json()["data"]["sections"]
        for r in p["sentences"]
        if r["sentence_id"] == row["sentence_id"]
    )
    assert changed_row["needs_rereview"] and changed_row["conditions"] == row["conditions"]
    blocked = call(client, "POST", chain, "/review-versions", {"version": 2}, "review-needs-check")
    assert blocked.status_code == 422 and blocked.json()["error"]["code"] == "TFT_DRAFT_INCOMPLETE"


def ready(app_dsn, admin, chain):
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.requirement_versions SET unresolved='[\"기술 조건 확인\"]',next_owner_field='TECH' WHERE requirement_id=%s",
            (chain["requirement"],),
        )
    generate(app_dsn, chain, uuid4())


def test_review_unresolved_with_next_action_is_conditional_and_idempotent(
    client, app_dsn, chain, admin
):
    ready(app_dsn, admin, chain)
    body = {"version": 1, "note": "사람이 확인한 검토본"}
    first = call(client, "POST", chain, "/review-versions", body, "review-snapshot-key")
    assert first.status_code == 201, first.text
    assert first.json()["data"]["judgement_status"] == "CONDITION_CHECK"
    assert first.json()["data"]["output_status"] == "REVIEW_DRAFT"
    replay = call(client, "POST", chain, "/review-versions", body, "review-snapshot-key")
    assert replay.status_code == 201 and replay.json()["data"] == first.json()["data"]
    assert (
        call(
            client, "POST", chain, "/review-versions", {"version": 2}, "review-snapshot-key"
        ).status_code
        == 409
    )


class Jobs:
    def __init__(self):
        self.calls = []
        self.id = uuid4()
        self.before_response = None

    async def post(self, path, body):
        self.calls.append((path, body))
        if self.before_response is not None:
            await self.before_response(body)
        return {
            "job_id": str(self.id),
            "status": "SUCCEEDED" if self.before_response is not None else "QUEUED",
            "status_url": f"/api/v1/platform/jobs/{self.id}",
        }


@pytest.mark.parametrize("finish_before_receipt", [False, True])
def test_export_id_replay_docx_file_scope_and_integrity(
    client, app_dsn, chain, admin, tmp_path, worker_dsn, finish_before_receipt
):
    ready(app_dsn, admin, chain)
    saved = call(client, "POST", chain, "/review-versions", {"version": 1}, "review-for-export")
    assert saved.status_code == 201, saved.text
    jobs = Jobs()
    store = LocalDocumentStore(tmp_path)

    async def early(body):
        early_job = TftJob(
            job_id=jobs.id,
            kind="rfp.export",
            project_id=chain["project"],
            input_version=body["input_version"],
            actor_id=SALES,
        )
        async with await psycopg.AsyncConnection.connect(worker_dsn, row_factory=dict_row) as conn:
            await draft_exports.process(conn, early_job, lambda event: None, store)

    if finish_before_receipt:
        jobs.before_response = early
    client.app.state.processing_jobs = jobs
    payload = {"review_version": 1, "format": "DOCX"}
    received = call(client, "POST", chain, "/exports", payload, "export-stable-key")
    assert received.status_code == 202, received.text
    data = received.json()["data"]
    repeat = call(client, "POST", chain, "/exports", payload, "export-stable-key")
    assert repeat.json()["data"]["export_id"] == data["export_id"]
    assert data["status"] == ("SUCCEEDED" if finish_before_receipt else "QUEUED")
    file_path = f"/exports/{data['export_id']}/file"
    assert call(client, "GET", chain, file_path).status_code == (
        200 if finish_before_receipt else 409
    )
    job = TftJob(
        job_id=jobs.id,
        kind="rfp.export",
        project_id=chain["project"],
        input_version=jobs.calls[0][1]["input_version"],
        actor_id=SALES,
    )
    store = LocalDocumentStore(tmp_path)

    async def execute():
        async with await psycopg.AsyncConnection.connect(worker_dsn, row_factory=dict_row) as conn:
            return await draft_exports.process(conn, job, lambda event: None, store)

    asyncio.run(execute())
    asyncio.run(execute())
    response = call(client, "GET", chain, file_path)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == draft_exports.DOCX_MIME
    assert response.headers["cache-control"] == "no-store"
    assert f"{data['export_id']}.docx" in response.headers["content-disposition"]
    assert zipfile.is_zipfile(io.BytesIO(response.content))
    with zipfile.ZipFile(io.BytesIO(response.content)) as package:
        assert DISCLAIMER in package.read("word/document.xml").decode()
    assert call(client, "GET", chain, file_path, user=OUTSIDER).status_code == 404
    assert (
        call(
            client, "POST", chain, "/exports", payload, "export-other-readonly", user=REVIEWER
        ).status_code
        == 403
    )
    with psycopg.connect(admin, row_factory=dict_row) as conn:
        row = conn.execute("SELECT * FROM tft.exports WHERE id=%s", (data["export_id"],)).fetchone()
        assert row["sha256"] == hashlib.sha256(response.content).hexdigest()
        receipt = conn.execute(
            "SELECT result FROM tft.rfp_commands WHERE project_id=%s AND operation='export.create'",
            (chain["project"],),
        ).fetchone()["result"]
        assert receipt["export_id"] == data["export_id"]
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.rfp_audit_events WHERE project_id=%s AND action='export.create' AND entity_id=%s",
                (chain["project"], data["export_id"]),
            ).fetchone()["count"]
            == 1
        )
        assert row["size"] == len(response.content) and row["completed_at"]
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.rfp_audit_events WHERE project_id=%s AND action='export.complete'",
                (chain["project"],),
            ).fetchone()["count"]
            == 1
        )
    (tmp_path / row["storage_key"]).write_bytes(b"corrupted")
    assert call(client, "GET", chain, file_path).status_code == 503


def test_pdf_has_no_export_reservation_or_job_request(client, chain, admin):
    jobs = Jobs()
    client.app.state.processing_jobs = jobs
    response = call(
        client, "POST", chain, "/exports", {"review_version": 1, "format": "PDF"}, "export-pdf-key"
    )
    assert (
        response.status_code == 422
        and response.json()["error"]["code"] == "TFT_EXPORT_FORMAT_UNAVAILABLE"
    )
    assert jobs.calls == []
    with psycopg.connect(admin) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.exports WHERE project_id=%s", (chain["project"],)
            ).fetchone()[0]
            == 0
        )


def test_concurrent_edits_publish_one_version(client, app_dsn, chain, admin):
    generate(app_dsn, chain, uuid4())
    current = call(client, "GET", chain).json()["data"]
    row = first_sentence(current)
    payload = {
        "version": 1,
        "edits": [
            {
                "sentence_id": row["sentence_id"],
                "text": "동시 수정",
                "reason": "원문 확인",
                "evidence": row["evidence"],
            }
        ],
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: call(client, "PATCH", chain, body=payload), range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    with psycopg.connect(admin) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.draft_versions WHERE project_id=%s", (chain["project"],)
            ).fetchone()[0]
            == 2
        )


def test_later_requirement_review_flag_is_visible_and_blocks_old_draft_review(
    client, app_dsn, chain, admin
):
    generate(app_dsn, chain, uuid4())
    old = call(client, "GET", chain).json()["data"]
    assert first_sentence(old)["needs_rereview"] is False
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.requirement_versions SET needs_review=true,unresolved=%s,next_owner_field='TECH' WHERE requirement_id=%s",
            ('["원문 확인"]', chain["requirement"]),
        )
    current = call(client, "GET", chain).json()["data"]
    assert first_sentence(current)["needs_rereview"] and first_sentence(current)["unconfirmed"]
    blocked = call(client, "POST", chain, "/review-versions", {"version": 1}, "later-needs-review")
    assert blocked.status_code == 422 and blocked.json()["error"]["code"] == "TFT_DRAFT_INCOMPLETE"
    assert any(detail["field"] == "sentence_id" for detail in blocked.json()["error"]["details"])
