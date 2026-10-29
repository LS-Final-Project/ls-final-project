"""평가 수치의 원응답→DB→화면→CSV 단계 대조(KAN-148). 파일·DB에 접근하지 않는다.

호출자가 단계마다 값을 넘긴다(DB 쪽은 eval/cs_stored_metrics.py·cs_report.py가 읽는다). None은 "값 없음"이며
0으로 바꾸지 않는다. 한 단계가 0이고 다른 단계가 None이면 누락을 0으로 채운 것으로 의심해 따로 표시한다.
중단·부분 완료 실행은 부분 결과로 표시하고 합격 판정에 쓰지 않는다.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

Stage = Literal["RAW", "DB", "SCREEN", "CSV"]
STAGES: tuple[Stage, ...] = ("RAW", "DB", "SCREEN", "CSV")
RunStatus = Literal["COMPLETED", "ABORTED", "PARTIAL"]
STATUSES = ("MATCH", "MISMATCH", "MISSING", "ZERO_FOR_MISSING")
PARTIAL_NOTE = "부분 결과이며 합격 판정에 쓰지 않는다"
Number = float | int


@dataclass(frozen=True)
class StageValues:
    """한 단계의 지표 값과 사례 수. None은 값 없음이다."""

    stage: Stage
    values: Mapping[str, Number | None]
    case_count: int | None


def _number(value: object) -> bool:
    """대조할 수 있는 값인지 본다.

    Args:
        value: 단계 값.

    Returns:
        bool: None 또는 유한한 int·float이면 True(bool은 아님).
    """
    if value is None:
        return True
    return type(value) in (int, float) and math.isfinite(value)  # type: ignore[arg-type]


def _compare(values: Mapping[Stage, Number | None], tolerance: float) -> dict:
    """한 지표의 단계별 값을 대조한다.

    Args:
        values: 단계별 값(있는 단계만, STAGES 순서).
        tolerance: 허용 절대 차.

    Returns:
        dict: status·values·diff_stages. 판정 순서는 ZERO_FOR_MISSING(0과 None이 함께 있음) → MISMATCH
            (기준과 tolerance 넘게 다른 값) → MISSING(None 단계) → MATCH다. 기준은 RAW 값이고, RAW가
            None이면 STAGES 순서의 첫 비None 값이다. diff_stages는 ZERO_FOR_MISSING·MISSING이면 None 단계,
            MISMATCH면 기준과 다른 단계다.
    """
    missing = [stage for stage, value in values.items() if value is None]
    present = {stage: value for stage, value in values.items() if value is not None}
    reference = next(iter(present.values()), None)
    differ = [
        stage
        for stage, value in present.items()
        if reference is not None and abs(value - reference) > tolerance
    ]
    if missing and any(value == 0 for value in present.values()):
        status, stages = "ZERO_FOR_MISSING", missing
    elif differ:
        status, stages = "MISMATCH", differ
    elif missing:
        status, stages = "MISSING", missing
    else:
        status, stages = "MATCH", []
    return {"status": status, "values": dict(values), "diff_stages": stages}


def reconcile(
    stages: list[StageValues],
    *,
    run_status: RunStatus,
    targets: int,
    evaluated: int,
    tolerance: float = 0.0,
) -> dict:
    """단계별 지표 값과 사례 수를 RAW 기준으로 대조한다.

    Args:
        stages: 단계 값. RAW는 필수이고 같은 단계는 하나만 허용한다. 없는 단계는 비교하지 않는다.
        run_status: 실행 상태. ABORTED·PARTIAL이면 부분 결과다.
        targets: 고정 대상 수.
        evaluated: 평가를 마친 대상 수. targets 이하.
        tolerance: 허용 절대 차(0 이상).

    Returns:
        dict: run_status, partial, coverage(targets·evaluated·ratio, targets 0이면 None), note(부분 결과일 때),
            metrics(key별 status·values·diff_stages), case_count(같은 규칙), summary(상태별 지표 수),
            consistent(MISMATCH·ZERO_FOR_MISSING이 지표·사례 수 모두 0건이고 부분 결과가 아니면 True).
            한 단계에 key가 없으면 그 단계 값은 None이다. 잘못된 입력은 EVAL_INVALID_INPUT ValueError다.
    """
    by_stage = {s.stage: s for s in stages if isinstance(s, StageValues)}
    if (
        len(by_stage) != len(stages)
        or "RAW" not in by_stage
        or any(stage not in STAGES for stage in by_stage)
        or run_status not in ("COMPLETED", "ABORTED", "PARTIAL")
        or type(targets) is not int
        or type(evaluated) is not int
        or not 0 <= evaluated <= targets
        or not math.isfinite(tolerance)
        or tolerance < 0
        or any(not _number(value) for s in stages for value in (*s.values.values(), s.case_count))
        or any(type(s.case_count) is float for s in stages)
    ):
        raise ValueError("EVAL_INVALID_INPUT")
    present = [by_stage[stage] for stage in STAGES if stage in by_stage]
    keys = sorted({key for s in present for key in s.values})
    metrics = {
        key: _compare({s.stage: s.values.get(key) for s in present}, tolerance) for key in keys
    }
    case_count = _compare({s.stage: s.case_count for s in present}, tolerance)
    partial = run_status != "COMPLETED"
    summary = {status: 0 for status in STATUSES}
    for row in metrics.values():
        summary[row["status"]] += 1
    bad = {"MISMATCH", "ZERO_FOR_MISSING"}
    return {
        "run_status": run_status,
        "partial": partial,
        "coverage": {
            "targets": targets,
            "evaluated": evaluated,
            "ratio": evaluated / targets if targets else None,
        },
        **({"note": PARTIAL_NOTE} if partial else {}),
        "metrics": metrics,
        "case_count": case_count,
        "summary": summary,
        "consistent": not partial
        and summary["MISMATCH"] + summary["ZERO_FOR_MISSING"] == 0
        and case_count["status"] not in bad,
    }


def csv_totals(rows: list[Mapping[str, str | None]], keys: tuple[str, ...]) -> dict:
    """CSV 행의 열 합계를 Decimal로 구한다. 빈 값은 누락으로 세고 0으로 더하지 않는다.

    Args:
        rows: CSV 행(열 이름 → 문자열). 빈 문자열·공백·None·열 없음은 누락이다.
        keys: 합계를 낼 열.

    Returns:
        dict: 열별 reported_count·missing_count·known_sum(보고된 값의 합, 없으면 None)·total(누락이 없을 때만
            known_sum, 아니면 None). 합계는 Decimal이다. 숫자로 바꿀 수 없거나 유한하지 않은 값은
            EVAL_INVALID_INPUT ValueError다.
    """
    result = {}
    for key in keys:
        values: list[Decimal] = []
        for row in rows:
            raw = row.get(key)
            if raw is None or not raw.strip():
                continue
            try:
                value = Decimal(raw.strip())
            except InvalidOperation:
                raise ValueError("EVAL_INVALID_INPUT") from None
            if not value.is_finite():
                raise ValueError("EVAL_INVALID_INPUT")
            values.append(value)
        known = sum(values, Decimal(0)) if values else None
        result[key] = {
            "reported_count": len(values),
            "missing_count": len(rows) - len(values),
            "known_sum": known,
            "total": known if len(values) == len(rows) and values else None,
        }
    return result


def screen_payload_values(
    payload: Mapping[str, Any], paths: Mapping[str, tuple[str, ...]]
) -> dict[str, Number | None]:
    """화면 DTO에서 경로로 지표 값을 꺼낸다(StageValues SCREEN용).

    Args:
        payload: 화면에 보낸 응답 dict.
        paths: 지표 key → 키 경로.

    Returns:
        dict: key별 값. 경로가 없거나 값이 None이면 None이다(0으로 채우지 않음). 경로 끝 값이 유한한 int·float가
            아니면 EVAL_INVALID_INPUT ValueError다.
    """
    values: dict[str, Number | None] = {}
    for key, path in paths.items():
        node: Any = payload
        for part in path:
            node = node.get(part) if isinstance(node, Mapping) else None
            if node is None:
                break
        if not _number(node):
            raise ValueError("EVAL_INVALID_INPUT")
        values[key] = node
    return values
