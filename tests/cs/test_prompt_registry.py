"""KAN-105 단계별 프롬프트 판본 레지스트리. 모델 호출 없음."""

import hashlib
from unittest.mock import Mock

import pytest

from backend.engine.cs import drafting, improvement_drafting, improvement_reply, reply
from backend.engine.cs.prompt_registry import (
    PROMPTS,
    get_prompt,
    list_versions,
    prompt_fingerprint,
)
from backend.shared.ai import Generation, Usage
from tests.cs.test_drafting import attempt, raw

# 판본 본문을 고치면 이 값이 바뀐다. 새 판본은 새 키로 추가하고 v1 본문은 그대로 둔다.
V1_FINGERPRINTS = {
    "classification": "ec6fefc1c1ba9e79",
    "draft": "963cfe6ef9dff4c1",
    "reply": "8cea1946d44a09cf",
    "improvement_reply": "40c64f4891bf60a2",
    "improvement_draft": "fa4e6aa624afd7ef",
}
# 분류 v2(KAN-105 3차, 긴급도 신호 지침). v1에 한 줄만 더한 판본이다.
V2_CLASSIFICATION_FINGERPRINT = "143d7655ee22fed7"
# 분류 v3(KAN-105 6차, return_subtype null 규칙 강조). v2의 return_subtype 줄만 바꾼 판본이다.
V3_CLASSIFICATION_FINGERPRINT = "f299989ca90bcf99"
MODULES = {
    "draft": (drafting, drafting.DRAFT_PROMPT),
    "reply": (reply, reply.REPLY_PROMPT),
    "improvement_reply": (improvement_reply, improvement_reply.IMPROVEMENT_REPLY_PROMPT),
    "improvement_draft": (improvement_drafting, improvement_drafting.IMPROVEMENT_DRAFT_PROMPT),
}


def test_v1_fingerprints_are_pinned():
    """다섯 단계 v1 본문 지문을 고정해 기존 판본 본문 변경을 잡는다.

    Args:
        없음.

    Returns:
        None: 지문과 판본 목록을 확인한다.
    """
    assert {stage: prompt_fingerprint(stage, "v1") for stage in PROMPTS} == V1_FINGERPRINTS
    assert list_versions("classification") == ("v1", "v2", "v3")
    assert all(list_versions(stage) == ("v1",) for stage in PROMPTS if stage != "classification")


@pytest.mark.parametrize("stage", sorted(MODULES))
def test_default_render_matches_existing_constant(stage):
    """기본 판본 렌더가 기존 상수(본문 + 출력 스키마)와 바이트 단위로 같다.

    Args:
        stage: 단계.

    Returns:
        None: 상수·지시문·렌더 결과를 확인한다.
    """
    module, constant = MODULES[stage]
    assert constant.startswith(get_prompt(stage, "v1"))
    assert module.instruction() == module.instruction("v1") == constant
    payload = Mock(model_dump_json=Mock(return_value='{"x": 1}'))
    assert module.render_prompt(payload) == constant + '\n\n입력:\n{"x": 1}'


def test_classification_v1_states_the_output_contract():
    """분류 v1 본문은 다섯 필드와 시리얼 원값 금지를 지시하고 스키마는 호출자가 붙인다.

    Args:
        없음.

    Returns:
        None: 본문 내용을 확인한다.
    """
    body = get_prompt("classification", "v1")
    for field in (
        "category_code",
        "secondary_category",
        "urgency_flag",
        "provided_info",
        "return_subtype",
    ):
        assert field in body
    assert "시리얼 원값" in body and '"properties"' not in body


def test_classification_v2_adds_only_the_urgency_guide():
    """분류 v2는 v1 줄을 순서대로 모두 두고 긴급도 지침 한 줄만 더하며, 지문을 고정한다.

    Args:
        없음.

    Returns:
        None: 지문·v1 불변·추가 줄 내용을 확인한다.
    """
    v1, v2 = get_prompt("classification", "v1"), get_prompt("classification", "v2")
    assert prompt_fingerprint("classification", "v1") == V1_FINGERPRINTS["classification"]
    assert prompt_fingerprint("classification", "v2") == V2_CLASSIFICATION_FINGERPRINT
    extra = [line for line in v2.splitlines() if line not in v1.splitlines()]
    assert len(extra) == 1
    assert [line for line in v2.splitlines() if line != extra[0]] == v1.splitlines()
    lines = v2.splitlines()
    assert (
        lines.index(extra[0])
        == lines.index(next(text for text in lines if "urgency_flag는" in text)) + 1
    )
    guide = extra[0]
    for label in ("SAFETY", "SITE_DOWN", "PROD_IMPACT", "COMMISSION", "DEADLINE"):
        assert label in guide
    assert "위 신호가 없으면 NONE" in guide and "가장 구체적인 신호 하나" in guide
    # 긴급도 사이 우선순위 규칙은 넣지 않는다.
    assert "SAFETY > " not in guide and "우선" not in guide


def test_classification_v3_replaces_only_the_return_subtype_rule():
    """분류 v3는 v2의 return_subtype 줄 하나만 같은 자리에서 바꾸고, v1·v2·v3 지문을 고정한다.

    Args:
        없음.

    Returns:
        None: 지문·교체 줄 위치·내용을 확인한다.
    """
    v2, v3 = get_prompt("classification", "v2"), get_prompt("classification", "v3")
    assert prompt_fingerprint("classification", "v1") == V1_FINGERPRINTS["classification"]
    assert prompt_fingerprint("classification", "v2") == V2_CLASSIFICATION_FINGERPRINT
    assert prompt_fingerprint("classification", "v3") == V3_CLASSIFICATION_FINGERPRINT
    old, new = v2.splitlines(), v3.splitlines()
    changed = [i for i, (a, b) in enumerate(zip(old, new, strict=True)) if a != b]
    assert len(changed) == 1 and old[changed[0]].startswith("- return_subtype은")
    rule = new[changed[0]]
    assert rule.startswith(
        "- return_subtype은 category_code 또는 secondary_category가 RETURN일 때만"
    )
    assert "둘 다 RETURN이 아니면 불량·고장 내용이 있어도 반드시 null" in rule
    assert "category_code WARRANTY, secondary_category DEFECT → return_subtype null" in rule
    # 긴급도 지침과 출력 형식 줄은 v2 그대로다.
    assert new[-1] == old[-1] and any("가장 구체적인 신호 하나" in line for line in new)


@pytest.mark.parametrize(
    "call",
    [
        lambda: get_prompt("draft", "v9"),
        lambda: get_prompt("ranking", "v1"),  # type: ignore[arg-type]
        lambda: list_versions("ranking"),  # type: ignore[arg-type]
        lambda: drafting.instruction("v9"),
        lambda: reply.render_prompt(Mock(), prompt_version="v9"),
    ],
)
def test_unknown_stage_or_version_is_rejected(call):
    """모르는 단계·판본은 거부한다.

    Args:
        call: 잘못된 단계·판본으로 부르는 함수.

    Returns:
        None: CS_UNKNOWN_PROMPT_VERSION을 확인한다.
    """
    with pytest.raises(ValueError, match="^CS_UNKNOWN_PROMPT_VERSION$"):
        call()


def test_draft_attempt_records_version_and_instruction_fingerprint():
    """초안 시도는 판본과 공급자에 보낸 지시문 전체의 지문을 남기고 모르는 판본은 호출 전에 거부한다.

    Args:
        없음.

    Returns:
        None: 시도 기록과 공급자 호출을 확인한다.
    """
    provider = Mock(return_value=Generation(raw(), Usage(), "fake-v1", ["STOP"]))
    result = attempt(provider)
    expected = hashlib.sha256(drafting.DRAFT_PROMPT.encode()).hexdigest()[:16]
    assert (result.prompt_version, result.instruction_fingerprint) == ("v1", expected)
    assert expected != prompt_fingerprint("draft", "v1")
    assert provider.call_args.args[0].startswith(drafting.DRAFT_PROMPT)
    unknown = Mock()
    with pytest.raises(ValueError, match="^CS_UNKNOWN_PROMPT_VERSION$"):
        attempt(unknown, prompt_version="v9")
    unknown.assert_not_called()


def test_draft_attempt_sends_the_selected_version(monkeypatch):
    """다른 판본을 고르면 그 본문으로 프롬프트를 만들고 지문도 달라진다.

    Args:
        monkeypatch: 검사용 판본을 잠시 등록한다.

    Returns:
        None: 공급자 프롬프트와 지문을 확인한다.
    """
    monkeypatch.setitem(PROMPTS["draft"], "test-v2", "검사용 판본 본문이다.\n")
    provider = Mock(return_value=Generation(raw(), Usage(), "fake-v1", ["STOP"]))
    result = attempt(provider, prompt_version="test-v2")
    assert provider.call_args.args[0].startswith("검사용 판본 본문이다.\n")
    assert result.prompt_version == "test-v2"
    assert (
        result.instruction_fingerprint
        != attempt(Mock(return_value=provider.return_value)).instruction_fingerprint
    )
