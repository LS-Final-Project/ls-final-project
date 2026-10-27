"""고정 C/S 대상의 언어·채널별 최초 시도 집계. 입력 본문은 받지 않는다."""

import re
from collections.abc import Mapping
from typing import Literal, cast

from backend.engine.cs.classification_call import ClassificationAttempt
from eval.cs_metrics import (
    summarize_category_first_attempts,
    summarize_secondary_first_attempts,
    summarize_urgency_first_attempts,
)

Metric = Literal["category", "urgency", "secondary"]
CHANNELS = frozenset({"EMAIL", "PHONE", "WEB_FORM", "PARTNER_PORTAL", "CHAT"})
UNKNOWN = "UNKNOWN"


def summarize_first_attempts_by_group(
    gold: Mapping[str, str | None],
    attempts: Mapping[str, ClassificationAttempt],
    metadata: Mapping[str, Mapping[str, str | None]],
    *,
    metric: Metric,
) -> dict:
    """전체와 각 축의 분모를 고정하고 누락 메타데이터도 별도 그룹에 보존한다.

    Args:
        gold: 해당 지표의 사전 잠금 대상과 정답. None은 보조 분류만 허용한다.
        attempts: 대상별 최초 시도. 누락 시도는 기존 집계처럼 실패로 남긴다.
        metadata: 대상별 channel/body_language. 누락 행·키·None은 UNKNOWN이다.
        metric: 집계할 기존 지표 이름. 임의 함수나 모델을 실행하지 않는다.

    Returns:
        dict: 전체·채널·언어·PHONE 여부별 지표와 메타데이터 누락 수.
            잘못된 메타데이터는 원문 없는 ValueError로 거부한다.
    """
    if metric not in {"category", "urgency", "secondary"} or not set(metadata) <= set(gold):
        raise ValueError("CS_STRATA_INVALID_INPUT")
    groups: dict[str, dict[str, list[str]]] = {"channel": {}, "body_language": {}, "phone": {}}
    missing = {"channel": 0, "body_language": 0}
    for case_id in gold:
        item = metadata.get(case_id, {})
        if not isinstance(item, Mapping) or not set(item) <= {"channel", "body_language"}:
            raise ValueError("CS_STRATA_INVALID_INPUT")
        channel, language = item.get("channel"), item.get("body_language")
        if channel is not None and (not isinstance(channel, str) or channel not in CHANNELS):
            raise ValueError("CS_STRATA_INVALID_INPUT")
        if language is not None and (
            not isinstance(language, str)
            or len(language) > 32
            or re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", language) is None
        ):
            raise ValueError("CS_STRATA_INVALID_INPUT")
        missing["channel"] += channel is None
        missing["body_language"] += language is None
        keys = {
            "channel": channel or UNKNOWN,
            "body_language": language or UNKNOWN,
            "phone": UNKNOWN if channel is None else "PHONE" if channel == "PHONE" else "NON_PHONE",
        }
        for axis, key in keys.items():
            groups[axis].setdefault(key, []).append(case_id)

    def summarize(
        targets: Mapping[str, str | None], selected: Mapping[str, ClassificationAttempt]
    ) -> dict:
        """기존 세 지표 중 선택한 계산만 호출한다.

        Args:
            targets: 해당 그룹의 고정 정답 집합.
            selected: 해당 그룹에 존재하는 최초 시도.

        Returns:
            dict: 기존 지표 계약 그대로의 결과.
        """
        if metric == "secondary":
            return summarize_secondary_first_attempts(targets, selected)
        # 주분류/긴급도 함수가 실제 값과 enum을 검증하므로 None도 거부된다.
        typed = cast(Mapping[str, str], targets)
        if metric == "category":
            return summarize_category_first_attempts(typed, selected)
        return summarize_urgency_first_attempts(typed, selected)

    overall = summarize(gold, attempts)  # 추가 ID·재시도를 그룹 필터 전에 거부한다.
    return {
        "version": "cs-first-attempt-strata-v1",
        "metric": metric,
        "quality_gate_evaluated": False,
        "overall": overall,
        "missing_metadata": missing,
        "groups": {
            axis: {
                key: summarize(
                    {case_id: gold[case_id] for case_id in ids},
                    {case_id: attempts[case_id] for case_id in ids if case_id in attempts},
                )
                for key, ids in sorted(values.items())
            }
            for axis, values in groups.items()
        },
    }
