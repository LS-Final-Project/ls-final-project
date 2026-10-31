"""KAN-188 2차 경로 2·4·5·6을 HTTP로, 실제 마이그레이션의 전용 PostgreSQL에서 검사한다.

행과 파일은 모두 합성이다.
"""

# ruff: noqa: F811

import asyncio
import hashlib
import io
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from backend.api.tft.main import create_app
from backend.api.tft.projects.repository import integration_state
from backend.api.tft.rfp import routes_write
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings
from tests.tft.test_rfp_http_base_db import (  # noqa: F401
    RESPONSIBLE,
    SALES,
    admin,
    app_dsn,
    chain,
)

OWNER = Principal(user_id=SALES, roles=frozenset({"sales"}))
CHIEF = Principal(user_id=RESPONSIBLE, roles=frozenset({"sales"}))
OTHER_SALES = Principal(user_id=uuid4(), roles=frozenset({"sales"}))
REVIEWER = Principal(user_id=uuid4(), roles=frozenset({"dept_staff"}), dept="RND_FW")
PDF = b"%PDF-1.7 synthetic"


def docx() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "<w:document/>")
    return buffer.getvalue()


def manager(chain) -> Principal:
    return Principal(
        user_id=uuid4(), roles=frozenset({"manager"}), managed_projects={chain["project"]}
    )


@pytest.fixture
def client(app_dsn, tmp_path, monkeypatch):
    monkeypatch.setenv("TFT_DOCUMENT_ROOT", str(tmp_path))
    monkeypatch.delenv("TFT_DOCUMENT_BUCKET", raising=False)
    return TestClient(
        create_app(Settings(_env_file=None, environment="test", database_url=app_dsn))
    )


def call(client, method, path, who, body=None, key=None):
    headers = principal_header(who)
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.request(method, "/api/v1/tft" + path, json=body, headers=headers)


def error(response):
    return response.status_code, response.json()["error"]["code"]


def project_body(**extra):
    return {
        "name": "합성 접수",
        "company_code": "HOST",
        "data_origin": "synthetic",
        "review_departments": ["RND_FW"],
        **extra,
    }


def upload(client, chain, who=OWNER, *, file=PDF, key=None, content_type="text/html", **fields):
    form = {"kind": "RFP_SPEC", "title": "합성 규격서", "data_origin": "synthetic", **fields}
    headers = principal_header(who) | {"Idempotency-Key": key or uuid4().hex}
    return client.post(
        f"/api/v1/tft/projects/{chain['project']}/documents",
        headers=headers,
        data=form,
        files={"file": ("../evil.html", file, content_type)},
    )


def test_create_makes_the_caller_the_sales_owner_and_replays(client):
    """2: 호출자가 담당, 책임자 없음, 같은 키는 같은 결과, 다른 본문은 409."""
    key = uuid4().hex
    body = project_body(strategic_customer=True, strategic_customer_note="영업 입력")
    created = call(client, "POST", "/projects", OWNER, body, key)
    assert created.status_code == 201, created.text
    project = created.json()["data"]
    assert project["sales_user_id"] == str(SALES) and project["responsible_user_id"] is None
    assert (project["output_status"], project["judgement_status"]) == ("INTAKE", "NEEDS_REVIEW")
    assert project["strategic_customer_set_by"] == str(SALES)
    assert project["latest_job"] is None and project["documents_count"] == 0
    assert project["requirement_counts"]["by_status"] == {}
    assert project["pending_proposals_by_origin"] == {"web": 0, "customer_reply": 0, "pdf_annot": 0}
    assert project["integration_responsibility"]["holder"] == "UNRESOLVED"
    again = call(client, "POST", "/projects", OWNER, body, key)
    assert again.status_code == 201 and again.json()["data"]["id"] == project["id"]
    changed = call(client, "POST", "/projects", OWNER, project_body(name="다른 이름"), key)
    assert error(changed) == (409, "IDEMPOTENCY_CONFLICT")


def test_create_issues_a_project_number_once_and_in_order(client, admin):
    """접수 번호는 연도별 순번이다. 동시 접수도 겹치지 않고 재전송은 새로 받지 않는다."""
    year = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%y")
    with psycopg.connect(admin) as conn:
        # 다른 출처의 번호는 세지 않는다. 형식이 다른 웹 번호도 세지 않는다.
        for source, instance, external in (
            ("sync", "other", f"P{year}-9000"),
            ("rfp", "web", f"P{year}-ABC"),
        ):
            conn.execute(
                "INSERT INTO tft.projects (id, source, source_instance, data_origin, "
                "derivation_kind, known_at, company_code, name, external_id) "
                "VALUES (%s,%s,%s,'synthetic','PROVIDED',now(),'HOST','번호 시험',%s)",
                (uuid4(), source, instance, external),
            )
    key = uuid4().hex
    first = call(client, "POST", "/projects", OWNER, project_body(), key)
    assert first.status_code == 201, first.text
    number = first.json()["data"]["external_id"]
    assert re.fullmatch(rf"P{year}-\d{{4}}", number)
    again = call(client, "POST", "/projects", OWNER, project_body(), key)
    assert again.json()["data"]["external_id"] == number
    with ThreadPoolExecutor(4) as pool:
        made = list(
            pool.map(
                lambda _: call(client, "POST", "/projects", OWNER, project_body(), uuid4().hex),
                range(4),
            )
        )
    numbers = sorted(r.json()["data"]["external_id"] for r in made)
    start = int(number.split("-")[1])
    assert start < 9000
    assert numbers == [f"P{year}-{start + n:04d}" for n in range(1, 5)]


def test_create_is_for_sales_with_known_departments_only(client, chain):
    """2: dept_staff·manager는 403, 없는 부서는 422."""
    for who in (REVIEWER, manager(chain)):
        response = call(client, "POST", "/projects", who, project_body(), uuid4().hex)
        assert response.status_code == 403
    unknown = project_body(review_departments=["NO_SUCH"])
    response = call(client, "POST", "/projects", OWNER, unknown, uuid4().hex)
    assert error(response) == (422, "VALIDATION_ERROR")


def test_update_changes_only_the_sent_fields(client, admin, chain):
    """4: 보낸 칸만, 판본 충돌 409, 상태 칸 거부, 통합 책임·전략 고객 입력자 기록."""
    url = f"/projects/{chain['project']}"
    done = call(client, "PATCH", url, CHIEF, {"version": 1, "customer_goal": "목표"})
    assert done.status_code == 200, done.text
    body = done.json()["data"]
    assert (body["version"], body["customer_goal"]) == (2, "목표")
    assert body["name"] == "합성 프로젝트"
    assert body["requirements_count"] == 1 and body["documents_count"] == 1
    assert body["review_departments"] == ["RND_FW", "TS_TRF"]
    stale = call(client, "PATCH", url, OWNER, {"version": 1, "name": "늦은 수정"})
    assert error(stale) == (409, "TFT_VERSION_CONFLICT")
    forbidden = {"version": 2, "output_status": "REVIEW_DRAFT"}
    assert call(client, "PATCH", url, OWNER, forbidden).status_code == 422
    assert error(call(client, "PATCH", url, OWNER, {"version": 2, "name": None})) == (
        422,
        "VALIDATION_ERROR",
    )
    marked = call(
        client,
        "PATCH",
        url,
        OWNER,
        {
            "version": 2,
            "strategic_customer": True,
            "strategic_customer_note": "메모",
            "integration_responsibility": {"holder": "OWN", "holder_candidate_codes": ["P-A"]},
        },
    ).json()["data"]
    assert marked["strategic_customer_set_by"] == str(SALES)
    assert marked["integration_responsibility"]["holder"] == "OWN"
    assert marked["integration_responsibility"]["set_by"] == str(SALES)
    cleared = call(client, "PATCH", url, OWNER, {"version": 3, "strategic_customer": None}).json()[
        "data"
    ]
    assert cleared["strategic_customer"] is None
    assert cleared["strategic_customer_note"] is None
    assert cleared["strategic_customer_set_by"] is None
    assert cleared["integration_responsibility"]["holder"] == "OWN"


def test_update_is_for_the_sales_owner_and_responsible(client, chain):
    """4: 볼 수만 있으면 403(검토 부서·관리 manager), 범위 밖은 404."""
    url = f"/projects/{chain['project']}"
    for who in (REVIEWER, manager(chain)):
        assert call(client, "PATCH", url, who, {"version": 1, "name": "x"}).status_code == 403
    outside = call(client, "PATCH", url, OTHER_SALES, {"version": 1, "name": "x"})
    assert error(outside) == (404, "TFT_PROJECT_NOT_FOUND")


def test_assignment_is_for_the_managing_manager_and_keeps_history(client, admin, chain):
    """5: 관리 manager만, 후보 밖 책임자 422, 호출마다 이력 한 행."""
    url = f"/projects/{chain['project']}/assignment"
    new_sales = uuid4()
    body = {
        "version": 1,
        "sales_user_id": str(new_sales),
        "responsible_user_id": str(new_sales),
        "reason": "담당 변경",
    }
    assert call(client, "PATCH", url, OWNER, body).status_code == 403
    other = Principal(user_id=uuid4(), roles=frozenset({"manager"}), managed_projects={uuid4()})
    assert error(call(client, "PATCH", url, other, body)) == (404, "TFT_PROJECT_NOT_FOUND")
    boss = manager(chain)
    wrong = {**body, "responsible_user_id": str(uuid4())}
    assert error(call(client, "PATCH", url, boss, wrong)) == (422, "VALIDATION_ERROR")
    done = call(client, "PATCH", url, boss, body)
    assert done.status_code == 200, done.text
    project = done.json()["data"]
    assert project["sales_user_id"] == project["responsible_user_id"] == str(new_sales)
    assert project["version"] == 2
    assert error(call(client, "PATCH", url, boss, body)) == (409, "TFT_VERSION_CONFLICT")
    with psycopg.connect(admin) as conn:
        rows = conn.execute(
            "SELECT project_version, previous_sales_user_id, sales_user_id, "
            "previous_responsible_user_id, responsible_user_id, reason, changed_by "
            "FROM tft.project_assignments WHERE project_id=%s",
            (chain["project"],),
        ).fetchall()
    assert rows == [(2, SALES, new_sales, RESPONSIBLE, new_sales, "담당 변경", boss.user_id)]


def test_upload_detects_the_type_from_content_and_replays(client, chain, tmp_path):
    """6: 저장 mime은 내용 판별값, 같은 키는 객체를 다시 쓰지 않고, 내려받으면 같은 바이트다."""
    key = uuid4().hex
    first = upload(client, chain, key=key)
    assert first.status_code == 201, first.text
    version = first.json()["data"]
    assert version["mime"] == "application/pdf" and version["edit_trace_support"] is False
    assert version["sha256"] == hashlib.sha256(PDF).hexdigest() and version["size"] == len(PDF)
    assert version["uploaded_by"] == str(SALES)
    assert "storage_key" not in version
    again = upload(client, chain, key=key)
    assert again.json()["data"]["document_version_id"] == version["document_version_id"]
    assert len([p for p in tmp_path.rglob("*") if p.is_file()]) == 1
    assert error(upload(client, chain, key=key, file=PDF + b"x")) == (409, "IDEMPOTENCY_CONFLICT")
    stored = client.get(
        f"/api/v1/tft/documents/{version['document_id']}/file", headers=principal_header(OWNER)
    )
    assert (stored.status_code, stored.content) == (200, PDF)
    word = upload(client, chain, file=docx(), content_type="application/pdf").json()["data"]
    assert word["mime"].endswith("wordprocessingml.document") and word["edit_trace_support"]


@pytest.mark.parametrize(
    "file", [b"<html>", b"", b"PK\x03\x04broken", None], ids=["html", "empty", "zip", "other"]
)
def test_upload_refuses_what_is_not_docx_or_pdf(client, chain, tmp_path, file):
    """6: 내용이 DOCX·PDF가 아니면 422이고 아무것도 저장하지 않는다."""
    if file is None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("a.txt", "x")
        file = buffer.getvalue()
    response = upload(client, chain, file=file, content_type="application/pdf")
    assert error(response) == (422, "VALIDATION_ERROR")
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]


def test_upload_limits_size_and_form_fields(client, chain, tmp_path, monkeypatch):
    """6: 25 MiB 초과는 413, 폼 칸 오류는 422, 저장소가 없으면 503."""
    monkeypatch.setattr(routes_write, "MAX_DOCUMENT_BYTES", len(PDF))
    assert upload(client, chain).status_code == 201
    assert error(upload(client, chain, file=PDF + b"x")) == (413, "TFT_DOCUMENT_TOO_LARGE")
    assert error(upload(client, chain, kind="NOPE")) == (422, "VALIDATION_ERROR")
    assert error(upload(client, chain, base_document_id="not-a-uuid")) == (422, "VALIDATION_ERROR")
    headers = principal_header(OWNER) | {"Idempotency-Key": uuid4().hex}
    missing = client.post(
        f"/api/v1/tft/projects/{chain['project']}/documents",
        headers=headers,
        data={"kind": "RFP_SPEC", "title": "x", "data_origin": "synthetic"},
    )
    assert error(missing) == (422, "VALIDATION_ERROR")
    monkeypatch.delenv("TFT_DOCUMENT_ROOT")
    monkeypatch.chdir(tmp_path)
    assert error(upload(client, chain)) == (503, "TFT_DOCUMENT_UNAVAILABLE")


def test_upload_supersedes_only_a_document_of_the_project(client, admin, chain):
    """6: 다음 판본은 최신 번호를 올리고, 다른 프로젝트 문서는 404, 종류가 다르면 422."""
    document = str(chain["document"])
    next_version = upload(client, chain, supersedes_document_id=document)
    assert next_version.status_code == 201, next_version.text
    assert next_version.json()["data"]["version"] == 2
    assert next_version.json()["data"]["document_id"] == document
    wrong_kind = upload(client, chain, kind="DRAWING", supersedes_document_id=document)
    assert error(wrong_kind) == (422, "VALIDATION_ERROR")
    annotated = upload(client, chain, kind="ANNOTATED_PDF", base_document_id=document)
    assert annotated.json()["data"]["base_document_id"] == document
    other = call(client, "POST", "/projects", OWNER, project_body(), uuid4().hex).json()["data"]
    elsewhere = {"project": other["id"]}
    for field in ("supersedes_document_id", "base_document_id"):
        response = upload(client, elsewhere, **{field: document})
        assert error(response) == (404, "TFT_DOCUMENT_NOT_FOUND")


def test_upload_is_for_the_sales_owner_and_responsible(client, chain):
    """6: 검토 부서·관리 manager는 403, 범위 밖은 404. 본문을 읽기 전에 거절한다."""
    assert upload(client, chain, CHIEF).status_code == 201
    for who in (REVIEWER, manager(chain)):
        assert upload(client, chain, who).status_code == 403
    assert error(upload(client, chain, OTHER_SALES)) == (404, "TFT_PROJECT_NOT_FOUND")


def test_project_read_survives_a_missing_integration_schema(admin, chain):
    """마이그레이션 전 칸이 없으면 통합 책임만 None이고 같은 트랜잭션의 조회는 이어진다.

    트랜잭션 안에서 칸 이름을 바꾸고 되돌리므로 DB에 적용된 마이그레이션과 상관없이 같은 결과다.
    """

    async def scenario():
        conn = await psycopg.AsyncConnection.connect(admin, row_factory=dict_row)
        try:
            assert await integration_state(conn, chain["project"]) is not None
            await conn.execute(
                "ALTER TABLE tft.projects RENAME COLUMN integration_holder TO integration_old"
            )
            assert await integration_state(conn, chain["project"]) is None
            cursor = await conn.execute(
                "SELECT name FROM tft.projects WHERE id=%s", (chain["project"],)
            )
            assert await cursor.fetchone() == {"name": "합성 프로젝트"}
        finally:
            await conn.rollback()
            await conn.close()

    asyncio.run(scenario())


def audits(admin, project):
    with psycopg.connect(admin) as conn:
        return conn.execute(
            "SELECT entity, entity_id, action, actor_user_id, detail FROM tft.rfp_audit_events "
            "WHERE project_id=%s ORDER BY at",
            (project,),
        ).fetchall()


def test_upload_stops_a_chunked_body_while_receiving(client, chain, tmp_path, monkeypatch):
    """Content-Length가 없어도 누적 한도를 넘으면 413이고 아무것도 저장하지 않는다."""
    monkeypatch.setattr(routes_write, "MAX_FORM_BYTES", 100)
    body = (
        b'--b\r\nContent-Disposition: form-data; name="file"; filename="a"\r\n\r\n'
        + PDF
        + b'\r\n--b\r\nContent-Disposition: form-data; name="title"\r\n\r\n'
        + b"x" * 200
        + b"\r\n--b--\r\n"
    )
    headers = principal_header(OWNER) | {
        "Idempotency-Key": uuid4().hex,
        "Content-Type": "multipart/form-data; boundary=b",
    }
    response = client.post(
        f"/api/v1/tft/projects/{chain['project']}/documents",
        headers=headers,
        content=iter([body[:50], body[50:]]),
    )
    assert error(response) == (413, "TFT_DOCUMENT_TOO_LARGE")
    assert "content-length" not in response.request.headers
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]


def test_audit_records_each_write_once_with_the_actor(client, admin, chain):
    """멱등 쓰기·PATCH 모두 한 행. 재전송과 실패는 남기지 않고 from·to는 값을 그대로 담는다."""
    key = uuid4().hex
    created = call(client, "POST", "/projects", OWNER, project_body(), key).json()["data"]
    call(client, "POST", "/projects", OWNER, project_body(), key)
    [row] = audits(admin, created["id"])
    assert row[:4] == ("project", created["id"], "project.create", SALES)

    url = f"/projects/{chain['project']}"
    stale = {"version": 9, "name": "늦은 수정"}
    assert call(client, "PATCH", url, OWNER, stale).status_code == 409
    assert audits(admin, chain["project"]) == []
    body = {
        "version": 1,
        "strategic_customer": True,
        "integration_responsibility": {"holder": "OWN", "holder_candidate_codes": ["P-A"]},
    }
    assert call(client, "PATCH", url, OWNER, body).status_code == 200
    [update] = audits(admin, chain["project"])
    assert update[:4] == ("project", str(chain["project"]), "project.update", SALES)
    assert update[4]["from"]["strategic_customer"] is None
    assert update[4]["from"]["integration_holder"] == "UNRESOLVED"
    assert update[4]["to"]["strategic_customer"] is True
    assert update[4]["to"]["integration_holder_candidates"] == ["P-A"]

    boss = manager(chain)
    new_sales = uuid4()
    assigned = {
        "version": 2,
        "sales_user_id": str(new_sales),
        "responsible_user_id": None,
        "reason": "담당 변경",
    }
    assert call(client, "PATCH", f"{url}/assignment", boss, assigned).status_code == 200
    _, assignment = audits(admin, chain["project"])
    assert assignment[0] == "assignment" and assignment[2:4] == ("assignment.update", boss.user_id)
    assert assignment[4]["from"] == {
        "sales_user_id": str(SALES),
        "responsible_user_id": str(RESPONSIBLE),
    }
    assert assignment[4]["to"] == {"sales_user_id": str(new_sales), "responsible_user_id": None}

    new_owner = Principal(user_id=new_sales, roles=frozenset({"sales"}))
    version = upload(client, chain, new_owner).json()["data"]
    assert audits(admin, chain["project"])[-1][:4] == (
        "document",
        version["document_version_id"],
        "document.upload",
        new_sales,
    )


def test_human_project_constraints_create_sparse_patch_clear_and_reject_empty_list(client):
    created = call(
        client,
        "POST",
        "/projects",
        OWNER,
        project_body(
            supply_region="한국",
            customer_manufacturer_list=["제조사 A"],
            off_list_permission="FORBIDDEN",
        ),
    ).json()["data"]
    assert (
        created["supply_region"],
        created["customer_manufacturer_list"],
        created["off_list_permission"],
    ) == ("한국", ["제조사 A"], "FORBIDDEN")
    url = f"/projects/{created['id']}"
    renamed = call(client, "PATCH", url, OWNER, {"version": 1, "name": "수정 이름"}).json()["data"]
    assert (
        renamed["supply_region"],
        renamed["customer_manufacturer_list"],
        renamed["off_list_permission"],
    ) == ("한국", ["제조사 A"], "FORBIDDEN")
    cleared = call(
        client,
        "PATCH",
        url,
        OWNER,
        {"version": 2, "supply_region": None, "customer_manufacturer_list": None},
    ).json()["data"]
    assert (
        cleared["supply_region"],
        cleared["customer_manufacturer_list"],
        cleared["off_list_permission"],
    ) == (None, None, "FORBIDDEN")
    assert (
        call(
            client, "POST", "/projects", OWNER, project_body(customer_manufacturer_list=[])
        ).status_code
        == 422
    )
