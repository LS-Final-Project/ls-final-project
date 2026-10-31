"""KAN-97 OCR 후보 비교 하네스. fake 공급자와 합성 영역만 쓰고 이미지·OCR·모델에 접근하지 않는다."""

from dataclasses import replace
from decimal import Decimal

import pytest

from eval.ocr_candidates import OcrCandidate, OcrGold, OcrRegion, OcrResult, run_ocr_candidates

BOX = (0, 0, 100, 40)
REGIONS = [
    OcrRegion("t1", "page-1.png", 1, BOX, "TEXT"),
    OcrRegion("t2", "page-1.png", 1, BOX, "TEXT"),
    OcrRegion("t3", "page-2.png", 2, None, "TEXT"),
    OcrRegion("t4", "page-2.png", 2, BOX, "TEXT"),
    OcrRegion("g1", "page-3.png", 3, BOX, "TABLE"),
    OcrRegion("g2", "page-3.png", 3, BOX, "TABLE"),
]
GOLD = {
    "t1": OcrGold("Rated 4000 A", None, "REVIEWED"),
    "t2": OcrGold("Class F", None, "REVIEWED"),
    "t3": OcrGold("Tap 2.5%", None, "REVIEWED"),
    "t4": OcrGold(None, None, "PENDING"),
    "g1": OcrGold(None, [["Item", "Value"], ["Bus", "15"]], "REVIEWED"),
    "g2": OcrGold(None, [["A", "B"], ["1", None]], "REVIEWED"),
}


def answer(region, *, elapsed=10.0, cost=Decimal("0.001")):
    """정답 그대로의 결과를 만든다.

    Args:
        region: 영역.
        elapsed: 처리 시간(ms).
        cost: 비용.

    Returns:
        OcrResult: 정답 결과.
    """
    gold = GOLD[region.region_id]
    return OcrResult(gold.text, gold.table, elapsed, cost)


def exact(region):
    return answer(region)


def typo(region):
    """오탈자·빈 값·짧은 표를 섞는다(비용 미보고).

    Args:
        region: 영역.

    Returns:
        OcrResult: 결과.
    """
    values = {"t1": ("Rated 4O00 A", None), "t3": ("  ", None), "g2": (None, [["A", "B"]])}
    text, table = values.get(
        region.region_id, (GOLD[region.region_id].text, GOLD[region.region_id].table)
    )
    return OcrResult(text, table, 5.0, None)


def crop(region):
    """bbox가 있으면 크롭에 실패하고 원페이지에서는 정답을 낸다.

    Args:
        region: 영역.

    Returns:
        OcrResult: 결과.
    """
    if region.bbox is not None:
        return OcrResult(None, None, 1.0, Decimal("0.002"), crop_failed=True)
    return answer(region, elapsed=20.0, cost=Decimal("0.002"))


CANDIDATES = [
    OcrCandidate("typo", typo, "engine-b", "cfg-2"),
    OcrCandidate("exact", exact, "engine-a", "cfg-1"),
    OcrCandidate("crop", crop, "engine-c", "cfg-1"),
]


def rows(result):
    return {row["name"]: row for row in result["candidates"]}


def test_candidates_match_hand_calculation():
    """CER·누락·표 구조·fallback·시간·비용·ranking 손계산과 PENDING 분모 제외를 확인한다.

    Args:
        없음.

    Returns:
        None: REVIEWED 5개(TEXT 3, TABLE 2), PENDING 1개.
    """
    result = run_ocr_candidates(REGIONS, gold=GOLD, candidates=CANDIDATES)
    assert (result["regions"], result["pending_regions"]) == (5, 1)
    assert result["ranking"] == ["crop", "exact", "typo"]
    by_name = rows(result)

    bad = by_name["typo"]
    assert (bad["scored"], bad["missing"]["count"], bad["missing"]["by_reason"]["EMPTY"]) == (
        4,
        1,
        1,
    )
    assert bad["text"]["scored"] == 2 and bad["text"]["cer_mean"] == pytest.approx(1 / 24)
    assert bad["text"]["exact_match_rate"] == 0.5
    assert (bad["table"]["structure_match_rate"], bad["table"]["cell_accuracy"]) == (0.5, 7 / 8)
    assert bad["cost_usd"] == {
        "reported_count": 0,
        "missing_count": 5,
        "known_sum": None,
        "total": None,
    }
    t3 = next(row for row in bad["per_region"] if row["region_id"] == "t3")
    assert t3 == {"region_id": "t3", "missing": "EMPTY", "fallback": False, "cer": None}

    good = by_name["exact"]
    assert (good["missing"]["count"], good["text"]["cer_mean"], good["table"]["cell_accuracy"]) == (
        0,
        0.0,
        1.0,
    )
    assert good["calls"] == 5 and good["cost_usd"]["total"] == "0.005"

    fallback = by_name["crop"]
    assert (fallback["fallback"]["used"], fallback["fallback"]["cer_mean"]) == (4, 0.0)
    assert fallback["calls"] == 9 and fallback["missing"]["count"] == 0
    assert fallback["latency_ms"]["p50"] == 20.0
    assert fallback["latency_ms"]["mean"] == pytest.approx(104 / 9)
    assert fallback["cost_usd"]["total"] == "0.018"
    for text in ("Rated", "Class F", "Bus", "4O00"):
        assert text not in str(result)


def test_pending_regions_are_never_called_and_order_is_shared():
    """후보마다 REVIEWED 영역만 같은 순서로 부른다.

    Args:
        없음.

    Returns:
        None: 호출 순서를 확인한다.
    """
    seen: dict[str, list[str]] = {"a": [], "b": []}

    def recorder(name):
        def provider(region):
            seen[name].append(region.region_id)
            return answer(region)

        return provider

    run_ocr_candidates(
        REGIONS,
        gold=GOLD,
        candidates=[OcrCandidate(n, recorder(n), "e", "c") for n in ("a", "b")],
    )
    assert seen["a"] == seen["b"] == ["t1", "t2", "t3", "g1", "g2"]


def test_provider_errors_and_failed_fallback_are_missing():
    """공급자 예외는 원문 없이 PROVIDER_ERROR 누락, 원페이지도 실패하면 CROP_FAILED 누락이다.

    Args:
        없음.

    Returns:
        None: 영역별 누락 사유를 확인한다.
    """

    def flaky(region):
        if region.region_id == "t1":
            raise RuntimeError("SECRET-OCR-TEXT")
        if region.region_id == "t2":
            return OcrResult(None, None, None, None, crop_failed=True)
        if region.region_id == "g1":
            return OcrResult(None, None, None, None, error_code="ENGINE_TIMEOUT")
        return answer(region)

    result = run_ocr_candidates(
        REGIONS, gold=GOLD, candidates=[OcrCandidate("flaky", flaky, "e", "c")]
    )
    [row] = result["candidates"]
    reasons = {r["region_id"]: (r["missing"], r["fallback"]) for r in row["per_region"]}
    assert reasons["t1"] == ("ERROR", False)
    assert reasons["t2"] == ("CROP_FAILED", True)
    assert reasons["g1"] == ("ERROR", False)
    assert row["missing"]["by_reason"] == {"EMPTY": 0, "ERROR": 2, "CROP_FAILED": 1}
    assert row["fallback"] == {"used": 1, "cer_mean": None}
    assert row["calls"] == 6 and "SECRET-OCR-TEXT" not in str(result)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"candidates": [CANDIDATES[0], replace(CANDIDATES[1], name="typo")]},
        {"candidates": []},
        {"regions": []},
        {"gold": {k: v for k, v in GOLD.items() if k != "g2"}},
        {"gold": {**GOLD, "t1": OcrGold(None, None, "REVIEWED")}},
        {"gold": {**GOLD, "g1": OcrGold("text", None, "REVIEWED")}},
        {"regions": [replace(REGIONS[0], page=0), *REGIONS[1:]]},
        {"regions": [REGIONS[0], *REGIONS]},
    ],
    ids=[
        "dup-name",
        "no-candidates",
        "no-regions",
        "gold-ids",
        "text-gold",
        "table-gold",
        "page",
        "dup-region",
    ],
)
def test_invalid_settings_are_rejected(kwargs):
    """후보 이름 중복·빈 후보·빈 영역·정답 ID 불일치·kind와 정답 불일치·잘못된 페이지·영역 중복을 거부한다.

    Args:
        kwargs: 바꿀 인자.

    Returns:
        None: EVAL_INVALID_INPUT을 확인한다.
    """
    settings = {"regions": REGIONS, "gold": GOLD, "candidates": CANDIDATES} | kwargs
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        run_ocr_candidates(
            settings["regions"], gold=settings["gold"], candidates=settings["candidates"]
        )


def test_ranking_orders_missing_then_cer_then_structure():
    """누락이 적은 후보가 먼저, 다음은 CER, 같으면 표 구조 일치율이 높은 후보가 앞선다.

    Args:
        없음.

    Returns:
        None: 각 키가 순서를 정하는 후보 넷의 ranking을 확인한다.
    """

    def variant(changes):
        def provider(region):
            text, table = changes.get(region.region_id, (None, None))
            base = answer(region)
            return replace(base, text=text or base.text, table=table or base.table)

        return provider

    providers = {
        "a_missing": variant({"t3": ("  ", None)}),
        "b_cer": variant({"t1": ("Rated 4O00 A", None)}),
        "c_table": variant({"g2": (None, [["A", "B"]])}),
        "d_perfect": variant({}),
    }
    candidates = [OcrCandidate(name, p, "e", "c") for name, p in providers.items()]
    result = run_ocr_candidates(REGIONS, gold=GOLD, candidates=candidates)
    assert result["ranking"] == ["d_perfect", "c_table", "b_cer", "a_missing"]
