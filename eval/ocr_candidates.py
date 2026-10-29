"""같은 사람 검수 영역에 OCR 후보 여러 개를 돌려 한 표로 비교한다(KAN-97).

실제 OCR·이미지 파일·모델에 접근하지 않고 공급자는 호출자가 주입한다. 채점은 document_metrics(KAN-101)의
cer·summarize_text_extraction·summarize_table_structure, 시간·비용은 cs_usage 집계를 그대로 쓴다. 분모는
REVIEWED 영역뿐이고 PENDING 영역은 호출하지 않고 건수만 낸다. 합격 판정과 원문 텍스트·표 셀 값은 결과에
넣지 않는다.
"""

import statistics
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Literal, cast

from eval.cs_usage import _percentile, _sum_report
from eval.document_metrics import Table, summarize_table_structure, summarize_text_extraction

RegionKind = Literal["TEXT", "TABLE"]
MissingReason = Literal["EMPTY", "ERROR", "CROP_FAILED"]


@dataclass(frozen=True)
class OcrRegion:
    """검수 영역 하나. image_ref는 경로나 ID 문자열일 뿐 하네스가 열지 않는다. bbox가 None이면 원페이지."""

    region_id: str
    image_ref: str
    page: int
    bbox: tuple[int, int, int, int] | None
    kind: RegionKind


@dataclass(frozen=True)
class OcrGold:
    """사람 검수 정답. TEXT는 text, TABLE은 table이 있어야 한다."""

    text: str | None
    table: Table | None
    review_status: Literal["REVIEWED", "PENDING"]


@dataclass(frozen=True)
class OcrResult:
    """공급자 반환값. crop_failed면 하네스가 원페이지로 한 번 더 부른다."""

    text: str | None
    table: Table | None
    elapsed_ms: float | None
    cost_usd: Decimal | None
    crop_failed: bool = False
    error_code: str | None = None


@dataclass(frozen=True)
class OcrCandidate:
    """비교할 OCR 후보. 엔진·설정 차이는 provider 안에 있고 하네스는 이름·판본만 기록한다."""

    name: str
    provider: Callable[[OcrRegion], OcrResult]
    engine: str
    config_version: str


def _call(candidate: OcrCandidate, region: OcrRegion) -> OcrResult:
    """공급자를 부르고 예외·잘못된 반환은 원문 없이 PROVIDER_ERROR로 바꾼다.

    Args:
        candidate: 후보.
        region: 영역.

    Returns:
        OcrResult: 공급자 결과 또는 PROVIDER_ERROR 결과.
    """
    try:
        result = candidate.provider(region)
    except Exception:
        result = None
    if not isinstance(result, OcrResult):
        return OcrResult(None, None, None, None, error_code="PROVIDER_ERROR")
    return result


def _missing(region: OcrRegion, result: OcrResult) -> MissingReason | None:
    """결과가 채점할 수 없는 누락인지 사유와 함께 본다.

    Args:
        region: 영역.
        result: 최종 결과(원페이지 대조 후).

    Returns:
        MissingReason | None: 오류 코드면 ERROR, 크롭 실패가 남으면 CROP_FAILED, 값이 비면 EMPTY.
    """
    if result.error_code is not None:
        return "ERROR"
    if result.crop_failed:
        return "CROP_FAILED"
    if region.kind == "TEXT":
        return None if result.text and result.text.strip() else "EMPTY"
    return None if result.table else "EMPTY"


def _check(
    regions: list[OcrRegion], gold: Mapping[str, OcrGold], candidates: list[OcrCandidate]
) -> None:
    """설정을 검사한다.

    Args:
        regions: 영역 목록.
        gold: 영역 ID별 정답.
        candidates: 후보 목록.

    Returns:
        None: 잘못되면 EVAL_INVALID_INPUT ValueError다.
    """
    names = [c.name for c in candidates if isinstance(c, OcrCandidate)]
    ids = [r.region_id for r in regions if isinstance(r, OcrRegion)]
    valid = (
        regions
        and candidates
        and len(names) == len(candidates)
        and all(names)
        and len(set(names)) == len(names)
        and len(ids) == len(regions)
        and len(set(ids)) == len(ids)
        and set(ids) == set(gold)
    )
    if not valid:
        raise ValueError("EVAL_INVALID_INPUT")
    for region in regions:
        answer = gold[region.region_id]
        if (
            not isinstance(answer, OcrGold)
            or answer.review_status not in ("REVIEWED", "PENDING")
            or region.kind not in ("TEXT", "TABLE")
            or not isinstance(region.page, int)
            or region.page < 1
            or not (region.bbox is None or len(region.bbox) == 4)
            or (
                answer.review_status == "REVIEWED"
                and (answer.text if region.kind == "TEXT" else answer.table) is None
            )
        ):
            raise ValueError("EVAL_INVALID_INPUT")


def run_ocr_candidates(
    regions: list[OcrRegion],
    *,
    gold: Mapping[str, OcrGold],
    candidates: list[OcrCandidate],
) -> dict:
    """후보마다 REVIEWED 영역을 같은 순서로 부르고 기존 채점기로 비교표를 만든다.

    Args:
        regions: 검수 영역 목록. region_id는 고유해야 한다.
        gold: 영역 ID별 정답. ID 집합이 regions와 같아야 하며 REVIEWED는 kind에 맞는 정답이 있어야 한다.
        candidates: 이름이 고유한 후보 목록.

    Returns:
        dict: {"regions"·"pending_regions"·"candidates"·"ranking"}. ranking은 누락 오름차순 →
            cer_mean 오름차순(None은 뒤) → structure_match_rate 내림차순(None은 뒤) → 이름이다.
            잘못된 설정은 EVAL_INVALID_INPUT ValueError다.
    """
    _check(regions, gold, candidates)
    reviewed = [r for r in regions if gold[r.region_id].review_status == "REVIEWED"]
    rows = [_row(candidate, reviewed, gold) for candidate in candidates]
    ranking = sorted(
        rows,
        key=lambda row: (
            row["missing"]["count"],
            row["text"]["cer_mean"] is None,
            row["text"]["cer_mean"] or 0.0,
            row["table"]["structure_match_rate"] is None,
            -(row["table"]["structure_match_rate"] or 0.0),
            row["name"],
        ),
    )
    return {
        "quality_gate_evaluated": False,
        "regions": len(reviewed),
        "pending_regions": len(regions) - len(reviewed),
        "candidates": rows,
        "ranking": [row["name"] for row in ranking],
    }


def _row(candidate: OcrCandidate, regions: list[OcrRegion], gold: Mapping[str, OcrGold]) -> dict:
    """후보 하나의 결과 행을 만든다.

    Args:
        candidate: 후보.
        regions: REVIEWED 영역(입력 순서).
        gold: 영역 ID별 정답.

    Returns:
        dict: 분모(scored·missing)·텍스트·표 지표·fallback·시간·비용·영역별 행. 원문은 넣지 않는다.
    """
    calls: list[OcrResult] = []
    texts: dict[str, tuple[str, str]] = {}
    tables_expected: dict[str, Table] = {}
    tables_actual: dict[str, Table] = {}
    per_region: list[dict[str, Any]] = []
    fallback_ids: set[str] = set()
    for region in regions:
        result = _call(candidate, region)
        calls.append(result)
        fallback = result.crop_failed and region.bbox is not None and result.error_code is None
        if fallback:
            result = _call(candidate, replace(region, bbox=None))
            calls.append(result)
            fallback_ids.add(region.region_id)
        reason = _missing(region, result)
        answer = gold[region.region_id]
        if reason is None and region.kind == "TEXT":
            texts[region.region_id] = (cast(str, answer.text), cast(str, result.text))
        elif reason is None:
            tables_expected[region.region_id] = cast(Table, answer.table)
            tables_actual[region.region_id] = cast(Table, result.table)
        per_region.append(
            {"region_id": region.region_id, "missing": reason, "fallback": fallback, "cer": None}
        )
    text = summarize_text_extraction(texts)
    cer = {block["block_id"]: block["cer"] for block in text["per_block"]}
    for row in per_region:
        row["cer"] = cer.get(row["region_id"])
    table = summarize_table_structure(tables_expected, tables_actual)
    fallback_cer = [cer[i] for i in fallback_ids if i in cer]
    latency = sorted(c.elapsed_ms for c in calls if c.elapsed_ms is not None)
    cost = _sum_report([{"cost": c.cost_usd} for c in calls], "cost")
    missing = [row for row in per_region if row["missing"]]
    return {
        "name": candidate.name,
        "engine": candidate.engine,
        "config_version": candidate.config_version,
        "scored": len(regions) - len(missing),
        "missing": {
            "count": len(missing),
            "by_reason": {
                reason: sum(row["missing"] == reason for row in missing)
                for reason in ("EMPTY", "ERROR", "CROP_FAILED")
            },
        },
        "text": {
            "scored": text["blocks"],
            "cer_mean": text["cer_mean"],
            "cer_median": text["cer_median"],
            "exact_match_rate": text["exact_match_rate"],
        },
        "table": {
            "scored": table["tables"],
            "structure_match_rate": table["structure_match_rate"],
            "cell_accuracy": table["cell_accuracy"],
        },
        "fallback": {
            "used": len(fallback_ids),
            "cer_mean": statistics.fmean(fallback_cer) if fallback_cer else None,
        },
        "calls": len(calls),
        "latency_ms": {
            "reported_count": len(latency),
            "mean": statistics.fmean(latency) if latency else None,
            # _percentile은 정수 지연용으로 선언됐지만 계산은 실수에도 같다.
            "p50": _percentile(cast(list[int], latency), 0.5),
            "p95": _percentile(cast(list[int], latency), 0.95),
        },
        # Decimal을 반올림하지 않고 cs_candidates처럼 십진 문자열로 둔다.
        "cost_usd": {
            key: format(value, "f")
            if key in {"known_sum", "total"} and value is not None
            else value
            for key, value in cost.items()
        },
        "per_region": per_region,
    }
