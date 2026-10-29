"""RFP 요구 추출·상태 판정·보완 경로·협력사 등급을 정답 사례와 비교한다(KAN-143). 파일·DB·모델에 접근하지 않는다.

정답은 호출자가 Mapping으로 준다(14번은 demo_cases.json에서 호출자가 변환, 10·11·12·18번 기대 결과는 저장소
밖). 이 모듈은 JSON을 읽지 않는다. 요구 키는 (clause_no, attribute)다. 정답 요구 전체를 행으로 두고 미추출은
NO_PREDICTION 열로 센다. 그래서 경로·necessity 일치율에도 미추출이 오답으로 들어가며, 추출 실패 자체는
P/R로 따로 본다. 합격 판정과 원문은 결과에 넣지 않는다.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, get_args

from backend.api.tft.partners.grading import Grade
from backend.api.tft.rfp.schemas import Necessity, RequirementStatus, SupplementPath
from eval.cs_metrics import NO_PREDICTION, _per_class_metrics

STATUSES: tuple[str, ...] = get_args(RequirementStatus)
NECESSITIES: tuple[str, ...] = get_args(Necessity)
PATHS: tuple[str, ...] = get_args(SupplementPath)
GRADES: tuple[str, ...] = get_args(Grade)
RECOMMENDED = ("MANDATORY_CONFIRMED", "REVIEW")
UNSUPPORTED_GOLD = ("SPEC_GAP", "INSUFFICIENT_EVIDENCE")
Key = tuple[str, str | None]


@dataclass(frozen=True)
class GoldRequirement:
    """정답 요구 하나. necessity·expected_path가 None이면 그 지표의 분모에서 뺀다."""

    clause_no: str
    attribute: str | None
    expected_status: RequirementStatus
    necessity: Necessity | None = None
    expected_path: SupplementPath | None = None


@dataclass(frozen=True)
class PredictedRequirement:
    """파이프라인이 낸 요구 하나. value_text는 편집 흔적 오추출 검사에만 쓴다."""

    clause_no: str
    attribute: str | None
    status: RequirementStatus
    necessity: Necessity | None
    path: SupplementPath | None
    evidence_block_ids: frozenset[str]
    value_text: str | None


def _check(value: object, allowed: tuple[str, ...], *, optional: bool = False) -> None:
    if not (optional and value is None) and value not in allowed:
        raise ValueError("EVAL_INVALID_INPUT")


def _key(requirement: GoldRequirement | PredictedRequirement) -> Key:
    if not isinstance(requirement.clause_no, str) or not (
        requirement.attribute is None or isinstance(requirement.attribute, str)
    ):
        raise ValueError("EVAL_INVALID_INPUT")
    return requirement.clause_no, requirement.attribute


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _matrix(rows: tuple[str, ...]) -> dict[str, dict[str, int]]:
    return {row: dict.fromkeys((*rows, NO_PREDICTION), 0) for row in rows}


def _classes(matrix: dict[str, dict[str, int]], labels: tuple[str, ...]) -> dict:
    """혼동행렬에서 클래스별 지표와 고정 클래스 macro-F1을 낸다.

    Args:
        matrix: NO_PREDICTION 열을 포함한 혼동행렬.
        labels: 정답 행 클래스.

    Returns:
        dict: confusion·per_class·macro_f1(정답 행이 0이면 None).
    """
    per_class = _per_class_metrics(matrix, labels)
    total = sum(sum(row.values()) for row in matrix.values())
    return {
        "confusion": matrix,
        "per_class": per_class,
        "macro_f1": sum(row["f1"] for row in per_class.values()) / len(labels) if total else None,
    }


def _gold_index(
    gold: Mapping[str, list[GoldRequirement]],
) -> dict[str, dict[Key, GoldRequirement]]:
    """정답을 사례·키로 색인하고 어휘와 키 중복을 검사한다.

    Args:
        gold: 사례별 정답 요구.

    Returns:
        dict: 사례 → 키 → 정답 요구. 같은 키가 둘 이상이면 EVAL_INVALID_INPUT ValueError다.
    """
    index: dict[str, dict[Key, GoldRequirement]] = {}
    for case_id, requirements in gold.items():
        index[case_id] = {}
        for requirement in requirements:
            if not isinstance(requirement, GoldRequirement):
                raise ValueError("EVAL_INVALID_INPUT")
            _check(requirement.expected_status, STATUSES)
            _check(requirement.necessity, NECESSITIES, optional=True)
            _check(requirement.expected_path, PATHS, optional=True)
            key = _key(requirement)
            if key in index[case_id]:
                raise ValueError("EVAL_INVALID_INPUT")
            index[case_id][key] = requirement
    return index


def _checked_predictions(requirements: list[PredictedRequirement]) -> list[PredictedRequirement]:
    for requirement in requirements:
        if not isinstance(requirement, PredictedRequirement) or not isinstance(
            requirement.evidence_block_ids, frozenset
        ):
            raise ValueError("EVAL_INVALID_INPUT")
        _check(requirement.status, STATUSES)
        _check(requirement.necessity, NECESSITIES, optional=True)
        _check(requirement.path, PATHS, optional=True)
        _key(requirement)
    return requirements


def gold_fingerprint(gold: Mapping[str, list[GoldRequirement]]) -> str:
    """두 실행이 같은 정답·같은 키로 비교했는지 확인할 지문을 만든다.

    Args:
        gold: 사례별 정답 요구.

    Returns:
        str: (case_id, clause_no, attribute, expected_status)를 정렬한 JSON의 sha256 앞 16자.
    """
    rows = sorted(
        [case_id, r.clause_no, r.attribute or "", r.expected_status]
        for case_id, requirements in gold.items()
        for r in requirements
    )
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]


def summarize_requirement_extraction(
    gold: Mapping[str, list[GoldRequirement]],
    predicted: Mapping[str, list[PredictedRequirement]],
    *,
    run_label: Literal["FIRST", "REMEASURE"],
) -> dict:
    """요구 추출 P/R/F1, 상태 혼동, 중대오류, 보완 경로·necessity 일치를 모은다.

    Args:
        gold: 사례별 정답 요구. 같은 사례 안에서 키가 겹치면 안 된다.
        predicted: 사례별 예측 요구. 정답에 없는 사례는 받지 않으며, 예측에 없는 정답 사례는 전부 미추출이다.
            같은 키가 둘 이상이면 첫 건만 매칭하고 나머지는 추출 FP로 센다.
        run_label: FIRST 또는 REMEASURE. 두 실행의 정답 동일성은 gold_fingerprint로 확인한다.

    Returns:
        dict: 추출 건수·P/R/F1과 "matched/gold"·"matched/predicted" 병기, 상태 4클래스 혼동·클래스별
            지표, 중대오류 (a) 근거 없는 충족 (b) 미충족·근거 부족을 충족으로 (c) 필수 요구 미추출의 건수와
            키, 경로 정확 일치율·혼동, necessity 정확 일치율, gold_fingerprint. 비율의 분모가 0이면 None이다.
            잘못된 입력은 EVAL_INVALID_INPUT ValueError다.
    """
    if run_label not in ("FIRST", "REMEASURE") or set(predicted) - set(gold):
        raise ValueError("EVAL_INVALID_INPUT")
    gold_index = _gold_index(gold)
    status_matrix, path_matrix = _matrix(STATUSES), _matrix(PATHS)
    unsupported: list[tuple[str, str, str | None]] = []
    overclaimed: list[tuple[str, str, str | None]] = []
    missed_mandatory: list[tuple[str, str, str | None]] = []
    predicted_total = matched = duplicates = 0
    necessity_total = necessity_hits = 0
    for case_id, by_key in gold_index.items():
        found: dict[Key, PredictedRequirement] = {}
        for requirement in _checked_predictions(list(predicted.get(case_id, []))):
            predicted_total += 1
            key = _key(requirement)
            if requirement.status == "EVIDENCE_FOUND" and not requirement.evidence_block_ids:
                unsupported.append((case_id, *key))
            if key in found:
                duplicates += 1
            else:
                found[key] = requirement
        for key, expected in by_key.items():
            guess = found.get(key)
            matched += guess is not None
            status_matrix[expected.expected_status][guess.status if guess else NO_PREDICTION] += 1
            if (
                guess
                and expected.expected_status in UNSUPPORTED_GOLD
                and guess.status == "EVIDENCE_FOUND"
            ):
                overclaimed.append((case_id, *key))
            if guess is None and expected.necessity == "MANDATORY":
                missed_mandatory.append((case_id, *key))
            if expected.expected_path is not None:
                column = guess.path if guess and guess.path else NO_PREDICTION
                path_matrix[expected.expected_path][column] += 1
            if expected.necessity is not None:
                necessity_total += 1
                necessity_hits += bool(guess and guess.necessity == expected.necessity)
    gold_total = sum(len(by_key) for by_key in gold_index.values())
    precision, recall = _rate(matched, predicted_total), _rate(matched, gold_total)
    path_total = sum(sum(row.values()) for row in path_matrix.values())
    return {
        "quality_gate_evaluated": False,
        "run_label": run_label,
        "gold_fingerprint": gold_fingerprint(gold),
        "extraction": {
            "gold": gold_total,
            "predicted": predicted_total,
            "matched": matched,
            "duplicate_predictions": duplicates,
            "precision": precision,
            "recall": recall,
            "f1": _rate(2 * matched, gold_total + predicted_total),
            "precision_count": f"{matched}/{predicted_total}",
            "recall_count": f"{matched}/{gold_total}",
        },
        "status": _classes(status_matrix, STATUSES),
        "critical_errors": {
            "unsupported_evidence_found": {"count": len(unsupported), "keys": unsupported},
            "gap_judged_found": {"count": len(overclaimed), "keys": overclaimed},
            "mandatory_not_extracted": {"count": len(missed_mandatory), "keys": missed_mandatory},
        },
        "supplement_path": {
            "total": path_total,
            "exact_match_rate": _rate(sum(path_matrix[p][p] for p in PATHS), path_total),
            "confusion": path_matrix,
        },
        "necessity": {
            "total": necessity_total,
            "exact_match_rate": _rate(necessity_hits, necessity_total),
        },
    }


def _fold(text: str) -> str:
    return " ".join(text.split()).casefold()


def summarize_edit_traces(
    predicted: Mapping[str, list[PredictedRequirement]],
    deleted_values: Mapping[str, frozenset[str]],
) -> dict:
    """예측 값이 지워진 값(취소선·추적 삭제)과 같은 편집 흔적 오추출을 센다.

    Args:
        predicted: 사례별 예측 요구.
        deleted_values: 사례별 문서의 삭제 값(KAN-96 deleted_values의 text).

    Returns:
        dict: 삭제 값이 있는 사례의 value_text 있는 예측 수, 정규화(앞뒤 공백·공백 접기·casefold) 후 삭제
            값과 같은 건수·비율(분모 0이면 None)·키. 삭제 값이 없는 사례는 분모에서 뺀다.
    """
    checked = 0
    hits: list[tuple[str, str, str | None]] = []
    for case_id, values in deleted_values.items():
        if not isinstance(values, frozenset) or not all(isinstance(v, str) for v in values):
            raise ValueError("EVAL_INVALID_INPUT")
        removed = {_fold(value) for value in values}
        if not removed:
            continue
        for requirement in _checked_predictions(list(predicted.get(case_id, []))):
            if requirement.value_text is None:
                continue
            if not isinstance(requirement.value_text, str):
                raise ValueError("EVAL_INVALID_INPUT")
            checked += 1
            if _fold(requirement.value_text) in removed:
                hits.append((case_id, *_key(requirement)))
    return {
        "quality_gate_evaluated": False,
        "predictions": checked,
        "edit_trace_extractions": len(hits),
        "rate": _rate(len(hits), checked),
        "keys": hits,
    }


def summarize_candidate_grades(
    gold: Mapping[str, Mapping[str, Grade]],
    predicted: Mapping[str, Mapping[str, Grade]],
) -> dict:
    """협력사 후보 등급의 혼동·추천 정밀도·위험 오류를 모은다.

    Args:
        gold: 사례별 후보 코드 → 정답 등급.
        predicted: 사례별 후보 코드 → 예측 등급. 정답에 없는 사례·후보는 받지 않는다.

    Returns:
        dict: 등급 4클래스 혼동(미예측은 NO_PREDICTION)·클래스별 지표, 추천 정밀도(예측 MANDATORY_CONFIRMED·
            REVIEW 중 정답도 그 둘인 비율, 분모 0이면 None), 위험 오류(정답 EXCLUDED를 추천으로 올림)
            건수와 (사례, 후보) 목록. 잘못된 입력은 EVAL_INVALID_INPUT ValueError다.
    """
    if set(predicted) - set(gold):
        raise ValueError("EVAL_INVALID_INPUT")
    matrix = _matrix(GRADES)
    recommended = correct = 0
    risky: list[tuple[str, str]] = []
    for case_id, grades in gold.items():
        guesses = predicted.get(case_id, {})
        if set(guesses) - set(grades):
            raise ValueError("EVAL_INVALID_INPUT")
        for candidate, expected in grades.items():
            _check(expected, GRADES)
            guess = guesses.get(candidate)
            _check(guess, GRADES, optional=True)
            matrix[expected][guess or NO_PREDICTION] += 1
            if guess in RECOMMENDED:
                recommended += 1
                correct += expected in RECOMMENDED
                if expected == "EXCLUDED":
                    risky.append((case_id, candidate))
    return {
        "quality_gate_evaluated": False,
        "grades": _classes(matrix, GRADES),
        "recommendation_precision": _rate(correct, recommended),
        "recommendation_count": f"{correct}/{recommended}",
        "excluded_recommended": {"count": len(risky), "keys": risky},
    }
