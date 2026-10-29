"""KAN-142 C/S 초안 채점과 심판 교정. 합성 시도만 쓰고 정답 파일·모델·DB에 접근하지 않는다."""

from dataclasses import replace

import pytest

from backend.engine.cs.drafting import DraftOutput
from backend.engine.cs.drafting_call import DraftAttempt
from backend.shared.ai import Usage
from eval.cs_draft_metrics import DraftGold, reconcile_judgements, summarize_draft_first_attempts

INPUT = frozenset({"c1", "c2", "c3"})


def opt(label, *, kind="EVIDENCE", ids=("c1",), asks=()):
    """선택지 하나를 만든다.

    Args:
        label: 선택지 라벨.
        kind: 문장 종류.
        ids: 근거 chunk_id.
        asks: 요청 정보 코드.

    Returns:
        dict: 원시 선택지.
    """
    return {
        "label": label,
        "sentences": [
            {"text": "Synthetic sentence.", "kind": kind, "evidence_chunk_ids": list(ids)}
        ],
        "info_requests": [{"info_code": code, "question": "Please send it."} for code in asks],
    }


def valid(*options, latency=100.0):
    """VALID 시도를 만든다.

    Args:
        options: 세 선택지.
        latency: 지연(ms).

    Returns:
        DraftAttempt: 합성 시도.
    """
    return DraftAttempt(
        1,
        "VALID",
        call_started=True,
        latency_ms=latency,
        usage=Usage(10, 2),
        raw_response="RAW_SECRET valid",
        prediction=DraftOutput.model_validate({"options": list(options)}),
    )


def failed(code):
    """원시 응답을 받은 스키마 위반 시도를 만든다.

    Args:
        code: 오류 코드.

    Returns:
        DraftAttempt: 합성 시도.
    """
    return DraftAttempt(
        1,
        "SCHEMA_VIOLATION",
        call_started=True,
        latency_ms=300.0,
        usage=Usage(10, 2),
        error_code=code,
        raw_response="RAW_SECRET invalid",
    )


def gold(codes=(), *, answerable=True):
    """정답 하나를 만든다.

    Args:
        codes: 정답 요청 코드.
        answerable: 근거로 답할 수 있는지.

    Returns:
        DraftGold: 정답.
    """
    return DraftGold(INPUT, frozenset({"c1"}), frozenset(codes), answerable)


ATTEMPTS = {
    "v1": valid(
        *(opt(label, asks=("TEST_PHOTO",)) for label in ("CONCISE", "DETAILED", "INFO_FIRST"))
    ),
    "v2": valid(
        opt("CONCISE", ids=("c2",)),
        opt("DETAILED", asks=("TEST_PHOTO", "TEST_INVOICE")),
        opt("INFO_FIRST", asks=("TEST_PHOTO", "TEST_SERIAL")),
        latency=200.0,
    ),
    "v3": valid(
        *(opt(label, kind="UNCONFIRMED", ids=()) for label in ("CONCISE", "DETAILED", "INFO_FIRST"))
    ),
    "v4": valid(*(opt(label) for label in ("CONCISE", "DETAILED", "INFO_FIRST"))),
    "s1": failed("CS_INVALID_OUTPUT"),
    "f1": failed("CS_FORBIDDEN_COMMITMENT"),
    "h1": DraftAttempt(1, "HELD", error_code="CS_INVALID_INPUT"),
}
GOLD = {
    "v1": gold({"TEST_PHOTO"}),
    "v2": gold({"TEST_PHOTO", "TEST_SERIAL"}),
    "v3": gold(),
    "v4": gold(answerable=False),
    "s1": gold(),
    "f1": gold(),
    "h1": gold(),
}


def test_schema_and_forbidden_are_counted_apart():
    """스키마 위반율은 CS_INVALID_OUTPUT과 초안 검증 코드, 금지 약속은 따로 세고 분모는 원시 응답을 받은 건이다.

    Args:
        없음.

    Returns:
        None: 건수·비율을 확인한다.
    """
    result = summarize_draft_first_attempts(GOLD, ATTEMPTS)
    schema = result["schema"]
    assert schema["outcomes"]["VALID"] == 4 and schema["outcomes"]["SCHEMA_VIOLATION"] == 2
    assert schema["outcomes"]["HELD"] == 1
    assert schema["received"] == 6 and schema["schema_violations"] == 1
    assert schema["schema_violation_rate"] == pytest.approx(1 / 6)
    assert schema["error_codes"] == {"CS_INVALID_OUTPUT": 1}
    assert result["forbidden"] == {"count": 1, "rate": pytest.approx(1 / 6)}
    # 내용 규칙 위반 코드(KAN-194 6차)도 스키마 위반으로 세어 위반율이 같고, 코드별로 따로 보인다.
    kept = summarize_draft_first_attempts(
        GOLD, ATTEMPTS | {"s1": replace(ATTEMPTS["s1"], error_code="CS_UNKNOWN_REFERENCE")}
    )["schema"]
    assert (kept["schema_violations"], kept["error_codes"]) == (1, {"CS_UNKNOWN_REFERENCE": 1})


def test_grounding_over_hold_and_unsupported_are_separate():
    """근거 이탈·과잉 보류(전 문장 미확인·비정상 종료)·답할 수 없는 사례의 근거 문장을 따로 센다.

    Args:
        없음.

    Returns:
        None: 손계산 값을 확인한다.
    """
    result = summarize_draft_first_attempts(GOLD, ATTEMPTS)
    grounding = result["grounding"]
    assert grounding["evidence_sentences"] == 9
    assert grounding["grounded_sentence_rate"] == pytest.approx(8 / 9)
    assert grounding["cases_with_evidence"] == 3
    assert grounding["grounded_case_rate"] == pytest.approx(2 / 3)
    assert grounding["outside_relevant_sentences"] == 1
    assert grounding["outside_input_sentences"] == 0
    assert result["over_hold"] == {
        "answerable": 6,
        "all_unconfirmed": 1,
        "not_valid": 3,
        "total": 4,
        "rate": pytest.approx(4 / 6),
    }
    assert result["unsupported_assertion"] == 1
    cases = {row["case_id"]: row for row in result["per_case"]}
    assert cases["v2"]["grounded"] is False and cases["v3"]["over_held"]
    assert cases["v4"]["over_held"] is False and cases["h1"]["grounded"] is None


def test_outside_input_chunk_is_counted():
    """입력에 없던 근거 ID 문장도 0이 아니면 드러난다.

    Args:
        없음.

    Returns:
        None: 입력 밖 문장 수를 확인한다.
    """
    attempts = {"x": valid(opt("CONCISE", ids=("c9",)), opt("DETAILED"), opt("INFO_FIRST"))}
    grounding = summarize_draft_first_attempts({"x": gold()}, attempts)["grounding"]
    assert grounding["outside_input_sentences"] == 1


def test_info_requests_per_label_match_hand_calculation():
    """라벨별 요청 코드 micro P/R/F1·완전 일치율·정답 밖 요청 수.

    Args:
        없음.

    Returns:
        None: 손계산 값을 확인한다.
    """
    rows = summarize_draft_first_attempts(GOLD, ATTEMPTS)["info_requests"]
    assert rows["CONCISE"] == pytest.approx(
        {
            "precision": 1.0,
            "recall": 1 / 3,
            "f1": 0.5,
            "exact_match_rate": 0.75,
            "outside_gold_requests": 0,
        }
    )
    assert rows["DETAILED"] == pytest.approx(
        {
            "precision": 2 / 3,
            "recall": 2 / 3,
            "f1": 2 / 3,
            "exact_match_rate": 0.75,
            "outside_gold_requests": 1,
        }
    )
    assert rows["INFO_FIRST"]["f1"] == 1.0 and rows["INFO_FIRST"]["exact_match_rate"] == 1.0


def test_usage_and_no_raw_text():
    """호출한 시도의 토큰·지연을 세고 원시 응답은 결과에 넣지 않는다.

    Args:
        없음.

    Returns:
        None: 사용량과 원문 비포함을 확인한다.
    """
    result = summarize_draft_first_attempts(GOLD, ATTEMPTS)
    tokens = result["usage"]["tokens"]["input_tokens"]
    assert (tokens["reported_count"], tokens["total"]) == (6, 60)
    assert result["usage"]["latency_ms"]["reported_count"] == 6
    # 지연 100·100·100·200·300·300의 (n-1)·0.5 위치 보간.
    assert result["usage"]["latency_ms"]["p50"] == 150.0
    dumped = str(result)
    assert "RAW_SECRET" not in dumped and "Synthetic sentence" not in dumped


@pytest.mark.parametrize(
    "gold_map,attempts",
    [
        ({k: v for k, v in GOLD.items() if k != "h1"}, ATTEMPTS),
        (GOLD, ATTEMPTS | {"v1": replace(ATTEMPTS["v1"], attempt_number=2)}),
        (GOLD | {"v1": replace(GOLD["v1"], forbidden_expected=True)}, ATTEMPTS),
        (GOLD | {"v1": DraftGold(frozenset(), frozenset({"c1"}), frozenset(), True)}, ATTEMPTS),
        (
            GOLD,
            ATTEMPTS
            | {"h1": DraftAttempt(1, "TRANSPORT_FAILURE", error_code="CS_FORBIDDEN_COMMITMENT")},
        ),
        (GOLD, ATTEMPTS | {"h1": DraftAttempt(1, "VALID")}),
    ],
    ids=[
        "id-mismatch",
        "retry",
        "forbidden-expected",
        "relevant-outside-input",
        "forbidden-not-schema",
        "valid-no-prediction",
    ],
)
def test_invalid_input_is_rejected(gold_map, attempts):
    """ID 불일치·재시도·금지 기대·입력 밖 정답 근거·결과 불일치를 거부한다.

    Args:
        gold_map: 정답.
        attempts: 시도.

    Returns:
        None: EVAL_INVALID_INPUT을 확인한다.
    """
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        summarize_draft_first_attempts(gold_map, attempts)


def test_human_judgement_overrides_judge():
    """사람 판정이 있으면 최종 라벨이고, 없으면 심판 값에 미검토 표시를 단다.

    Args:
        없음.

    Returns:
        None: 최종 라벨·일치율·혼동행렬·교정 수를 확인한다.
    """
    result = reconcile_judgements(
        {"a": "PASS", "b": "FAIL", "c": "HOLD"}, {"b": "PASS", "c": "HOLD", "d": "FAIL"}
    )
    assert result["final"] == {
        "a": {"label": "PASS", "source": "JUDGE", "unreviewed": True},
        "b": {"label": "PASS", "source": "HUMAN", "unreviewed": False},
        "c": {"label": "HOLD", "source": "HUMAN", "unreviewed": False},
        "d": {"label": "FAIL", "source": "HUMAN", "unreviewed": False},
    }
    assert (result["compared"], result["overridden"], result["unreviewed"]) == (2, 1, 1)
    assert result["agreement_rate"] == 0.5
    assert result["confusion_matrix"]["FAIL"]["PASS"] == 1
    assert result["confusion_matrix"]["HOLD"]["HOLD"] == 1
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        reconcile_judgements({"a": "MAYBE"}, {})


@pytest.mark.parametrize(
    "attempt",
    [
        DraftAttempt(1, "SCHEMA_VIOLATION", error_code="CS_INVALID_OUTPUT"),
        DraftAttempt(1, "TRANSPORT_FAILURE", error_code="CS_INVALID_OUTPUT"),
        DraftAttempt(1, "TRANSPORT_FAILURE", error_code="CS_MISSING_INFO_REQUEST"),
    ],
    ids=["schema-without-raw", "invalid-output-on-transport", "rule-code-on-transport"],
)
def test_schema_violation_needs_raw_and_matching_outcome(attempt):
    """원시 응답 없는 스키마 위반과 다른 결과에 붙은 CS_INVALID_OUTPUT은 집계가 어긋나므로 거부한다.

    Args:
        attempt: 일관성이 깨진 시도.

    Returns:
        None: EVAL_INVALID_INPUT을 확인한다.
    """
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        summarize_draft_first_attempts(GOLD, ATTEMPTS | {"h1": attempt})


def test_usage_helpers_are_public_with_underscore_aliases():
    """cs_usage의 공개 이름과 기존 밑줄 별칭이 같은 함수이고 실수 지연도 받는다.

    Args:
        없음.

    Returns:
        None: 별칭과 계산을 확인한다.
    """
    from eval import cs_usage

    assert cs_usage._percentile is cs_usage.percentile
    assert cs_usage._sum_report is cs_usage.sum_report
    assert cs_usage.percentile([100.0, 200.5], 0.5) == pytest.approx(150.25)
