"""KAN-153 Q05: 14번 시연 흐름에서 변경 제안 기각·목록 구분과 가상·검토용 표시를 확인한다(전용 PostgreSQL, 가짜 추출기).

A09 시험(test_a09_demo_flow_db.py)이 흐름 전체·승인·재실행 보존·검토본·내보내기를 이미 다룬다. 이 파일은 그와 겹치지 않는
Q05 완료 조건만 본다: (1) 기각하면 요구 판본은 그대로이고 제안 행(결정자·사유)과 명령·감사 기록이 남는다, (2) 제안 목록이 승인 대기·
승인·기각을 구분해 보여 준다, (3) 협력사 후보·초안·내보낸 검토본에 가상(합성) 표시와 검토용 문구가 남는다.

한계: 요구 추출은 A09의 가짜 추출기 값이다. 재처리 때 사람이 손댄 요구를 변경 제안으로 다시 띄우는 규칙은 구현에 없다
(contracts/tft/rfp-data.md KAN-189 항목, 처리 함수에 사람 판본 대비 제안 생성 경로 없음) — 그래서 여기서 시험하지 않는다.
"""

# ruff: noqa: F811

import asyncio
import io
import zipfile
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from backend.engine.rfp.draft import DISCLAIMER
from tests.tft.test_a09_demo_flow_db import (  # noqa: F401
    DEMO_A,
    client_for,
    demo_profiles,
    processed,
    sales_headers,
)
from tests.tft.test_rfp_http_base_db import SALES, admin, app_dsn  # noqa: F401
from tests.tft.test_worker_processor_db import worker_dsn  # noqa: F401


def requirement_of(admin, project, item):
    """시연 A 항목의 요구 ID."""
    with psycopg.connect(admin) as conn:
        return conn.execute(
            "SELECT r.id FROM tft.requirements r JOIN tft.requirement_versions v ON v.requirement_id=r.id "
            "WHERE r.project_id=%s AND v.quote=%s LIMIT 1",
            (project["project"], DEMO_A[item]["quote"]),
        ).fetchone()[0]


def versions_of(admin, requirement_id):
    with psycopg.connect(admin) as conn:
        return conn.execute(
            "SELECT version, status, provenance_kind FROM tft.requirement_versions "
            "WHERE requirement_id=%s ORDER BY version",
            (requirement_id,),
        ).fetchall()


def propose(client, project, requirement_id, status, rationale):
    made = client.post(
        f"/api/v1/tft/projects/{project['project']}/proposals",
        json={
            "requirement_id": str(requirement_id),
            "proposed": {"status": status, "reason": rationale},
            "rationale": rationale,
        },
        headers=sales_headers(uuid4().hex),
    )
    assert made.status_code == 201, made.text
    return made.json()["data"]


def test_rejecting_a_proposal_keeps_the_requirement_and_leaves_only_history(
    worker_dsn, admin, app_dsn, tmp_path, demo_profiles
):
    """기각은 요구 판본·재검토 표시를 만들지 않고, 제안 행에 결정자·시각·사유가, 명령·감사 기록에 기각이 남는다. 다시 결정할 수 없다."""
    project = processed(worker_dsn, admin, tmp_path)
    requirement = requirement_of(admin, project, "BIL 95kV")
    before = versions_of(admin, requirement)
    assert before == [(1, "INSUFFICIENT_EVIDENCE", "ai_draft")]
    with client_for(app_dsn) as client:
        proposal = propose(client, project, requirement, "SPEC_GAP", "BIL 95kV 미달로 보임")
        rejected = client.post(
            f"/api/v1/tft/proposals/{proposal['proposal_id']}/reject",
            json={
                "version": proposal["version"],
                "reason": "데이터시트에 BIL 값이 없을 뿐 미달 근거는 아님",
            },
            headers=sales_headers(uuid4().hex),
        )
        assert rejected.status_code == 200, rejected.text
        body = rejected.json()["data"]
        assert body["status"] == "REJECTED" and body["decided_by"] == str(SALES)
        assert body["version"] == proposal["version"] + 1
        # 이미 결정한 제안은 승인으로 뒤집을 수 없다.
        again = client.post(
            f"/api/v1/tft/proposals/{proposal['proposal_id']}/approve",
            json={"version": body["version"]},
            headers=sales_headers(uuid4().hex),
        )
        assert again.status_code == 409, again.text
        assert again.json()["error"]["code"] == "TFT_PROPOSAL_DECIDED"
    # 요구는 그대로다(새 판본 없음).
    assert versions_of(admin, requirement) == before
    with psycopg.connect(admin) as conn:
        row = conn.execute(
            "SELECT status, decided_by, decided_at IS NOT NULL, decision_note, current = proposed "
            "FROM tft.change_proposals WHERE id=%s",
            (proposal["proposal_id"],),
        ).fetchone()
        assert row == (
            "REJECTED",
            SALES,
            True,
            "데이터시트에 BIL 값이 없을 뿐 미달 근거는 아님",
            False,
        )
        marks = conn.execute(
            "SELECT count(*) FROM tft.rereview_marks WHERE cause_proposal_id=%s",
            (proposal["proposal_id"],),
        ).fetchone()[0]
        assert marks == 0
        commands = conn.execute(
            "SELECT operation FROM tft.rfp_commands WHERE project_id=%s ORDER BY created_at",
            (project["project"],),
        ).fetchall()
        assert [c[0] for c in commands][-2:] == ["proposal.create", "proposal.reject"]


def test_the_proposal_list_separates_pending_approved_and_rejected(
    worker_dsn, admin, app_dsn, tmp_path, demo_profiles
):
    """제안 3개(대기·승인·기각)가 상태 필터마다 정확히 나뉘고, 요구 필터는 그 요구의 제안만 준다."""
    project = processed(worker_dsn, admin, tmp_path)
    ids = {
        item: requirement_of(admin, project, item)
        for item in ("알루미늄 권선", "이중 온도센서", "ModBus RS-485 연동")
    }
    base = f"/api/v1/tft/projects/{project['project']}/proposals"
    with client_for(app_dsn) as client:
        pending = propose(
            client, project, ids["알루미늄 권선"], "CONDITIONAL", "권선 재질 회신 대기"
        )
        approved = propose(client, project, ids["이중 온도센서"], "CONDITIONAL", "센서 옵션 확인")
        rejected = propose(
            client, project, ids["ModBus RS-485 연동"], "SPEC_GAP", "통신 미지원으로 보임"
        )
        ok = client.post(
            f"/api/v1/tft/proposals/{approved['proposal_id']}/approve",
            json={"version": approved["version"]},
            headers=sales_headers(uuid4().hex),
        )
        assert ok.status_code == 200, ok.text
        no = client.post(
            f"/api/v1/tft/proposals/{rejected['proposal_id']}/reject",
            json={"version": rejected["version"], "reason": "통신 모듈 옵션 존재"},
            headers=sales_headers(uuid4().hex),
        )
        assert no.status_code == 200, no.text

        def listed(**params):
            got = client.get(base, params=params, headers=sales_headers())
            assert got.status_code == 200, got.text
            return {(p["proposal_id"], p["status"]) for p in got.json()["data"]["items"]}

        everything = listed()
        assert everything == {
            (pending["proposal_id"], "PENDING"),
            (approved["proposal_id"], "APPROVED"),
            (rejected["proposal_id"], "REJECTED"),
        }
        assert listed(status="PENDING") == {(pending["proposal_id"], "PENDING")}
        assert listed(status="APPROVED") == {(approved["proposal_id"], "APPROVED")}
        assert listed(status="REJECTED") == {(rejected["proposal_id"], "REJECTED")}
        assert listed(requirement_id=str(ids["이중 온도센서"])) == {
            (approved["proposal_id"], "APPROVED")
        }
        # 프로젝트 건수의 승인 대기 수도 대기 1건이다.
        detail = client.get(f"/api/v1/tft/projects/{project['project']}", headers=sales_headers())
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["pending_proposals_count"] == 1
    # 승인한 요구만 사람 판본이 생기고 대기·기각 요구는 그대로다.
    assert versions_of(admin, ids["이중 온도센서"])[-1][1:] == ("CONDITIONAL", "human_input")
    assert len(versions_of(admin, ids["알루미늄 권선"])) == 1
    assert len(versions_of(admin, ids["ModBus RS-485 연동"])) == 1


def test_partner_draft_and_export_keep_synthetic_and_review_markers(
    worker_dsn, admin, app_dsn, tmp_path, demo_profiles, monkeypatch
):
    """협력사 후보는 합성 프로필로, 초안·내보낸 검토본은 합성 자료·검토용 문구·가상 협력사 표시를 지우지 않는다."""
    from backend.agent.processor_registry import TftJob
    from backend.api.tft.rfp import draft_exports
    from backend.api.tft.rfp.storage import LocalDocumentStore

    monkeypatch.setenv("TFT_DOCUMENT_ROOT", str(tmp_path))
    monkeypatch.setenv("TFT_DOCUMENT_BUCKET", "")
    project = processed(worker_dsn, admin, tmp_path)
    with psycopg.connect(admin) as conn:
        origin = conn.execute(
            "SELECT data_origin FROM tft.projects WHERE id=%s", (project["project"],)
        ).fetchone()[0]
    assert origin == "synthetic"
    with client_for(app_dsn) as client:
        partners = client.get(
            f"/api/v1/tft/projects/{project['project']}/partner-candidates",
            headers=sales_headers(),
        )
        assert partners.status_code == 200, partners.text
        items = partners.json()["data"]["items"]
        assert items, "협력사 후보가 하나도 없다"
        assert {p["profile_source"] for p in items} == {"synthetic"}
        assert all(p["display_name"].startswith("가상 협력사") for p in items)

        base = f"/api/v1/tft/projects/{project['project']}/draft"
        draft = client.get(base, headers=sales_headers())
        assert draft.status_code == 200, draft.text
        data = draft.json()["data"]
        assert data["data_origin"] == "synthetic"
        assert data["disclaimer"] == DISCLAIMER
        partner_rows = {sec["section"]: sec["sentences"] for sec in data["sections"]}[
            "PARTNER_SUPPLEMENT"
        ]
        assert partner_rows
        assert all(row["unconfirmed"] for row in partner_rows)
        assert all(
            any(c.startswith("가상 협력사 요구별 등급") for c in row["conditions"])
            for row in partner_rows
            if any(e["type"] == "partner" for e in row["evidence"])
        )
        saved = client.post(
            f"{base}/review-versions",
            json={"version": data["version"]},
            headers=sales_headers(uuid4().hex),
        )
        assert saved.status_code == 201, saved.text

        class Jobs:
            def __init__(self):
                self.calls = []
                self.id = uuid4()

            async def post(self, path, body):
                self.calls.append(body)
                return {
                    "job_id": str(self.id),
                    "status": "QUEUED",
                    "status_url": f"/api/v1/platform/jobs/{self.id}",
                }

        jobs = Jobs()
        client.app.state.processing_jobs = jobs
        received = client.post(
            f"{base}/exports",
            json={"review_version": saved.json()["data"]["review_version"], "format": "DOCX"},
            headers=sales_headers(uuid4().hex),
        )
        assert received.status_code == 202, received.text
        export_id = received.json()["data"]["export_id"]
        job = TftJob(
            job_id=jobs.id, kind="rfp.export", project_id=project["project"],
            input_version=jobs.calls[0]["input_version"], actor_id=SALES,
        )  # fmt: skip

        async def execute():
            async with await psycopg.AsyncConnection.connect(
                worker_dsn, row_factory=dict_row
            ) as conn:
                return await draft_exports.process(
                    conn, job, lambda event: None, LocalDocumentStore(tmp_path)
                )

        asyncio.run(execute())
        file = client.get(f"{base}/exports/{export_id}/file", headers=sales_headers())
        assert file.status_code == 200, file.text
        with zipfile.ZipFile(io.BytesIO(file.content)) as package:
            text = package.read("word/document.xml").decode()
    # 앞뒤 두 번 검토용 문구, 자료 구분은 합성, 근거 줄에 가상 협력사.
    assert text.count(DISCLAIMER) == 2
    assert "자료 구분: 합성 자료" in text
    assert "가상 협력사" in text


@pytest.mark.xfail(
    strict=True,
    reason="KAN-153 관찰: 제안 응답·목록에 기각 사유(decision_note)가 없어 화면이 기각 이유를 보여 줄 수 없다(DB에만 남음)",
)
def test_a_rejection_reason_is_visible_in_the_proposal_list(
    worker_dsn, admin, app_dsn, tmp_path, demo_profiles
):
    project = processed(worker_dsn, admin, tmp_path)
    requirement = requirement_of(admin, project, "BIL 95kV")
    with client_for(app_dsn) as client:
        proposal = propose(client, project, requirement, "SPEC_GAP", "미달로 보임")
        rejected = client.post(
            f"/api/v1/tft/proposals/{proposal['proposal_id']}/reject",
            json={"version": proposal["version"], "reason": "근거 부족일 뿐 미달 아님"},
            headers=sales_headers(uuid4().hex),
        )
        assert rejected.status_code == 200, rejected.text
        got = client.get(
            f"/api/v1/tft/projects/{project['project']}/proposals",
            params={"status": "REJECTED"},
            headers=sales_headers(),
        )
        assert got.status_code == 200, got.text
        assert got.json()["data"]["items"][0].get("decision_note") == "근거 부족일 뿐 미달 아님"
