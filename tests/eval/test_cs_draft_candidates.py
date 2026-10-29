"""KAN-142 2차 초안 후보 비교 하네스. 합성 사례와 fake 공급자만 쓰고 정답 파일·모델·DB에 접근하지 않는다."""

import json
from collections.abc import Iterator, Mapping

import pytest

from backend.engine.cs.classification import ClassificationOutput
from backend.engine.cs.drafting import EvidenceSnippet
from backend.shared.ai import Generation, Usage
from eval.cs_draft_candidates import (
    SUBSET_NOTE,
    DraftCandidate,
    DraftCase,
    run_draft_candidates,
)
from eval.cs_draft_metrics import DraftGold

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
    DraftCandidate("forbidden", provider(text="RMA가 승인되었습니다."), "fake-a", "p3"),
    DraftCandidate("ungrounded", provider(cite="other"), "fake-a", "p2"),
    DraftCandidate("grounded", provider(), "fake-a", "p1"),
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
    assert rows["grounded"]["model"] == "fake-a" and rows["grounded"]["prompt_version"] == "p1"
    assert result["subset"] == {"rerun": 5, "total": 5} and "note" not in result


def test_forbidden_then_blocked_rank_before_grounding():
    """금지 약속·차단 코드를 낸 후보는 근거 비율이 높아도 그 건수가 적은 후보 뒤에 선다.

    Args:
        없음.

    Returns:
        None: blocked 건수와 순위를 확인한다.
    """
    blocked = DraftCandidate(
        "blocked", provider(text="See FLT-042 notes.", only="1"), "fake-a", "p4"
    )
    forbid_one = DraftCandidate(
        "forbid-one", provider(text="RMA가 승인되었습니다.", only="2"), "fake-a", "p5"
    )
    result = run(candidates=[forbid_one, blocked, CANDIDATES[1]])
    rows = {row["name"]: row for row in result["candidates"]}
    assert rows["blocked"]["blocked"] == 1 and rows["forbid-one"]["forbidden"]["count"] == 1
    assert rows["blocked"]["grounding"]["grounded_case_rate"] == 1.0
    assert rows["forbid-one"]["grounding"]["grounded_case_rate"] == 1.0
    # 근거 비율이 높아도 금지 약속 → 차단 근거 순으로 먼저 뒤로 간다.
    assert result["ranking"] == ["ungrounded", "blocked", "forbid-one"]


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
        candidates=[DraftCandidate("grounded", counting, "fake-a", "p1")],
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
