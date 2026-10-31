"""KAN-193 금지 약속 초안의 문장 재작성. 가짜 공급자만 쓴다(모델 호출 없음).

금지 약속에 걸린 초안은 버리기 전에 걸린 문장만 고치게 한 번 재작성한다. 검증 실패 재작성과 합쳐 한 번이다.
"""

import json

from langgraph.checkpoint.memory import InMemorySaver

from backend.engine.cs.drafting import FORBIDDEN_REASON
from backend.engine.cs.graph import initial_state, thread_config
from backend.shared.ai import Generation, Usage
from tests.cs.graph_fakes import draft_provider, verification_provider
from tests.cs.test_graph import make

PROMISE = "Your RMA is approved."


def drafter(*, forbidden_times, where="sentence"):
    """처음 forbidden_times번은 금지 약속을 넣고, 그 뒤로는 정상 초안을 낸다."""
    prompts = []

    def provider(prompt):
        prompts.append(prompt)
        body = json.loads(draft_provider(prompt).text)
        if len(prompts) <= forbidden_times:
            detailed = next(o for o in body["options"] if o["label"] == "DETAILED")
            if where == "sentence":
                detailed["sentences"].append(
                    {"text": PROMISE, "kind": "CONDITION", "evidence_chunk_ids": []}
                )
            else:
                info = next(o for o in body["options"] if o["label"] == "INFO_FIRST")
                info["info_requests"][0]["question"] = "Your RMA is approved, please send it."
        return Generation(json.dumps(body), Usage(20, 5), "fake-v1", ["STOP"], model="fake")

    return provider, prompts


def verifier(verdict="SUPPORTED"):
    calls = []

    def provider(text):
        calls.append(text)
        body = json.loads(verification_provider(text).text)
        for sentence in body["sentences"]:
            sentence["verdict"] = verdict
            sentence["reason"] = "Synthetic verification feedback."
            if verdict != "SUPPORTED":
                sentence["evidence_chunk_ids"] = []
        return Generation(json.dumps(body), Usage(7, 3), "fake", ["STOP"], attempts=1)

    return provider, calls


def run(**kwargs):
    graph, store = make(InMemorySaver(), **kwargs)
    config = thread_config("forbidden-case")
    graph.invoke(initial_state("t-1"), config)
    state = graph.get_state(config)
    assert state.next == ("human_review",)
    return graph, store, state.values


def feedback(prompt):
    return json.loads(prompt.split("\n\n입력:\n", 1)[1])["revision_feedback"]


def test_a_forbidden_sentence_is_rewritten_once_and_then_verified():
    """걸린 문장 하나만 이유와 함께 넘기고, 고친 초안을 처음 검증으로 보낸다."""
    draft, prompts = drafter(forbidden_times=1)
    check, checks = verifier()
    graph, store, values = run(draft_provider=draft, verification_provider=check)
    assert len(prompts) == 2 and len(checks) == 1
    [failed] = feedback(prompts[1])["failed_sentences"]
    assert (failed["label"], failed["sentence_index"]) == ("DETAILED", 1)
    assert failed["reason"] == FORBIDDEN_REASON
    assert PROMISE not in json.dumps(values["draft"])
    assert values["draft_rewritten"] is True and values["forbidden_feedback"] is None
    assert values["errors"] == [] and values["verification"]["status"] == "VERIFIED"
    assert [(a["kind"], a["error_code"]) for a in values["attempts"]] == [
        ("classification", None),
        ("draft", "CS_FORBIDDEN_COMMITMENT"),
        ("draft", None),
        ("verification", None),
    ]
    assert values["draft_attempt_id"] == values["attempts"][2]["attempt_id"]
    # 사람 확인 재개는 모델을 다시 부르지 않는다.
    graph.invoke(None, thread_config("forbidden-case"))
    assert (len(prompts), len(checks), len(store.attempts)) == (2, 1, 4)


def test_the_rewrite_budget_is_shared_with_verification():
    """금지 약속으로 이미 고쳐 썼으면 검증에서 UNSUPPORTED가 나와도 다시 쓰지 않는다."""
    draft, prompts = drafter(forbidden_times=1)
    check, checks = verifier("UNSUPPORTED")
    _, _, values = run(draft_provider=draft, verification_provider=check)
    assert len(prompts) == 2 and len(checks) == 1
    assert values["verification"]["status"] == "NEEDS_REVIEW"


def test_a_rewrite_that_still_promises_leaves_no_draft():
    draft, prompts = drafter(forbidden_times=2)
    check, checks = verifier()
    _, _, values = run(draft_provider=draft, verification_provider=check)
    assert len(prompts) == 2 and checks == []
    assert values["draft"] is None and values["draft_rewritten"] is False
    assert values["errors"] == [
        {"node": "draft", "code": "CS_FORBIDDEN_COMMITMENT"},
        {"node": "rewrite", "code": "CS_FORBIDDEN_COMMITMENT"},
    ]


def test_a_promise_in_an_info_question_is_not_rewritten():
    """질문은 문장 위치로 가리킬 수 없어 예전처럼 초안 없이 사람에게 넘긴다."""
    draft, prompts = drafter(forbidden_times=1, where="question")
    _, _, values = run(draft_provider=draft, verification_provider=verifier()[0])
    assert len(prompts) == 1 and values["draft"] is None
    assert values["errors"] == [{"node": "draft", "code": "CS_FORBIDDEN_COMMITMENT"}]


def test_the_rewrite_runs_even_when_verification_is_off():
    draft, prompts = drafter(forbidden_times=1)
    _, _, values = run(draft_provider=draft, verification_enabled=False)
    assert len(prompts) == 2 and values["draft"] is not None
    assert values["verification"]["reason"] == "CS_VERIFICATION_DISABLED"
