"""잠긴 C/S 대상 하나에 분류 후보 여러 개를 돌려 한 표로 비교한다(KAN-105).

파일·DB에 접근하지 않고 공급자는 호출자가 주입한다. 지표는 기존 집계(cs_metrics·cs_strata·
cs_usage)를 그대로 쓰며 새 계산식을 만들지 않는다. 합격 판정과 원시 응답·원문은 결과에 넣지 않는다.
"""

import statistics
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Literal

from backend.engine.cs.classification import ClassificationInput
from backend.engine.cs.classification_call import ClassificationAttempt, run_classification_attempt
from backend.shared.ai import Generation, ModelPrice, estimate_cost
from eval.cs_metrics import (
    OUTCOMES,
    summarize_category_first_attempts,
    summarize_final_attempts,
    summarize_urgency_first_attempts,
)
from eval.cs_strata import CHANNELS, summarize_first_attempts_by_group
from eval.cs_usage import TOKEN_FIELDS, percentile, sum_report


@dataclass(frozen=True)
class Candidate:
    """비교할 후보. 프롬프트 차이는 provider 안에 있고 하네스는 이름·판본만 기록한다."""

    name: str
    provider: Callable[[ClassificationInput], Generation]
    model: str
    prompt_version: str
    # 생성 조절 항목(thinking 예산·출력 상한 등). 기록만 하고 입력 hash 검사에는 넣지 않는다.
    knobs: Mapping[str, int | str] = field(default_factory=dict)


def run_candidates(
    records: Mapping[str, Mapping[str, object]],
    *,
    gold: Mapping[str, Mapping[str, str]],
    candidates: list[Candidate],
    sensitive_values: Mapping[str, Literal["SERIAL", "PERSONAL"]],
    allowed_info_codes: frozenset[str],
    price_table: dict[str, ModelPrice] | None = None,
    return_attempts: bool = False,
    strip_phone_annotation: bool = False,
    invalid_output_retries: int = 1,
    on_case: Callable[[str, str, ClassificationAttempt, ClassificationAttempt | None], None]
    | None = None,
    prior: Mapping[str, Mapping[str, tuple[ClassificationAttempt, ClassificationAttempt | None]]]
    | None = None,
) -> dict:
    """후보마다 모든 대상의 최초 시도를 실행하고 기존 지표로 비교표를 만든다.

    형식 위반(CS_INVALID_OUTPUT)인 대상은 invalid_output_retries만큼(0 또는 1) 같은 입력으로 attempt 2를 더
    호출한다. 두 시도는 모두 남고, 행의 기존 키는 최초 시도 지표이며 final 블록이 최종 시도 지표다.

    Args:
        records: 대상 ID별 정규화 문의. 정답 칸이 있어도 build_input이 고르지 않는다.
        gold: {"category": {ID: 코드}, "urgency": {ID: 코드}}. 잠긴 정답이며 ID 집합이 records와 같아야 한다.
        candidates: 이름이 고유한 후보 목록.
        sensitive_values: 어댑터가 식별한 민감값과 마스킹 종류(모든 대상 공통).
        allowed_info_codes: 검수된 정보 코드 집합.
        price_table: load_price_table 결과. 없거나 모델 단가가 없으면 비용은 None이다.
        return_attempts: True면 "attempts"(후보 이름 → {대상 ID: 최초 ClassificationAttempt})와 "retries"(후보
            이름 → {대상 ID: attempt 2})를 더한다. 시도에는 raw_response가 들어 있으므로 호출자는 이 키를 꺼낸
            뒤 결과를 저장한다.
        strip_phone_annotation: 분석용. True면 PHONE 상담원 표기를 지운 v3 입력으로 돌린다(기본 False).
        invalid_output_retries: 형식 위반 재호출 횟수(0 또는 1, 기본 1).
        on_case: 사례 하나(최초와 선택 시도 2)가 끝날 때마다 (후보, 대상, 최초, 시도 2 | None)로 부른다. 진행
            기록용이며 prior로 넣은 사례에는 부르지 않는다.
        prior: 이미 끝난 사례 {후보: {대상: (최초, 시도 2 | None)}}. 호출하지 않고 그대로 집계에 넣는다(재개).
            모르는 후보·대상이 있으면 CS_INVALID_CANDIDATES다.

    Returns:
        dict: {"targets": 대상 수, "candidates": 후보별 행, "ranking": 이름 목록}. 순위는 최초 시도 지표로 정한다. ranking은 긴급도
            CRITICAL 누락 오름차순 → 긴급도 macro-F1 내림차순 → 주분류 macro-F1 내림차순(동률은 이름순)이다.
            입력 검사 실패는 CS_INVALID_CANDIDATES, 후보 간 입력 hash가 다르면
            CS_CANDIDATE_INPUT_MISMATCH ValueError다.
    """
    names = [candidate.name for candidate in candidates]
    if (
        not records
        or not candidates
        or not all(isinstance(c, Candidate) and c.name for c in candidates)
        or len(set(names)) != len(names)
        or set(gold) != {"category", "urgency"}
        or not set(gold["category"]) == set(gold["urgency"]) == set(records)
        or type(invalid_output_retries) is not int
        or invalid_output_retries not in (0, 1)
    ):
        raise ValueError("CS_INVALID_CANDIDATES")
    # 채널만 층화에 넘긴다. 형식이 틀린 채널은 HELD로 남고 층화에서는 UNKNOWN이다.
    metadata = {
        target: {"channel": channel if channel in CHANNELS else None}
        for target, record in records.items()
        for channel in [record.get("channel")]
    }
    done = prior or {}
    if not set(done) <= set(names) or any(
        not set(cases) <= set(records) for cases in done.values()
    ):
        raise ValueError("CS_INVALID_CANDIDATES")
    runs: dict[str, dict[str, ClassificationAttempt]] = {name: {} for name in names}
    retries: dict[str, dict[str, ClassificationAttempt]] = {name: {} for name in names}
    for candidate in candidates:

        def attempt(target: str, number: int) -> ClassificationAttempt:
            return run_classification_attempt(
                records[target],
                sensitive_values=sensitive_values,
                allowed_info_codes=allowed_info_codes,
                provider=candidate.provider,
                attempt_number=number,
                strip_phone_annotation=strip_phone_annotation,
            )

        for target in records:
            if target in done.get(candidate.name, {}):
                initial, retry = done[candidate.name][target]
            else:
                # 형식 위반은 그 사례의 최초 시도 직후 시도 2로 다시 부른다(사례 단위로 끝내야 진행 기록이 맞다).
                initial = attempt(target, 1)
                retry = (
                    attempt(target, 2)
                    if invalid_output_retries and initial.error_code == "CS_INVALID_OUTPUT"
                    else None
                )
                if on_case is not None:
                    on_case(candidate.name, target, initial, retry)
            runs[candidate.name][target] = initial
            if retry is not None:
                retries[candidate.name][target] = retry
    first = runs[names[0]]
    if any(
        attempts[target].input_sha256 != first[target].input_sha256
        for attempts in runs.values()
        for target in records
    ) or any(
        attempt.input_sha256 != first[target].input_sha256
        for attempts in retries.values()
        for target, attempt in attempts.items()
    ):
        raise ValueError("CS_CANDIDATE_INPUT_MISMATCH")
    rows = [
        _row(candidate, runs[candidate.name], gold, metadata, price_table or {})
        | {
            "final": _final(
                candidate, runs[candidate.name], retries[candidate.name], gold, price_table or {}
            )
        }
        for candidate in candidates
    ]
    ranking = sorted(
        rows,
        key=lambda row: (
            row["urgency"]["critical"]["missed"],
            -row["urgency"]["macro_f1"],
            -row["category"]["macro_f1"],
            row["name"],
        ),
    )
    return {
        "targets": len(records),
        "candidates": rows,
        "ranking": [row["name"] for row in ranking],
        **({"attempts": runs, "retries": retries} if return_attempts else {}),
    }


def _final(
    candidate: Candidate,
    firsts: Mapping[str, ClassificationAttempt],
    retries: Mapping[str, ClassificationAttempt],
    gold: Mapping[str, Mapping[str, str]],
    price_table: dict[str, ModelPrice],
) -> dict:
    """최종 시도(재시도가 있으면 attempt 2, 없으면 최초) 지표와 모든 호출의 사용량·비용.

    Args:
        candidate: 후보.
        firsts: 대상별 최초 시도.
        retries: 형식 위반으로 다시 부른 대상별 attempt 2.
        gold: 주분류·긴급도 정답.
        price_table: 단가표.

    Returns:
        dict: retried(재호출 수)·recovered(재호출이 VALID인 수)·category·urgency·outcomes(최종 시도 기준)·
            calls(호출 시작 수)·usage·cost_usd(두 시도 모든 호출 합).
    """
    finals = {target: retries.get(target, attempt) for target, attempt in firsts.items()}
    category = summarize_final_attempts(gold["category"], finals, metric="category")
    urgency = summarize_final_attempts(gold["urgency"], finals, metric="urgency")
    called = [a for a in (*firsts.values(), *retries.values()) if a.call_started]
    outcomes: Counter[str] = Counter(attempt.outcome for attempt in finals.values())
    usage = [asdict(attempt.usage) for attempt in called]
    return {
        "retried": len(retries),
        "recovered": sum(attempt.outcome == "VALID" for attempt in retries.values()),
        "category": {"accuracy": category["accuracy"], "macro_f1": category["macro_f1"]},
        "urgency": {
            "macro_f1": urgency["macro_f1"],
            "non_none_recall": urgency["non_none"]["recall"],
            "critical": urgency["critical"],
        },
        "outcomes": {outcome: outcomes[outcome] for outcome in sorted(OUTCOMES)},
        "calls": len(called),
        "usage": {field: sum_report(usage, field) for field in TOKEN_FIELDS},
        "cost_usd": _cost(called, candidate.model, price_table),
    }


def _cost(
    called: list[ClassificationAttempt], model: str, price_table: dict[str, ModelPrice]
) -> dict:
    """호출한 시도들의 비용 합계(십진 문자열).

    Args:
        called: 호출을 시작한 시도.
        model: 단가표 모델 ID.
        price_table: 단가표.

    Returns:
        dict: sum_report 형식. 저장 경로(smoke_worker.attempt_cost)와 같이 입력·답변 금액이 모두 있고 USD일
            때만 확정 비용으로 센다.
    """
    estimates = [estimate_cost(a.usage, model, price_table) for a in called]
    costs = [
        {
            "cost": c.total
            if c.input is not None and c.output is not None and c.currency == "USD"
            else None
        }
        for c in estimates
    ]
    cost = sum_report(costs, "cost")
    # Decimal을 반올림하지 않고 cs_usage처럼 십진 문자열로 둔다.
    return {
        key: format(value, "f") if key in {"known_sum", "total"} and value is not None else value
        for key, value in cost.items()
    }


def _row(
    candidate: Candidate,
    attempts: dict[str, ClassificationAttempt],
    gold: Mapping[str, Mapping[str, str]],
    metadata: Mapping[str, Mapping[str, str | None]],
    price_table: dict[str, ModelPrice],
) -> dict:
    """후보 하나의 결과 행을 기존 집계로 만든다.

    Args:
        candidate: 후보.
        attempts: 대상별 최초 시도.
        gold: 주분류·긴급도 정답.
        metadata: 대상별 채널(층화용).
        price_table: 단가표.

    Returns:
        dict: 지표·결과 건수·누출 점검·사용량·지연·비용. 원시 응답은 넣지 않는다.
    """
    category = summarize_category_first_attempts(gold["category"], attempts)
    urgency = summarize_urgency_first_attempts(gold["urgency"], attempts)
    phone = summarize_first_attempts_by_group(
        gold["urgency"], attempts, metadata, metric="urgency"
    )["groups"]["phone"]
    called = [attempt for attempt in attempts.values() if attempt.call_started]
    usage = [asdict(attempt.usage) for attempt in called]
    latency = sorted(a.latency_ms for a in called if a.latency_ms is not None)
    outcomes: Counter[str] = Counter(attempt.outcome for attempt in attempts.values())
    return {
        "name": candidate.name,
        "model": candidate.model,
        "prompt_version": candidate.prompt_version,
        "knobs": dict(candidate.knobs),
        "category": {"accuracy": category["accuracy"], "macro_f1": category["macro_f1"]},
        "urgency": {
            "macro_f1": urgency["macro_f1"],
            "non_none_recall": urgency["non_none"]["recall"],
            "critical": urgency["critical"],
        },
        "outcomes": {outcome: outcomes[outcome] for outcome in sorted(OUTCOMES)},
        "leakage": {
            "phone_targets": sum(item["channel"] == "PHONE" for item in metadata.values()),
            "removed_annotations_total": sum(a.removed_annotations or 0 for a in attempts.values()),
            "phone_unsupported_annotation": sum(
                a.error_code == "CS_UNSUPPORTED_PHONE_ANNOTATION" for a in attempts.values()
            ),
            "urgency_macro_f1_by_phone": {
                key: phone[key]["macro_f1"] for key in ("PHONE", "NON_PHONE") if key in phone
            },
        },
        "usage": {field: sum_report(usage, field) for field in TOKEN_FIELDS},
        "latency_ms": {
            "reported_count": len(latency),
            "mean": statistics.fmean(latency) if latency else None,
            "p50": percentile(latency, 0.5),
            "p95": percentile(latency, 0.95),
        },
        "cost_usd": _cost(called, candidate.model, price_table),
    }
