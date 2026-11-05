import json

import pytest
from pydantic import ValidationError

from backend.engine.cs.fault_matching import (
    MAX_TEXT_CHARS,
    GroupView,
    MatchInput,
    build_prompt,
    parse_output,
)


def payload(**changes: object) -> MatchInput:
    base: dict[str, object] = {
        "product_name": "Oil Transformer 1000kVA",
        "query": "Fuga de aceite en la junta",
        "groups": [
            GroupView(ref="G1", examples=["Oil leak at gasket after delivery"]),
            GroupView(ref="G2", examples=["Buchholz relay alarm"]),
        ],
    }
    return MatchInput(**{**base, **changes})


def test_input_has_no_fault_or_resolution_fields() -> None:
    # 고장 ID나 해결 기록 문장을 넣을 자리 자체가 없어야 한다(넣으면 글자 맞추기로 정답이 나온다).
    with pytest.raises(ValidationError):
        MatchInput.model_validate({**payload().model_dump(), "fault_id": "FLT-067"})
    with pytest.raises(ValidationError):
        GroupView.model_validate({"ref": "G1", "examples": ["x"], "symptom": "Oil leak"})


def test_prompt_carries_only_masked_refs_and_texts() -> None:
    prompt = build_prompt(payload())
    body = json.loads(prompt.split("입력:\n", 1)[1])
    assert body["query"] == "Fuga de aceite en la junta"
    assert [g["ref"] for g in body["groups"]] == ["G1", "G2"]
    assert "FLT-" not in prompt


def test_long_text_is_clipped() -> None:
    prompt = build_prompt(payload(query="a" * (MAX_TEXT_CHARS + 50)))
    body = json.loads(prompt.split("입력:\n", 1)[1])
    assert len(body["query"]) == MAX_TEXT_CHARS + 2


def test_duplicate_refs_and_bad_refs_are_rejected() -> None:
    with pytest.raises(ValidationError):
        payload(groups=[GroupView(ref="G1", examples=["a"]), GroupView(ref="G1", examples=["b"])])
    with pytest.raises(ValidationError):
        GroupView(ref="FLT-067", examples=["a"])


@pytest.mark.parametrize(
    "raw",
    [
        {"decision": "EXISTING", "group_ref": "G9", "evidence_quote": None, "reason": "r"},
        {"decision": "EXISTING", "group_ref": None, "evidence_quote": None, "reason": "r"},
        {"decision": "NEW", "group_ref": "G1", "evidence_quote": None, "reason": "r"},
        {"decision": "UNSURE", "group_ref": "G2", "evidence_quote": None, "reason": "r"},
        {"decision": "EXISTING", "group_ref": "G1", "reason": "r"},
    ],
)
def test_out_of_rule_outputs_fail(raw: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="CS_FAULT_MATCH_INVALID_OUTPUT"):
        parse_output(json.dumps(raw), {"G1", "G2"})


def test_valid_outputs_pass() -> None:
    existing = parse_output(
        json.dumps(
            {"decision": "EXISTING", "group_ref": "G1", "evidence_quote": "aceite", "reason": "r"}
        ),
        {"G1", "G2"},
    )
    assert existing.group_ref == "G1"
    new = parse_output(
        json.dumps({"decision": "NEW", "group_ref": None, "evidence_quote": None, "reason": "r"}),
        {"G1"},
    )
    assert new.decision == "NEW"
    with pytest.raises(ValueError):
        parse_output("not json", {"G1"})
