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
from psycopg.types.json import Jsonb

from backend.agent.processor_registry import TftJob
from backend.api.tft.main import create_app
from backend.api.tft.rfp import draft_exports, drafts, repository
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
        # 명령 키는 actor/operation 범위다. 매개변수화된 다른 프로젝트 사례와 충돌하지 않게 한다.
        headers["Idempotency-Key"] = f"{key}:{chain['project']}"
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


def add_clause_question(app_dsn, admin, chain, clause_id):
    """요구가 뽑히지 않은 조항과 그 조항의 미정 질문을 넣는다."""
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.clauses (id, document_version_id, clause_no, depth, ordinal, text, "
            "data_origin, derivation_kind, known_at) "
            "SELECT %s, document_version_id, '9.9.Z', depth, ordinal + 1000, '용량: 미정', "
            "data_origin, derivation_kind, known_at FROM tft.clauses WHERE id = %s",
            (clause_id, chain["clause"]),
        )

    async def work():
        async with await psycopg.AsyncConnection.connect(app_dsn, row_factory=dict_row) as conn:
            await repository.insert_question_draft(
                conn,
                project_id=chain["project"],
                requirement_id=None,
                clause={"id": clause_id, "page": None},
                issue_type="EMPTY_VALUE",
                question_text="확정 값을 알려 주시겠습니까?",
                internal_assumption={
                    "value": "미정",
                    "basis_standard": None,
                    "basis_clause": "9.9.Z",
                    "approver_user_id": None,
                    "shown_in_draft": False,
                },
                draft_key=hashlib.sha256(str(clause_id).encode()).hexdigest(),
                data_origin="synthetic",
                actor=SALES,
            )
            await conn.commit()

    asyncio.run(work())


def test_a_question_on_a_clause_without_requirements_does_not_fail_the_draft(
    client, app_dsn, chain, admin
):
    clause_id = uuid4()
    add_clause_question(app_dsn, admin, chain, clause_id)
    generate(app_dsn, chain, uuid4())
    data = call(client, "GET", chain).json()["data"]
    evidence = {
        (e["type"], e["id"])
        for part in data["sections"]
        for row in part["sentences"]
        for e in row["evidence"]
    }
    assert ("clause", str(clause_id)) in evidence


def test_clause_ids_stay_inside_the_project(app_dsn, chain, admin):
    clause_id = uuid4()
    add_clause_question(app_dsn, admin, chain, clause_id)

    async def work(project):
        async with await psycopg.AsyncConnection.connect(app_dsn, row_factory=dict_row) as conn:
            return await repository.project_clause_ids(conn, project)

    assert asyncio.run(work(chain["project"])) == {chain["clause"], clause_id}
    assert asyncio.run(work(uuid4())) == set()


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


@pytest.mark.parametrize("change", ["requirement", "mark"])
def test_review_export_snapshot_survives_live_changes_and_put_before_db_retry(
    client, app_dsn, chain, admin, tmp_path, worker_dsn, change
):
    """불변 review의 파일은 현재 검토 표시와 무관하고, 저장 객체만 남은 재시도도 완료한다."""
    from backend.api.tft.rfp import draft_repository as db

    ready(app_dsn, admin, chain)
    saved = call(client, "POST", chain, "/review-versions", {"version": 1}, "snapshot-review")
    assert saved.status_code == 201, saved.text
    jobs = Jobs()
    client.app.state.processing_jobs = jobs
    accepted = call(
        client,
        "POST",
        chain,
        "/exports",
        {"review_version": 1, "format": "DOCX"},
        "snapshot-export",
    )
    assert accepted.status_code == 202, accepted.text
    export_id = accepted.json()["data"]["export_id"]
    job = TftJob(
        job_id=jobs.id,
        kind="rfp.export",
        project_id=chain["project"],
        input_version=jobs.calls[0][1]["input_version"],
        actor_id=SALES,
    )
    store = LocalDocumentStore(tmp_path)

    async def frozen():
        async with await psycopg.AsyncConnection.connect(worker_dsn, row_factory=dict_row) as conn:
            return await db.load_snapshot(conn, chain["project"], 1)

    async def head():
        async with await psycopg.AsyncConnection.connect(app_dsn, row_factory=dict_row) as conn:
            return (
                await db.evidence_labels(conn, chain["project"]),
                await db.project_title(conn, chain["project"]),
            )

    original = asyncio.run(frozen())
    names, title = asyncio.run(head())
    expected = draft_exports.render_docx(original, 1, names, title)
    events = []

    def crash_after_put(event):
        events.append(event)
        if len(events) == 2:
            raise RuntimeError("crash after file before DB")

    async def execute(emit):
        async with await psycopg.AsyncConnection.connect(worker_dsn, row_factory=dict_row) as conn:
            return await draft_exports.process(conn, job, emit, store)

    with pytest.raises(RuntimeError, match="crash after file"):
        asyncio.run(execute(crash_after_put))
    path = tmp_path / f"rfp/{chain['project']}/exports/{export_id}.docx"
    assert path.read_bytes() == expected
    with psycopg.connect(admin) as conn:
        # 재적용 뒤에도 worker는 이름을 읽지 못하고, 접수 때 고정한 머리로 같은 파일을 만든다.
        assert conn.execute(
            "SELECT has_column_privilege('tft_worker','tft.projects','name','SELECT')"
        ).fetchone() == (False,)
        conn.execute(
            "UPDATE tft.projects SET name='바뀐 프로젝트 이름' WHERE id=%s", (chain["project"],)
        )
        assert (
            conn.execute(
                "SELECT completed_at FROM tft.exports WHERE id=%s", (export_id,)
            ).fetchone()[0]
            is None
        )
        if change == "requirement":
            conn.execute(
                "UPDATE tft.requirement_versions SET needs_review=true WHERE requirement_id=%s",
                (chain["requirement"],),
            )
        else:
            proposal = uuid4()
            conn.execute(
                "INSERT INTO tft.change_proposals (id,project_id,requirement_id,base_requirement_version,origin,current,proposed,rationale,proposer_kind,proposer_user_id,data_origin,derivation_kind,known_at) "
                "VALUES (%s,%s,%s,1,'web','{}','{}','후속 검토','user',%s,'synthetic','PROVIDED',now())",
                (proposal, chain["project"], chain["requirement"], SALES),
            )
            conn.execute(
                "INSERT INTO tft.rereview_marks (id,project_id,target_kind,target_id,cause_proposal_id) VALUES (%s,%s,'draft_sentence',%s,%s)",
                (
                    uuid4(),
                    chain["project"],
                    original.sections[0].sentences[0].sentence_id,
                    proposal,
                ),
            )
    current = call(client, "GET", chain, suffix="?version=1").json()["data"]
    assert first_sentence(current)["needs_rereview"] is True
    blocked = call(
        client, "POST", chain, "/review-versions", {"version": 1}, "current-review-blocked"
    )
    assert blocked.status_code == 422 and blocked.json()["error"]["code"] == "TFT_DRAFT_INCOMPLETE"
    assert draft_exports.render_docx(asyncio.run(frozen()), 1, names, title) == expected
    asyncio.run(execute(lambda event: None))
    response = call(client, "GET", chain, f"/exports/{export_id}/file")
    assert response.status_code == 200 and response.content == expected
    assert path.read_bytes() == expected
    with psycopg.connect(admin) as conn:
        completed_at, digest = conn.execute(
            "SELECT completed_at,sha256 FROM tft.exports WHERE id=%s", (export_id,)
        ).fetchone()
        assert completed_at is not None and digest == hashlib.sha256(expected).hexdigest()


@pytest.mark.parametrize("change", ["requirement", "mark"])
def test_edit_does_not_persist_live_review_flags(client, app_dsn, chain, admin, change):
    """일시적인 검토 표시 중 다른 문장을 편집해도 해소 후 정적 flags로 고착되지 않는다."""
    from backend.api.tft.rfp import draft_repository as db

    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.requirement_versions SET status='EVIDENCE_FOUND',unresolved='[]',next_owner_field=NULL WHERE requirement_id=%s",
            (chain["requirement"],),
        )
    generate(app_dsn, chain, uuid4())
    goal = first_sentence(call(client, "GET", chain).json()["data"])
    added = call(
        client,
        "PATCH",
        chain,
        body={
            "version": 1,
            "edits": [
                {
                    "sentence_id": None,
                    "section": "QUESTIONS_NEXT_ACTIONS",
                    "text": "별도 검토 행동",
                    "reason": "검토 추가",
                    "evidence": goal["evidence"],
                }
            ],
        },
    )
    assert added.status_code == 200, added.text

    async def frozen(version):
        async with await psycopg.AsyncConnection.connect(app_dsn, row_factory=dict_row) as conn:
            return await db.load_snapshot(conn, chain["project"], version)

    original = asyncio.run(frozen(2))
    sentences = [row for part in original.sections for row in part.sentences]
    tracked = next(
        row
        for row in sentences
        if not row.needs_rereview
        and not row.unconfirmed
        and any(e.type == "requirement" for e in row.evidence)
    )
    other = next(row for row in sentences if row.sentence_id != tracked.sentence_id)
    mark = uuid4()
    with psycopg.connect(admin) as conn:
        if change == "requirement":
            conn.execute(
                "UPDATE tft.requirement_versions SET needs_review=true WHERE requirement_id=%s",
                (chain["requirement"],),
            )
        else:
            proposal = uuid4()
            conn.execute(
                "INSERT INTO tft.change_proposals (id,project_id,requirement_id,base_requirement_version,origin,current,proposed,rationale,proposer_kind,proposer_user_id,data_origin,derivation_kind,known_at) "
                "VALUES (%s,%s,%s,1,'web','{}','{}','후속 검토','user',%s,'synthetic','PROVIDED',now())",
                (proposal, chain["project"], chain["requirement"], SALES),
            )
            conn.execute(
                "INSERT INTO tft.rereview_marks (id,project_id,target_kind,target_id,cause_proposal_id) VALUES (%s,%s,'draft_sentence',%s,%s)",
                (mark, chain["project"], tracked.sentence_id, proposal),
            )
    body = {
        "version": 2,
        "edits": [
            {
                "sentence_id": str(other.sentence_id),
                "text": "다른 문장의 사람 수정",
                "reason": "문구 확인",
                "evidence": [e.model_dump() for e in other.evidence],
            }
        ],
    }
    edited = call(client, "PATCH", chain, body=body)
    assert edited.status_code == 200, edited.text
    displayed = {
        row["sentence_id"]: row
        for part in edited.json()["data"]["sections"]
        for row in part["sentences"]
    }
    assert displayed[str(tracked.sentence_id)]["needs_rereview"] is True
    assert displayed[str(tracked.sentence_id)]["unconfirmed"] is True
    snapshot3 = asyncio.run(frozen(3))
    stored = {row.sentence_id: row for part in snapshot3.sections for row in part.sentences}
    assert stored[tracked.sentence_id] == tracked
    # 엔진에서 저장한 정적 flags는 편집하지 않은 문장 모두에 보존된다.
    for row in sentences:
        if row.sentence_id != other.sentence_id:
            assert stored[row.sentence_id] == row
    with psycopg.connect(admin) as conn:
        if change == "requirement":
            conn.execute(
                "UPDATE tft.requirement_versions SET needs_review=false WHERE requirement_id=%s",
                (chain["requirement"],),
            )
        else:
            conn.execute(
                "UPDATE tft.rereview_marks SET cleared_by=%s,cleared_at=now() WHERE id=%s",
                (SALES, mark),
            )
    current = call(client, "GET", chain).json()["data"]
    displayed = {
        row["sentence_id"]: row for part in current["sections"] for row in part["sentences"]
    }
    assert displayed[str(tracked.sentence_id)]["needs_rereview"] is False
    assert displayed[str(tracked.sentence_id)]["unconfirmed"] is False
    assert asyncio.run(frozen(2)) == original
    assert asyncio.run(frozen(3)) == snapshot3


def test_editing_a_flagged_sentence_clears_only_that_sentence(client, app_dsn, chain, admin):
    """제안 승인이 남긴 표시는 고쳐 저장한 문장만 풀린다(명세 결정 10). 안 풀리면 검토본을 못 저장한다."""
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.requirement_versions SET status='EVIDENCE_FOUND',unresolved='[]',"
            "next_owner_field=NULL WHERE requirement_id=%s",
            (chain["requirement"],),
        )
    generate(app_dsn, chain, uuid4())
    goal = first_sentence(call(client, "GET", chain).json()["data"])
    added = {
        "version": 1,
        "edits": [
            {
                "sentence_id": None,
                "section": "QUESTIONS_NEXT_ACTIONS",
                "text": "별도 검토 행동",
                "reason": "검토 추가",
                "evidence": goal["evidence"],
            }
        ],
    }
    assert call(client, "PATCH", chain, body=added).status_code == 200
    rows = [
        row
        for part in call(client, "GET", chain).json()["data"]["sections"]
        for row in part["sentences"]
    ]
    proposal = uuid4()
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.change_proposals (id,project_id,requirement_id,base_requirement_version,origin,current,proposed,rationale,proposer_kind,proposer_user_id,data_origin,derivation_kind,known_at) "
            "VALUES (%s,%s,%s,1,'web','{}','{}','후속 검토','user',%s,'synthetic','PROVIDED',now())",
            (proposal, chain["project"], chain["requirement"], SALES),
        )
        for row in rows[:2]:
            conn.execute(
                "INSERT INTO tft.rereview_marks (id,project_id,target_kind,target_id,cause_proposal_id) "
                "VALUES (%s,%s,'draft_sentence',%s,%s)",
                (uuid4(), chain["project"], row["sentence_id"], proposal),
            )
    body = {
        "version": 2,
        "edits": [
            {
                "sentence_id": rows[0]["sentence_id"],
                "text": "사람이 다시 확인한 문구",
                "reason": "승인 뒤 재확인",
                "evidence": rows[0]["evidence"],
            }
        ],
    }
    edited = call(client, "PATCH", chain, body=body)
    assert edited.status_code == 200, edited.text
    shown = {
        row["sentence_id"]: row["needs_rereview"]
        for part in edited.json()["data"]["sections"]
        for row in part["sentences"]
    }
    assert shown[rows[0]["sentence_id"]] is False
    assert shown[rows[1]["sentence_id"]] is True


def test_new_job_migrates_human_edits_and_marks_without_freezing_live_flags(
    client, app_dsn, chain, admin
):
    from backend.api.tft.rfp import draft_repository as db

    generate(app_dsn, chain, uuid4())
    original = first_sentence(call(client, "GET", chain).json()["data"])
    edited = call(
        client,
        "PATCH",
        chain,
        body={
            "version": 1,
            "edits": [
                {
                    "sentence_id": original["sentence_id"],
                    "text": "사람 편집  원형\n600 A",
                    "reason": "현행 문구 확인",
                    "evidence": original["evidence"],
                },
                {
                    "sentence_id": None,
                    "section": "QUESTIONS_NEXT_ACTIONS",
                    "text": "직접 추가한 행동",
                    "reason": "후속 확인",
                    "evidence": original["evidence"],
                },
            ],
        },
    ).json()["data"]
    humans = {
        r["sentence_id"]: r
        for part in edited["sections"]
        for r in part["sentences"]
        if r["provenance_kind"] == "human_input"
    }
    marked = original["sentence_id"]
    proposal, mark = uuid4(), uuid4()
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.change_proposals (id,project_id,requirement_id,base_requirement_version,origin,current,proposed,rationale,proposer_kind,proposer_user_id,data_origin,derivation_kind,known_at) "
            "VALUES (%s,%s,%s,1,'web','{}','{}','후속 검토','user',%s,'synthetic','PROVIDED',now())",
            (proposal, chain["project"], chain["requirement"], SALES),
        )
        conn.execute(
            "INSERT INTO tft.rereview_marks (id,project_id,target_kind,target_id,cause_proposal_id) VALUES (%s,%s,'draft_sentence',%s,%s)",
            (mark, chain["project"], marked, proposal),
        )
    job = uuid4()
    third = generate(app_dsn, chain, job)
    assert third.version == 3
    shown = {str(row.sentence_id): row for part in third.sections for row in part.sentences}
    assert shown[marked].needs_rereview is True
    added_id = next(key for key in humans if key != marked)
    assert shown[added_id].needs_rereview == humans[added_id]["needs_rereview"]

    async def frozen():
        async with await psycopg.AsyncConnection.connect(app_dsn, row_factory=dict_row) as conn:
            return await db.load_snapshot(conn, chain["project"], 3)

    snapshot = asyncio.run(frozen())
    saved = {
        str(row.sentence_id): row.model_dump(mode="json")
        for part in snapshot.sections
        for row in part.sentences
    }
    for key, row in humans.items():
        assert saved[key] == row
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.rereview_marks SET cleared_at=now(),cleared_by=%s WHERE id=%s",
            (SALES, mark),
        )
        assert (
            conn.execute(
                "SELECT edit_reason FROM tft.draft_sentences WHERE project_id=%s AND draft_version=3 AND sentence_key=%s",
                (chain["project"], marked),
            ).fetchone()[0]
            == "현행 문구 확인"
        )
    latest = call(client, "GET", chain).json()["data"]
    latest_rows = {r["sentence_id"]: r for part in latest["sections"] for r in part["sentences"]}
    assert latest_rows[marked] == humans[marked]
    updated = call(
        client,
        "PATCH",
        chain,
        body={
            "version": 3,
            "edits": [
                {
                    "sentence_id": marked,
                    "text": "나중 편집",
                    "reason": "재확인",
                    "evidence": original["evidence"],
                }
            ],
        },
    )
    assert updated.status_code == 200
    assert generate(app_dsn, chain, job).version == 3
    assert call(client, "GET", chain).json()["data"]["version"] == 4
    stale = call(
        client,
        "PATCH",
        chain,
        body={
            "version": 3,
            "edits": [
                {
                    "sentence_id": marked,
                    "text": "오래된 편집",
                    "reason": "충돌",
                    "evidence": original["evidence"],
                }
            ],
        },
    )
    assert stale.status_code == 409


def test_template_backfill_uses_actual_automatic_origin_not_current_human_text(
    client, app_dsn, chain, admin
):
    from pathlib import Path

    from tests.tft.test_rfp_schema_db import apply

    generate(app_dsn, chain, uuid4())
    original = first_sentence(call(client, "GET", chain).json()["data"])
    edited = call(
        client,
        "PATCH",
        chain,
        body={
            "version": 1,
            "edits": [
                {
                    "sentence_id": original["sentence_id"],
                    "text": "사람이 고친 본문은 양식이 아니다",
                    "reason": "문구 확인",
                    "evidence": original["evidence"],
                },
                {
                    "sentence_id": None,
                    "section": "OUR_RESPONSE",
                    "text": "새 사람 문장",
                    "reason": "직접 추가",
                    "evidence": original["evidence"],
                },
            ],
        },
    )
    assert edited.status_code == 200, edited.text
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.draft_sentences SET template_text=NULL WHERE project_id=%s",
            (chain["project"],),
        )
        apply(conn, Path("infra/migrations/tft/20261111_KAN-188_draft_rewrite.sql"))
        existing = conn.execute(
            "SELECT text,template_text FROM tft.draft_sentences WHERE project_id=%s AND draft_version=2 AND sentence_key=%s",
            (chain["project"], original["sentence_id"]),
        ).fetchone()
        direct = conn.execute(
            "SELECT template_text FROM tft.draft_sentences WHERE project_id=%s AND text='새 사람 문장'",
            (chain["project"],),
        ).fetchone()
        assert existing == ("사람이 고친 본문은 양식이 아니다", original["text"])
        assert direct == (None,)


def test_rewrite_summary_tells_full_partial_failed_and_discarded(client, app_dsn, chain, admin):
    """호출 원장에서 사유를 세어 전체·일부·전부 실패·폐기를 구분한다."""
    ready(app_dsn, admin, chain)
    sections = call(client, "GET", chain).json()["data"]["sections"]
    total = sum(len(part["sentences"]) for part in sections)
    assert total >= 1
    project = chain["project"]

    def add(state, reasons=None, result=None):
        job = uuid4()
        row = uuid4()
        with psycopg.connect(admin) as conn:
            conn.execute(
                "INSERT INTO tft.draft_rewrites(id,project_id,base_version,actor_id,request_key,"
                "input_version,job_id,state,result_version) VALUES (%s,%s,1,%s,%s,%s,%s,%s,%s)",
                (row, project, SALES, uuid4().hex, uuid4().hex * 2, job, state, result),
            )
            for attempt, (outcome, values) in enumerate(reasons or []):
                conn.execute(
                    "INSERT INTO tft.draft_rewrite_calls(rewrite_id,section,attempt,outcome,reasons,"
                    "prompt_version,requested_model,elapsed_ms,provider_attempts) "
                    "VALUES (%s,'S',%s,%s,%s,'p','m',0,0)",
                    (row, attempt, outcome, Jsonb(values)),
                )
        response = call(client, "GET", chain, f"/rewrites/{job}")
        assert response.status_code == 200, response.text
        return response.json()["data"]

    full = add("saved", [("passed", {})], 2)
    assert (full["outcome"], full["model_sentences"]) == ("SUCCEEDED", total)
    assert full["template_sentences"] == 0 and full["result_version"] == 2
    failed = add(
        "saved",
        [
            (
                "skipped",
                {"1": "INPUT_BLOCKED"} | {str(n): "CALL_FAILED" for n in range(2, total + 1)},
            )
        ],
    )
    assert (failed["outcome"], failed["model_sentences"]) == ("FAILED", 0)
    assert failed["failures"]["INPUT_BLOCKED"] == 1 and failed["result_version"] is None
    # 재시도로 통과한 문장은 사유에 넣지 않는다.
    retried = add("saved", [("retry", {"1": "STRUCTURE"}), ("passed", {})], 3)
    assert retried["outcome"] == "SUCCEEDED" and retried["failures"] == {}
    assert add("discarded")["outcome"] == "DISCARDED"
    assert add("reserved")["outcome"] == "PENDING"
    missing = call(client, "GET", chain, f"/rewrites/{uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "TFT_DRAFT_REWRITE_NOT_FOUND"
    assert call(client, "GET", chain, f"/rewrites/{uuid4()}", user=OUTSIDER).status_code == 404


def test_draft_gives_readable_evidence_numbers(client, app_dsn, chain, admin):
    """조회 응답은 근거 ID를 읽을 수 있는 번호로 바꾸는 표를 함께 준다."""
    ready(app_dsn, admin, chain)
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.requirements SET code='abcd1234:2.4.E.1.a:2' WHERE id=%s",
            (chain["requirement"],),
        )
    data = call(client, "GET", chain).json()["data"]
    assert data["evidence_labels"][str(chain["requirement"])] == "2.4.E.1.a-2"
