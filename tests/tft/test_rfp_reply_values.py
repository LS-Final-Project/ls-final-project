"""KAN-224: 고객 회신에서 새 요구값 후보 읽기(규칙 우선, 애매하면 AI, 근거 검사). 모델 호출 없음."""

import json

import pytest

from backend.engine.rfp.reply_values import read_reply_value
from backend.shared.ai import Generation, Usage

ASK = {
    "unit": "kV",
    "attribute": "power_frequency_withstand",
    "question_text": "값을 알려 주십시오.",
}
# RFP-91 합성 시연 회신 Q-01(artifacts/kan188-demo-rfp/replies/replies.json과 같은 문장).
Q01 = "MV-SWG-01 상용주파 내전압은 28 kV, 1분으로 적용 바랍니다(정격전압 12 kV 표준값)."


def read(text: str, **over: object):
    return read_reply_value(text, **{**ASK, "condition_names": ["duration"], **over})


def fake(payload: object):
    calls: list[str] = []

    def provider(prompt: str) -> Generation:
        calls.append(prompt)
        return Generation(
            text=json.dumps(payload), usage=Usage(), model_version="fake", finish_reasons=[]
        )

    return provider, calls


def test_rfp91_q01_reads_one_value_and_duration_by_rule():
    got = read(Q01)
    assert (got.method, got.value, got.unit) == ("rule", "28", "kV")
    assert [(c.name, c.value, c.unit) for c in got.conditions] == [("duration", "1", "min")]
    assert got.quote == Q01  # 괄호 안 12 kV는 부연이라 값 후보가 아니다


@pytest.mark.parametrize("text", ["28kVrms 1 min", "28 kV/1min 적용", "28 KV, 1분간"])
def test_notation_variants(text):
    got = read(text)
    assert (got.method, got.value) == ("rule", "28")
    assert got.conditions[0].unit == "min"


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("기존 사양대로 해 주세요", "NO_VALUE"),
        ("28 kV 또는 38 kV 중 고르겠습니다", "MULTIPLE_VALUES"),
        ("28 kV가 아니라 38 kV입니다", "CORRECTION"),
        ("실내 설치 시 28 kV, 옥외는 별도 협의", "CONDITIONAL"),
        ("TQ-1 28 kV, TQ-2 25 kA", "MULTIPLE_QUESTIONS"),
    ],
)
def test_ambiguous_replies_are_not_read_by_rule(text, reason):
    got = read(text)
    assert (got.method, got.value, got.reason) == ("none", None, reason)


def test_thousands_comma_is_one_value():
    assert read("주모선 1,250 A 이상", unit="A").value == "1250"


def test_ai_is_called_only_when_the_rule_cannot_read():
    provider, calls = fake({"value": "38", "unit": "kV", "quote": "38 kV입니다", "conditions": []})
    assert read(Q01, provider=provider).method == "rule" and calls == []
    got = read("28 kV가 아니라 38 kV입니다", provider=provider)
    assert (got.method, got.value, got.quote) == ("ai", "38", "38 kV입니다") and len(calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"value": "38", "unit": "kV", "quote": "38 kV로 하겠습니다"},  # 원문에 없는 문장
        {"value": "40", "unit": "kV", "quote": "38 kV입니다"},  # 값이 근거 문장에 없음
        {"value": "38", "unit": "kA", "quote": "38 kV입니다"},  # 기대 단위가 아님
        {"value": None, "quote": None},
        "not json",
    ],
)
def test_ungrounded_ai_output_is_dropped(payload):
    provider, _ = fake(payload)
    got = read("28 kV가 아니라 38 kV입니다", provider=provider)
    assert (got.method, got.value, got.reason) == ("none", None, "AI_UNGROUNDED")


def test_ai_failure_does_not_break_reply():
    def boom(prompt: str) -> Generation:
        raise RuntimeError("down")

    got = read("기존 사양대로", provider=boom)
    assert (got.method, got.reason) == ("none", "AI_FAILED")
