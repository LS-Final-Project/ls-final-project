"""KAN-200 검증·재작성의 호출 상한, 실패 시 보존과 사람 확인 중단점을 검사한다."""

import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from backend.agent.cs_runner import outcome
from backend.engine.cs.graph import initial_state, thread_config
from backend.shared.ai import Generation, Usage
from tests.cs.graph_fakes import draft_provider, verification_provider
from tests.cs.test_graph import make


def verdict_provider(verdicts):
    calls = []

    def provider(text):
        calls.append(text)
        body = json.loads(verification_provider(text).text)
        verdict = verdicts[min(len(calls) - 1, len(verdicts) - 1)]
        for sentence in body["sentences"]:
            sentence["verdict"] = verdict
            sentence["reason"] = "Synthetic verification feedback."
        return Generation(json.dumps(body), Usage(7, 3), "fake", ["STOP"], attempts=1)

    return provider, calls


def invoke(**kwargs):
    graph, store = make(InMemorySaver(), **kwargs)
    config = thread_config("verification-case")
    graph.invoke(initial_state("t-1"), config)
    state = graph.get_state(config)
    assert state.next == ("human_review",)
    return graph, store, state


def test_supported_draft_is_verified_once_and_resume_never_calls_again():
    provider, calls = verdict_provider(["SUPPORTED"])
    graph, store, state = invoke(verification_provider=provider)
    assert state.values["verification"]["status"] == "VERIFIED"
    assert len(calls) == 1
    kinds = [kind for kind, _, _ in store.attempts]
    assert kinds == ["classification", "draft", "verification"]
    verification = store.attempts[-1][2]
    assert verification.draft_id == state.values["draft_attempt_id"]
    assert verification.usage.input_tokens == 7
    graph.invoke(None, thread_config("verification-case"))
    assert len(calls) == 1


def test_unverifiable_is_marked_without_a_rewrite():
    """근거가 모자라 판정 못 한 문장은 고쳐도 근거가 늘지 않는다. 표시만 하고 사람에게 간다."""
    verifier, checks = verdict_provider(["UNVERIFIABLE"])
    drafts = []

    def drafter(text):
        drafts.append(text)
        return draft_provider(text)

    _, _, state = invoke(verification_provider=verifier, draft_provider=drafter)
    assert len(drafts) == len(checks) == 1
    assert state.values["verification"]["status"] == "NEEDS_REVIEW"


def test_rewrite_feedback_carries_only_the_unsupported_sentences():
    checks = []

    def verifier(text):
        checks.append(text)
        body = json.loads(verification_provider(text).text)
        for index, sentence in enumerate(body["sentences"]):
            sentence["verdict"] = "UNSUPPORTED" if index == 0 else "UNVERIFIABLE"
            sentence["evidence_chunk_ids"] = []
            sentence["reason"] = "Synthetic verification feedback."
        return Generation(json.dumps(body), Usage(7, 3), "fake", ["STOP"], attempts=1)

    drafts = []

    def drafter(text):
        drafts.append(text)
        return draft_provider(text)

    invoke(verification_provider=verifier, draft_provider=drafter)
    data = json.loads(drafts[-1].split("\n\n입력:\n", 1)[1])
    assert len(data["revision_feedback"]["failed_sentences"]) == 1
    assert len(checks) == 2


def test_a_rewrite_without_evidence_sentences_keeps_the_first_verdict():
    """재작성본을 검증할 수 없어도 재작성을 부른 판정은 사람에게 남는다."""
    drafts = []

    def drafter(text):
        drafts.append(text)
        body = json.loads(draft_provider(text).text)
        if len(drafts) > 1:
            for option in body["options"]:
                option["sentences"] = [
                    {"text": "합성 조건 문장", "kind": "CONDITION", "evidence_chunk_ids": []}
                ]
        return Generation(json.dumps(body), Usage(7, 3), "fake", ["STOP"], attempts=1)

    verifier, checks = verdict_provider(["UNSUPPORTED"])
    _, _, state = invoke(verification_provider=verifier, draft_provider=drafter)
    found = state.values["verification"]
    assert len(checks) == 1 and len(drafts) == 2
    assert found["status"] == "NEEDS_REVIEW" and found["reason"] == "CS_NO_EVIDENCE_SENTENCES"
    assert {s["verdict"] for s in found["sentences"]} == {"UNSUPPORTED"}


def test_a_draft_without_evidence_sentences_calls_no_verifier():
    def condition_only(text):
        body = json.loads(draft_provider(text).text)
        for option in body["options"]:
            option["sentences"] = [
                {"text": "합성 조건 문장", "kind": "CONDITION", "evidence_chunk_ids": []}
            ]
        return Generation(json.dumps(body), Usage(7, 3), "fake", ["STOP"], attempts=1)

    verifier, checks = verdict_provider(["SUPPORTED"])
    _, _, state = invoke(verification_provider=verifier, draft_provider=condition_only)
    assert checks == []
    assert state.values["verification"]["status"] == "NOT_VERIFIED"
    assert state.values["verification"]["reason"] == "CS_NO_EVIDENCE_SENTENCES"


@pytest.mark.parametrize("final", ["SUPPORTED", "UNSUPPORTED", "UNVERIFIABLE"])
def test_failure_rewrites_once_rechecks_once_and_keeps_the_final_verdict(final):
    verifier, checks = verdict_provider(["UNSUPPORTED", final])
    drafts = []

    def drafter(text):
        drafts.append(text)
        return draft_provider(text)

    graph, store, state = invoke(verification_provider=verifier, draft_provider=drafter)
    assert len(drafts) == len(checks) == 2
    assert "Synthetic verification feedback." in drafts[-1]
    assert "revision_feedback" in drafts[-1] and "revision_feedback" not in drafts[0]
    assert {s["verdict"] for s in state.values["verification"]["sentences"]} == {final}
    assert state.values["verification"]["status"] == (
        "VERIFIED" if final == "SUPPORTED" else "NEEDS_REVIEW"
    )
    attempts = [a for _, _, a in store.attempts]
    assert [k for k, _, _ in store.attempts] == [
        "classification",
        "draft",
        "verification",
        "draft",
        "verification",
    ]
    assert attempts[2].draft_id != attempts[4].draft_id
    assert attempts[4].draft_id == state.values["draft_attempt_id"]
    assert attempts[1].input_sha256 != attempts[3].input_sha256
    assert attempts[3].input_version == "cs-draft-revision-input-v1"
    result = outcome(graph, "verification-case")
    assert result.retried == result.recovered == 0  # round=2 검증은 형식 재시도가 아니다.


@pytest.mark.parametrize("failure", ["timeout", "bad-json"])
def test_verification_failure_preserves_original_draft_without_rewrite(failure):
    calls = []

    def verifier(text):
        calls.append(text)
        if failure == "timeout":
            raise TimeoutError("private")
        return Generation("not JSON", Usage(1, 1), "fake", [])

    _, store, state = invoke(verification_provider=verifier)
    assert len(calls) == 1
    assert [kind for kind, _, _ in store.attempts].count("draft") == 1
    assert state.values["draft"] is not None
    assert state.values["verification"]["status"] == "NOT_VERIFIED"
    assert {s["verdict"] for s in state.values["verification"]["sentences"]} == {"UNVERIFIABLE"}
    assert "private" not in json.dumps(state.values)


def test_rewrite_schema_failure_has_no_format_retry_and_preserves_original_verdict():
    verifier, checks = verdict_provider(["UNSUPPORTED"])
    calls = []

    def drafter(text):
        calls.append(text)
        return (
            draft_provider(text)
            if len(calls) == 1
            else Generation("bad JSON", Usage(1, 1), "fake", [])
        )

    _, store, state = invoke(verification_provider=verifier, draft_provider=drafter)
    assert len(calls) == 2 and len(checks) == 1
    original = store.attempts[1][2]
    assert state.values["draft"] == original.prediction.model_dump(mode="json")
    assert state.values["verification"]["status"] == "NEEDS_REVIEW"
    assert state.values["errors"][-1] == {"node": "rewrite", "code": "CS_INVALID_OUTPUT"}


@pytest.mark.parametrize(
    "enabled,reason", [(False, "CS_VERIFICATION_DISABLED"), (True, "CS_VERIFICATION_UNAVAILABLE")]
)
def test_disabled_or_missing_provider_is_explicitly_unverified(enabled, reason):
    _, store, state = invoke(verification_enabled=enabled)
    assert state.values["verification"]["status"] == "NOT_VERIFIED"
    assert state.values["verification"]["reason"] == reason
    assert [kind for kind, _, _ in store.attempts] == ["classification", "draft"]


def test_initial_draft_format_retry_is_preserved_before_verification():
    calls = []

    def drafter(text):
        calls.append(text)
        return (
            Generation("bad JSON", Usage(), "fake", []) if len(calls) == 1 else draft_provider(text)
        )

    graph, _, state = invoke(draft_provider=drafter, verification_provider=verification_provider)
    assert len(calls) == 2 and state.values["verification"]["status"] == "VERIFIED"
    assert (
        outcome(graph, "verification-case").retried,
        outcome(graph, "verification-case").recovered,
    ) == (1, 1)


def test_reverification_timeout_keeps_rewritten_draft_and_marks_it_unverified():
    checks = []
    first, _ = verdict_provider(["UNSUPPORTED"])

    def verifier(text):
        checks.append(text)
        if len(checks) == 2:
            raise TimeoutError("private")
        return first(text)

    _, store, state = invoke(verification_provider=verifier)
    assert len(checks) == 2
    assert state.values["draft"] == store.attempts[3][2].prediction.model_dump(mode="json")
    assert state.values["verification"]["status"] == "NOT_VERIFIED"
    assert state.values["draft_attempt_id"] == store.attempts[4][2].draft_id


def test_worker_maps_verification_nodes_to_draft_progress_and_can_stop_before_rewrite():
    from types import SimpleNamespace

    from backend.agent.cs_worker import run_graph

    graph, _ = make(InMemorySaver(), verification_provider=verdict_provider(["UNSUPPORTED"])[0])
    events = []

    def progress(event):
        events.append(event)
        return len(events) < 5

    lease = SimpleNamespace(job_id="progress-test", subject_id="t-1")
    assert run_graph(graph, lease, progress) is False
    assert [e["step"] for e in events] == ["classify", "rules", "retrieve", "draft", "draft"]
    assert graph.get_state(thread_config("progress-test")).next == ("rewrite",)
