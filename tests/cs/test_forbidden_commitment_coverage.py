"""KAN-161 금지 약속 정규식의 다국어 범위 회귀. 네트워크·모델·DB 없음.

고정물 fixtures/forbidden_commitments_multilang.json은 2026-11-05 점검
(artifacts/presentation-checks-20261105/C-verifier-regex)의 187문장을 옮긴 것이다. 문장은 모델(Claude)이 쓴
합성 예문이고 실제 고객 자료가 아니다. 16개 언어, 금지 119문장·통제 68문장이다.

기준선(335f244b): 금지 재현율 19/119(en 9/18, ko 10/17, 나머지 14개 언어 각 0/6), 통제 오탐 0/68.
find_forbidden_commitment는 영어·한국어 패턴만 있는 보조 차단이라 나머지 언어 문장은 잡지 못한다.
놓친 100문장은 strict xfail이라 정규식을 넓혀 하나라도 잡히면 XPASS로 실패한다. 그때는 고정물의 caught를
true로 바꾸고 아래 기준선 수치를 고친다.

언어와 관계없는 차단은 검증 지시문 v2의 문장별 firm_commitment 표시가 맡는다(KAN-161). 모델 재현율은
artifacts/presentation-fixes-20261105/7-commitment-check에서 쟀고, 여기서는 표시를 받은 뒤의 코드(형식 검사·
UNSUPPORTED 변환·재작성·저장 모양)만 fake로 본다. 이 파일의 시험은 Gemini를 부르지 않는다.
"""

import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from backend.engine.cs.drafting import FORBIDDEN_REASON, DraftOutput, find_forbidden_commitment
from backend.engine.cs.graph import initial_state, thread_config
from backend.engine.cs.verification import (
    build_verification_input,
    case_status,
    commitment_flags,
    rewrite_targets,
    validate_output,
)
from backend.shared.ai import Generation, Usage
from tests.cs.graph_fakes import draft_provider, verification_provider
from tests.cs.test_graph import make
from tests.cs.test_verification import EVIDENCE

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "forbidden_commitments_multilang.json").read_text(
        encoding="utf-8"
    )
)
ITEMS = [
    (f"{item['language']}-{item['category']}-{index}", item)
    for index, item in enumerate(FIXTURE["items"])
]
CONTROLS = [pytest.param(item, id=name) for name, item in ITEMS if item["kind"] == "control"]
CAUGHT = [
    pytest.param(item, id=name)
    for name, item in ITEMS
    if item["kind"] == "forbidden" and item["caught"]
]
MISSED = [
    pytest.param(
        item,
        id=name,
        marks=pytest.mark.xfail(
            strict=True, reason=f"정규식 범위 밖: {item['language']} {item.get('variant', 'base')}"
        ),
    )
    for name, item in ITEMS
    if item["kind"] == "forbidden" and not item["caught"]
]


def test_baseline_counts_match_the_fixture():
    """고정물의 기준선이 금지 19/119 적중, 통제 68문장이다."""
    forbidden = [item for _, item in ITEMS if item["kind"] == "forbidden"]
    assert (len(ITEMS), len(forbidden), len(CONTROLS)) == (187, 119, 68)
    assert (len(CAUGHT), len(MISSED)) == (19, 100)
    assert not any(item["caught"] for _, item in ITEMS if item["kind"] == "control")


@pytest.mark.parametrize("item", CONTROLS)
def test_conditional_controls_are_not_flagged(item):
    """조건부 안내 통제 문장은 어느 언어든 걸리지 않는다."""
    assert not find_forbidden_commitment(item["text"])


@pytest.mark.parametrize("item", CAUGHT)
def test_currently_caught_commitments_stay_caught(item):
    """지금 잡는 금지 문장 19개를 정규식 변경 뒤에도 계속 잡는다."""
    assert find_forbidden_commitment(item["text"])


@pytest.mark.parametrize("item", MISSED)
def test_known_gaps_are_still_missed(item):
    """지금 놓치는 금지 문장 100개. 잡히면 XPASS로 실패하니 고정물을 고친다."""
    assert find_forbidden_commitment(item["text"])


# 정규식이 놓치는 독일어 RMA 승인 문장(고정물 de-rma_approved). 비근거(CONDITION) 문장으로 넣는다.
GERMAN = next(
    item["text"]
    for _, item in ITEMS
    if (item["language"], item["category"], item["kind"]) == ("de", "rma_approved", "forbidden")
)


def german_draft(times=1):
    """처음 times번은 DETAILED 끝에 GERMAN을 CONDITION으로 붙인 fake 초안을 낸다."""
    prompts = []

    def provider(prompt):
        prompts.append(prompt)
        body = json.loads(draft_provider(prompt).text)
        if len(prompts) <= times:
            detailed = next(o for o in body["options"] if o["label"] == "DETAILED")
            detailed["sentences"].append(
                {"text": GERMAN, "kind": "CONDITION", "evidence_chunk_ids": []}
            )
        return Generation(json.dumps(body), Usage(20, 5), "fake", ["STOP"], model="fake")

    return provider, prompts


def flagging(prompt):
    """GERMAN 문장만 firm_commitment=true로 표시하는 v2 fake 검증자."""
    body = json.loads(verification_provider(prompt).text)
    texts = {
        (s["label"], s["sentence_index"]): s["text"]
        for s in json.loads(prompt.split("\n\n입력:\n", 1)[1])["sentences"]
    }
    for row in body["sentences"]:
        row["firm_commitment"] = texts[(row["label"], row["sentence_index"])] == GERMAN
    return Generation(json.dumps(body), Usage(7, 3), "fake", ["STOP"], attempts=1)


def test_the_german_sentence_is_outside_the_regex():
    assert GERMAN and not find_forbidden_commitment(GERMAN)


def test_a_flagged_sentence_becomes_unsupported_and_blocks_verified():
    """v2 원답의 표시는 EVIDENCE면 판정을, 비근거 문장이면 새 판정 줄을 UNSUPPORTED·FORBIDDEN_REASON으로 만든다."""
    provider, _ = german_draft()
    draft = DraftOutput.model_validate_json(
        provider(
            "\n\n입력:\n" + json.dumps({"evidence": [{"chunk_id": "c1"}], "request_info_codes": []})
        ).text
    )
    payload = build_verification_input(draft, EVIDENCE, prompt_version="v2")
    prompt = "firm_commitment\n\n입력:\n" + payload.model_dump_json()
    clean = validate_output(
        verification_provider(prompt).text, payload=payload, prompt_version="v2"
    )
    assert case_status(clean) == "VERIFIED" and commitment_flags(clean) == []
    # 비근거 문장은 표시가 없으면 저장 모양에서 빠진다.
    assert {s.sentence_index for s in clean.sentences if s.label == "DETAILED"} == {0}

    flagged = validate_output(flagging(prompt).text, payload=payload, prompt_version="v2")
    [hit] = commitment_flags(flagged)
    assert (hit.label, hit.verdict, hit.reason, hit.evidence_chunk_ids) == (
        "DETAILED",
        "UNSUPPORTED",
        FORBIDDEN_REASON,
        [],
    )
    assert case_status(flagged) == "NEEDS_REVIEW" and rewrite_targets(flagged) == [hit]
    # 저장 경로는 초안 전체 문장 payload로 v1 모양을 다시 검사한다. 표시 줄은 통과한다.
    assert validate_output(flagged.model_dump_json(), payload=payload) == flagged

    body = json.loads(flagging(prompt).text)
    body["sentences"][0]["firm_commitment"] = True
    evidence = validate_output(json.dumps(body), payload=payload, prompt_version="v2")
    assert evidence.sentences[0].verdict == "UNSUPPORTED"
    assert evidence.sentences[0].evidence_chunk_ids == ["c1"]


@pytest.mark.parametrize("change", ["missing_flag", "missing_condition_sentence"])
def test_v2_answers_must_flag_every_sentence(change):
    provider, _ = german_draft()
    draft = DraftOutput.model_validate_json(
        provider(
            "\n\n입력:\n" + json.dumps({"evidence": [{"chunk_id": "c1"}], "request_info_codes": []})
        ).text
    )
    payload = build_verification_input(draft, EVIDENCE, prompt_version="v2")
    body = json.loads(flagging("firm_commitment\n\n입력:\n" + payload.model_dump_json()).text)
    if change == "missing_flag":
        del body["sentences"][0]["firm_commitment"]
        code = "CS_INVALID_VERIFICATION_OUTPUT"
    else:
        body["sentences"] = [r for r in body["sentences"] if r["firm_commitment"] is False]
        code = "CS_VERIFICATION_SENTENCE_MISMATCH"
    with pytest.raises(ValueError, match=f"^{code}$"):
        validate_output(json.dumps(body), payload=payload, prompt_version="v2")


def run_graph(times):
    draft, prompts = german_draft(times)
    graph, _ = make(InMemorySaver(), draft_provider=draft, verification_provider=flagging)
    config = thread_config("commitment-case")
    graph.invoke(initial_state("t-1"), config)
    return prompts, graph.get_state(config).values


def test_the_graph_rewrites_a_flagged_sentence_once_and_verifies_again():
    """정규식이 놓친 문장을 검증이 표시하면 금지 약속 이유로 한 번 고쳐 쓰고 다시 검증한다."""
    prompts, values = run_graph(times=1)
    assert len(prompts) == 2
    [failed] = json.loads(prompts[1].split("\n\n입력:\n", 1)[1])["revision_feedback"][
        "failed_sentences"
    ]
    assert (failed["label"], failed["reason"]) == ("DETAILED", FORBIDDEN_REASON)
    assert GERMAN not in json.dumps(values["draft"])
    assert values["verification"]["status"] == "VERIFIED"


def test_a_rewrite_that_keeps_the_commitment_ends_needs_review():
    prompts, values = run_graph(times=2)
    assert len(prompts) == 2
    assert values["verification"]["status"] == "NEEDS_REVIEW"
    assert [s["reason"] for s in values["verification"]["sentences"]].count(FORBIDDEN_REASON) == 1
