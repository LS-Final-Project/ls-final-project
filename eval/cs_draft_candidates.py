"""같은 C/S 사례 집합에 답변 초안 후보 여러 개를 돌려 한 표로 비교한다(KAN-142 2차).

KAN-105 cs_candidates와 같은 모양이다. 지표는 cs_draft_metrics.summarize_draft_first_attempts를 그대로 쓰고
새 계산식을 만들지 않는다. subset을 주면 그 사례만 재실행한다(시연의 "이 대상만 재실행"). 모델 호출·DB·파일
접근은 없고 공급자는 호출자가 주입한다. 합격 판정과 원시 응답·원문은 결과에 넣지 않는다.
"""

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Literal

from backend.engine.cs.classification import ClassificationOutput
from backend.engine.cs.drafting import EvidenceSnippet
from backend.engine.cs.drafting_call import DraftAttempt, run_draft_attempt
from backend.shared.ai import Generation, ModelPrice, estimate_cost
from eval.cs_draft_metrics import INVALID_OUTPUT, DraftGold, summarize_draft_first_attempts
from eval.cs_usage import TOKEN_FIELDS, sum_report

BLOCKED = "CS_BLOCKED_EVIDENCE"
SUBSET_NOTE = "부분 집합 결과는 전체 집계·합격선에 반영하지 않는다"


@dataclass(frozen=True)
class DraftCase:
    """사례 하나의 초안 입력. run_draft_attempt에 그대로 넘긴다."""

    record: Mapping[str, object]
    classification: ClassificationOutput
    required_info_codes: frozenset[str]
    already_asked_codes: frozenset[str]
    evidence: list[EvidenceSnippet]
    sensitive_values: Mapping[str, Literal["SERIAL", "PERSONAL"]]


@dataclass(frozen=True)
class DraftCandidate:
    """비교할 초안 후보. 프롬프트·모델 차이는 provider 안에 있고 하네스는 이름·판본만 기록한다."""

    name: str
    provider: Callable[[str], Generation]
    model: str
    prompt_version: str
    # 생성 조절 항목(thinking 예산·출력 상한 등). 기록만 하고 입력 hash 검사에는 넣지 않는다.
    knobs: Mapping[str, int | str] = field(default_factory=dict)


def _cost(attempts: list[DraftAttempt], model: str, price_table: dict[str, ModelPrice]) -> dict:
    """호출한 시도의 예상 비용을 합한다.

    Args:
        attempts: 호출이 시작된 시도.
        model: 단가표에서 찾을 모델 ID.
        price_table: 단가표.

    Returns:
        dict: sum_report 형식. 입력·답변 금액이 모두 있고 USD일 때만 확정 비용으로 센다(cs_candidates와 같은
            규칙). 금액은 반올림하지 않은 십진 문자열이다.
    """
    estimates = [estimate_cost(a.usage, model, price_table) for a in attempts]
    costs = [
        {
            "cost": c.total
            if c.input is not None and c.output is not None and c.currency == "USD"
            else None
        }
        for c in estimates
    ]
    return {
        key: format(value, "f") if key in {"known_sum", "total"} and value is not None else value
        for key, value in sum_report(costs, "cost").items()
    }


def _or_lowest(rate: float | None) -> float:
    """정렬용 비율. None은 0보다 낮게 본다.

    Args:
        rate: 비율 또는 None.

    Returns:
        float: rate, None이면 -1.0.
    """
    return -1.0 if rate is None else rate


def run_draft_candidates(
    cases: Mapping[str, DraftCase],
    *,
    gold: Mapping[str, DraftGold],
    candidates: list[DraftCandidate],
    price_table: dict[str, ModelPrice] | None = None,
    subset: frozenset[str] | None = None,
    strip_phone_annotation: bool = False,
    return_attempts: bool = False,
    invalid_output_retries: int = 1,
) -> dict:
    """후보마다 사례의 최초 초안 시도를 실행하고 기존 초안 지표로 비교표를 만든다.

    형식 위반(CS_INVALID_OUTPUT)인 사례만 invalid_output_retries만큼(0 또는 1) 같은 입력으로 attempt 2를 더
    호출한다. 금지 약속·차단 근거는 재호출하지 않는다(안전 지표를 가리지 않게). 두 시도는 모두 남고, 행의 기존
    키는 최초 시도 지표이며 final 블록이 최종 시도 지표다.

    Args:
        cases: 사례 ID별 초안 입력.
        gold: 사례 ID별 잠긴 정답. ID 집합이 cases와 같아야 한다.
        candidates: 이름이 고유한 후보 목록.
        price_table: load_price_table 결과. 없거나 모델 단가가 없으면 비용은 None이다.
        subset: 재실행할 사례 ID. None이면 전체이고, 있으면 cases 안의 비지 않은 집합이어야 한다.
        strip_phone_annotation: 분석용. True면 PHONE 상담원 표기를 지운 v1 초안 입력으로 돌린다(기본 False).
        return_attempts: True면 "attempts"(후보 이름 → {사례 ID: 최초 DraftAttempt}, 실행한 사례만)와
            "retries"(후보 이름 → {사례 ID: attempt 2})를 더한다. 시도에는 원시 응답·초안이 있으므로 호출자가 공개
            산출물에 그대로 쓰지 않는다.
        invalid_output_retries: 형식 위반 재호출 횟수(0 또는 1, 기본 1).

    Returns:
        dict: subset(재실행 대상 수·전체 수), note(부분 집합일 때), candidates(후보별 지표 행), ranking.
            ranking은 금지 약속 건수 오름차순 → 차단 근거(CS_BLOCKED_EVIDENCE) 건수 오름차순 → VALID 건수
            내림차순 → 근거 사례 비율 내림차순 → INFO_FIRST F1 내림차순 → 이름순이며 None 비율은 가장 낮게 본다.
            금지 약속은 평균으로 상쇄하지 않는 기준이라 첫 키이고, VALID 건수로 깨진 응답을 낸 후보를 뒤로 보낸다. 입력 검사 실패는
            EVAL_INVALID_INPUT, 후보 간 입력 hash가 다르면 CS_CANDIDATE_INPUT_MISMATCH ValueError다.
    """
    names = [c.name for c in candidates if isinstance(c, DraftCandidate)]
    if (
        not cases
        or not candidates
        or len(names) != len(candidates)
        or not all(names)
        or len(set(names)) != len(names)
        or set(gold) != set(cases)
        or not all(isinstance(case, DraftCase) for case in cases.values())
        or (subset is not None and (not subset or not subset <= set(cases)))
        or type(invalid_output_retries) is not int
        or invalid_output_retries not in (0, 1)
    ):
        raise ValueError("EVAL_INVALID_INPUT")
    selected = [case_id for case_id in cases if subset is None or case_id in subset]
    runs = {
        candidate.name: {
            case_id: run_draft_attempt(
                cases[case_id].record,
                classification=cases[case_id].classification,
                required_info_codes=cases[case_id].required_info_codes,
                already_asked_codes=cases[case_id].already_asked_codes,
                evidence=cases[case_id].evidence,
                sensitive_values=cases[case_id].sensitive_values,
                provider=candidate.provider,
                attempt_number=1,
                prompt_version=candidate.prompt_version,
                strip_phone_annotation=strip_phone_annotation,
            )
            for case_id in selected
        }
        for candidate in candidates
    }
    retries = {
        candidate.name: {
            case_id: run_draft_attempt(
                cases[case_id].record,
                classification=cases[case_id].classification,
                required_info_codes=cases[case_id].required_info_codes,
                already_asked_codes=cases[case_id].already_asked_codes,
                evidence=cases[case_id].evidence,
                sensitive_values=cases[case_id].sensitive_values,
                provider=candidate.provider,
                attempt_number=2,
                prompt_version=candidate.prompt_version,
                strip_phone_annotation=strip_phone_annotation,
            )
            for case_id, attempt in runs[candidate.name].items()
            if invalid_output_retries and attempt.error_code == INVALID_OUTPUT
        }
        for candidate in candidates
    }
    first = runs[names[0]]
    if any(
        attempts[case_id].input_sha256 != first[case_id].input_sha256
        for attempts in runs.values()
        for case_id in selected
    ) or any(
        attempt.input_sha256 != first[case_id].input_sha256
        for attempts in retries.values()
        for case_id, attempt in attempts.items()
    ):
        raise ValueError("CS_CANDIDATE_INPUT_MISMATCH")
    scoped = {case_id: gold[case_id] for case_id in selected}
    rows = []
    for candidate in candidates:
        attempts = runs[candidate.name]
        summary = summarize_draft_first_attempts(scoped, attempts)
        called = [a for a in attempts.values() if a.call_started]
        rows.append(
            {
                "name": candidate.name,
                "model": candidate.model,
                "prompt_version": candidate.prompt_version,
                "knobs": dict(candidate.knobs),
                **{
                    key: summary[key]
                    for key in ("schema", "forbidden", "grounding", "info_requests", "over_hold")
                },
                "blocked": sum(a.error_code == BLOCKED for a in attempts.values()),
                "usage": summary["usage"]["tokens"],
                "latency_ms": summary["usage"]["latency_ms"],
                "cost_usd": _cost(called, candidate.model, price_table or {}),
                "per_case": [
                    {key: row[key] for key in ("case_id", "outcome", "error_code", "grounded")}
                    for row in summary["per_case"]
                ],
                "final": _final(candidate, attempts, retries[candidate.name], scoped, price_table),
            }
        )
    ranking = sorted(
        rows,
        key=lambda row: (
            row["forbidden"]["count"],
            row["blocked"],
            -row["schema"]["outcomes"]["VALID"],
            -_or_lowest(row["grounding"]["grounded_case_rate"]),
            -_or_lowest(row["info_requests"]["INFO_FIRST"]["f1"]),
            row["name"],
        ),
    )
    partial = len(selected) < len(cases)
    return {
        "subset": {"rerun": len(selected), "total": len(cases)},
        **({"note": SUBSET_NOTE} if partial else {}),
        "candidates": rows,
        "ranking": [row["name"] for row in ranking],
        **({"attempts": runs, "retries": retries} if return_attempts else {}),
    }


def _final(
    candidate: DraftCandidate,
    firsts: Mapping[str, DraftAttempt],
    retries: Mapping[str, DraftAttempt],
    gold: Mapping[str, DraftGold],
    price_table: dict[str, ModelPrice] | None,
) -> dict:
    """최종 시도(재시도가 있으면 attempt 2, 없으면 최초) 지표와 모든 호출의 비용.

    Args:
        candidate: 후보.
        firsts: 사례별 최초 시도.
        retries: 형식 위반으로 다시 부른 사례별 attempt 2.
        gold: 실행한 사례의 정답.
        price_table: 단가표.

    Returns:
        dict: retried·recovered, 최종 시도 기준 schema·forbidden·grounding·info_requests, calls(호출 시작 수)와
            두 시도 모든 호출의 usage·cost_usd.
    """
    finals = {case_id: retries.get(case_id, attempt) for case_id, attempt in firsts.items()}
    summary = summarize_draft_first_attempts(gold, finals, final=True)
    called = [a for a in (*firsts.values(), *retries.values()) if a.call_started]
    usage = [asdict(a.usage) for a in called]
    return {
        "retried": len(retries),
        "recovered": sum(attempt.outcome == "VALID" for attempt in retries.values()),
        **{key: summary[key] for key in ("schema", "forbidden", "grounding", "info_requests")},
        "calls": len(called),
        "usage": {field: sum_report(usage, field) for field in TOKEN_FIELDS},
        "cost_usd": _cost(called, candidate.model, price_table or {}),
    }
