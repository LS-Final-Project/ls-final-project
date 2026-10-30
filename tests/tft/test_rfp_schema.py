"""KAN-182 RFP 상태 값·경로 골격이 명세 v1.1과 맞는지 확인한다(DB 없음)."""

from typing import get_args
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.tft.main import create_app
from backend.api.tft.rfp import schemas as s
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

# 명세 v1.1 §2.1의 값 그대로. 바꾸려면 명세와 이 표를 함께 고친다.
SPEC = {
    "OutputStatus": ["INTAKE", "ANALYZING", "REVIEW_DRAFT"],
    "JudgementStatus": ["NEEDS_REVIEW", "CONDITION_CHECK", "OWNER_CONFIRMED"],
    "RequirementStatus": ["EVIDENCE_FOUND", "CONDITIONAL", "SPEC_GAP", "INSUFFICIENT_EVIDENCE"],
    "Necessity": ["MANDATORY", "PREFERRED", "CONDITION_BOUND", "UNDETERMINED"],
    # 명세 v1.3 §2(2026-10-30). OWN_TERMS_REVIEW는 명세 추가 요청 중(KAN-186 댓글).
    "SupplementPath": [
        "OTHER_OWN_PRODUCT",
        "OWN_DEVELOPMENT",
        "OWN_TERMS_REVIEW",
        "ALTERNATIVE_SPEC",
        "EXTERNAL_PRODUCT",
        "TASK_SUBCONTRACT",
        "JOINT_PROPOSAL",
        "CUSTOMER_SPECIFIED_PROCUREMENT",
        "NONE",
    ],
    "DeficiencyCause": [
        "RATING",
        "CERTIFICATION",
        "FORM",
        "TASK",
        "DESIGNATED_MODEL",
        "COMMERCIAL",
        "CORE_EQUIPMENT_MISSING",
    ],
    "CustomerPermission": ["ALLOWED", "FORBIDDEN", "UNSTATED"],
    "NextOwner": ["SALES", "TECH", "PURCHASE", "CUSTOMER", "RND"],
    "RndCheck": [
        "NOT_REQUIRED",
        "PENDING",
        "CONFIRMED_FEASIBLE",
        "CONFIRMED_NOT_FEASIBLE",
        "CONFIRMED_SCHEDULE_MISMATCH",
        "WAIVED",
    ],
    "Grade": ["MANDATORY_CONFIRMED", "REVIEW", "REFERENCE_ONLY", "EXCLUDED"],
    "QuestionStatus": ["DRAFT", "OPEN", "REPLIED"],
    "ProposalOrigin": ["web", "customer_reply", "pdf_annot"],
    "ProposalStatus": ["PENDING", "APPROVED", "REJECTED"],
    "JobKind": ["rfp.process", "rfp.export", "rfp.annotate"],
    "JobStep": [
        "parse",
        "extract",
        "search",
        "compare",
        "supplement",
        "candidates",
        "draft",
        "export",
        "annotate",
    ],
}


@pytest.mark.parametrize("name", sorted(SPEC))
def test_state_values_match_the_spec_exactly(name):
    values = get_args(s.Grade if name == "Grade" else getattr(s, name))
    assert list(values) == SPEC[name]


def test_requirement_nature_and_status_use_different_words():
    # 요구 성격의 조건부(CONDITION_BOUND)와 요구 상태의 조건부 대응(CONDITIONAL)을 섞지 않는다.
    assert set(get_args(s.Necessity)).isdisjoint(get_args(s.RequirementStatus))


def test_project_creation_requires_an_explicit_data_origin():
    with pytest.raises(ValueError):
        s.ProjectCreate(name="p", company_code="HOST")
    assert s.ProjectCreate(name="p", company_code="HOST", data_origin="synthetic").purpose == (
        "INITIAL_PROPOSAL"
    )


def test_every_business_detail_carries_version_and_both_provenance_axes():
    for model in (
        s.RfpProjectDetail,
        s.DocumentVersion,
        s.RequirementDetail,
        s.Comparison,
        s.SupplementOptions,
        s.SupplementSelected,
        s.Question,
        s.Proposal,
        s.Draft,
    ):
        assert {"version", "data_origin", "derivation_kind", "known_at"} <= set(
            model.model_fields
        ), model.__name__


def test_candidate_grades_have_no_score():
    fields = set(s.CandidateGrade.model_fields) | set(s.PartnerCandidate.model_fields)
    assert not {f for f in fields if "score" in f or "similarity" in f}


SALES = principal_header(Principal(user_id=uuid4(), roles={"sales"}))


@pytest.fixture
def client():
    return TestClient(create_app(Settings(_env_file=None, environment="test")))


def test_openapi_lists_all_spec_routes_as_implemented(client):
    paths = client.get("/openapi.json").json()["paths"]
    planned = [
        (path, method)
        for path, ops in paths.items()
        for method, op in ops.items()
        if op.get("x-implementation-status") == "planned"
    ]
    # 변경 제안 4경로와 초안 5경로 통합으로 명세 경로에 남은 골격이 없다.
    assert planned == []
    assert ("/api/v1/tft/requirements/{requirement_id}/supplement-selection", "post") not in planned
    upload = paths["/api/v1/tft/projects/{project_id}/documents"]["post"]
    assert "multipart/form-data" in upload["requestBody"]["content"]
    assert "413" in upload["responses"]


def test_every_state_creating_post_requires_an_idempotency_key(client):
    paths = client.get("/openapi.json").json()["paths"]
    posts = [
        (path, op)
        for path, ops in paths.items()
        for method, op in ops.items()
        if method == "post"
        and (
            op.get("x-implementation-status") == "planned"
            or op.get("operationId") in {"tft_save_rfp_review_version", "tft_export_rfp_draft"}
        )
    ]
    assert len(posts) == 2
    for path, op in posts:
        keys = [x for x in op.get("parameters", []) if x["name"] == "Idempotency-Key"]
        assert keys and keys[0]["in"] == "header" and keys[0]["required"], path


def test_file_downloads_are_declared_as_binary_only(client):
    paths = client.get("/openapi.json").json()["paths"]
    expected = {
        "/api/v1/tft/documents/{document_id}/file": "application/octet-stream",
        "/api/v1/tft/projects/{project_id}/draft/exports/{export_id}/file": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    for path, mime in expected.items():
        assert list(paths[path]["get"]["responses"]["200"]["content"]) == [mime], path


def test_every_rfp_route_checks_the_user_before_anything_else(client):
    body = {"name": "p", "company_code": "HOST", "data_origin": "synthetic"}
    key = {"Idempotency-Key": "create-0001"}
    # 사용자 맥락이 없으면 501·422보다 먼저 401이다.
    assert client.post("/api/v1/tft/projects", json=body, headers=key).status_code == 401
    assert client.get(f"/api/v1/tft/requirements/{uuid4()}").status_code == 401
    agent = principal_header(Principal(user_id=uuid4(), roles={"cs_agent"}))
    assert (
        client.post("/api/v1/tft/projects", json=body, headers={**key, **agent}).status_code == 403
    )


def test_internal_questions_need_a_department_and_internal_issue_types_stay_internal():
    """명세 v1.3 §5.3: INTERNAL 질문은 target_dept가 있어야 하고, 내부 자문 유형은 INTERNAL에만 쓴다."""
    base = {
        "requirement_id": None,
        "origin": {"clause_id": None, "page": None},
        "question_text": "?",
    }
    ok = s.QuestionCreate(
        **base, issue_type="RND_FEASIBILITY", target="INTERNAL", target_dept="RND_FW"
    )
    assert ok.target == "INTERNAL"
    assert s.QuestionCreate(**base, issue_type="EMPTY_VALUE").target == "CUSTOMER"
    for bad in (
        {"issue_type": "RND_FEASIBILITY", "target": "INTERNAL"},
        {"issue_type": "EMPTY_VALUE", "target_dept": "RND_FW"},
        {"issue_type": "TECH_ADVICE"},
    ):
        with pytest.raises(ValueError):
            s.QuestionCreate(**base, **bad)
    with pytest.raises(ValueError):
        s.SupplementSelection(
            version=1, path="EXTERNAL_PRODUCT", role_split="x", rnd_check_waiver_reason="짧음"
        )


def test_project_strategic_customer_and_integration_fields_follow_spec_v1_3():
    """명세 v1.3 §4 #2·#3·#4·§6·§7: 전략 고객은 선택 표시값, 통합 책임은 사람이 정하는 4값."""
    created = s.ProjectCreate(name="14번", company_code="HOST", data_origin="synthetic")
    assert (created.strategic_customer, created.strategic_customer_note) == (None, None)
    update = s.ProjectUpdate(
        version=1,
        strategic_customer=True,
        integration_responsibility={"holder": "SINGLE_PARTNER", "holder_candidate_codes": ["P-A"]},
    )
    assert update.integration_responsibility.holder == "SINGLE_PARTNER"
    for bad in (
        {"strategic_customer_note": "x" * 201},
        {"integration_responsibility": {"holder": "NOBODY"}},
    ):
        with pytest.raises(ValueError):
            s.ProjectUpdate(version=1, **bad)
    # Responses default to "not entered" and "unresolved" for projects stored before v1.3.
    assert s.IntegrationResponsibility().holder == "UNRESOLVED"
    item = s.DesignatedItem(manufacturer="SEL", model="SEL-751A", clause_id=None)
    assert item.procurement == "SPECIFIED_PROCUREMENT"
