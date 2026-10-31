"""일반 합성 스냅샷으로 제안 초안의 근거·조건·참조 경계를 검증한다(KAN-188)."""

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, get_args
from uuid import UUID

import pytest

from backend.api.tft.rfp import schemas as s
from backend.engine.rfp import draft as engine
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
    assert all("계약 범위 확인" in row.conditions and "공급 조건" in row.conditions for row in rows)
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
    assert any(
        "R&D 개발·수행 확인 미완료(확인 절차 생략·사유 확인 필요)" in row.conditions for row in rows
    )
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


def test_forbidden_unselected_option_does_not_block_draft(snapshot: dict[str, Any]) -> None:
    snapshot["options"][0].customer_permission = "FORBIDDEN"
    assert snapshot["options"][0].options[0].allowed is True
    assert snapshot["selections"] == []
    result = build_draft(**snapshot)
    assert sentences(result, "CUSTOMER_GOAL_SCOPE")
    assert sentences(result, "OUR_RESPONSE")
    assert sentences(result, "PARTNER_SUPPLEMENT") == []
    assert result.disclaimer == DISCLAIMER


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
    assert (
        "내부 다음 행동(담당 부서 연구개발 담당)" if target == "INTERNAL" else "고객 확인 질문"
    ) in rows[0].text
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
    assert not any(e.type == "product" and e.id == "SYN-PRODUCT" for e in row.evidence)


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


@pytest.mark.parametrize("tolerance", ["±5%", "+0/-3%", None])
def test_latest_schema_tolerance_is_preserved_without_assuming_null_means_none(
    snapshot: dict[str, Any], tolerance: str | None
) -> None:
    snapshot["requirements"][0].spec.tolerance = tolerance
    rows = sentences(build_draft(**snapshot), "CUSTOMER_GOAL_SCOPE")
    assert len(rows) == 1
    if tolerance is not None:
        assert f"요구 허용 오차(원문): {tolerance}" in rows[0].conditions
    else:
        assert not any("허용 오차" in item for item in rows[0].conditions)
        assert "허용 오차 없음" not in rows[0].text


def test_latest_system_review_flag_propagates_to_related_sentences(
    snapshot: dict[str, Any],
) -> None:
    req = snapshot["requirements"][0]
    req.needs_review = True
    req.needs_rereview = False
    req.judgement.status = "EVIDENCE_FOUND"
    snapshot["questions"][0].status = "REPLIED"
    snapshot["integration"].holder = "OWN"
    result = build_draft(**snapshot)
    related = [
        row
        for row in sentences(result)
        if any(e.type == "requirement" and e.id == str(REQ) for e in row.evidence)
    ]
    assert related and all(row.needs_rereview and row.unconfirmed for row in related)
    assert (
        "추출 결과의 원문·허용 오차 등 검토 필요"
        in sentences(result, "CUSTOMER_GOAL_SCOPE")[0].conditions
    )
    assert req.needs_review is True and req.needs_rereview is False


def bulk_snapshot(snapshot: dict[str, Any], count: int = 240) -> dict[str, Any]:
    """실제 문서/제품과 무관한 다장비·다상태·과업·선택 혼합 스냅샷."""
    result = deepcopy(snapshot)
    for key in ("requirements", "comparisons", "options", "questions", "selections"):
        result[key] = []
    partner = result["partners"][0]
    partner.grades = []
    for index in range(count):
        req = snapshot["requirements"][0].model_copy(deep=True)
        req.requirement_id = UUID(int=1000 + index)
        req.code = f"{req.requirement_id.hex[:8]}:opaque:{index}"
        req.origin.clause_id = UUID(int=2000 + index)
        req.origin.clause_no = f"4.{index + 1}.A"
        req.origin.quote = (
            f"Cabinet series {index % 30} requires rating {500 + index} A under site conditions."
        )
        req.spec.equipment = f"cabinet series {index % 30}"
        req.spec.attribute = "rated_current"
        req.spec.value = str(500 + index)
        req.spec.task = "ASSEMBLY" if index % 3 == 0 else None
        req.spec.conditions = [
            s.Condition(name="ambient_temperature", value=str(25 + index), unit="°C")
        ]
        req.judgement.status = list(get_args(s.RequirementStatus))[index % 4]
        req.judgement.unresolved = [f"현장 경계 {index} 확인"]
        req.needs_rereview = index % 2 == 0
        req.needs_review = index % 11 == 0
        question = snapshot["questions"][0].model_copy(deep=True)
        question.question_id = UUID(int=3000 + index)
        question.requirement_id = req.requirement_id
        question.origin.clause_id = req.origin.clause_id
        question.question_text = f"작업 {index}의 적용 조건을 확인해 주시겠습니까?"
        question.target = "INTERNAL" if index % 2 else "CUSTOMER"
        question.target_dept = list(get_args(s.DepartmentKind))[index % 5] if index % 2 else None
        question.status = list(get_args(s.QuestionStatus))[index % 3]
        req.links.question_ids = [question.question_id]
        comp = snapshot["comparisons"][0].model_copy(deep=True)
        comp.requirement_id = req.requirement_id
        options = snapshot["options"][0].model_copy(deep=True)
        options.requirement_id = req.requirement_id
        options.permission_clause_id = req.origin.clause_id
        grade = snapshot["partners"][0].grades[0].model_copy(deep=True)
        grade.requirement_id = req.requirement_id
        partner.grades.append(grade)
        if index % 2:
            selected = selection()
            selected.requirement_id = req.requirement_id
            result["selections"].append(selected)
        for key, value in (
            ("requirements", req),
            ("comparisons", comp),
            ("options", options),
            ("questions", question),
        ):
            result[key].append(value)
    result["integration"].basis_clause_ids = [result["requirements"][0].origin.clause_id]
    result["integration"].basis_clause_nos = [result["requirements"][0].origin.clause_no]
    return result


def evidence_of(rows: list[s.Sentence]) -> set[tuple[str, str]]:
    return {(proof.type, proof.id) for row in rows for proof in row.evidence}


def test_bulk_summary_caps_sections_without_losing_evidence_conditions_or_flags(
    snapshot, monkeypatch
):
    data = bulk_snapshot(snapshot)
    before = deepcopy(data)
    capped = build_draft(**data)
    monkeypatch.setattr(engine, "MAX_SENTENCES_PER_SECTION", 10000)
    uncapped = build_draft(**data)
    assert len(sentences(uncapped)) > 48
    assert len(sentences(capped)) <= 48
    flags = {
        str(req.requirement_id): req.needs_review or req.needs_rereview
        for req in data["requirements"]
    }
    for section in get_args(s.DraftSection):
        small, large = sentences(capped, section), sentences(uncapped, section)
        assert len(small) <= 8
        assert evidence_of(small) == evidence_of(large)
        assert {condition for row in small for condition in row.conditions} == {
            condition for row in large for condition in row.conditions
        }
        for row in small:
            assert row.needs_rereview == any(
                flags[e.id] for e in row.evidence if e.type == "requirement"
            )
            assert row.unconfirmed == any(
                other.unconfirmed
                for other in large
                if set((e.type, e.id) for e in other.evidence)
                <= set((e.type, e.id) for e in row.evidence)
            )
            assert row.text.count("「") <= 3
            assert len(row.text) <= 4000
    expected = (
        {("requirement", str(req.requirement_id)) for req in data["requirements"]}
        | {("clause", str(req.origin.clause_id)) for req in data["requirements"]}
        | {("question", str(q.question_id)) for q in data["questions"]}
        | {("product", "SYN-PRODUCT"), ("partner", "SYN-PARTNER")}
    )
    assert evidence_of(sentences(capped)) == expected
    assert all(
        f"현장 경계 {index} 확인" in {c for row in sentences(capped) for c in row.conditions}
        for index in range(240)
    )
    assert (
        len(
            [
                row
                for row in sentences(capped, "ASSUMPTIONS_EXCLUSIONS")
                if "통합 책임자:" in row.text
            ]
        )
        == 1
    )
    mixed = [row for row in sentences(capped) if "혼합 항목 추가 검토" in row.text]
    assert mixed and all("동일 항목이나 공급 가능 판단이 아님" in row.text for row in mixed)
    assert capped.evidence_missing_count == 0 and all(row.evidence for row in sentences(capped))
    assert capped.disclaimer == DISCLAIMER and data == before
    ids = [row.sentence_id for row in sentences(capped)]
    assert len(ids) == len(set(ids))
    monkeypatch.setattr(engine, "MAX_SENTENCES_PER_SECTION", 8)
    reordered = deepcopy(data)
    for key in ("requirements", "comparisons", "options", "questions", "selections"):
        reordered[key].reverse()
    reordered["partners"][0].grades.reverse()
    assert build_draft(**reordered) == capped


def test_representatives_are_first_three_in_clause_order_and_full_evidence_remains(snapshot):
    data = bulk_snapshot(snapshot, 12)
    data["integration"] = s.IntegrationResponsibility()
    for req in data["requirements"]:
        req.spec.equipment = "common cabinet"
        req.spec.task = None
        req.judgement.status = "INSUFFICIENT_EVIDENCE"
    result = build_draft(**data)
    rows = sentences(result, "CUSTOMER_GOAL_SCOPE")
    assert len(rows) == 1 and "요구 12건" in rows[0].text
    for index in range(3):
        assert data["requirements"][index].origin.quote in rows[0].text
    assert data["requirements"][3].origin.quote not in rows[0].text
    assert {e.id for e in rows[0].evidence if e.type == "requirement"} == {
        str(req.requirement_id) for req in data["requirements"]
    }


def test_summary_hides_internal_fields_without_redacting_original_equipment_or_standard(snapshot):
    req = snapshot["requirements"][0]
    req.code = "abcd1234:4.7.C:2"
    req.spec.equipment = "Cabinet GX-810"
    req.origin.quote = "Cabinet GX-810 requires 600 A with IEC 61439-2 and a 690 V supply."
    req.spec.attribute = "rated_current"
    snapshot["questions"][0].question_text = "rated_current 요구의 조건을 확인해 주시겠습니까?"
    result = build_draft(**snapshot)
    body = " ".join(row.text for row in sentences(result))
    for internal in (
        req.code,
        "rated_current",
        "SPEC_GAP",
        "CONDITIONAL",
        "EXTERNAL_PRODUCT",
        "UNCONFIRMED",
        "REVIEW",
        "PENDING",
        "TECH",
    ):
        assert internal not in body
    assert "Cabinet GX-810" in body and "IEC 61439-2" in body and "690 V" in body
    assert any(e.id == str(REQ) for row in sentences(result) for e in row.evidence)


def test_internal_key_display_handles_korean_particles_without_partial_identifier_replacement(
    snapshot,
):
    question = snapshot["questions"][0]
    question.question_text = "rated_current가 아닌 protection_degree를 확인해 주세요."
    body = " ".join(row.text for row in sentences(build_draft(**snapshot)))
    assert "rated current가 아닌 protection degree를" in body
    assert "rated_current가" not in body and "protection_degree를" not in body
    question.question_text = (
        "prerated_current 및 rated_current_suffix, rated_current2 표기는 그대로 확인."
    )
    body = " ".join(row.text for row in sentences(build_draft(**snapshot)))
    assert question.question_text in body


@pytest.mark.parametrize("department", [None, "", " ", "TS_SYNTHETIC"])
def test_internal_question_read_snapshot_never_falls_back_to_customer(snapshot, department):
    question = snapshot["questions"][0]
    payload = question.model_dump() | {"target": "INTERNAL", "target_dept": department}
    snapshot["questions"] = [s.Question.model_validate(payload)]
    result = build_draft(**snapshot)
    rows = [
        row
        for row in sentences(result, "QUESTIONS_NEXT_ACTIONS")
        if any(e.type == "question" for e in row.evidence)
    ]
    assert len(rows) == 1 and "내부 다음 행동" in rows[0].text
    assert "고객 확인 질문" not in rows[0].text
    assert (
        "담당 부서 미정" if not department or not department.strip() else "담당 부서 지정됨"
    ) in rows[0].text
    assert (
        snapshot["questions"][0].target == "INTERNAL"
        and snapshot["questions"][0].target_dept == department
    )
    assert any(e.type == "question" and e.id == str(QUESTION) for e in rows[0].evidence)


def test_multiple_own_representatives_identify_each_product_and_its_values(snapshot):
    snapshot["requirements"][0].spec.attribute = "rated_current"
    first = snapshot["comparisons"][0].candidates[0]
    first.product_code = "SERIES_ALPHA"
    second = first.model_copy(deep=True)
    second.product_code = "SERIES_BETA"
    second.attribute_match.our_value = "720"
    third = first.model_copy(deep=True)
    third.product_code = "rated_current"  # 제품 식별명이 키와 같아도 원형을 보존한다.
    third.attribute_match.our_value = "960"
    snapshot["comparisons"][0].candidates = [first, second, third]
    row = sentences(build_draft(**snapshot), "OUR_RESPONSE")[0]
    for code, value in (("SERIES_ALPHA", "500"), ("SERIES_BETA", "720"), ("rated_current", "960")):
        representative = next(part for part in row.text.split("「") if code in part)
        assert (
            f"제품 {code}" in representative
            and "600 A" in representative
            and f"{value} A" in representative
        )
        assert any(e.type == "product" and e.id == code for e in row.evidence)
    assert row.text.count("「") == 3 and "DIFFERS" not in row.text
    assert "속성 rated current" in row.text and "속성 rated_current" not in row.text
    assert "특정 구성에서만 적용" in row.conditions and "현장 시험 필요" in row.conditions


def test_question_target_not_department_controls_public_read_snapshot_classification(snapshot):
    question = snapshot["questions"][0]
    snapshot["questions"] = [
        s.Question.model_validate(
            question.model_dump() | {"target": "CUSTOMER", "target_dept": "RND"}
        )
    ]
    rows = [
        row
        for row in sentences(build_draft(**snapshot), "QUESTIONS_NEXT_ACTIONS")
        if any(e.type == "question" for e in row.evidence)
    ]
    assert (
        len(rows) == 1 and "고객 확인 질문" in rows[0].text and "내부 다음 행동" not in rows[0].text
    )


def test_missing_internal_department_does_not_bypass_blank_question_guard(snapshot):
    snapshot["questions"][0].target = "INTERNAL"
    snapshot["questions"][0].target_dept = None
    snapshot["questions"][0].question_text = " \t "
    with pytest.raises(ValueError, match="RFP_DRAFT_INVALID_QUESTION"):
        build_draft(**snapshot)


@pytest.mark.parametrize("long_field", ["product", "attribute", "values"])
def test_comparison_sample_keeps_complete_values_ahead_of_long_labels(snapshot, long_field):
    req = snapshot["requirements"][0]
    candidate = snapshot["comparisons"][0].candidates[0]
    if long_field == "product":
        candidate.product_code = "MODEL_" + "X" * 200
    elif long_field == "attribute":
        req.spec.attribute = "rated_current " * 150
        candidate.product_code = "rated_current"
    else:
        req.spec.value = "1234567890" * 30
        candidate.attribute_match.our_value = "9876543210" * 30
    row = sentences(build_draft(**snapshot), "OUR_RESPONSE")[0]
    assert f"요구 {req.spec.value} A" in row.text
    assert f"후보 {candidate.attribute_match.our_value} A" in row.text
    assert candidate.product_code in row.text
    assert len(row.text) <= 4000
    assert any(e.type == "product" and e.id == candidate.product_code for e in row.evidence)
    if long_field == "attribute":
        raw = next(c for c in row.conditions if c.startswith("자사 비교 원문:"))
        assert "제품 「rated_current」" in raw
        assert f"속성 「{req.spec.attribute}」" in raw
        assert "제품 「rated current」" not in raw
        assert "긴 속성명: 조건 원문 확인" in row.text


@pytest.mark.parametrize("side", ["requirement", "candidate", "both"])
def test_oversized_values_are_preserved_in_literal_conditions_not_partial_numbers(snapshot, side):
    req = snapshot["requirements"][0]
    candidate = snapshot["comparisons"][0].candidates[0]
    if side in {"requirement", "both"}:
        req.spec.value = "7" * 1600
    if side in {"candidate", "both"}:
        candidate.attribute_match.our_value = "9" * 1700
    row = sentences(build_draft(**snapshot), "OUR_RESPONSE")[0]
    raw = next(c for c in row.conditions if c.startswith("자사 비교 원문:"))
    assert f"요구값 「{req.spec.value}」" in raw
    assert f"후보값 「{candidate.attribute_match.our_value}」" in raw
    assert f"제품 「{candidate.product_code}」" in raw
    if side in {"requirement", "both"}:
        assert "긴 요구값: 조건 원문 확인" in row.text
        assert "7" * 80 not in row.text
    else:
        assert "요구 600 A" in row.text
    if side in {"candidate", "both"}:
        assert "긴 후보값: 조건 원문 확인" in row.text
        assert "9" * 80 not in row.text
    else:
        assert "후보 500 A" in row.text
    assert len(row.text) <= 4000 and row.unconfirmed


def test_three_long_comparison_samples_and_title_stay_within_body_limit(snapshot):
    req = snapshot["requirements"][0]
    req.spec.equipment = "cabinet long designation " * 100
    req.spec.attribute = "attribute long description " * 100
    req.spec.value = "1234567890" * 40
    original = snapshot["comparisons"][0].candidates[0]
    candidates = []
    for index in range(3):
        candidate = original.model_copy(deep=True)
        candidate.product_code = f"SERIES_{index}_" + "Z" * 1200
        candidate.attribute_match.our_value = str(index + 5) * 400
        candidates.append(candidate)
    snapshot["comparisons"][0].candidates = candidates
    result = build_draft(**snapshot)
    row = sentences(result, "OUR_RESPONSE")[0]
    assert row.text.count("「") == 3 and len(row.text) <= 4000
    for candidate in candidates:
        assert f"후보 {candidate.attribute_match.our_value} A" in row.text
        assert any(
            f"제품 「{candidate.product_code}」" in c
            and f"후보값 「{candidate.attribute_match.our_value}」" in c
            for c in row.conditions
        )
        assert any(e.type == "product" and e.id == candidate.product_code for e in row.evidence)
    assert "긴 제품 식별명: 근거·조건 원문 확인" in row.text
    assert all(len(part.sentences) <= 8 for part in result.sections)
    assert sum(len(part.sentences) for part in result.sections) <= 48


def test_customer_quotes_bypass_code_mapping_and_whitespace_rewriting_in_every_section(snapshot):
    quote = "고객 원문:\t rated_current가  600 A이며\nEXTERNAL_PRODUCT 표기는 그대로 유지."
    snapshot["requirements"][0].origin.quote = quote
    before = deepcopy(snapshot)
    result = build_draft(**snapshot)
    for section in (
        "CUSTOMER_GOAL_SCOPE",
        "DEVIATIONS",
        "PARTNER_SUPPLEMENT",
        "ASSUMPTIONS_EXCLUSIONS",
        "QUESTIONS_NEXT_ACTIONS",
    ):
        assert any(quote in row.text for row in sentences(result, section))
    assert snapshot == before
    assert evidence_of(sentences(result)) == {
        ("requirement", str(REQ)),
        ("clause", str(CLAUSE)),
        ("question", str(QUESTION)),
        ("product", "SYN-PRODUCT"),
        ("partner", "SYN-PARTNER"),
    }


def test_long_customer_quote_only_has_explicit_length_ellipsis(snapshot):
    quote = "rated_current가\t  600 A. " + "원문 검토 조건 " * 100
    snapshot["requirements"][0].origin.quote = quote
    row = sentences(build_draft(**snapshot), "CUSTOMER_GOAL_SCOPE")[0]
    excerpt = row.text.split("「", 1)[1].split("」", 1)[0]
    assert excerpt.endswith("…") and quote.startswith(excerpt[:-1])
    assert "rated_current가\t  600 A." in excerpt
    assert len(excerpt) <= engine.REPRESENTATIVE_CHARS
