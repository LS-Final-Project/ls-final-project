"""같은 C/S 사례 집합에 답변 초안 후보 여러 개를 돌려 한 표로 비교한다(KAN-142 2차).

KAN-105 cs_candidates와 같은 모양이다. 지표는 cs_draft_metrics.summarize_draft_first_attempts를 그대로 쓰고
새 계산식을 만들지 않는다. subset을 주면 그 사례만 재실행한다(시연의 "이 대상만 재실행"). 모델 호출·DB·파일
접근은 없고 공급자는 호출자가 주입한다. 합격 판정과 원시 응답·원문은 결과에 넣지 않는다.
"""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Literal

from backend.engine.cs.classification import ClassificationOutput
from backend.engine.cs.drafting import EvidenceSnippet, RevisionFeedback, RevisionSentence
from backend.engine.cs.drafting_call import DraftAttempt, run_draft_attempt
from backend.engine.cs.verification import (
    NO_EVIDENCE_SENTENCES,
    build_verification_input,
    case_status,
    rewrite_targets,
)
from backend.engine.cs.verification_call import VerificationAttempt, run_verification_attempt
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
    # 있으면 초안마다 운영 그래프(KAN-200)와 같은 검증·재작성·재검증을 돈다.
    verification_provider: Callable[[str], Generation] | None = None


@dataclass(frozen=True)
class VerificationRun:
    """사례 하나의 검증 흐름. 호출하지 않은 단계는 None이고 skipped가 그 이유다."""

    first: VerificationAttempt | None = None
    rewrite: DraftAttempt | None = None
    second: VerificationAttempt | None = None
    skipped: str | None = None


def _cost(
    attempts: Sequence[DraftAttempt | VerificationAttempt],
    model: str,
    price_table: dict[str, ModelPrice],
) -> dict:
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


def _verify_case(
    case: DraftCase, drafted: DraftAttempt, candidate: DraftCandidate, strip: bool
) -> VerificationRun:
    """초안 하나를 검증하고, 실패 문장이 있으면 한 번 재작성해 다시 검증한다(graph와 같은 순서).

    Args:
        case: 사례.
        drafted: 최종 초안 시도.
        candidate: 후보.
        strip: PHONE 표기 제거 여부.

    Returns:
        VerificationRun: 단계별 시도.
    """
    verifier = candidate.verification_provider
    if drafted.prediction is None or verifier is None:
        return VerificationRun(skipped="CS_DRAFT_MISSING")
    try:
        payload = build_verification_input(drafted.prediction, case.evidence)
    except ValueError as error:
        return VerificationRun(skipped=_input_problem(error))
    first = run_verification_attempt(payload, provider=verifier, attempt_number=1)
    failed = [
        RevisionSentence(label=s.label, sentence_index=s.sentence_index, reason=s.reason)
        for s in (rewrite_targets(first.prediction) if first.prediction else [])
    ]
    if not failed:
        return VerificationRun(first=first)
    rewrite = run_draft_attempt(
        case.record,
        classification=case.classification,
        required_info_codes=case.required_info_codes,
        already_asked_codes=case.already_asked_codes,
        evidence=case.evidence,
        sensitive_values=case.sensitive_values,
        provider=candidate.provider,
        attempt_number=1,
        prompt_version=candidate.prompt_version,
        strip_phone_annotation=strip,
        revision_feedback=RevisionFeedback(prior_draft=drafted.prediction, failed_sentences=failed),
    )
    if rewrite.prediction is None:
        return VerificationRun(first=first, rewrite=rewrite)
    try:
        again = build_verification_input(rewrite.prediction, case.evidence)
    except ValueError as error:
        return VerificationRun(first=first, rewrite=rewrite, skipped=_input_problem(error))
    second = run_verification_attempt(again, provider=verifier, attempt_number=2)
    return VerificationRun(first=first, rewrite=rewrite, second=second)


def _input_problem(error: ValueError) -> str:
    """검증 입력을 못 만든 사유(graph verify와 같은 이름).

    Args:
        error: build_verification_input의 오류.

    Returns:
        str: 고정 사유 코드.
    """
    nothing = error.args == (NO_EVIDENCE_SENTENCES,)
    return NO_EVIDENCE_SENTENCES if nothing else "CS_VERIFICATION_INPUT_INVALID"


def _status(attempt: VerificationAttempt | None) -> str:
    """검증 시도 하나의 결과(graph verify와 같은 이름).

    Args:
        attempt: 검증 시도.

    Returns:
        str: VERIFIED·NEEDS_REVIEW·NOT_VERIFIED.
    """
    if attempt is None or attempt.prediction is None:
        return "NOT_VERIFIED"
    return case_status(attempt.prediction)


def _verification_row(
    candidate: DraftCandidate,
    runs: Mapping[str, VerificationRun],
    drafted: Mapping[str, DraftAttempt],
    gold: Mapping[str, DraftGold],
    price_table: dict[str, ModelPrice] | None,
) -> dict:
    """검증 흐름을 호출·비용·판정 분포로 기록한다. 합격 판정은 하지 않는다(합격선은 사람이 정한다).

    문장 이유·초안 본문은 넣지 않는다.

    Args:
        candidate: 후보.
        runs: 사례별 검증 흐름.
        drafted: 사례별 검증에 넘긴 초안 시도.
        gold: 실행한 사례의 기대값.
        price_table: 단가표.

    Returns:
        dict: status·verdicts(처음·최종)·not_verified·rewrite·calls·usage·cost_usd·latency_ms·
            after_rewrite(재작성을 반영한 초안 지표)·per_case.
    """
    checks = [a for r in runs.values() for a in (r.first, r.second) if a and a.call_started]
    rewrites = [r.rewrite for r in runs.values() if r.rewrite and r.rewrite.call_started]
    called: list[DraftAttempt | VerificationAttempt] = [*checks, *rewrites]
    lasts = {case_id: r.second or r.first for case_id, r in runs.items()}

    def verdicts(attempts: list[VerificationAttempt | None]) -> dict[str, int]:
        return dict(
            Counter(
                s.verdict for a in attempts if a and a.prediction for s in a.prediction.sentences
            )
        )

    finals = {
        case_id: r.rewrite if r.rewrite and r.rewrite.prediction else drafted[case_id]
        for case_id, r in runs.items()
    }
    summary = summarize_draft_first_attempts(gold, finals, final=True)
    usage = [asdict(a.usage) for a in called]
    return {
        "status": {
            "first": dict(Counter(_status(r.first) for r in runs.values())),
            "final": dict(Counter(_status(a) for a in lasts.values())),
        },
        "verdicts": {
            "first": verdicts([r.first for r in runs.values()]),
            "final": verdicts(list(lasts.values())),
        },
        "not_verified": dict(
            Counter(
                r.skipped or (a.error_code or a.outcome if a else "CS_DRAFT_MISSING")
                for r, a in ((r, r.second or r.first) for r in runs.values())
                if a is None or a.prediction is None
            )
        ),
        # 재작성본을 검증하지 못한 사례. 상태와 판정은 첫 검증 값이다(graph reverify와 같다).
        "reverify_skipped": dict(
            Counter(r.skipped for r in runs.values() if r.skipped and r.first)
        ),
        "rewrite": {
            "called": len(rewrites),
            "valid": sum(a.prediction is not None for a in rewrites),
            "errors": dict(
                Counter(a.error_code or a.outcome for a in rewrites if not a.prediction)
            ),
        },
        "calls": {"verification": len(checks), "rewrite": len(rewrites)},
        "usage": {name: sum_report(usage, name) for name in TOKEN_FIELDS},
        "cost_usd": _cost(called, candidate.model, price_table or {}),
        "latency_ms": {
            "verification": [a.latency_ms for a in checks],
            "rewrite": [a.latency_ms for a in rewrites],
        },
        "after_rewrite": {
            key: summary[key] for key in ("schema", "forbidden", "grounding", "info_requests")
        },
        "per_case": [
            {
                "case_id": case_id,
                "first": _status(r.first),
                "rewrite": None if r.rewrite is None else r.rewrite.error_code or r.rewrite.outcome,
                "final": _status(lasts[case_id]),
            }
            for case_id, r in runs.items()
        ],
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
    on_case: Callable[[str, str, DraftAttempt, DraftAttempt | None], None] | None = None,
    prior: Mapping[str, Mapping[str, tuple[DraftAttempt, DraftAttempt | None]]] | None = None,
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
        on_case: 사례 하나(최초와 선택 시도 2)가 끝날 때마다 (후보, 사례, 최초, 시도 2 | None)로 부른다. 진행
            기록용이며 prior로 넣은 사례에는 부르지 않는다.
        prior: 이미 끝난 사례 {후보: {사례: (최초, 시도 2 | None)}}. 호출하지 않고 그대로 집계에 넣는다(재개).
            모르는 후보이거나 실행 범위 밖 사례면 EVAL_INVALID_INPUT이다.

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
    done = prior or {}
    if not set(done) <= set(names) or any(not set(ids) <= set(selected) for ids in done.values()):
        raise ValueError("EVAL_INVALID_INPUT")
    runs: dict[str, dict[str, DraftAttempt]] = {name: {} for name in names}
    retries: dict[str, dict[str, DraftAttempt]] = {name: {} for name in names}
    verified: dict[str, dict[str, VerificationRun]] = {name: {} for name in names}
    for candidate in candidates:

        def attempt(case_id: str, number: int) -> DraftAttempt:
            case = cases[case_id]
            return run_draft_attempt(
                case.record,
                classification=case.classification,
                required_info_codes=case.required_info_codes,
                already_asked_codes=case.already_asked_codes,
                evidence=case.evidence,
                sensitive_values=case.sensitive_values,
                provider=candidate.provider,
                attempt_number=number,
                prompt_version=candidate.prompt_version,
                strip_phone_annotation=strip_phone_annotation,
            )

        for case_id in selected:
            if case_id in done.get(candidate.name, {}):
                initial, retry = done[candidate.name][case_id]
            else:
                # 형식 위반만 그 사례의 최초 시도 직후 시도 2로 다시 부른다(사례 단위로 끝내야 진행 기록이 맞다).
                initial = attempt(case_id, 1)
                retry = (
                    attempt(case_id, 2)
                    if invalid_output_retries and initial.error_code == INVALID_OUTPUT
                    else None
                )
                if on_case is not None:
                    on_case(candidate.name, case_id, initial, retry)
            runs[candidate.name][case_id] = initial
            if retry is not None:
                retries[candidate.name][case_id] = retry
            if candidate.verification_provider is not None:
                verified[candidate.name][case_id] = _verify_case(
                    cases[case_id], retry or initial, candidate, strip_phone_annotation
                )
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
                **(
                    {
                        "verification": _verification_row(
                            candidate,
                            verified[candidate.name],
                            {
                                case_id: retries[candidate.name].get(case_id, attempt)
                                for case_id, attempt in attempts.items()
                            },
                            scoped,
                            price_table,
                        )
                    }
                    if candidate.verification_provider is not None
                    else {}
                ),
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
