"""KAN-114 조건 비교 조회의 순수 함수: 근거 숨김, 응답 계산, 모델 규칙(DB 없음)."""

import pytest
from pydantic import ValidationError

from backend.api.cs.comparisons import examples
from backend.api.cs.comparisons.schemas import ConditionItem, Side
from backend.api.cs.comparisons.view import build, hide

SEEN, UNSEEN = examples.VISIBLE_TICKET, examples.HIDDEN_TICKET
EVIDENCE = [examples.CASE_EVIDENCE, examples.TICKET_EVIDENCE, examples.DOCUMENT_EVIDENCE]


def test_full_scope_hides_nothing():
    items, evidence, count = hide([examples.HIDDEN], EVIDENCE, None)
    assert count == 0 and evidence == EVIDENCE
    assert items[0]["evidence_ids"] == ["ev-1", "ev-2"] and items[0]["hidden_evidence_count"] == 0
    assert items[0]["target_value"] == examples.HIDDEN["target_value"]


def test_hidden_ticket_removes_its_evidence_and_derived_values_but_keeps_the_judgement():
    [item], evidence, count = hide([examples.HIDDEN], EVIDENCE, {SEEN})
    assert count == 1
    assert [e["evidence_id"] for e in evidence] == ["ev-1", "ev-3"]
    assert item["evidence_ids"] == ["ev-1"] and item["hidden_evidence_count"] == 1
    assert (item["judgement"], item["finding"]) == ("CONFIRMED", "DIFFERS")
    assert (item["masked_summary"], item["summary_missing_reason"]) == (None, "HIDDEN_BY_SCOPE")
    assert (item["target_value"], item["target_missing_reason"]) == (None, "HIDDEN_BY_SCOPE")
    assert item["baseline_value"] == "주간 운전"


def test_hiding_overrides_a_value_that_was_not_provided():
    row = examples.UNCONFIRMED | {
        "target_evidence_ids": ["ev-2"],
        "evidence_ids": ["ev-1", "ev-2"],
    }
    [item], _, _ = hide([row], EVIDENCE, {SEEN})
    assert item["target_missing_reason"] == "HIDDEN_BY_SCOPE"
    assert item["judgement"] == "UNCONFIRMED"


def test_document_evidence_and_items_without_hidden_evidence_are_untouched():
    items, evidence, count = hide([examples.SIGNAL, examples.SAME], EVIDENCE, set())
    assert count == 2 and [e["evidence_id"] for e in evidence] == ["ev-3"]
    signal, same = items
    assert signal["evidence_ids"] == ["ev-3"] and signal["hidden_evidence_count"] == 0
    assert same["masked_summary"] is None and same["hidden_evidence_count"] == 1


def build_one(rows, latest=1, version=1):
    shown, kept, count = hide(rows, [examples.CASE_EVIDENCE], None)
    request = examples.request(1)
    return build(
        examples.comparison(1, latest=latest),
        examples.version(version, request),
        request,
        shown,
        kept,
        count,
    )


def test_only_all_confirmed_conditions_confirm_the_group():
    assert build_one([examples.SAME]).computed_condition_status == "CONFIRMED"
    mixed = build_one([examples.SAME, examples.UNCONFIRMED])
    assert mixed.computed_condition_status == "UNKNOWN"
    assert mixed.summary.model_dump() == {"confirmed": 1, "suspected": 0, "unconfirmed": 1}
    # A confirmed cause alone confirms nothing: causes are not conditions.
    cause = examples.CAUSE | {"judgement": "CONFIRMED", "provenance_kind": "human_input"}
    assert build_one([cause]).computed_condition_status == "UNKNOWN"


def test_a_past_version_is_not_the_latest():
    data = build_one([examples.SAME], latest=4, version=3)
    assert (data.version, data.latest_version, data.is_latest) == (3, 4, False)


def test_a_group_without_a_request_has_no_request_values():
    shown, kept, count = hide([examples.SAME], [examples.CASE_EVIDENCE], None)
    data = build(
        examples.comparison(1),
        examples.version(1, examples.request(1))
        | {"improvement_request_id": None, "request_version": None},
        None,
        shown,
        kept,
        count,
    )
    assert data.improvement_request_id is None and data.condition_summary is None
    assert data.fw_released_on is None


@pytest.mark.parametrize(
    "changes",
    [
        {"finding": None},
        {"judgement": "UNCONFIRMED", "unconfirmed_reason": "NO_EVIDENCE"},
        {"unconfirmed_reason": "NO_EVIDENCE"},
        {"masked_summary": None},
        {"baseline": {"masked_value": None, "missing_reason": None}},
        {"provenance_kind": "ai_draft"},
    ],
)
def test_condition_items_follow_the_contract_rules(changes):
    valid = build_one([examples.SAME]).conditions[0].model_dump()
    ConditionItem.model_validate(valid)
    with pytest.raises(ValidationError):
        ConditionItem.model_validate(valid | changes)


def test_a_side_needs_a_value_or_a_reason():
    with pytest.raises(ValidationError):
        Side(masked_value="x", missing_reason="NOT_PROVIDED")


def test_examples_cover_the_five_contract_cases():
    data = {name: examples.response(name).data for name in examples.SEEDS}
    assert data["same"].conditions[0].finding == "SAME"
    assert data["differs"].conditions[0].judgement == "SUSPECTED"
    assert data["unconfirmed"].conditions[0].unconfirmed_reason == "VALUE_NOT_PROVIDED"
    assert data["hidden"].hidden_evidence_count == 1
    assert data["past_version"].is_latest is False
    assert set(examples.openapi_examples()) == set(examples.SEEDS)
