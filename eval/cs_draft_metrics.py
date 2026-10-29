"""C/S 답변 초안 최초 시도 채점과 AI 심판 교정(KAN-142). 파일·DB·모델에 접근하지 않는다.

스키마 위반·금지 약속·근거·정보 요청·과잉 보류를 따로 센다. drafting_call은 금지 약속을 outcome
SCHEMA_VIOLATION, error_code CS_FORBIDDEN_COMMITMENT로 남기므로 outcome 건수는 그대로 두고, 스키마 위반율은
CS_INVALID_OUTPUT만, 금지 약속은 error_code로 따로 센다. 합격 판정과 원문·원시 응답은 결과에 넣지 않는다.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal, cast, get_args

from backend.engine.cs.drafting import OptionLabel
from backend.engine.cs.drafting_call import DraftAttempt
from eval.cs_metrics import OUTCOMES
from eval.cs_usage import TOKEN_FIELDS, _percentile, _sum_report

OPTION_LABELS: tuple[str, ...] = get_args(OptionLabel)
FORBIDDEN = "CS_FORBIDDEN_COMMITMENT"
INVALID_OUTPUT = "CS_INVALID_OUTPUT"
Label = Literal["PASS", "FAIL", "HOLD"]
LABELS: tuple[str, ...] = get_args(Label)


@dataclass(frozen=True)
class DraftGold:
    """사례 하나의 잠긴 정답. 입력 근거·정답 근거·정답 요청 코드와 답할 수 있는지 여부."""

    input_chunk_ids: frozenset[str]
    relevant_chunk_ids: frozenset[str]
    request_codes: frozenset[str]
    answerable: bool
    # 정답이 금지 약속을 요구하는 사례는 없다. True면 입력 오류로 거부한다.
    forbidden_expected: bool = False


def _rate(part: int, whole: int) -> float | None:
    """비율. 분모가 0이면 None이다.

    Args:
        part: 분자.
        whole: 분모.

    Returns:
        float | None: part / whole.
    """
    return part / whole if whole else None


def _check(gold: Mapping[str, DraftGold], attempts: Mapping[str, DraftAttempt]) -> None:
    """정답·시도의 ID 일치, 최초 시도, 결과와 예측·오류 코드의 일관성을 검사한다.

    Args:
        gold: 사례별 정답.
        attempts: 사례별 최초 시도.

    Returns:
        None: 어긋나면 EVAL_INVALID_INPUT ValueError다.
    """
    if set(gold) != set(attempts) or any(
        not isinstance(item, DraftGold)
        or item.forbidden_expected
        or not item.relevant_chunk_ids <= item.input_chunk_ids
        for item in gold.values()
    ):
        raise ValueError("EVAL_INVALID_INPUT")
    for attempt in attempts.values():
        if (
            not isinstance(attempt, DraftAttempt)
            or attempt.attempt_number != 1
            or attempt.outcome not in OUTCOMES
            or (attempt.outcome == "VALID") != (attempt.prediction is not None)
            or (attempt.error_code == FORBIDDEN and attempt.outcome != "SCHEMA_VIOLATION")
        ):
            raise ValueError("EVAL_INVALID_INPUT")


def summarize_draft_first_attempts(
    gold: Mapping[str, DraftGold], attempts: Mapping[str, DraftAttempt]
) -> dict:
    """최초 초안 시도를 스키마·금지 약속·근거·정보 요청·과잉 보류·사용량으로 나눠 집계한다.

    Args:
        gold: 사례별 잠긴 정답.
        attempts: 사례별 최초 시도(attempt_number 1). ID 집합이 gold와 같아야 한다.

    Returns:
        dict: schema(outcome 건수·스키마 위반율), forbidden(건수·비율), grounding(문장·사례 기준 정답 근거
            비율, 정답 근거 밖·입력 밖 문장 수), info_requests(라벨별 micro P/R/F1·완전 일치율·정답 밖 요청 수),
            over_hold(답할 수 있는 사례의 전 문장 미확인·비정상 종료와 합계), unsupported_assertion(답할 수 없는
            사례의 근거 문장 사례 수), usage(토큰·지연), per_case. 비율 분모가 0이면 None이다.
            잘못된 입력은 EVAL_INVALID_INPUT ValueError다.
    """
    _check(gold, attempts)
    outcomes: Counter[str] = Counter(attempt.outcome for attempt in attempts.values())
    received = [a for a in attempts.values() if a.raw_response is not None]
    invalid = sum(a.error_code == INVALID_OUTPUT for a in received)
    forbidden = sum(a.error_code == FORBIDDEN for a in received)
    sentences = {"total": 0, "grounded": 0, "outside_relevant": 0, "outside_input": 0}
    grounded_cases = cases_with_evidence = unsupported = 0
    over = {"all_unconfirmed": 0, "not_valid": 0}
    answerable = 0
    requests = {label: {"tp": 0, "fp": 0, "fn": 0, "exact": 0} for label in OPTION_LABELS}
    valid = 0
    per_case: list[dict[str, Any]] = []
    for case_id, expected in gold.items():
        attempt = attempts[case_id]
        grounded: bool | None = None
        over_held = False
        if attempt.prediction is not None:
            valid += 1
            options = attempt.prediction.options
            evidence = [s for o in options for s in o.sentences if s.kind == "EVIDENCE"]
            ok = [set(s.evidence_chunk_ids) <= expected.relevant_chunk_ids for s in evidence]
            sentences["total"] += len(evidence)
            sentences["grounded"] += sum(ok)
            sentences["outside_relevant"] += len(ok) - sum(ok)
            sentences["outside_input"] += sum(
                not set(s.evidence_chunk_ids) <= expected.input_chunk_ids for s in evidence
            )
            if evidence:
                grounded = all(ok)
                cases_with_evidence += 1
                grounded_cases += grounded
                unsupported += not expected.answerable
            if expected.answerable and all(
                s.kind == "UNCONFIRMED" for o in options for s in o.sentences
            ):
                over["all_unconfirmed"] += 1
                over_held = True
            for option in options:
                asked = {r.info_code for r in option.info_requests}
                row = requests[option.label]
                row["tp"] += len(asked & expected.request_codes)
                row["fp"] += len(asked - expected.request_codes)
                row["fn"] += len(expected.request_codes - asked)
                row["exact"] += asked == expected.request_codes
        elif expected.answerable:
            over["not_valid"] += 1
            over_held = True
        answerable += expected.answerable
        per_case.append(
            {
                "case_id": case_id,
                "outcome": attempt.outcome,
                "error_code": attempt.error_code,
                "grounded": grounded,
                "over_held": over_held,
            }
        )
    called = [a for a in attempts.values() if a.call_started]
    usage = [asdict(a.usage) for a in called]
    latency = sorted(a.latency_ms for a in called if a.latency_ms is not None)
    held = over["all_unconfirmed"] + over["not_valid"]
    return {
        "version": "cs-draft-first-attempt-v1",
        "attempt_number": 1,
        "quality_gate_evaluated": False,
        "total": len(gold),
        "schema": {
            "outcomes": {outcome: outcomes[outcome] for outcome in sorted(OUTCOMES)},
            "received": len(received),
            "schema_violations": invalid,
            "schema_violation_rate": _rate(invalid, len(received)),
        },
        "forbidden": {"count": forbidden, "rate": _rate(forbidden, len(received))},
        "grounding": {
            "evidence_sentences": sentences["total"],
            "grounded_sentence_rate": _rate(sentences["grounded"], sentences["total"]),
            "cases_with_evidence": cases_with_evidence,
            "grounded_case_rate": _rate(grounded_cases, cases_with_evidence),
            "outside_relevant_sentences": sentences["outside_relevant"],
            "outside_input_sentences": sentences["outside_input"],
        },
        "info_requests": {
            label: {
                "precision": _rate(row["tp"], row["tp"] + row["fp"]),
                "recall": _rate(row["tp"], row["tp"] + row["fn"]),
                "f1": _rate(2 * row["tp"], 2 * row["tp"] + row["fp"] + row["fn"]),
                "exact_match_rate": _rate(row["exact"], valid),
                "outside_gold_requests": row["fp"],
            }
            for label, row in requests.items()
        },
        "over_hold": {
            "answerable": answerable,
            **over,
            "total": held,
            "rate": _rate(held, answerable),
        },
        "unsupported_assertion": unsupported,
        "usage": {
            "tokens": {field: _sum_report(usage, field) for field in TOKEN_FIELDS},
            "latency_ms": {
                "reported_count": len(latency),
                # _percentile은 정수 지연용으로 선언됐지만 계산은 실수에도 같다.
                "p50": _percentile(cast(list[int], latency), 0.5),
                "p95": _percentile(cast(list[int], latency), 0.95),
            },
        },
        "per_case": per_case,
    }


def reconcile_judgements(judge: Mapping[str, Label], human: Mapping[str, Label]) -> dict:
    """AI 심판 라벨을 사람 판정으로 교정한다. 최종 라벨은 사람 판정이 있으면 사람 값이다.

    Args:
        judge: 사례별 AI 심판 라벨.
        human: 사례별 사람 판정 라벨. 심판이 없는 사례도 받을 수 있다.

    Returns:
        dict: final(사례별 label·source(HUMAN/JUDGE)·unreviewed), 둘 다 있는 사례 수·일치율·혼동행렬
            (judge 행 × human 열)·사람이 바꾼 수, 사람 미검토 수. kappa 같은 통계는 내지 않는다.
            허용 밖 라벨은 EVAL_INVALID_INPUT ValueError다.
    """
    if any(label not in LABELS for label in (*judge.values(), *human.values())):
        raise ValueError("EVAL_INVALID_INPUT")
    both = [case for case in judge if case in human]
    matrix = {row: dict.fromkeys(LABELS, 0) for row in LABELS}
    for case in both:
        matrix[judge[case]][human[case]] += 1
    agreed = sum(judge[case] == human[case] for case in both)
    final = {
        case: (
            {"label": human[case], "source": "HUMAN", "unreviewed": False}
            if case in human
            else {"label": judge[case], "source": "JUDGE", "unreviewed": True}
        )
        for case in sorted({*judge, *human})
    }
    return {
        "version": "cs-judge-reconcile-v1",
        "final": final,
        "compared": len(both),
        "agreement_rate": _rate(agreed, len(both)),
        "confusion_matrix": matrix,
        "overridden": len(both) - agreed,
        "unreviewed": sum(item["unreviewed"] for item in final.values()),
    }
