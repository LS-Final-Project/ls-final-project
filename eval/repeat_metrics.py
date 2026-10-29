"""같은 후보를 같은 대상에 여러 번 돌린 결과의 반복 일치율·캐시 활용·예산 소진 집계(KAN-145). 파일·DB·모델에
접근하지 않는다.

실제 반복 실행은 승인 뒤 별도로 한다. 결과에는 사례 ID를 넣지 않고 건수와 정렬 ID 목록의 sha256만 넣는다.
합격 판정은 하지 않는다.
"""

import hashlib
import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from backend.engine.cs.classification import ClassificationOutput

EXACT_FIELDS = ("category_code", "secondary_category", "urgency_flag", "return_subtype")
FIELDS = (*EXACT_FIELDS, "provided_info")
CACHE_NOTE = (
    "캐시 토큰 보고가 0건이면 캐시 미사용과 미보고를 구분할 수 없다(공급자가 0을 생략할 수 있다)"
)
NUMBER = re.compile(r"(0|[1-9][0-9]*)(\.[0-9]+)?")


def _rate(part: int, whole: int) -> float | None:
    """비율. 분모가 0이면 None이다.

    Args:
        part: 분자.
        whole: 분모.

    Returns:
        float | None: part / whole.
    """
    return part / whole if whole else None


def _digest(ids: list[str]) -> str:
    """사례 ID 목록을 정렬해 sha256으로 바꾼다.

    Args:
        ids: 사례 ID.

    Returns:
        str: 줄바꿈으로 이은 정렬 ID의 sha256 hex(목록이 비면 빈 문자열의 해시).
    """
    return hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()


def summarize_repeat_agreement(label_runs: list[Mapping[str, Mapping[str, Any] | None]]) -> dict:
    """반복 실행 사이에 사례별 예측이 같은 비율을 필드별로 센다.

    Args:
        label_runs: 실행별 {사례 ID: ClassificationOutput dict 또는 None(실패·보류)}. 2개 이상이고 ID 집합이 같아야
            한다.

    Returns:
        dict: runs·cases, 필드별 일치 건수·비율(정확 일치, provided_info는 집합 일치), 모든 필드 일치 건수·비율,
            validity_mismatch(어떤 실행은 결과가 있고 다른 실행은 None), all_null(모든 실행이 None),
            disagreements(모든 필드가 일치하지 않은 사례 수)와 disagreements_sha256(그 ID 목록의 정렬 해시).
            비율 분모는 전체 사례 수이며 None이 하나라도 있는 사례는 일치로 세지 않는다. 실행 수·ID 집합·출력
            형식이 틀리면 EVAL_INVALID_INPUT ValueError다.
    """
    if len(label_runs) < 2 or any(set(run) != set(label_runs[0]) for run in label_runs):
        raise ValueError("EVAL_INVALID_INPUT")
    try:
        runs = [
            {
                case: None if value is None else ClassificationOutput.model_validate(value)
                for case, value in run.items()
            }
            for run in label_runs
        ]
    except (ValidationError, TypeError):
        raise ValueError("EVAL_INVALID_INPUT") from None
    agree = dict.fromkeys(FIELDS, 0)
    all_fields = validity_mismatch = all_null = 0
    disagreements: list[str] = []
    for case in label_runs[0]:
        outputs = [run[case] for run in runs]
        present = [output for output in outputs if output is not None]
        if len(present) != len(outputs):
            validity_mismatch += bool(present)
            all_null += not present
            disagreements.append(case)
            continue
        same = {
            **{field: len({getattr(o, field) for o in present}) == 1 for field in EXACT_FIELDS},
            "provided_info": len({frozenset(o.provided_info) for o in present}) == 1,
        }
        for field, ok in same.items():
            agree[field] += ok
        if all(same.values()):
            all_fields += 1
        else:
            disagreements.append(case)
    cases = len(label_runs[0])
    return {
        "version": "cs-repeat-agreement-v1",
        "quality_gate_evaluated": False,
        "runs": len(label_runs),
        "cases": cases,
        "fields": {
            field: {"agree": agree[field], "rate": _rate(agree[field], cases)} for field in FIELDS
        },
        "all_fields": {"agree": all_fields, "rate": _rate(all_fields, cases)},
        "validity_mismatch": validity_mismatch,
        "all_null": all_null,
        "disagreements": len(disagreements),
        "disagreements_sha256": _digest(disagreements),
    }


def _report(usage: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    """후보 행 usage에서 토큰 하나의 보고 묶음을 꺼낸다.

    Args:
        usage: {토큰 이름: {reported_count, missing_count, known_sum, total}}.
        field: 토큰 이름.

    Returns:
        Mapping: 보고 묶음. 형식이 틀리면 EVAL_INVALID_INPUT ValueError다.
    """
    item = usage.get(field)
    if (
        not isinstance(item, Mapping)
        or any(
            type(item.get(k)) is not int or item[k] < 0 for k in ("reported_count", "missing_count")
        )
        or not (item.get("known_sum") is None or type(item["known_sum"]) is int)
    ):
        raise ValueError("EVAL_INVALID_INPUT")
    return item


def summarize_cache(usage_rows: list[Mapping[str, Any]]) -> dict:
    """하네스 후보 행 usage에서 캐시 입력 토큰 보고와 입력 대비 비율을 모은다.

    Args:
        usage_rows: 실행별 후보 행의 usage(cs_usage.sum_report 형식의 input_tokens·cached_input_tokens 포함).

    Returns:
        dict: 실행별·합계 cached reported/missing/known_sum, input known_sum, 캐시 비율(cached known_sum /
            input known_sum, 캐시 보고 0건이거나 입력 미보고면 None). 캐시 보고가 0건이면 note로 구분 불가를
            알린다. 형식이 틀리면 EVAL_INVALID_INPUT ValueError다.
    """
    if not usage_rows:
        raise ValueError("EVAL_INVALID_INPUT")
    rows = []
    for usage in usage_rows:
        cached, given = _report(usage, "cached_input_tokens"), _report(usage, "input_tokens")
        rows.append(
            {
                "cached_reported": cached["reported_count"],
                "cached_missing": cached["missing_count"],
                "cached_known_sum": cached["known_sum"],
                "input_known_sum": given["known_sum"],
            }
        )

    def ratio(cached_reported: int, cached_sum: int | None, input_sum: int | None) -> float | None:
        """캐시 비율.

        Args:
            cached_reported: 캐시 보고 수.
            cached_sum: 캐시 합.
            input_sum: 입력 합.

        Returns:
            float | None: 비율 또는 None.
        """
        if not cached_reported or cached_sum is None or not input_sum:
            return None
        return cached_sum / input_sum

    for row in rows:
        row["cache_ratio"] = ratio(
            row["cached_reported"], row["cached_known_sum"], row["input_known_sum"]
        )
    reported = sum(row["cached_reported"] for row in rows)
    cached_sum = sum(row["cached_known_sum"] or 0 for row in rows) if reported else None
    inputs = [row["input_known_sum"] for row in rows if row["input_known_sum"] is not None]
    input_sum = sum(inputs) if inputs else None
    return {
        "version": "cs-cache-usage-v1",
        "runs": rows,
        "total": {
            "cached_reported": reported,
            "cached_missing": sum(row["cached_missing"] for row in rows),
            "cached_known_sum": cached_sum,
            "input_known_sum": input_sum,
            "cache_ratio": ratio(reported, cached_sum, input_sum),
        },
        **({"note": CACHE_NOTE} if reported == 0 else {}),
    }


def _cost(value: object) -> Decimal | None:
    """실행 비용을 Decimal로 받는다.

    Args:
        value: Decimal, 하네스가 낸 십진 문자열, 또는 None.

    Returns:
        Decimal | None: 비용. 음수·비유한·형식 밖 값은 EVAL_INVALID_INPUT ValueError다.
    """
    if value is None:
        return None
    if isinstance(value, Decimal) and value.is_finite() and value >= 0:
        return value
    if isinstance(value, str) and NUMBER.fullmatch(value):
        return Decimal(value)
    raise ValueError("EVAL_INVALID_INPUT")


def summarize_budget(
    runs: list[Mapping[str, Any]], *, budget_usd: Decimal | None, budget_tokens: int | None
) -> dict:
    """실행별·누적 비용과 토큰을 예산 상한과 비교한다.

    Args:
        runs: 실행별 {name, cost_usd(Decimal·십진 문자열·None), total_tokens(int·None), calls(int)}.
        budget_usd: USD 예산 상한. None이면 비용 소진률을 내지 않는다.
        budget_tokens: 토큰 예산 상한. None이면 토큰 소진률을 내지 않는다.

    Returns:
        dict: 실행별 비용·토큰·호출과 누적 known_sum, 비용·토큰이 None인 실행 수, 예산 대비 소진률(known_sum
            기준, 예산 없으면 None)과 over_budget. 누락이 있으면 complete=False이며 소진률은 하한이다. 비용은
            반올림하지 않은 Decimal이다. 형식이 틀리면 EVAL_INVALID_INPUT ValueError다.
    """
    if (
        not runs
        or (budget_usd is not None and (not budget_usd.is_finite() or budget_usd <= 0))
        or (budget_tokens is not None and (type(budget_tokens) is not int or budget_tokens <= 0))
    ):
        raise ValueError("EVAL_INVALID_INPUT")
    rows = []
    for run in runs:
        tokens, calls = run.get("total_tokens"), run.get("calls")
        if (
            not isinstance(run.get("name"), str)
            or not (tokens is None or (type(tokens) is int and tokens >= 0))
            or type(calls) is not int
            or calls < 0
        ):
            raise ValueError("EVAL_INVALID_INPUT")
        rows.append(
            {
                "name": run["name"],
                "cost_usd": _cost(run.get("cost_usd")),
                "total_tokens": tokens,
                "calls": calls,
            }
        )
    costs = [row["cost_usd"] for row in rows if row["cost_usd"] is not None]
    tokens_known = [row["total_tokens"] for row in rows if row["total_tokens"] is not None]
    cost_sum = sum(costs, Decimal(0)) if costs else None
    token_sum = sum(tokens_known) if tokens_known else None
    cost_rate = cost_sum / budget_usd if budget_usd is not None and cost_sum is not None else None
    token_rate = (
        token_sum / budget_tokens if budget_tokens is not None and token_sum is not None else None
    )
    return {
        "version": "cs-run-budget-v1",
        "runs": rows,
        "calls": sum(row["calls"] for row in rows),
        "cost_usd": {
            "known_sum": cost_sum,
            "missing_runs": len(rows) - len(costs),
            "complete": len(costs) == len(rows),
            "budget": budget_usd,
            "burn_rate": cost_rate,
            "over_budget": None if cost_rate is None else cost_rate > 1,
        },
        "tokens": {
            "known_sum": token_sum,
            "missing_runs": len(rows) - len(tokens_known),
            "complete": len(tokens_known) == len(rows),
            "budget": budget_tokens,
            "burn_rate": token_rate,
            "over_budget": None if token_rate is None else token_rate > 1,
        },
    }
