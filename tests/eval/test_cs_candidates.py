"""KAN-105 후보 비교 하네스. 합성 대상과 fake 공급자만 쓰고 정답 파일·모델·DB에 접근하지 않는다."""

import json
import re
from collections.abc import Iterator, Mapping
from datetime import date
from decimal import Decimal

import pytest

from backend.engine.cs.classification import ClassificationInput
from backend.shared.ai import Generation, ModelPrice, Usage
from eval.cs_candidates import Candidate, run_candidates

# (채널, 본문, 주분류, 긴급도). PHONE 두 건은 제거 대상 Urgency 주석을 담는다.
CASES = {
    "t1": ("EMAIL", "case-1 smoke from the panel", "DEFECT", "SAFETY"),
    "t2": ("PHONE", "Urgency: SITE_DOWN\ncase-2 plant stopped", "DEFECT", "SITE_DOWN"),
    "t3": ("EMAIL", "case-3 need the manual", "MANUAL", "NONE"),
    "t4": ("PHONE", "Urgency: NONE\ncase-4 price please", "QUOTE", "NONE"),
    "t5": ("CHAT", "case-5 line slowed down", "RETURN", "PROD_IMPACT"),
    "t6": ("WEB_FORM", "case-6 commissioning next week", "INSTALL", "COMMISSION"),
}
RECORDS = {
    target: {"channel": channel, "body_language": "en", "subject": None, "body": body}
    | {"category_code": "GOLD_SECRET"}
    for target, (channel, body, _, _) in CASES.items()
}
GOLD = {
    "category": {target: case[2] for target, case in CASES.items()},
    "urgency": {target: case[3] for target, case in CASES.items()},
}


def provider(*, urgency: Mapping[str, str] | None = None, broken=frozenset(), usage=None):
    """본문의 case 번호로 정답을 찾아 답하는 fake 공급자를 만든다.

    Args:
        urgency: 대상 ID별로 바꿔 답할 긴급도. 없는 대상은 정답을 답한다.
        broken: 스키마 위반 원문을 돌려줄 대상 ID.
        usage: 응답 사용량. 없으면 Usage(10, 2, None, 0, 12).

    Returns:
        Callable: ClassificationInput을 받아 Generation을 돌려주는 함수.
    """

    def call(payload: ClassificationInput) -> Generation:
        """fake 응답.

        Args:
            payload: 정제 입력.

        Returns:
            Generation: 합성 응답.
        """
        match = re.search(r"case-(\d)", payload.body)
        assert match is not None
        target = f"t{match.group(1)}"
        spent = usage or Usage(10, 2, None, 0, 12)
        if target in broken:
            return Generation("RAW_SECRET", spent, "fake-v1", ["STOP"], model="fake")
        body = {
            "category_code": GOLD["category"][target],
            "secondary_category": None,
            "urgency_flag": (urgency or {}).get(target, GOLD["urgency"][target]),
            "provided_info": [],
            "return_subtype": None,
        }
        return Generation(json.dumps(body), spent, "fake-v1", ["STOP"], model="fake")

    return call


CANDIDATES = [
    Candidate("all-none", provider(urgency=dict.fromkeys(CASES, "NONE")), "fake-a", "p-none"),
    Candidate("flaky", provider(broken=frozenset({"t2", "t5"}), usage=Usage(5)), "fake-b", "p2"),
    Candidate("oracle", provider(), "fake-a", "p1"),
]
PRICES = {
    "fake-a": ModelPrice(
        input_per_million=Decimal("1"),
        output_per_million=Decimal("2"),
        cached_input_per_million=Decimal("0.5"),
        currency="USD",
        as_of=date(2026, 10, 1),
    )
}


def run(records=None, candidates=None, gold=None):
    """합성 설정으로 하네스를 돌린다.

    Args:
        records: 대체 대상.
        candidates: 대체 후보.
        gold: 대체 정답.

    Returns:
        dict: 비교표.
    """
    return run_candidates(
        RECORDS if records is None else records,
        gold=GOLD if gold is None else gold,
        candidates=CANDIDATES if candidates is None else candidates,
        sensitive_values={},
        allowed_info_codes=frozenset(),
        price_table=PRICES,
    )


def test_candidates_are_ranked_by_critical_misses_then_f1():
    """CRITICAL 누락이 적은 후보가 앞서고, 지표는 기존 집계 값이다.

    Args:
        없음.

    Returns:
        None: 순위·누락·결과 건수·누출 점검을 확인한다.
    """
    result = run()
    rows = {row["name"]: row for row in result["candidates"]}
    assert result["targets"] == 6
    assert result["ranking"] == ["oracle", "flaky", "all-none"]
    assert [rows[name]["urgency"]["critical"]["missed"] for name in result["ranking"]] == [0, 1, 2]
    assert rows["oracle"]["category"] == {"accuracy": 1.0, "macro_f1": 0.5}
    assert rows["all-none"]["urgency"]["non_none_recall"] == 0.0
    assert rows["flaky"]["outcomes"] == {
        "HELD": 0,
        "SCHEMA_VIOLATION": 2,
        "TIMEOUT": 0,
        "TRANSPORT_FAILURE": 0,
        "VALID": 4,
    }
    assert rows["oracle"]["model"] == "fake-a" and rows["oracle"]["prompt_version"] == "p1"
    leakage = rows["oracle"]["leakage"]
    assert leakage["phone_targets"] == 2 and leakage["removed_annotations_total"] == 2
    assert leakage["phone_unsupported_annotation"] == 0
    assert set(leakage["urgency_macro_f1_by_phone"]) == {"PHONE", "NON_PHONE"}
    assert "passed" not in json.dumps(result)


def test_usage_sums_keep_unreported_tokens_apart():
    """미보고 토큰은 0으로 바꾸지 않고 보고 수와 함께 둔다. 비용은 단가가 있는 모델만 합한다.

    Args:
        없음.

    Returns:
        None: 토큰 합·지연·비용 구분을 확인한다.
    """
    rows = {row["name"]: row for row in run()["candidates"]}
    oracle, flaky = rows["oracle"]["usage"], rows["flaky"]["usage"]
    assert oracle["input_tokens"] == {
        "reported_count": 6,
        "missing_count": 0,
        "known_sum": 60,
        "total": 60,
    }
    assert oracle["thought_tokens"]["reported_count"] == 0
    assert oracle["thought_tokens"]["known_sum"] is None
    assert flaky["input_tokens"]["total"] == 30
    assert flaky["output_tokens"] == {
        "reported_count": 0,
        "missing_count": 6,
        "known_sum": None,
        "total": None,
    }
    assert rows["oracle"]["latency_ms"]["reported_count"] == 6
    assert rows["oracle"]["latency_ms"]["p50"] is not None
    # 입력 10·답변 2 토큰 × 6건: (10×1 + 2×2)/100만 × 6.
    assert rows["oracle"]["cost_usd"]["total"] == "0.000084"
    assert rows["flaky"]["cost_usd"]["total"] is None  # fake-b 단가 없음


def test_result_has_no_raw_response_or_body():
    """원시 응답·본문·정답 칸 원문을 결과에 넣지 않는다.

    Args:
        없음.

    Returns:
        None: 직렬화 결과에 원문이 없음을 확인한다.
    """
    dumped = json.dumps(run())
    for secret in ("RAW_SECRET", "case-1", "smoke from the panel", "GOLD_SECRET"):
        assert secret not in dumped


def test_unsupported_phone_annotation_is_counted_without_call():
    """제거 형식이 아닌 PHONE 주석은 호출 없이 보류되고 누출 점검 건수로 남는다.

    Args:
        없음.

    Returns:
        None: HELD·건수와 후보 간 입력 hash 일치(None)를 확인한다.
    """
    records = RECORDS | {"t4": RECORDS["t4"] | {"body": "Urgency: HIGH\ncase-4 price please"}}
    row = run(records)["candidates"][2]
    assert row["outcomes"]["HELD"] == 1
    assert row["leakage"]["phone_unsupported_annotation"] == 1
    assert row["leakage"]["removed_annotations_total"] == 1
    # 호출하지 않은 보류 시도는 사용량 미보고로 세지 않는다.
    assert row["usage"]["input_tokens"]["missing_count"] == 0
    assert row["usage"]["input_tokens"]["reported_count"] == 5


def test_critical_misses_rank_before_higher_f1():
    """macro-F1이 높아도 CRITICAL을 놓친 후보는 뒤로 간다.

    Args:
        없음.

    Returns:
        None: 정렬 첫 기준이 CRITICAL 누락임을 확인한다.
    """
    misses_safety = provider(urgency={"t1": "NONE"})
    critical_only = provider(urgency={t: "DEADLINE" for t in ("t3", "t4", "t5", "t6")})
    result = run(
        candidates=[
            Candidate("misses-safety", misses_safety, "fake-a", "p1"),
            Candidate("critical-only", critical_only, "fake-a", "p1"),
        ]
    )
    rows = {row["name"]: row for row in result["candidates"]}
    assert (
        rows["misses-safety"]["urgency"]["macro_f1"] > rows["critical-only"]["urgency"]["macro_f1"]
    )
    assert result["ranking"] == ["critical-only", "misses-safety"]


class ShiftingRecord(Mapping):
    """읽을 때마다 본문이 바뀌는 대상. 후보 간 입력 불일치를 재현한다."""

    def __init__(self) -> None:
        """읽은 횟수를 0으로 둔다.

        Returns:
            None: 초기화.
        """
        self.reads = 0

    def __getitem__(self, key: str) -> object:
        """본문을 읽을 때마다 다른 값을 준다.

        Args:
            key: 필드 이름.

        Returns:
            object: 필드 값.
        """
        if key == "body":
            self.reads += 1
            return f"case-1 read {self.reads}"
        return RECORDS["t1"][key]

    def __iter__(self) -> Iterator[str]:
        """필드 이름을 돌려준다.

        Returns:
            Iterator[str]: 필드 이름.
        """
        return iter(RECORDS["t1"])

    def __len__(self) -> int:
        """필드 수.

        Returns:
            int: 필드 수.
        """
        return len(RECORDS["t1"])


def test_input_hash_mismatch_between_candidates_is_rejected():
    """같은 대상인데 후보마다 정제 입력이 다르면 비교하지 않는다.

    Args:
        없음.

    Returns:
        None: CS_CANDIDATE_INPUT_MISMATCH를 확인한다.
    """
    with pytest.raises(ValueError, match="^CS_CANDIDATE_INPUT_MISMATCH$"):
        run(RECORDS | {"t1": ShiftingRecord()})


@pytest.mark.parametrize(
    "kwargs",
    [
        {"candidates": []},
        {"candidates": [CANDIDATES[0], CANDIDATES[0]]},
        {"records": {}},
        {"gold": {"category": GOLD["category"]}},
        {"gold": GOLD | {"urgency": {"t1": "SAFETY"}}},
    ],
    ids=["no-candidates", "duplicate-name", "no-targets", "missing-urgency", "id-mismatch"],
)
def test_invalid_candidate_setup_is_rejected(kwargs):
    """빈 후보·이름 중복·빈 대상·정답 ID 불일치는 실행 전에 거부한다.

    Args:
        kwargs: 잘못된 합성 설정.

    Returns:
        None: CS_INVALID_CANDIDATES를 확인한다.
    """
    with pytest.raises(ValueError, match="^CS_INVALID_CANDIDATES$"):
        run(**kwargs)
