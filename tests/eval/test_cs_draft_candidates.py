"""KAN-142 2차 초안 후보 비교 하네스. 합성 사례와 fake 공급자만 쓰고 정답 파일·모델·DB에 접근하지 않는다."""

import json
from collections.abc import Iterator, Mapping
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.engine.cs.classification import ClassificationOutput
from backend.engine.cs.drafting import EvidenceSnippet
from backend.engine.cs.drafting_call import DraftAttempt
from backend.engine.cs.verification import VerificationOutput
from backend.shared.ai import AISettings, Generation, Usage
from eval.cs_draft_candidates import (
    SUBSET_NOTE,
    DraftCandidate,
    DraftCase,
    run_draft_candidates,
)
from eval.cs_draft_metrics import DraftGold
from scripts import cs_draft_run

CLASSIFICATION = ClassificationOutput(
    category_code="WARRANTY",
    secondary_category=None,
    urgency_flag="NONE",
    provided_info=["TEST_SERIAL"],
    return_subtype=None,
)


def snippet(chunk_id):
    """합성 근거 하나.

    Args:
        chunk_id: 청크 ID.

    Returns:
        EvidenceSnippet: 근거.
    """
    return EvidenceSnippet(
        chunk_id=chunk_id,
        snippet=f"Synthetic clause {chunk_id}.",
        section_path=["S"],
        document_sha256="a" * 64,
    )


def case(number):
    """합성 사례 하나(근거 2개 중 앞의 것이 정답 근거).

    Args:
        number: 사례 번호.

    Returns:
        DraftCase: 사례.
    """
    record = {
        "channel": "EMAIL",
        "body_language": "en",
        "subject": None,
        "body": f"case-{number} secret body person@example.test",
        "category_code": "GOLD_SECRET",
    }
    return DraftCase(
        record,
        CLASSIFICATION,
        frozenset({"TEST_SERIAL", "TEST_PHOTO"}),
        frozenset(),
        [snippet(f"rel-{number}"), snippet(f"other-{number}")],
        {},
    )


CASES = {f"k{n}": case(n) for n in range(1, 6)}
GOLD = {
    f"k{n}": DraftGold(
        frozenset({f"rel-{n}", f"other-{n}"}),
        frozenset({f"rel-{n}"}),
        frozenset({"TEST_PHOTO"}),
        True,
    )
    for n in range(1, 6)
}


def provider(*, cite="rel", text="Synthetic answer.", only=None):
    """프롬프트의 입력 JSON을 읽어 세 선택지를 답하는 fake 공급자를 만든다.

    Args:
        cite: 인용할 근거 접두어(rel=정답 근거, other=정답 밖 근거).
        text: 문장. only가 있으면 그 사례에만 쓴다.
        only: text를 쓸 사례 번호.

    Returns:
        Callable: 프롬프트를 받아 Generation을 돌려주는 함수.
    """

    def call(prompt):
        """fake 응답.

        Args:
            prompt: 렌더링한 프롬프트.

        Returns:
            Generation: 합성 응답.
        """
        payload = json.loads(prompt.split("\n\n입력:\n", 1)[1])
        # 재작성 지시문은 입력을 input 아래에 둔다.
        payload = payload.get("input", payload)
        chunk = next(e["chunk_id"] for e in payload["evidence"] if e["chunk_id"].startswith(cite))
        number = chunk.split("-")[1]
        sentence = text if only in (None, number) else "Synthetic answer."
        options = [
            {
                "label": label,
                "sentences": [
                    {"text": sentence, "kind": "EVIDENCE", "evidence_chunk_ids": [chunk]}
                ],
                "info_requests": (
                    [
                        {"info_code": c, "question": "Please send it."}
                        for c in payload["request_info_codes"]
                    ]
                    if label == "INFO_FIRST"
                    else []
                ),
            }
            for label in ("CONCISE", "DETAILED", "INFO_FIRST")
        ]
        return Generation(
            json.dumps({"options": options}), Usage(10, 2), "fake-v1", ["STOP"], model="fake"
        )

    return call


CANDIDATES = [
    DraftCandidate("forbidden", provider(text="RMA가 승인되었습니다."), "fake-a", "v1"),
    DraftCandidate("ungrounded", provider(cite="other"), "fake-a", "v1"),
    DraftCandidate("grounded", provider(), "fake-a", "v1"),
]


def run(**kwargs):
    """기본 설정으로 하네스를 돌린다.

    Args:
        kwargs: 바꿀 인자.

    Returns:
        dict: 비교표.
    """
    return run_draft_candidates(
        **({"cases": CASES, "gold": GOLD, "candidates": CANDIDATES} | kwargs)
    )


def test_ranking_separates_forbidden_from_grounding():
    """금지 약속은 스키마 위반과 따로 세고, 근거 이탈 후보는 근거 비율로 뒤에 선다.

    Args:
        없음.

    Returns:
        None: 순위·금지·근거·요청 지표를 확인한다.
    """
    result = run()
    rows = {row["name"]: row for row in result["candidates"]}
    assert result["ranking"] == ["grounded", "ungrounded", "forbidden"]
    assert rows["forbidden"]["forbidden"]["count"] == 5
    assert rows["forbidden"]["schema"]["schema_violations"] == 0
    assert rows["grounded"]["grounding"]["grounded_case_rate"] == 1.0
    assert rows["ungrounded"]["grounding"]["grounded_case_rate"] == 0.0
    assert rows["ungrounded"]["grounding"]["outside_relevant_sentences"] == 15
    assert rows["grounded"]["info_requests"]["INFO_FIRST"]["f1"] == 1.0
    assert rows["grounded"]["usage"]["input_tokens"]["total"] == 50
    assert rows["grounded"]["model"] == "fake-a" and rows["grounded"]["prompt_version"] == "v1"
    assert result["subset"] == {"rerun": 5, "total": 5} and "note" not in result


def test_forbidden_then_blocked_rank_before_grounding():
    """금지 약속·차단 코드를 낸 후보는 근거 비율이 높아도 그 건수가 적은 후보 뒤에 선다.

    Args:
        없음.

    Returns:
        None: blocked 건수와 순위를 확인한다.
    """
    blocked = DraftCandidate(
        "blocked", provider(text="See FLT-042 notes.", only="1"), "fake-a", "v1"
    )
    forbid_one = DraftCandidate(
        "forbid-one", provider(text="RMA가 승인되었습니다.", only="2"), "fake-a", "v1"
    )
    result = run(candidates=[forbid_one, blocked, CANDIDATES[1]])
    rows = {row["name"]: row for row in result["candidates"]}
    assert rows["blocked"]["blocked"] == 1 and rows["forbid-one"]["forbidden"]["count"] == 1
    assert rows["blocked"]["grounding"]["grounded_case_rate"] == 1.0
    assert rows["forbid-one"]["grounding"]["grounded_case_rate"] == 1.0
    # 근거 비율이 높아도 금지 약속 → 차단 근거 순으로 먼저 뒤로 간다.
    assert result["ranking"] == ["ungrounded", "blocked", "forbid-one"]


def broken(*, only=None):
    """깨진 JSON을 내는 fake 공급자를 만든다.

    Args:
        only: 이 사례 번호만 깨뜨린다. None이면 전부 깨뜨린다.

    Returns:
        Callable: 프롬프트를 받아 Generation을 돌려주는 함수.
    """
    good = provider()

    def call(prompt):
        """fake 응답.

        Args:
            prompt: 프롬프트.

        Returns:
            Generation: 합성 응답.
        """
        if only is None or f"case-{only} " in prompt:
            return Generation("RAW_SECRET not json", Usage(10, 2), "fake-v1", ["STOP"])
        return good(prompt)

    return call


def test_broken_candidates_rank_after_valid_ones():
    """금지·차단이 같으면 깨진 응답이 많은 후보가 뒤로 간다(근거 비율이 높아도).

    Args:
        없음.

    Returns:
        None: 전부·일부 깨진 후보의 순위를 확인한다.
    """
    result = run(
        candidates=[
            DraftCandidate("all-broken", broken(), "fake-a", "v1"),
            DraftCandidate("one-broken", broken(only="3"), "fake-a", "v1"),
            CANDIDATES[1],
        ]
    )
    rows = {row["name"]: row for row in result["candidates"]}
    assert rows["all-broken"]["schema"]["schema_violations"] == 5
    assert rows["one-broken"]["grounding"]["grounded_case_rate"] == 1.0
    # 정답 밖 근거만 인용해도 VALID 5건인 후보가 VALID 4건(근거 비율 1.0)보다 앞선다.
    assert result["ranking"] == ["ungrounded", "one-broken", "all-broken"]


def test_forbidden_candidate_ranks_after_all_broken():
    """금지 약속은 평균으로 상쇄하지 않는다. 아무것도 못 낸 후보가 승인 약속을 낸 후보보다 앞선다.

    Args:
        없음.

    Returns:
        None: 순위를 확인한다.
    """
    forbid_one = DraftCandidate(
        "forbid-one", provider(text="RMA가 승인되었습니다.", only="2"), "fake-a", "v1"
    )
    result = run(candidates=[forbid_one, DraftCandidate("all-broken", broken(), "fake-a", "v1")])
    assert result["ranking"] == ["all-broken", "forbid-one"]


def test_subset_reruns_only_selected_cases():
    """부분 집합은 그 사례만 재실행하고 전체 집계에 쓰지 않는다는 표시를 단다.

    Args:
        없음.

    Returns:
        None: 대상 수·per_case·note를 확인한다.
    """
    calls = []

    def counting(prompt):
        """호출 수를 세는 공급자.

        Args:
            prompt: 프롬프트.

        Returns:
            Generation: grounded 응답.
        """
        calls.append(prompt)
        return provider()(prompt)

    result = run(
        candidates=[DraftCandidate("grounded", counting, "fake-a", "v1")],
        subset=frozenset({"k2", "k4"}),
    )
    assert len(calls) == 2
    assert result["subset"] == {"rerun": 2, "total": 5} and result["note"] == SUBSET_NOTE
    [row] = result["candidates"]
    assert [c["case_id"] for c in row["per_case"]] == ["k2", "k4"]
    assert set(row["per_case"][0]) == {"case_id", "outcome", "error_code", "grounded"}


def test_result_has_no_raw_response_or_source_text():
    """원시 응답·문의 본문·근거 원문·정답 칸을 결과에 넣지 않는다.

    Args:
        없음.

    Returns:
        None: 직렬화 결과를 확인한다.
    """
    dumped = json.dumps(run())
    for secret in (
        "secret body",
        "person@example.test",
        "Synthetic clause",
        "GOLD_SECRET",
        "RMA가",
    ):
        assert secret not in dumped


class ShiftingRecord(Mapping):
    """읽을 때마다 본문이 바뀌는 문의. 후보 간 입력 불일치를 재현한다."""

    def __init__(self) -> None:
        """읽은 횟수를 0으로 둔다.

        Returns:
            None: 초기화.
        """
        self.reads = 0

    def __getitem__(self, key):
        """본문을 읽을 때마다 다른 값을 준다.

        Args:
            key: 필드 이름.

        Returns:
            object: 필드 값.
        """
        if key == "body":
            self.reads += 1
            return f"case-1 read {self.reads}"
        return CASES["k1"].record[key]

    def __iter__(self) -> Iterator[str]:
        """필드 이름.

        Returns:
            Iterator[str]: 필드 이름.
        """
        return iter(CASES["k1"].record)

    def __len__(self) -> int:
        """필드 수.

        Returns:
            int: 필드 수.
        """
        return len(CASES["k1"].record)


def test_input_hash_mismatch_between_candidates_is_rejected():
    """같은 사례인데 후보마다 정제 입력이 다르면 비교하지 않는다.

    Args:
        없음.

    Returns:
        None: CS_CANDIDATE_INPUT_MISMATCH를 확인한다.
    """
    shifting = DraftCase(
        ShiftingRecord(),
        *[
            getattr(CASES["k1"], f)
            for f in (
                "classification",
                "required_info_codes",
                "already_asked_codes",
                "evidence",
                "sensitive_values",
            )
        ],
    )
    with pytest.raises(ValueError, match="^CS_CANDIDATE_INPUT_MISMATCH$"):
        run(cases=CASES | {"k1": shifting})


@pytest.mark.parametrize(
    "kwargs",
    [
        {"candidates": []},
        {"candidates": [CANDIDATES[0], CANDIDATES[0]]},
        {"cases": {}, "gold": {}},
        {"gold": {k: v for k, v in GOLD.items() if k != "k5"}},
        {"subset": frozenset()},
        {"subset": frozenset({"k9"})},
    ],
    ids=[
        "no-candidates",
        "duplicate-name",
        "no-cases",
        "gold-mismatch",
        "empty-subset",
        "unknown-subset",
    ],
)
def test_invalid_setup_is_rejected(kwargs):
    """빈 후보·이름 중복·빈 사례·정답 불일치·잘못된 부분 집합을 거부한다.

    Args:
        kwargs: 잘못된 설정.

    Returns:
        None: EVAL_INVALID_INPUT을 확인한다.
    """
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        run(**kwargs)


def test_each_candidate_runs_its_own_prompt_version(monkeypatch):
    """후보의 prompt_version이 실제 프롬프트 판본으로 쓰인다(이름표가 아니다).

    Args:
        monkeypatch: 검사용 draft 판본을 잠시 등록한다.

    Returns:
        None: 후보별 공급자가 받은 프롬프트 첫머리를 확인한다.
    """
    from backend.engine.cs import prompt_registry
    from backend.engine.cs.drafting import instruction

    monkeypatch.setitem(prompt_registry.PROMPTS["draft"], "test-v2", "검사용 초안 판본 본문.\n")
    seen: dict[str, list[str]] = {"v1": [], "test-v2": []}

    def recording(version):
        """판본별로 받은 프롬프트를 기록하는 공급자.

        Args:
            version: 기록할 판본 키.

        Returns:
            Callable: grounded 응답을 내는 공급자.
        """

        def call(prompt):
            seen[version].append(prompt)
            return provider()(prompt)

        return call

    run(
        candidates=[
            DraftCandidate("v1", recording("v1"), "fake-a", "v1"),
            DraftCandidate("v2", recording("test-v2"), "fake-a", "test-v2"),
        ]
    )
    assert seen["v1"] and all(p.startswith(instruction("v1")) for p in seen["v1"])
    assert seen["test-v2"] and all(
        p.startswith("검사용 초안 판본 본문.\n") for p in seen["test-v2"]
    )


def test_candidate_knobs_are_recorded_on_the_row():
    """초안 후보 행은 조절 항목을 그대로 기록하고 기본은 빈 dict다.

    Args:
        없음.

    Returns:
        None: 행의 knobs를 확인한다.
    """
    knobs = {"thinking_level": "LOW", "max_output_tokens": 2048}
    result = run(
        candidates=[
            DraftCandidate("tuned", provider(), "fake-a", "v1", knobs=knobs),
            DraftCandidate("plain", provider(), "fake-a", "v1"),
        ]
    )
    rows = {row["name"]: row for row in result["candidates"]}
    assert rows["tuned"]["knobs"] == knobs and rows["plain"]["knobs"] == {}


def test_return_attempts_adds_selected_attempts_only_when_asked():
    """return_attempts=True면 후보별 실행 사례의 시도를 더하고, 기본은 기존 형식 그대로다.

    Args:
        없음.

    Returns:
        None: attempts 유무·사례 범위·나머지 결과 동일을 확인한다.
    """
    plain = run(subset=frozenset({"k2", "k4"}))
    with_attempts = run(subset=frozenset({"k2", "k4"}), return_attempts=True)
    assert "attempts" not in plain and "retries" not in plain
    attempts = with_attempts.pop("attempts")
    assert with_attempts.pop("retries") == {candidate.name: {} for candidate in CANDIDATES}
    # 지연 시간은 실행마다 달라 비교에서 뺀다.
    for result in (plain, with_attempts):
        for row in result["candidates"]:
            row.pop("latency_ms")
    assert with_attempts == plain
    assert set(attempts) == {candidate.name for candidate in CANDIDATES}
    assert all(set(runs) == {"k2", "k4"} for runs in attempts.values())
    assert all(isinstance(a, DraftAttempt) for runs in attempts.values() for a in runs.values())


def test_only_malformed_drafts_are_retried_as_attempt_two():
    """형식 위반(CS_INVALID_OUTPUT)만 attempt 2로 한 번 더 부르고, 금지 약속은 재호출하지 않는다.

    Args:
        없음.

    Returns:
        None: 최초·최종 지표, 재호출 수, 호출 수, 재호출 끔을 확인한다.
    """
    seen = set()
    healthy = provider()

    def once_broken(prompt):
        """사례별 첫 호출만 깨진 원문을 낸다.

        Args:
            prompt: 렌더링한 프롬프트.

        Returns:
            Generation: 처음이면 깨진 응답, 아니면 근거 있는 응답.
        """
        first = prompt not in seen
        seen.add(prompt)
        if first:
            return Generation("RAW_SECRET not json", Usage(10, 2), "fake-v1", ["STOP"])
        return healthy(prompt)

    result = run(
        candidates=[
            DraftCandidate("recovers", once_broken, "fake-a", "v1"),
            CANDIDATES[0],
        ],
        return_attempts=True,
    )
    rows = {row["name"]: row for row in result["candidates"]}
    recovers, forbidden = rows["recovers"], rows["forbidden"]
    assert recovers["schema"]["schema_violations"] == len(CASES)
    final = recovers["final"]
    assert (final["retried"], final["recovered"], final["calls"]) == (5, 5, 10)
    assert final["schema"]["outcomes"]["VALID"] == len(CASES)
    assert final["schema"]["schema_violations"] == 0
    assert (forbidden["final"]["retried"], forbidden["final"]["forbidden"]["count"]) == (0, 5)
    assert result["retries"]["forbidden"] == {}
    assert {a.attempt_number for a in result["retries"]["recovers"].values()} == {2}
    seen.clear()
    off = run(candidates=[DraftCandidate("recovers", once_broken, "fake-a", "v1")],
              invalid_output_retries=0)  # fmt: skip
    assert off["candidates"][0]["final"]["retried"] == 0
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        run(invalid_output_retries=2)


def test_rule_violations_keep_their_code_and_are_not_retried():
    """형식은 맞지만 입력 밖 근거를 인용한 초안은 CS_UNKNOWN_REFERENCE로 남고 재호출하지 않는다(KAN-194 6차).

    Args:
        없음.

    Returns:
        None: 최초 위반 코드, 재호출 0, 스키마 위반 수를 확인한다.
    """
    healthy = provider()

    def outside_evidence(prompt):
        """근거 ID를 입력에 없는 값으로 바꾼 응답을 낸다.

        Args:
            prompt: 렌더링한 프롬프트.

        Returns:
            Generation: 입력 밖 근거를 인용한 응답.
        """
        generation = healthy(prompt)
        body = json.loads(generation.text)
        for option in body["options"]:
            for sentence in option["sentences"]:
                if sentence["evidence_chunk_ids"]:
                    sentence["evidence_chunk_ids"] = ["not-in-input"]
        return replace(generation, text=json.dumps(body))

    result = run(
        candidates=[DraftCandidate("cites-outside", outside_evidence, "fake-a", "v1")],
        return_attempts=True,
    )
    row = result["candidates"][0]
    attempts = result["attempts"]["cites-outside"]
    cited = [a for a in attempts.values() if a.error_code == "CS_UNKNOWN_REFERENCE"]
    assert cited and all(a.outcome == "SCHEMA_VIOLATION" for a in cited)
    assert row["final"]["retried"] == 0 and result["retries"]["cites-outside"] == {}
    assert row["schema"]["schema_violations"] == len(cited)
    assert row["schema"]["error_codes"] == {"CS_UNKNOWN_REFERENCE": len(cited)}


def test_draft_on_case_and_prior_replay_without_calls():
    """초안 하네스도 on_case 기록을 prior로 넘기면 호출 없이 같은 결과를 내고, 실행 범위 밖 prior는 거부한다.

    Args:
        없음.

    Returns:
        None: 기록 수·재생 결과·호출 수를 확인한다.
    """
    seen = {}
    live = run(
        on_case=lambda name, case_id, first, retry: seen.update({(name, case_id): (first, retry)})
    )
    assert len(seen) == len(CASES) * len(CANDIDATES)
    prior = {}
    for (name, case_id), pair in seen.items():
        prior.setdefault(name, {})[case_id] = pair
    calls = []

    def counting(prompt):
        calls.append(prompt)
        raise AssertionError("prior 사례는 부르지 않는다")

    replay = run(
        candidates=[
            DraftCandidate(c.name, counting, c.model, c.prompt_version) for c in CANDIDATES
        ],
        prior=prior,
    )
    assert calls == []
    for result in (live, replay):
        for row in result["candidates"]:
            row.pop("latency_ms")
    assert replay == live
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        run(subset=frozenset({"k1"}), prior={"grounded": {"k2": seen["grounded", "k2"]}})


def verifier(*verdicts, failure=None):
    """호출마다 입력 문장 전부에 같은 판정을 내는 fake 검증자. 판정은 사례마다 순서대로 돈다.

    Args:
        verdicts: 호출 순서대로 쓸 판정(모자라면 마지막 값).
        failure: 있으면 이 예외를 낸다.

    Returns:
        Callable: 호출 기록을 calls에 가진 공급자.
    """
    calls = []

    def call(prompt):
        calls.append(prompt)
        if failure is not None:
            raise failure
        payload = json.loads(prompt.rsplit("입력:\n", 1)[1])
        verdict = verdicts[min(len(calls), len(verdicts)) - 1]
        sentences = [
            {
                "label": s["label"],
                "sentence_index": s["sentence_index"],
                "verdict": verdict,
                "reason": "synthetic secret reason",
                "evidence_chunk_ids": s["evidence_chunk_ids"],
            }
            for s in payload["sentences"]
        ]
        return Generation(json.dumps({"sentences": sentences}), Usage(5, 7), "fake-v", ["STOP"])

    call.calls = calls
    return call


def verified(checker, draft=None, cases=None):
    """검증을 켠 후보 하나를 돌린다."""
    picked = cases or {"k1": CASES["k1"], "k2": CASES["k2"]}
    result = run(
        cases=picked,
        gold={k: GOLD[k] for k in picked},
        candidates=[
            DraftCandidate(
                "checked", draft or provider(), "fake-a", "v1", verification_provider=checker
            )
        ],
    )
    return result["candidates"][0]


def test_supported_drafts_are_verified_once_without_a_rewrite():
    checker = verifier("SUPPORTED")
    row = verified(checker)["verification"]
    assert row["calls"] == {"verification": 2, "rewrite": 0}
    assert row["status"] == {"first": {"VERIFIED": 2}, "final": {"VERIFIED": 2}}
    assert row["verdicts"]["first"] == {"SUPPORTED": 6}
    assert row["rewrite"] == {"called": 0, "valid": 0, "errors": {}}
    assert row["usage"]["input_tokens"]["total"] == 10


def test_a_failed_sentence_rewrites_once_and_verifies_again():
    """검증 실패면 재작성 1회·재검증 1회다. 판정 분포는 처음과 최종을 따로 남긴다."""
    checker = verifier("UNSUPPORTED", "SUPPORTED")
    calls = []
    base = provider()

    def draft(prompt):
        calls.append(prompt)
        return base(prompt)

    row = verified(checker, draft, {"k1": CASES["k1"]})["verification"]
    assert row["calls"] == {"verification": 2, "rewrite": 1}
    assert len(calls) == 2
    assert row["status"] == {"first": {"NEEDS_REVIEW": 1}, "final": {"VERIFIED": 1}}
    assert row["verdicts"]["first"] == {"UNSUPPORTED": 3}
    assert row["verdicts"]["final"] == {"SUPPORTED": 3}
    assert row["rewrite"]["valid"] == 1
    assert row["after_rewrite"]["schema"]["outcomes"]["VALID"] == 1
    assert row["per_case"] == [
        {"case_id": "k1", "first": "NEEDS_REVIEW", "rewrite": "VALID", "final": "VERIFIED"}
    ]
    assert "gate" not in json.dumps(row) and "passed" not in row


def test_after_rewrite_metrics_read_the_rewritten_draft():
    """재작성한 초안이 최종이다. 재작성이 근거를 바꾸면 지표도 그 값을 읽는다."""
    answers = [provider(), provider(cite="other")]
    used = []

    def draft(prompt):
        used.append(prompt)
        return answers[len(used) - 1](prompt)

    row = verified(verifier("UNSUPPORTED", "SUPPORTED"), draft, {"k1": CASES["k1"]})
    assert row["final"]["grounding"]["grounded_case_rate"] == 1.0
    assert row["verification"]["after_rewrite"]["grounding"]["grounded_case_rate"] == 0.0


def test_unverifiable_is_counted_without_a_rewrite():
    row = verified(verifier("UNVERIFIABLE"), cases={"k1": CASES["k1"]})["verification"]
    assert row["calls"] == {"verification": 1, "rewrite": 0}
    assert row["status"]["final"] == {"NEEDS_REVIEW": 1}


def test_a_rewrite_without_evidence_sentences_counts_the_first_verdict():
    """graph reverify와 같은 뜻이다. 미검증이 아니라 첫 판정이 남는다."""
    base = provider()
    seen = []

    def drafter(prompt):
        seen.append(prompt)
        result = base(prompt)
        if len(seen) == 1:
            return result
        body = json.loads(result.text)
        for option in body["options"]:
            option["sentences"] = [
                {"text": "합성 조건 문장", "kind": "CONDITION", "evidence_chunk_ids": []}
            ]
        return replace(result, text=json.dumps(body))

    checker = verifier("UNSUPPORTED")
    row = verified(checker, drafter, {"k1": CASES["k1"]})["verification"]
    assert row["calls"] == {"verification": 1, "rewrite": 1}
    assert row["status"]["final"] == {"NEEDS_REVIEW": 1}
    assert row["verdicts"]["final"] == row["verdicts"]["first"]
    assert row["not_verified"] == {}
    assert row["reverify_skipped"] == {"CS_NO_EVIDENCE_SENTENCES": 1}


def test_still_failing_after_a_rewrite_stops_there():
    checker = verifier("UNSUPPORTED")
    row = verified(checker, cases={"k1": CASES["k1"]})["verification"]
    assert row["calls"] == {"verification": 2, "rewrite": 1}
    assert row["status"]["final"] == {"NEEDS_REVIEW": 1}


def test_a_failed_verification_is_recorded_without_a_rewrite_or_its_text():
    checker = verifier(failure=RuntimeError("private provider text"))
    row = verified(checker, cases={"k1": CASES["k1"]})
    block = row["verification"]
    assert block["calls"] == {"verification": 1, "rewrite": 0}
    assert block["status"]["final"] == {"NOT_VERIFIED": 1}
    assert list(block["not_verified"].values()) == [1]
    assert "private provider text" not in json.dumps(row, ensure_ascii=False)


def test_verification_results_carry_no_reason_text():
    row = verified(verifier("UNSUPPORTED"), cases={"k1": CASES["k1"]})
    assert "synthetic secret reason" not in json.dumps(row, ensure_ascii=False)


def test_a_draft_that_failed_is_not_sent_to_the_verifier():
    checker = verifier("SUPPORTED")

    def broken(prompt):
        return Generation("{}", Usage(1, 1), "fake-v1", ["STOP"])

    row = verified(checker, broken, {"k1": CASES["k1"]})["verification"]
    assert checker.calls == [] and row["not_verified"] == {"CS_DRAFT_MISSING": 1}


def test_rows_have_no_verification_block_unless_asked():
    assert "verification" not in run()["candidates"][0]


def test_output_limit_is_a_candidate_setting():
    """출력 상한은 후보 인자다. 검증 상한을 따로 주면 그 값, 없으면 초안 값이다."""
    spec = {"name": "c", "model": "m", "prompt_version": "v2", "max_output_tokens": 4096}
    assert cs_draft_run.knobs_of(spec).max_output_tokens == 4096
    built = []

    class Models:
        def generate_content(self, **kwargs):
            raise AssertionError

    client = SimpleNamespace(models=Models())

    def adapter(client_, config, *, model, response_schema, generation):
        built.append((response_schema, generation.max_output_tokens))
        return lambda text: None

    with (
        patch.object(cs_draft_run, "gemini_text_provider", adapter),
        patch.object(cs_draft_run, "create_client", lambda settings: client),
    ):
        factory = cs_draft_run.verification_factory(AISettings())
        factory(spec, AISettings())
        factory({**spec, "verification_max_output_tokens": 1024}, AISettings())
    assert built == [(VerificationOutput, 4096), (VerificationOutput, 1024)]


@pytest.mark.parametrize("limit", [0, -1, "1024", 1.5, True])
def test_a_bad_verification_limit_is_refused_when_loading_candidates(tmp_path, limit):
    """검증 상한도 파일을 읽을 때 걸러, 유료 호출 전에 드러난다."""
    path = tmp_path / "candidates.json"
    spec = {"name": "c", "model": "m", "prompt_version": "v2"}
    path.write_text(json.dumps([{**spec, "verification_max_output_tokens": limit}]))
    with pytest.raises(ValueError):
        cs_draft_run.load_specs(path)
    path.write_text(json.dumps([{**spec, "verification_max_output_tokens": 1024}]))
    assert cs_draft_run.load_specs(path)[0]["verification_max_output_tokens"] == 1024
