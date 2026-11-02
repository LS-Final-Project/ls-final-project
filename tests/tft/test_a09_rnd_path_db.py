"""KAN-190 A09 후속: 시연 B의 R&D 확인 경로와 면제(WAIVED) 경로(전용 PostgreSQL, 가짜 추출기).

오새결 KAN-190 댓글 10774(API 명세 v1.3 부록 D)의 순서를 HTTP로 밟는다: 자사 개발(OWN_DEVELOPMENT) 선택 → R&D 내부 질문
자동 생성 → R&D 답변(rnd_feasibility) → 협력사 경로 선택 → 통합 책임 OWN/SINGLE_PARTNER 지정 → 검토본 저장. 면제 경로는
열린 R&D 질문이 있는데 사유(20자 이상)를 적고 협력사 경로를 고르는 조건부 회귀다. 대상은 시연 B-1 형식 요구(SPEC_GAP)다.

한계: 요구는 A09 시험의 가짜 추출기 값이다(추출 품질은 보지 않는다). 처리·HTTP 연결만 확인한다.
"""

# ruff: noqa: F811

from uuid import uuid4

import psycopg
import pytest

from backend.shared.access import Principal, principal_header
from tests.tft.test_a09_demo_flow_db import (
    DEMO_B1,
    client_for,
    demo_profiles,  # noqa: F401
    processed,
    sales_headers,
)
from tests.tft.test_rfp_http_base_db import admin, app_dsn  # noqa: F401
from tests.tft.test_worker_processor_db import worker_dsn  # noqa: F401

FORM = "형식: 금속 폐쇄형·고정식·퓨즈 부하개폐기"
WAIVER = "R&D 회신 일정이 제안 마감보다 늦어 협력사 경로를 먼저 검토합니다"


def rnd_headers(key=None):
    """프로젝트 검토 부서(RND_FW, 분류 RND) 담당자."""
    headers = principal_header(
        Principal(user_id=uuid4(), roles=frozenset({"dept_staff"}), dept="RND_FW")
    )
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def requirement_of(admin, project, quote):
    with psycopg.connect(admin) as conn:
        return conn.execute(
            "SELECT r.id FROM tft.requirements r JOIN tft.requirement_versions v ON v.requirement_id=r.id "
            "WHERE r.project_id=%s AND v.quote=%s LIMIT 1",
            (project["project"], quote),
        ).fetchone()[0]


def detail(client, requirement):
    got = client.get(f"/api/v1/tft/requirements/{requirement}", headers=sales_headers())
    assert got.status_code == 200, got.text
    return got.json()["data"]


def options(client, requirement):
    got = client.get(
        f"/api/v1/tft/requirements/{requirement}/supplement-options", headers=sales_headers()
    )
    assert got.status_code == 200, got.text
    return {o["path"]: o for o in got.json()["data"]["options"]}


def select(client, requirement, version, path, **extra):
    return client.post(
        f"/api/v1/tft/requirements/{requirement}/supplement-selection",
        json={"version": version, "path": path, "role_split": "자사 검토 후 결정", **extra},
        headers=sales_headers(uuid4().hex),
    )


def stored_selection(admin, requirement):
    with psycopg.connect(admin) as conn:
        return conn.execute(
            "SELECT path, rnd_check, rnd_check_waiver_reason, candidate_codes FROM tft.supplement_selections "
            "WHERE requirement_id=%s ORDER BY decided_at DESC LIMIT 1",
            (requirement,),
        ).fetchone()


def rnd_questions(admin, requirement):
    with psycopg.connect(admin) as conn:
        return conn.execute(
            "SELECT id, target, target_dept, issue_type, status FROM tft.customer_questions "
            "WHERE requirement_id=%s AND issue_type='RND_FEASIBILITY' ORDER BY created_at",
            (requirement,),
        ).fetchall()


def assembly_project(worker_dsn, admin, tmp_path):
    """시연 B 조립품 조건(14번 2.3 A "Single assembly")까지 넣어 처리한 프로젝트. 통합 책임 확인이 필요해진다."""
    from tests.tft.test_a09_demo_flow_db import ALL, SPEC14, DemoExtractor, req
    from tests.tft.test_worker_processor_db import make_project, run

    spec = SPEC14.replace(
        "2.4 DUAL",
        "2.3 MANUFACTURED UNITS\n\nA. Indoor Unit Arrangement: Single assembly.\n\n2.4 DUAL",
    )
    items = [
        *ALL.values(),
        req("2.3.A", "Indoor Unit Arrangement: Single assembly.", "OTHER", None, "single assembly",
            operator="TEXT", equipment="unit substation", assembly_clause=True),
    ]  # fmt: skip
    project = make_project(admin, tmp_path, spec.encode())
    extractor = DemoExtractor(items)
    run(worker_dsn, project, extractor)
    return project, lambda: run(worker_dsn, project, extractor)


def draft_text(client, project):
    got = client.get(f"/api/v1/tft/projects/{project['project']}/draft", headers=sales_headers())
    assert got.status_code == 200, got.text
    data = got.json()["data"]
    return data["version"], [
        (section["section"], sentence["text"])
        for section in data["sections"]
        for sentence in section["sentences"]
    ]


def save_review(client, project, version):
    return client.post(
        f"/api/v1/tft/projects/{project['project']}/draft/review-versions",
        json={"version": version},
        headers=sales_headers(uuid4().hex),
    )


@pytest.mark.parametrize(
    ("holder", "named"), [("OWN", "자사"), ("SINGLE_PARTNER", "단일 협력사(유닛 일괄 공급·통합)")]
)
def test_rnd_path_from_own_development_to_partner_and_review_save(
    worker_dsn, admin, app_dsn, tmp_path, demo_profiles, holder, named
):
    """자사 개발 선택 → R&D 질문 → 불가 답변 → 협력사 경로 → 통합 책임 지정 → 검토본 저장(명세 v1.3 부록 D, 기획서 4.4·4.8)."""
    project, reprocess = assembly_project(worker_dsn, admin, tmp_path)
    requirement = requirement_of(admin, project, DEMO_B1[FORM]["quote"])
    with client_for(app_dsn) as client:
        # 1. 보완 경로 순서: 자사 개발이 협력사보다 먼저고, 협력사 경로는 자사 개발 확인을 거친다(기획서 4.4).
        first = detail(client, requirement)
        assert first["judgement"]["status"] == "SPEC_GAP"
        assert first["judgement"]["rnd_check"] == "NOT_REQUIRED"
        paths = options(client, requirement)
        assert paths["OWN_DEVELOPMENT"]["allowed"] and paths["EXTERNAL_PRODUCT"]["allowed"]
        assert paths["OWN_DEVELOPMENT"]["rank"] < paths["EXTERNAL_PRODUCT"]["rank"]
        assert paths["EXTERNAL_PRODUCT"]["gate"]["requires"] == "OWN_DEVELOPMENT_CHECK"
        assert "P-A" in paths["EXTERNAL_PRODUCT"]["candidate_codes"]

        # 2. 자사 개발을 고르면 R&D 부서(RND_FW) 대상 내부 질문이 생기고 R&D 확인은 진행 중이 된다.
        chosen = select(client, requirement, first["version"], "OWN_DEVELOPMENT")
        assert chosen.status_code == 200, chosen.text
        assert stored_selection(admin, requirement)[:2] == ("OWN_DEVELOPMENT", "PENDING")
        questions = rnd_questions(admin, requirement)
        assert [(q[1], q[2], q[3], q[4]) for q in questions] == [
            ("INTERNAL", "RND_FW", "RND_FEASIBILITY", "OPEN")
        ]
        pending = detail(client, requirement)["judgement"]
        assert (pending["rnd_check"], pending["rnd_question_id"]) == (
            "PENDING",
            str(questions[0][0]),
        )

        # 3. R&D 답 없이 협력사 경로를 사유 없이 고르면 막힌다.
        early = select(
            client, requirement, first["version"], "EXTERNAL_PRODUCT", candidate_codes=["P-A"]
        )
        assert early.status_code == 422, early.text
        assert early.json()["error"]["code"] == "TFT_RND_CHECK_REQUIRED"
        assert early.json()["error"]["details"] == [
            {"field": "rnd_question_id", "reason": str(questions[0][0])}
        ]

        # 4. R&D 부서 담당자가 개발 불가로 답한다. 자사 개발 경로가 막히고 협력사 경로가 열린다.
        replied = client.post(
            f"/api/v1/tft/questions/{questions[0][0]}/reply",
            json={
                "version": 1,
                "reply_text": "15kV 퓨즈 부하개폐기반은 자사 개발 계획이 없습니다.",
                "received_at": "2026-11-02T01:00:00Z",
                "rnd_feasibility": "NOT_FEASIBLE",
            },
            headers=rnd_headers(uuid4().hex),
        )
        assert replied.status_code == 200, replied.text
        answered = detail(client, requirement)
        assert answered["judgement"]["rnd_check"] == "CONFIRMED_NOT_FEASIBLE"
        assert answered["needs_rereview"] is True
        paths = options(client, requirement)
        assert paths["OWN_DEVELOPMENT"]["allowed"] is False
        assert paths["OWN_DEVELOPMENT"]["blocked_reason"] == "OWN_DEVELOPMENT_NOT_FEASIBLE"
        assert paths["EXTERNAL_PRODUCT"]["allowed"] is True

        # 5. 협력사 경로(외부 제품, 검토 후보 P-A)를 고르면 R&D 확인 결과가 함께 남고 재검토 표시가 풀린다.
        partner = select(
            client, requirement, answered["version"], "EXTERNAL_PRODUCT", candidate_codes=["P-A"]
        )
        assert partner.status_code == 200, partner.text
        assert stored_selection(admin, requirement) == (
            "EXTERNAL_PRODUCT", "CONFIRMED_NOT_FEASIBLE", None, ["P-A"],
        )  # fmt: skip
        assert detail(client, requirement)["needs_rereview"] is False

        # 6. 조립품 공급(2.3 A)이라 통합 책임자가 정해지기 전에는 검토본을 저장할 수 없다(기획서 4.8).
        project_row = client.get(
            f"/api/v1/tft/projects/{project['project']}", headers=sales_headers()
        )
        integration = project_row.json()["data"]["integration_responsibility"]
        assert (integration["required"], integration["holder"], integration["basis_clause_nos"]) == (
            True, "UNRESOLVED", ["2.3.A"],
        )  # fmt: skip
        version, _ = draft_text(client, project)
        blocked = save_review(client, project, version)
        assert blocked.status_code == 422, blocked.text
        assert blocked.json()["error"]["code"] == "TFT_DRAFT_INCOMPLETE"
        assert blocked.json()["error"]["details"] == [
            {"field": "integration_responsibility", "reason": "UNRESOLVED"}
        ]

        # 7. 통합 책임을 사람이 정한다(OWN 또는 SINGLE_PARTNER).
        patched = client.patch(
            f"/api/v1/tft/projects/{project['project']}",
            json={
                "version": project_row.json()["data"]["version"],
                "integration_responsibility": {
                    "holder": holder,
                    "holder_candidate_codes": ["P-A"] if holder == "SINGLE_PARTNER" else [],
                },
            },
            headers=sales_headers(uuid4().hex),
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["data"]["integration_responsibility"]["holder"] == holder

    # 8. 다음 처리에서 초안 전제 문장에 지정한 책임자가 들어가고, 그 초안을 검토본으로 저장한다.
    reprocess()
    with client_for(app_dsn) as client:
        version, sentences = draft_text(client, project)
        assert any(
            section == "ASSUMPTIONS_EXCLUSIONS" and f"통합 책임자: {named}." in text
            for section, text in sentences
        ), sentences
        saved = save_review(client, project, version)
        assert saved.status_code == 201, saved.text
        assert saved.json()["data"]["output_status"] == "REVIEW_DRAFT"
        # 다른 미해결 필수 조건이 남아 판단 상태는 "조건 확인 필요"다(기획서 4.1).
        assert saved.json()["data"]["judgement_status"] == "CONDITION_CHECK"


def test_waived_partner_path_keeps_the_waiver_reason(
    worker_dsn, admin, app_dsn, tmp_path, demo_profiles
):
    """조건부 회귀(면제): R&D 질문이 열려 있어도 20자 이상 사유를 적으면 협력사 경로를 고를 수 있고, 사유와 WAIVED가 남는다."""
    project = processed(worker_dsn, admin, tmp_path)
    requirement = requirement_of(admin, project, DEMO_B1[FORM]["quote"])
    with client_for(app_dsn) as client:
        version = detail(client, requirement)["version"]
        assert select(client, requirement, version, "OWN_DEVELOPMENT").status_code == 200
        assert detail(client, requirement)["judgement"]["rnd_check"] == "PENDING"
        short = select(client, requirement, version, "EXTERNAL_PRODUCT", candidate_codes=["P-A"],
                       rnd_check_waiver_reason="일정이 촉박함")  # fmt: skip
        assert short.status_code == 422, short.text
        assert short.json()["error"]["details"][0]["field"] == "body.rnd_check_waiver_reason"
        waived = select(client, requirement, version, "EXTERNAL_PRODUCT", candidate_codes=["P-A"],
                        rnd_check_waiver_reason=WAIVER)  # fmt: skip
        assert waived.status_code == 200, waived.text
        assert stored_selection(admin, requirement) == (
            "EXTERNAL_PRODUCT",
            "WAIVED",
            WAIVER,
            ["P-A"],
        )
        assert detail(client, requirement)["judgement"]["rnd_check"] == "WAIVED"
        # 면제해도 R&D 질문은 지우지 않는다(기록으로 남음).
        assert [q[4] for q in rnd_questions(admin, requirement)] == ["OPEN"]


@pytest.mark.xfail(
    strict=True,
    reason="KAN-190 관찰(dev 9df5089): rnd.rnd_question_for 설명은 호출자가 next_owner_field=RND로 둔다고 하지만 "
    "자사 개발 선택 뒤에도 요구 판단의 next_owner_field가 TECH로 남는다",
)
def test_own_development_hands_the_requirement_to_rnd(
    worker_dsn, admin, app_dsn, tmp_path, demo_profiles
):
    project = processed(worker_dsn, admin, tmp_path)
    requirement = requirement_of(admin, project, DEMO_B1[FORM]["quote"])
    with client_for(app_dsn) as client:
        version = detail(client, requirement)["version"]
        assert select(client, requirement, version, "OWN_DEVELOPMENT").status_code == 200
        assert detail(client, requirement)["judgement"]["next_owner_field"] == "RND"


@pytest.mark.xfail(
    strict=True,
    reason="KAN-190 관찰(dev 9df5089): 통합 책임자를 지정해도 초안 전제 문장은 다음 처리 전까지 '미확보'로 남아, "
    "지정 직후 저장한 검토본에 옛 문장이 들어간다",
)
def test_a_review_saved_right_after_naming_the_holder_carries_it(
    worker_dsn, admin, app_dsn, tmp_path, demo_profiles
):
    project, _ = assembly_project(worker_dsn, admin, tmp_path)
    with client_for(app_dsn) as client:
        row = client.get(f"/api/v1/tft/projects/{project['project']}", headers=sales_headers())
        patched = client.patch(
            f"/api/v1/tft/projects/{project['project']}",
            json={
                "version": row.json()["data"]["version"],
                "integration_responsibility": {"holder": "OWN"},
            },
            headers=sales_headers(uuid4().hex),
        )
        assert patched.status_code == 200, patched.text
        _, sentences = draft_text(client, project)
        assert any("통합 책임자: 자사." in text for _, text in sentences), sentences


@pytest.mark.xfail(
    strict=True,
    reason="KAN-190 관찰(dev 9df5089): rnd.check_selection 설명은 면제로 협력사 경로를 고르면 초안 ASSUMPTIONS에 "
    "'자사 개발 가능성 미확인' 문장이 생긴다고 하지만 다시 처리해도 그 문장이 없다",
)
def test_a_waived_choice_adds_the_unconfirmed_own_development_assumption(
    worker_dsn, admin, app_dsn, tmp_path, demo_profiles
):
    project, reprocess = assembly_project(worker_dsn, admin, tmp_path)
    requirement = requirement_of(admin, project, DEMO_B1[FORM]["quote"])
    with client_for(app_dsn) as client:
        version = detail(client, requirement)["version"]
        assert select(client, requirement, version, "OWN_DEVELOPMENT").status_code == 200
        waived = select(client, requirement, version, "EXTERNAL_PRODUCT", candidate_codes=["P-A"],
                        rnd_check_waiver_reason=WAIVER)  # fmt: skip
        assert waived.status_code == 200, waived.text
    reprocess()
    with client_for(app_dsn) as client:
        _, sentences = draft_text(client, project)
        assert any(
            section == "ASSUMPTIONS_EXCLUSIONS" and "자사 개발 가능성 미확인" in text
            for section, text in sentences
        ), sentences
