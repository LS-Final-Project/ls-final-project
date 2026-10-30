"""일반 합성 스냅샷으로 제안 초안의 근거·조건·참조 경계를 검증한다(KAN-188)."""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, get_args
from uuid import UUID

import pytest

from backend.api.tft.rfp import schemas as s
from backend.engine.rfp.draft import DISCLAIMER, build_draft

NOW = datetime(2026, 10, 30, tzinfo=UTC)
PROJECT = UUID(int=1)
REQ = UUID(int=2)
CLAUSE = UUID(int=3)
QUESTION = UUID(int=4)
USER = UUID(int=5)
META = dict(version=2, data_origin="synthetic", derivation_kind="DERIVED", known_at=NOW)


@pytest.fixture
def snapshot() -> dict[str, Any]:
    """실제 데이터에 의존하지 않는 요구 하나의 공개 스냅샷을 만든다."""
    requirement = s.RequirementDetail.model_validate(
        dict(
            **META,
            requirement_id=REQ,
            code="SYN-REQ",
            origin=dict(
                document_version_id=UUID(int=6),
                clause_id=CLAUSE,
                clause_no="S.1",
                page=1,
                quote="The control panel shall have rated current 600 A.",
                deleted_values=[],
                edit_trace_support=True,
            ),
            spec=dict(
                equipment="control panel",
                task=None,
                attribute="rated current",
                value="600",
                unit="A",
                operator="EQ",
                conditions=[dict(name="ambient", value="35", unit="°C")],
                standard_system="IEC",
                necessity="MANDATORY",
                designated_model=None,
                undetermined_items=[],
            ),
            judgement=dict(
                status="CONDITIONAL",
                reason="근거의 적용 조건 확인 필요",
                unresolved=["현장 적용 범위"],
                path="EXTERNAL_PRODUCT",
                next_owner_field="TECH",
                provenance_kind="ai_draft",
                decided_by=None,
                decided_at=None,
                rnd_check="PENDING",
            ),
            links=dict(
                comparison_summary=None,
                supplement_summary=None,
                partner_candidate_ids=["SYN-PARTNER"],
                question_ids=[QUESTION],
                pending_proposal_ids=[],
                draft_sentence_ids=[],
            ),
            needs_rereview=False,
        )
    )
    comparison = s.Comparison.model_validate(
        dict(
            **META,
            requirement_id=REQ,
            requirement_version=2,
            provenance_kind="ai_draft",
            candidates=[
                dict(
                    product_code="SYN-PRODUCT",
                    variant=None,
                    scope=["control"],
                    lifecycle_status="ACTIVE",
                    attribute_match=dict(
                        matched_attribute="rated_current",
                        our_value="500",
                        our_unit="A",
                        our_conditions=[dict(name="ambient", value="40", unit="°C")],
                        comparable=True,
                        result="DIFFERS",
                        evidence=[
                            dict(
                                document="synthetic-datasheet",
                                clause="T.2",
                                version="1",
                                applicability="특정 구성에서만 적용",
                                limits="현장 시험 필요",
                            )
                        ],
                    ),
                )
            ],
            search_trace=dict(
                filters=["family=CONTROL"], documents_searched=1, search_failed=False
            ),
        )
    )
    option = s.SupplementOption.model_validate(
        dict(
            path="EXTERNAL_PRODUCT",
            rank=3,
            gate=dict(requires="OWN_DEVELOPMENT_CHECK", status="PENDING"),
            allowed=True,
            conditions_to_confirm=["계약 범위"],
            required_expertise=["control"],
            candidate_codes=["SYN-PARTNER"],
            blocked_reason=None,
        )
    )
    options = s.SupplementOptions.model_validate(
        dict(
            **META,
            requirement_id=REQ,
            deficiency_cause="RATING",
            customer_permission="UNSTATED",
            permission_clause_id=CLAUSE,
            options=[
                option,
                option.model_copy(
                    update={
                        "path": "ALTERNATIVE_SPEC",
                        "rank": 2,
                        "candidate_codes": [],
                    }
                ),
            ],
        )
    )
    partner = s.PartnerCandidate.model_validate(
        dict(
            candidate_code="SYN-PARTNER",
            display_name="가상 후보",
            profile_source="synthetic",
            profile_version=3,
            scope=[],
            spec_facts_attributes=[],
            certifications=[],
            integrator_capable={},
            on_customer_manufacturer_list=None,
            lead_time=None,
            warranty=None,
            confirmation_status="UNCONFIRMED",
            overall_grade="REVIEW",
            conditions_to_confirm=["공급 조건"],
            grades=[
                dict(
                    requirement_id=REQ,
                    grade="REVIEW",
                    reasons=["현장 조건 미확인"],
                    exclusion_reasons=[],
                    mandatory_check=dict(
                        passed=["rated_current"], failed=[], unknown=["contract_scope"]
                    ),
                )
            ],
        )
    )
    question = s.Question.model_validate(
        dict(
            **META,
            question_id=QUESTION,
            requirement_id=REQ,
            origin=dict(clause_id=CLAUSE, page=1),
            issue_type="CONDITION_MISSING",
            question_text="현장 적용 조건을 확인해 주시겠습니까?",
            internal_assumption=None,
            status="DRAFT",
            reply_received_at=None,
            created_by=USER,
            created_at=NOW,
        )
    )
    return dict(
        project_id=PROJECT,
        version=1,
        data_origin="synthetic",
        known_at=NOW,
        requirements=[requirement],
        comparisons=[comparison],
        options=[options],
        selections=[],
        partners=[partner],
        questions=[question],
        integration=s.IntegrationResponsibility(
            required=True,
            holder="UNRESOLVED",
            basis_clause_ids=[CLAUSE],
            basis_clause_nos=["S.1"],
            unresolved=["통합 책임자 확인"],
        ),
    )


def sentences(draft: s.Draft, section: s.DraftSection | None = None) -> list[s.Sentence]:
    """초안 문장이나 지정 절 문장을 꺼낸다."""
    return [
        row
        for part in draft.sections
        if section is None or part.section == section
        for row in part.sentences
    ]


def test_six_sections_grounding_counts_and_input_are_preserved(snapshot: dict[str, Any]) -> None:
    before = deepcopy(snapshot)
    result = build_draft(**snapshot)
    assert isinstance(result, s.Draft)
    assert s.Draft.model_validate(result.model_dump()) == result
    assert [part.section for part in result.sections] == list(get_args(s.DraftSection))
    assert all(part.sentences for part in result.sections)
    assert result.disclaimer == "시연용 검토본이며 실제 제안서가 아니다." == DISCLAIMER
    assert result.version == 1 and result.known_at == NOW and result.derivation_kind == "DERIVED"
    registry = {
        ("requirement", str(REQ)),
        ("clause", str(CLAUSE)),
        ("question", str(QUESTION)),
        ("product", "SYN-PRODUCT"),
        ("partner", "SYN-PARTNER"),
    }
    for row in sentences(result):
        assert row.evidence and not row.evidence_missing
        assert {(e.type, e.id) for e in row.evidence} <= registry
        assert row.provenance_kind == "system_record"
    assert (
        result.evidence_missing_count == sum(row.evidence_missing for row in sentences(result)) == 0
    )
    ids = [row.sentence_id for row in sentences(result)]
    assert len(ids) == len(set(ids))
    assert build_draft(**snapshot) == result
    assert snapshot == before


def test_empty_snapshot_does_not_create_unsupported_goal_or_filler() -> None:
    result = build_draft(
        project_id=PROJECT,
        version=1,
        data_origin="demo",
        known_at=NOW,
        requirements=[],
        integration=s.IntegrationResponsibility(),
    )
    assert len(result.sections) == 6 and sentences(result) == []
    assert result.evidence_missing_count == 0 and result.disclaimer == DISCLAIMER


@pytest.mark.parametrize(
    "key", ["requirements", "comparisons", "options", "selections", "partners", "questions"]
)
def test_duplicate_snapshots_are_rejected(snapshot: dict[str, Any], key: str) -> None:
    if key == "selections":
        snapshot[key] = [selection()]
    snapshot[key].append(deepcopy(snapshot[key][0]))
    with pytest.raises(ValueError, match="DUPLICATE_REFERENCE"):
        build_draft(**snapshot)


@pytest.mark.parametrize("key", ["comparisons", "options"])
def test_requirement_version_mismatch_is_rejected(snapshot: dict[str, Any], key: str) -> None:
    if key == "comparisons":
        snapshot[key][0].requirement_version = 1
    else:
        snapshot[key][0].version = 1
    with pytest.raises(ValueError, match="VERSION_MISMATCH"):
        build_draft(**snapshot)


@pytest.mark.parametrize("key", ["comparisons", "options", "questions"])
def test_unknown_requirement_is_rejected(snapshot: dict[str, Any], key: str) -> None:
    snapshot[key][0].requirement_id = UUID(int=900)
    with pytest.raises(ValueError, match="UNKNOWN_REFERENCE"):
        build_draft(**snapshot)


@pytest.mark.parametrize(
    "case",
    [
        "question_link",
        "partner_link",
        "question_clause",
        "permission_clause",
        "option_candidate",
        "holder",
    ],
)
def test_dangling_public_references_are_rejected(snapshot: dict[str, Any], case: str) -> None:
    if case == "question_link":
        snapshot["requirements"][0].links.question_ids = [UUID(int=900)]
    elif case == "partner_link":
        snapshot["requirements"][0].links.partner_candidate_ids = ["missing"]
    elif case == "question_clause":
        snapshot["questions"][0].origin.clause_id = UUID(int=900)
    elif case == "permission_clause":
        snapshot["options"][0].permission_clause_id = UUID(int=900)
    elif case == "option_candidate":
        snapshot["options"][0].options[0].candidate_codes = ["missing"]
    else:
        snapshot["integration"].holder_candidate_codes = ["missing"]
    with pytest.raises(ValueError, match="UNKNOWN_REFERENCE"):
        build_draft(**snapshot)


@pytest.mark.parametrize(
    "case",
    [
        "blank_origin",
        "data_origin",
        "partner_grade",
        "integration_basis",
        "integration_number",
        "incomparable",
    ],
)
def test_invalid_evidence_snapshot_is_rejected(snapshot: dict[str, Any], case: str) -> None:
    if case == "blank_origin":
        snapshot["requirements"][0].origin.quote = " \t "
    elif case == "data_origin":
        snapshot["comparisons"][0].data_origin = "real"
    elif case == "partner_grade":
        snapshot["partners"][0].grades[0].requirement_id = UUID(int=900)
    elif case == "integration_basis":
        snapshot["integration"].basis_clause_ids = []
    elif case == "integration_number":
        snapshot["integration"].basis_clause_nos = ["missing"]
    else:
        snapshot["comparisons"][0].candidates[0].attribute_match.comparable = False
    with pytest.raises(ValueError, match="RFP_DRAFT_"):
        build_draft(**snapshot)


@pytest.mark.parametrize("status", list(get_args(s.RequirementStatus)))
def test_four_states_and_unconfirmed_conditions_do_not_become_promises(
    snapshot: dict[str, Any], status: s.RequirementStatus
) -> None:
    req = snapshot["requirements"][0]
    req.judgement.status = status
    req.needs_rereview = True
    result = build_draft(**snapshot)
    related = [
        row for row in sentences(result) if any(e.type == "requirement" for e in row.evidence)
    ]
    assert all(row.needs_rereview and row.unconfirmed for row in related)
    assert all(
        "현장 적용 범위" in row.conditions for row in sentences(result, "CUSTOMER_GOAL_SCOPE")
    )
    assert req.judgement.status == status
    if status == "SPEC_GAP":
        assert any("사양 차이" in row.text for row in sentences(result, "DEVIATIONS"))
    if status == "INSUFFICIENT_EVIDENCE":
        assert any("미충족 확정 아님" in row.text for row in sentences(result))


def test_only_grounded_own_candidate_is_described_with_its_limits(snapshot: dict[str, Any]) -> None:
    own = sentences(build_draft(**snapshot), "OUR_RESPONSE")
    assert len(own) == 1
    assert "600 A" in own[0].text and "500 A" in own[0].text
    assert "특정 구성에서만 적용" in own[0].conditions and "현장 시험 필요" in own[0].conditions
    assert "전체 요구 충족이나 공급 확약이 아님" in own[0].text
    snapshot["comparisons"][0].candidates[0].attribute_match.evidence = []
    assert sentences(build_draft(**snapshot), "OUR_RESPONSE") == []


def test_same_partner_in_two_paths_has_distinct_ids_and_keeps_both_conditions(
    snapshot: dict[str, Any],
) -> None:
    option = snapshot["options"][0].options[0]
    snapshot["options"][0].options.append(
        option.model_copy(
            update={
                "path": "JOINT_PROPOSAL",
                "conditions_to_confirm": ["공동 제안 책임 경계"],
            }
        )
    )
    draft = build_draft(**snapshot)
    rows = [
        row
        for row in sentences(draft, "PARTNER_SUPPLEMENT")
        if any(e.type == "partner" for e in row.evidence)
    ]
    assert len(rows) == 2 and len({row.sentence_id for row in rows}) == 2
    assert any("외부 제품" in row.text and "계약 범위" in row.conditions for row in rows)
    assert any("공동 제안" in row.text and "공동 제안 책임 경계" in row.conditions for row in rows)
    assert all("contract_scope" in row.conditions and "공급 조건" in row.conditions for row in rows)
    assert build_draft(**snapshot) == draft


def selection() -> s.SupplementSelected:
    """요구 판본과 독립적인 사람 선택 판본을 만든다."""
    return s.SupplementSelected.model_validate(
        dict(
            **(META | {"version": 8}),
            requirement_id=REQ,
            path="EXTERNAL_PRODUCT",
            candidate_codes=["SYN-PARTNER"],
            role_split="제어반 구성 검토",
            conditions=["사람 선택 조건"],
            note=None,
            provenance_kind="human_input",
            decided_by=USER,
            decided_at=NOW,
        )
    )


def test_selection_does_not_confirm_feasibility_and_waiver_remains_condition(
    snapshot: dict[str, Any],
) -> None:
    snapshot["selections"] = [selection()]
    snapshot["requirements"][0].judgement.rnd_check = "WAIVED"
    snapshot["options"][0].options[0].gate.status = "WAIVED"
    rows = sentences(build_draft(**snapshot), "PARTNER_SUPPLEMENT")
    assert any("사람 선택 기록 있음" in row.text for row in rows)
    assert all(row.unconfirmed for row in rows)
    assert any("R&D 개발·수행 확인 미완료(WAIVED)" in row.conditions for row in rows)
    assert any("사람 선택 조건" in row.conditions for row in rows)
    assert not any("확인 완료" in row.text for row in rows)


@pytest.mark.parametrize(
    "case", ["blocked", "unknown_candidate", "missing_path", "excluded", "forbidden"]
)
def test_invalid_or_blocked_selection_is_rejected(snapshot: dict[str, Any], case: str) -> None:
    snapshot["selections"] = [selection()]
    if case == "blocked":
        snapshot["options"][0].options[0].allowed = False
    elif case == "unknown_candidate":
        snapshot["selections"][0].candidate_codes = ["missing"]
    elif case == "missing_path":
        snapshot["selections"][0].path = "OWN_DEVELOPMENT"
    elif case == "excluded":
        snapshot["partners"][0].grades[0].grade = "EXCLUDED"
    else:
        snapshot["options"][0].customer_permission = "FORBIDDEN"
    with pytest.raises(ValueError, match="RFP_DRAFT_"):
        build_draft(**snapshot)


@pytest.mark.parametrize("summary", [False, True])
def test_excluded_partner_is_not_recommended(snapshot: dict[str, Any], summary: bool) -> None:
    if summary:
        snapshot["partners"][0].overall_grade = "EXCLUDED"
    else:
        snapshot["partners"][0].grades[0].grade = "EXCLUDED"
    rows = sentences(build_draft(**snapshot))
    assert not any(e.type == "partner" for row in rows for e in row.evidence)


def test_blocked_unselected_path_and_unprovided_candidate_are_not_invented(
    snapshot: dict[str, Any],
) -> None:
    snapshot["options"][0].options[0].allowed = False
    rows = sentences(build_draft(**snapshot), "PARTNER_SUPPLEMENT")
    assert rows == []
    assert not any("사람 선택 기록 있음" in row.text for row in sentences(build_draft(**snapshot)))


@pytest.mark.parametrize("holder", ["OWN", "SINGLE_PARTNER", "JOINT", "UNRESOLVED"])
def test_integration_responsibility_keeps_basis_and_never_promises_unit_supply(
    snapshot: dict[str, Any], holder: s.IntegrationHolder
) -> None:
    snapshot["integration"].holder = holder
    if holder in {"SINGLE_PARTNER", "JOINT"}:
        snapshot["integration"].holder_candidate_codes = ["SYN-PARTNER"]
    rows = [
        row
        for row in sentences(build_draft(**snapshot), "ASSUMPTIONS_EXCLUSIONS")
        if "통합 책임자:" in row.text
    ]
    assert len(rows) == 1
    assert any(e.type == "clause" and e.id == str(CLAUSE) for e in rows[0].evidence)
    if holder == "UNRESOLVED":
        assert "미확보" in rows[0].text and rows[0].unconfirmed
        assert "통합 책임자 확인" in rows[0].conditions
    else:
        assert "공급·납기 확약이 아님" in rows[0].text


@pytest.mark.parametrize("target", ["CUSTOMER", "INTERNAL"])
@pytest.mark.parametrize("status", ["DRAFT", "OPEN", "REPLIED"])
def test_customer_questions_and_internal_next_actions_preserve_lifecycle(
    snapshot: dict[str, Any], target: s.QuestionTarget, status: s.QuestionStatus
) -> None:
    question = snapshot["questions"][0]
    question.target, question.status = target, status
    question.target_dept = "RND" if target == "INTERNAL" else None
    rows = [
        row
        for row in sentences(build_draft(**snapshot), "QUESTIONS_NEXT_ACTIONS")
        if any(e.type == "question" for e in row.evidence)
    ]
    assert len(rows) == 1 and question.question_text in rows[0].text
    assert ("내부 다음 행동(담당 부서 RND)" if target == "INTERNAL" else "고객 확인 질문") in rows[
        0
    ].text
    if status == "DRAFT":
        assert "미발송" in rows[0].text and rows[0].unconfirmed
    if status == "REPLIED":
        assert "반영 여부 별도 확인" in rows[0].text


def test_shown_assumption_is_grounded_and_never_used_as_fulfillment(
    snapshot: dict[str, Any],
) -> None:
    snapshot["questions"][0].internal_assumption = s.InternalAssumption(
        value="주변 온도 잠정 35°C",
        basis_standard=None,
        basis_clause=None,
        approver_user_id=None,
        shown_in_draft=True,
    )
    rows = [
        row
        for row in sentences(build_draft(**snapshot), "ASSUMPTIONS_EXCLUSIONS")
        if "내부 가정" in row.text
    ]
    assert len(rows) == 1 and rows[0].unconfirmed
    assert "충족 판정에 사용 금지" in rows[0].text
    assert any(e.type == "question" and e.id == str(QUESTION) for e in rows[0].evidence)
    snapshot["questions"][0].internal_assumption.shown_in_draft = False
    assert not any("내부 가정" in row.text for row in sentences(build_draft(**snapshot)))


def test_synthetic_product_identity_is_input_driven(snapshot: dict[str, Any]) -> None:
    snapshot["comparisons"][0].candidates[0].product_code = "OTHER-SYNTHETIC"
    row = sentences(build_draft(**snapshot), "OUR_RESPONSE")[0]
    assert "OTHER-SYNTHETIC" in row.text and "SYN-PRODUCT" not in row.text
    assert any(e.type == "product" and e.id == "OTHER-SYNTHETIC" for e in row.evidence)


def test_product_from_another_requirement_cannot_ground_own_supplement(
    snapshot: dict[str, Any],
) -> None:
    other = snapshot["requirements"][0].model_copy(deep=True)
    other.requirement_id = UUID(int=90)
    other.code = "SYN-OTHER"
    other.links.question_ids = []
    other.links.partner_candidate_ids = []
    snapshot["requirements"].append(other)
    option_set = snapshot["options"][0].model_copy(deep=True)
    option_set.requirement_id = other.requirement_id
    option_set.options = [
        option_set.options[0].model_copy(
            update={
                "path": "OTHER_OWN_PRODUCT",
                "candidate_codes": ["SYN-PRODUCT"],
            }
        )
    ]
    snapshot["options"].append(option_set)
    with pytest.raises(ValueError, match="UNKNOWN_REFERENCE"):
        build_draft(**snapshot)


def test_question_and_known_clause_must_belong_to_same_requirement(
    snapshot: dict[str, Any],
) -> None:
    other = snapshot["requirements"][0].model_copy(deep=True)
    other.requirement_id = UUID(int=90)
    other.code = "SYN-OTHER"
    other.origin.clause_id = UUID(int=91)
    other.origin.clause_no = "S.2"
    other.links.question_ids = []
    other.links.partner_candidate_ids = []
    snapshot["requirements"].append(other)
    snapshot["questions"][0].origin.clause_id = other.origin.clause_id
    with pytest.raises(ValueError, match="QUESTION_CLAUSE_MISMATCH"):
        build_draft(**snapshot)


def test_project_question_without_requirement_does_not_invent_requirement_reference(
    snapshot: dict[str, Any],
) -> None:
    question = snapshot["questions"][0]
    question.requirement_id = None
    question.origin.clause_id = None
    result = build_draft(
        project_id=PROJECT,
        version=1,
        data_origin="synthetic",
        known_at=NOW,
        requirements=[],
        integration=s.IntegrationResponsibility(),
        questions=[question],
    )
    rows = sentences(result)
    assert len(rows) == 1 and rows[0].evidence == [s.Evidence(type="question", id=str(QUESTION))]
    assert "초안·미발송" in rows[0].text
