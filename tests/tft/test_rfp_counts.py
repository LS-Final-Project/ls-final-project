"""목록과 검토본이 공유할 미해결 필수 집계의 경계 사례."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from backend.api.tft.rfp.counts import count_unresolved_mandatory, is_unresolved_mandatory
from backend.api.tft.rfp.schemas import RequirementDetail


def requirement(
    necessity="MANDATORY", status="EVIDENCE_FOUND", unresolved=None, rereview=False, path=None
):
    return RequirementDetail.model_validate(
        {
            "requirement_id": str(uuid4()),
            "code": "synthetic",
            "version": 1,
            "data_origin": "synthetic",
            "derivation_kind": "DERIVED",
            "known_at": datetime.now(UTC),
            "origin": {
                "document_version_id": str(uuid4()),
                "clause_id": None,
                "clause_no": "1",
                "page": None,
                "quote": "synthetic",
                "deleted_values": [],
                "edit_trace_support": True,
            },
            "spec": {
                "equipment": None,
                "task": None,
                "attribute": None,
                "value": None,
                "unit": None,
                "operator": "TEXT",
                "conditions": [],
                "standard_system": "NONE",
                "necessity": necessity,
                "designated_model": None,
                "undetermined_items": [],
            },
            "judgement": {
                "status": status,
                "reason": "synthetic",
                "unresolved": unresolved or [],
                "path": path,
                "next_owner_field": None,
                "provenance_kind": "human_input",
                "decided_by": None,
                "decided_at": None,
            },
            "links": {
                "comparison_summary": None,
                "supplement_summary": None,
                "partner_candidate_ids": [],
                "question_ids": [],
                "pending_proposal_ids": [],
                "draft_sentence_ids": [],
            },
            "needs_rereview": rereview,
        }
    )


@pytest.mark.parametrize("necessity", ["MANDATORY", "CONDITION_BOUND"])
@pytest.mark.parametrize(
    "status", ["EVIDENCE_FOUND", "CONDITIONAL", "SPEC_GAP", "INSUFFICIENT_EVIDENCE"]
)
def test_required_states(necessity, status):
    assert is_unresolved_mandatory(requirement(necessity, status)) == (status != "EVIDENCE_FOUND")


@pytest.mark.parametrize("necessity", ["MANDATORY", "CONDITION_BOUND"])
def test_remaining_conditions_and_rereview_are_counted_once(necessity):
    for outstanding, rereview in [(["未確認"], False), ([], True), (["condition"], True)]:
        item = requirement(necessity, unresolved=outstanding, rereview=rereview)
        assert count_unresolved_mandatory([item]) == 1


@pytest.mark.parametrize("necessity", ["PREFERRED", "UNDETERMINED"])
def test_nonmandatory_is_excluded(necessity):
    assert not is_unresolved_mandatory(requirement(necessity, "SPEC_GAP", ["unknown"], True))


def test_path_choice_does_not_resolve_and_duplicate_versions_are_rejected():
    item = requirement(status="CONDITIONAL", path="EXTERNAL_PRODUCT")
    assert count_unresolved_mandatory([item]) == 1
    assert count_unresolved_mandatory([]) == 0
    with pytest.raises(ValueError):
        count_unresolved_mandatory([item, item.model_copy(update={"version": 2})])
