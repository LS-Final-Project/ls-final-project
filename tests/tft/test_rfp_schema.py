"""KAN-182 RFP 상태 값·경로 골격이 명세 v1.1과 맞는지 확인한다(DB 없음)."""

from typing import get_args

import pytest
from fastapi.testclient import TestClient

from backend.api.tft.main import create_app
from backend.api.tft.rfp import schemas as s
from backend.shared.settings import Settings

# 명세 v1.1 §2.1의 값 그대로. 바꾸려면 명세와 이 표를 함께 고친다.
SPEC = {
    "OutputStatus": ["INTAKE", "ANALYZING", "REVIEW_DRAFT"],
    "JudgementStatus": ["NEEDS_REVIEW", "CONDITION_CHECK", "OWNER_CONFIRMED"],
    "RequirementStatus": ["EVIDENCE_FOUND", "CONDITIONAL", "SPEC_GAP", "INSUFFICIENT_EVIDENCE"],
    "Necessity": ["MANDATORY", "PREFERRED", "CONDITION_BOUND", "UNDETERMINED"],
    "SupplementPath": [
        "OTHER_OWN_PRODUCT",
        "ALTERNATIVE_SPEC",
        "EXTERNAL_PRODUCT",
        "TASK_SUBCONTRACT",
        "JOINT_PROPOSAL",
        "NONE",
    ],
    "DeficiencyCause": ["RATING", "CERTIFICATION", "FORM", "TASK"],
    "CustomerPermission": ["ALLOWED", "FORBIDDEN", "UNSTATED"],
    "Grade": ["MANDATORY_CONFIRMED", "REVIEW", "REFERENCE_ONLY", "EXCLUDED"],
    "QuestionStatus": ["OPEN", "REPLIED"],
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


@pytest.fixture
def client():
    return TestClient(create_app(Settings(_env_file=None, environment="test")))


def test_declared_routes_answer_not_implemented(client):
    response = client.post(
        "/api/v1/tft/projects",
        json={"name": "p", "company_code": "HOST", "data_origin": "synthetic"},
        headers={"Idempotency-Key": "create-0001"},
    )
    assert (response.status_code, response.json()["error"]["code"]) == (501, "TFT_NOT_IMPLEMENTED")


def test_openapi_lists_the_spec_routes_as_planned(client):
    paths = client.get("/openapi.json").json()["paths"]
    planned = [
        (path, method)
        for path, ops in paths.items()
        for method, op in ops.items()
        if op.get("x-implementation-status") == "planned"
    ]
    # 명세 §3의 30개 중 기존 목록·상세 2개를 뺀 28개.
    assert len(planned) == 28
    assert ("/api/v1/tft/requirements/{requirement_id}/supplement-selection", "post") in planned
    upload = paths["/api/v1/tft/projects/{project_id}/documents"]["post"]
    assert "multipart/form-data" in upload["requestBody"]["content"]


def test_every_state_creating_post_requires_an_idempotency_key(client):
    paths = client.get("/openapi.json").json()["paths"]
    posts = [
        (path, op)
        for path, ops in paths.items()
        for method, op in ops.items()
        if method == "post" and op.get("x-implementation-status") == "planned"
    ]
    assert len(posts) == 12
    for path, op in posts:
        keys = [x for x in op.get("parameters", []) if x["name"] == "Idempotency-Key"]
        assert keys and keys[0]["in"] == "header" and keys[0]["required"], path


def test_file_downloads_are_declared_as_binary_only(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in (
        "/api/v1/tft/documents/{document_id}/file",
        "/api/v1/tft/projects/{project_id}/draft/exports/{export_id}/file",
    ):
        assert list(paths[path]["get"]["responses"]["200"]["content"]) == [
            "application/octet-stream"
        ], path
