"""KAN-221: 저장한 검토본을 XLSX(네 시트)와 요약서 DOCX로 내보내 실제 파일을 다시 연다(전용 PostgreSQL, 모델 없음)."""

# ruff: noqa: F811

import asyncio
import io
import zipfile
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from backend.agent.processor_registry import TftJob
from backend.api.tft.rfp import draft_exports
from backend.api.tft.rfp.storage import LocalDocumentStore
from tests.tft.decision_gate import settle_decisions
from tests.tft.test_a09_demo_flow_db import (  # noqa: F401
    client_for,
    demo_profiles,
    processed,
    sales_headers,
)
from tests.tft.test_rfp_bid_outputs import read_xlsx
from tests.tft.test_rfp_http_base_db import SALES, admin, app_dsn  # noqa: F401
from tests.tft.test_worker_processor_db import worker_dsn  # noqa: F401


def export(client, base, project, worker_dsn, root, format):
    """내보내기를 접수하고 worker 권한(tft_worker)으로 처리한 뒤 내려받는다."""

    class Jobs:
        def __init__(self):
            self.calls = []
            self.id = uuid4()

        async def post(self, path, body):
            self.calls.append(body)
            return {"job_id": str(self.id), "status": "QUEUED", "status_url": f"/x/{self.id}"}

    jobs = Jobs()
    client.app.state.processing_jobs = jobs
    received = client.post(
        f"{base}/exports",
        json={"review_version": 1, "format": format},
        headers=sales_headers(uuid4().hex),
    )
    assert received.status_code == 202, received.text
    job = TftJob(
        job_id=jobs.id,
        kind="rfp.export",
        project_id=project["project"],
        input_version=jobs.calls[0]["input_version"],
        actor_id=SALES,
    )

    async def execute():
        async with await psycopg.AsyncConnection.connect(worker_dsn, row_factory=dict_row) as conn:
            return await draft_exports.process(
                conn, job, lambda event: None, LocalDocumentStore(root)
            )

    asyncio.run(execute())
    file = client.get(
        f"{base}/exports/{received.json()['data']['export_id']}/file", headers=sales_headers()
    )
    assert file.status_code == 200, file.text
    return file


def test_xlsx_and_summary_docx_from_a_saved_review(
    worker_dsn, admin, app_dsn, tmp_path, demo_profiles, monkeypatch
):
    monkeypatch.setenv("TFT_DOCUMENT_ROOT", str(tmp_path))
    monkeypatch.setenv("TFT_DOCUMENT_BUCKET", "")
    project = processed(worker_dsn, admin, tmp_path)
    base = f"/api/v1/tft/projects/{project['project']}/draft"
    with client_for(app_dsn) as client:
        draft = client.get(base, headers=sales_headers()).json()["data"]
        recorded = settle_decisions(client, admin, project["project"])
        saved = client.post(
            f"{base}/review-versions",
            json={"version": draft["version"]},
            headers=sales_headers(uuid4().hex),
        )
        assert saved.status_code == 201, saved.text
        with psycopg.connect(admin) as conn:
            requirements = conn.execute(
                "SELECT count(*) FROM tft.requirements WHERE project_id=%s", (project["project"],)
            ).fetchone()[0]

        xlsx = export(client, base, project, worker_dsn, tmp_path, "XLSX")
        assert xlsx.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml"
        )
        assert xlsx.headers["content-disposition"].endswith('.xlsx"')
        sheets = read_xlsx(xlsx.content)
        assert list(sheets) == ["편차표", "질의서", "현황표", "요구별"]
        meta = sheets["요구별"][0][0]
        assert "검토본 1" in meta
        assert sheets["요구별"][1][:4] == ["No", "요구 번호", "조항", "설비"]
        # 저장 조건(KAN-220)을 채우려고 결정이 필요한 요구를 모두 편차표에 기재했다. 질의서 행은 없다.
        # 요구별은 모든 요구(뺀 요구·합친 중복 없음).
        assert recorded and len(sheets["편차표"]) == 2 + recorded
        assert len(sheets["요구별"]) == 2 + requirements
        assert meta.endswith("| 확정")
        total = sheets["현황표"][-1]
        assert total[0] == "합계" and sum(map(int, total[1:6])) == requirements

        docx = export(client, base, project, worker_dsn, tmp_path, "DOCX")
        with zipfile.ZipFile(io.BytesIO(docx.content)) as package:
            body = package.read("word/document.xml").decode()
        assert "검토용 초안입니다" in body  # 요약서는 검토본에 고정한 초안 그대로다

        listed = client.get(f"{base}/exports", headers=sales_headers()).json()["data"]["items"]
        assert sorted(item["file_name"][-4:] for item in listed) == ["docx", "xlsx"]
