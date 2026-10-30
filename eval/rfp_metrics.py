"""RFP 요구 추출·상태 판정·보완 경로·협력사 등급을 정답 사례와 비교한다(KAN-143). 파일·DB·모델에 접근하지 않는다.

정답은 호출자가 Mapping으로 준다(14번은 demo_cases.json에서 호출자가 변환, 10·11·12·18번 기대 결과는 저장소
밖). 이 모듈은 JSON을 읽지 않는다. 요구 키는 (clause_no, attribute)다. 정답 요구 전체를 행으로 두고 미추출은
NO_PREDICTION 열로 센다. 그래서 경로·necessity 일치율에도 미추출이 오답으로 들어가며, 추출 실패 자체는
P/R로 따로 본다. 합격 판정과 원문은 결과에 넣지 않는다.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
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
# 중대한 잘못된 충족의 '필수 요구'(gates-rfp-v1 rfp_critical_false_fulfillment_count).
BINDING_NECESSITIES = ("MANDATORY", "CONDITION_BOUND")
Key = tuple[str, str | None]


@dataclass(frozen=True)
class GoldRequirement:
    """정답 요구 하나. necessity·expected_path가 None이면 그 지표의 분모에서 뺀다.

    expected_reason은 엔진 사유 어휘를 그대로 쓴 정답 사유다. 있으면 상태 일치에 사유 일치(정규화 문장)도 요구한다.
    """

    clause_no: str
    attribute: str | None
    expected_status: RequirementStatus
    necessity: Necessity | None = None
    expected_path: SupplementPath | None = None
    expected_reason: str | None = None


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
    reason: str | None = None


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
            if requirement.expected_reason is not None and not isinstance(
                requirement.expected_reason, str
            ):
                raise ValueError("EVAL_INVALID_INPUT")
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
        if requirement.reason is not None and not isinstance(requirement.reason, str):
            raise ValueError("EVAL_INVALID_INPUT")
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
            키, 경로 정확 일치율·혼동, necessity 정확 일치율, gold_fingerprint. status_match는 상태(정답 사유가 있으면
            사유도)가 맞은 정답 요구 비율이고, critical_errors.mandatory_gap_judged_found는 필수 미충족·미확인을 확정
            충족으로 판정한 건수와 분모다(necessity가 없는 정답 요구가 하나라도 있으면 None). 비율의 분모가 0이면 None이다.
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
    status_matched = reason_compared = 0
    binding_total = necessity_missing = 0
    binding_found: list[tuple[str, str, str | None]] = []
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
            reason_compared += expected.expected_reason is not None
            status_matched += bool(
                guess
                and guess.status == expected.expected_status
                and (
                    expected.expected_reason is None
                    or guess.reason is not None
                    and _fold(guess.reason) == _fold(expected.expected_reason)
                )
            )
            if expected.necessity is None:
                # 필수 여부를 모르는 정답 요구가 하나라도 있으면 0건으로 보이지 않게 집계를 None으로 둔다.
                necessity_missing += 1
            elif (
                expected.necessity in BINDING_NECESSITIES
                and expected.expected_status in UNSUPPORTED_GOLD
            ):
                binding_total += 1
                if guess and guess.status == "EVIDENCE_FOUND":
                    binding_found.append((case_id, *key))
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
        "status_match": {
            "matched": status_matched,
            "total": gold_total,
            "rate": _rate(status_matched, gold_total),
            "count": f"{status_matched}/{gold_total}",
            "reason_compared_count": reason_compared,
            "reason_compared": bool(gold_total) and reason_compared == gold_total,
        },
        "critical_errors": {
            "unsupported_evidence_found": {"count": len(unsupported), "keys": unsupported},
            "gap_judged_found": {"count": len(overclaimed), "keys": overclaimed},
            "mandatory_not_extracted": {"count": len(missed_mandatory), "keys": missed_mandatory},
            # 필수(MANDATORY·CONDITION_BOUND) 미충족·미확인을 확정 충족으로. necessity가 없는 정답 요구가 하나라도
            # 있으면 분모에서 빠진 필수 요구가 있을 수 있어 None(NOT_EVALUABLE)이다.
            "mandatory_gap_judged_found": {
                "count": None if necessity_missing else len(binding_found),
                "denominator": None if necessity_missing else binding_total,
                "necessity_missing": necessity_missing,
                "keys": binding_found,
            },
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


# 등급 판정이 고객 목록 밖 제조사에 붙이는 확인 문구(grading._manufacturer). 이 문구가 있을 때만 manufacturer_list를
# 표시한 것으로 본다(지정 모델 안내 같은 다른 확인 문구는 제조사 확인이 아니다, #318 리뷰 P3).
MANUFACTURER_CONDITIONS = frozenset({"발주처 승인 필요", "목록 밖 제조사 허용 여부 확인 필요"})


def unconfirmed_items(summary: Mapping, grades: Sequence[Mapping]) -> set[str]:
    """후보 하나가 화면에 '확인 필요'로 보이는 항목 이름(검토 후보 미확인 조건 표시 확인용).

    등급 판정(backend/api/tft/partners/grading.py)의 검사 항목 이름을 그대로 쓴다: 요구별 mandatory_check.unknown
    (속성명, certification:규격, integrator_capable.항목, supply_region)과, 후보 요약의 제조사 목록 확인 문구가
    있으면 manufacturer_list. 문장은 표현이 바뀌면 대조가 깨지므로 이름만 본다.

    Args:
        summary: CandidateSummary를 dict로 덤프한 것(on_customer_manufacturer_list·conditions_to_confirm).
        grades: 그 후보의 RequirementGrade dict 목록.

    Returns:
        set[str]: 표시된 미확인 항목 이름.
    """
    names = {name for g in grades for name in g["mandatory_check"]["unknown"]}
    shown = set(summary.get("conditions_to_confirm") or ())
    if summary.get("on_customer_manufacturer_list") is False and MANUFACTURER_CONDITIONS & shown:
        names.add("manufacturer_list")
    return names


def _review_listing(
    gold: Mapping[str, Mapping[str, Grade]],
    predicted: Mapping[str, Mapping[str, Grade]],
    gold_unconfirmed: Mapping[str, Mapping[str, Sequence[str]]],
    predicted_unconfirmed: Mapping[str, Mapping[str, Sequence[str]]],
) -> dict:
    """정답·예측 모두 REVIEW인 후보마다 정답 미확인 항목이 예측 표시에 빠짐없이 있는지 센다."""
    listed = total = 0
    missing: list[tuple[str, str, list[str]]] = []
    for case_id, grades in gold.items():
        for candidate, expected in grades.items():
            if expected != "REVIEW" or predicted.get(case_id, {}).get(candidate) != "REVIEW":
                continue
            needed = gold_unconfirmed.get(case_id, {}).get(candidate)
            if needed is None:
                # 정답 REVIEW 후보에 미확인 항목 목록이 없으면 정답이 덜 만들어진 것이다.
                raise ValueError("EVAL_INVALID_INPUT")
            shown = set(predicted_unconfirmed.get(case_id, {}).get(candidate, ()))
            total += 1
            lacking = sorted(set(needed) - shown)
            if lacking:
                missing.append((case_id, candidate, lacking))
            else:
                listed += 1
    return {
        "rate": _rate(listed, total),
        "count": f"{listed}/{total}",
        "missing": missing,
    }


def summarize_candidate_grades(
    gold: Mapping[str, Mapping[str, Grade]],
    predicted: Mapping[str, Mapping[str, Grade]],
    gold_unconfirmed: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
    predicted_unconfirmed: Mapping[str, Mapping[str, Sequence[str]]] | None = None,
) -> dict:
    """협력사 후보 등급의 혼동·추천 정밀도·위험 오류를 모은다.

    Args:
        gold: 사례별 후보 코드 → 정답 등급.
        predicted: 사례별 후보 코드 → 예측 등급. 정답에 없는 사례·후보는 받지 않는다.
        gold_unconfirmed: 사례별 후보 코드 → 정답 REVIEW 후보가 표시해야 할 미확인 항목 이름(unconfirmed_items와
            같은 이름). 정답 REVIEW 후보마다 있어야 한다.
        predicted_unconfirmed: 사례별 후보 코드 → 예측 결과에 표시된 미확인 항목 이름(unconfirmed_items 결과).

    Returns:
        dict: 등급 4클래스 혼동(미예측은 NO_PREDICTION)·클래스별 지표, 추천 정밀도(예측 MANDATORY_CONFIRMED·
            REVIEW 중 정답도 그 둘인 비율, 분모 0이면 None), 필수 기술조건 확인 정밀도(예측 MANDATORY_CONFIRMED 중
            정답도 MANDATORY_CONFIRMED인 비율), 위험 오류(정답 EXCLUDED를 추천으로 올림) 건수·분모(정답 EXCLUDED
            수)와 (사례, 후보) 목록. review_unconfirmed_listed는 정답·예측 모두 REVIEW인 후보 중 정답 미확인 항목을
            빠짐없이 표시한 비율·건수·빠진 항목(KAN-191)이며, 미확인 항목 입력이 없으면 None이다. 잘못된 입력은
            EVAL_INVALID_INPUT ValueError다.
    """
    if set(predicted) - set(gold):
        raise ValueError("EVAL_INVALID_INPUT")
    matrix = _matrix(GRADES)
    recommended = correct = confirmed = confirmed_correct = excluded = 0
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
            excluded += expected == "EXCLUDED"
            if guess == "MANDATORY_CONFIRMED":
                confirmed += 1
                confirmed_correct += expected == "MANDATORY_CONFIRMED"
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
        "mandatory_confirmed_precision": _rate(confirmed_correct, confirmed),
        "mandatory_confirmed_count": f"{confirmed_correct}/{confirmed}",
        "review_unconfirmed_listed": _review_listing(
            gold, predicted, gold_unconfirmed, predicted_unconfirmed
        )
        if gold_unconfirmed is not None and predicted_unconfirmed is not None
        else None,
        "excluded_recommended": {"count": len(risky), "denominator": excluded, "keys": risky},
    }


def summarize_integration_responsibility(
    gold: Mapping[str, Sequence[str]], predicted: Mapping[str, Mapping]
) -> dict:
    """통합 책임 확인(gates-rfp-v1 rfp_integration_responsibility_rate)을 조립품 공급 요구 단위로 센다.

    분자는 조립·시험·연동·보증 책임자 확인이 필수 항목으로 표시되고(`check_item_listed`, 근거 조항에 그 요구가
    들어 있음), 책임자가 미확보면 처리 경로(`fallback_paths`)가 제시된 조립품 공급 요구다. 분모는 정답의 조립품
    공급 요구 전부다. 미예측 문서는 분자에 들어가지 않는다.

    Args:
        gold: 문서(사례)별 조립품 공급 요구의 조항 번호.
        predicted: 문서별 `integration.integration_responsibility` 결과(model_dump). 정답에 없는 문서는 받지 않는다.

    Returns:
        dict: rate(분모 0이면 None)·count('n/m')·denominator·missed(문서, 조항) 목록. 잘못된 입력은
            EVAL_INVALID_INPUT ValueError다.
    """
    if set(predicted) - set(gold):
        raise ValueError("EVAL_INVALID_INPUT")
    hits = total = 0
    missed: list[tuple[str, str]] = []
    for case_id, clauses in gold.items():
        if isinstance(clauses, str) or not all(isinstance(c, str) for c in clauses):
            raise ValueError("EVAL_INVALID_INPUT")
        guess = predicted.get(case_id) or {}
        listed = bool(guess.get("check_item_listed"))
        basis = set(guess.get("basis_clause_nos") or [])
        resolved = guess.get("holder") not in (None, "UNRESOLVED")
        handled = resolved or bool(guess.get("fallback_paths"))
        for clause in clauses:
            total += 1
            if listed and clause in basis and handled:
                hits += 1
            else:
                missed.append((case_id, clause))
    return {
        "quality_gate_evaluated": False,
        "rate": _rate(hits, total),
        "count": f"{hits}/{total}",
        "denominator": total,
        "missed": missed,
    }


def summarize_extraction_keys(
    gold: Mapping[str, Sequence[Key]], predicted: Mapping[str, Sequence[Key]]
) -> dict:
    """상태 없이 (clause_no, attribute) 키만으로 요구 추출 P/R을 센다(KAN-143 4차).

    요구 추출 측정(scripts/rfp_requirements_eval.py)은 비교 상태가 없어 summarize_requirement_extraction에 넣을 수
    없다. 키 일치·중복 처리는 그 함수와 같다(정답 키 중복은 거부, 예측 키 중복은 첫 건만 매칭하고 나머지는 FP).

    Args:
        gold: 사례별 정답 키.
        predicted: 사례별 예측 키. 정답에 없는 사례는 받지 않는다.

    Returns:
        dict: {"extraction": ...} summarize_requirement_extraction의 extraction 블록과 같은 필드. 잘못된 입력은
            EVAL_INVALID_INPUT ValueError다.
    """
    if set(predicted) - set(gold):
        raise ValueError("EVAL_INVALID_INPUT")
    gold_total = predicted_total = matched = duplicates = 0
    for case_id, keys in gold.items():
        wanted = [_plain_key(key) for key in keys]
        if len(set(wanted)) != len(wanted):
            raise ValueError("EVAL_INVALID_INPUT")
        gold_total += len(wanted)
        found: set[Key] = set()
        for key in (_plain_key(k) for k in predicted.get(case_id, [])):
            predicted_total += 1
            if key in found:
                duplicates += 1
            found.add(key)
        matched += len(found & set(wanted))
    return {
        "extraction": {
            "gold": gold_total,
            "predicted": predicted_total,
            "matched": matched,
            "duplicate_predictions": duplicates,
            "precision": _rate(matched, predicted_total),
            "recall": _rate(matched, gold_total),
            "f1": _rate(2 * matched, gold_total + predicted_total),
            "precision_count": f"{matched}/{predicted_total}",
            "recall_count": f"{matched}/{gold_total}",
        }
    }


def _plain_key(value: object) -> Key:
    """(clause_no, attribute) 튜플을 _key와 같은 규칙으로 검사한다.

    Args:
        value: 키 후보.

    Returns:
        Key: 그대로의 키. 형식이 틀리면 EVAL_INVALID_INPUT ValueError다.
    """
    if (
        not isinstance(value, tuple)
        or len(value) != 2
        or not isinstance(value[0], str)
        or not (value[1] is None or isinstance(value[1], str))
    ):
        raise ValueError("EVAL_INVALID_INPUT")
    return value[0], value[1]


def gate_values(
    extraction: Mapping | None, grades: Mapping | None, integration: Mapping | None = None
) -> dict[str, float | int | None]:
    """두 요약에서 gates-rfp-v1 게이트 9개 값을 꺼낸다(eval_adopt --task rfp 후보 행 키와 같다).

    Args:
        extraction: summarize_requirement_extraction 결과(없으면 None).
        grades: summarize_candidate_grades 결과(없으면 None).
        integration: summarize_integration_responsibility 결과(없으면 None). 분모가 0이면 None이다.

    Returns:
        dict: 게이트 키 → 값. 요약이 없거나 측정 도구가 없는 키(근거 정확성·초안 추적), 사유를 비교하지
            못한 상태 일치(reason_compared false), necessity가 없는 정답 요구가 있는 중대 오충족, 발생 가능 건수가
            0인 두 0건 게이트, 검토 후보 미확인 조건 표시를 같이 재지 않은 협력사 추천 정밀도는 None이다.
    """
    critical = extraction["critical_errors"]["mandatory_gap_judged_found"] if extraction else {}
    excluded = grades["excluded_recommended"] if grades else {}
    return {
        "rfp_requirement_recall": extraction["extraction"]["recall"] if extraction else None,
        "rfp_requirement_precision": extraction["extraction"]["precision"] if extraction else None,
        # 게이트는 '상태와 사유'가 모두 일치한 비율이다. 사유가 없는 정답 요구가 있으면(상태만 비교) None이다.
        "rfp_status_match_rate": extraction["status_match"]["rate"]
        if extraction and extraction["status_match"]["reason_compared"]
        else None,
        "rfp_evidence_accuracy": None,
        # 0건 게이트는 발생 가능 건수(분모)가 0이면 측정한 것이 없으므로 통과로 보이지 않게 None이다.
        "rfp_critical_false_fulfillment_count": critical.get("count")
        if critical.get("denominator")
        else None,
        # 게이트 값은 '필수 기술조건 확인 후보' 정밀도다. 정의의 나머지 절반(검토 후보 미확인 조건 표시)을 같이
        # 재지 않았으면 None이다. 표시 결과는 review_unconfirmed_listed로 따로 공개한다(게이트 정의 "따로 확인").
        "rfp_partner_recommendation_precision": grades["mandatory_confirmed_precision"]
        # 대상(정답·예측 모두 REVIEW) 후보가 0건이면 잰 것이 없으므로 None이다(#318 리뷰 P3).
        if grades and (grades.get("review_unconfirmed_listed") or {}).get("rate") is not None
        else None,
        "rfp_integration_responsibility_rate": integration.get("rate")
        if integration and integration.get("denominator")
        else None,
        "rfp_mandatory_constraint_violation_count": excluded.get("count")
        if excluded.get("denominator")
        else None,
        "rfp_draft_trace_rate": None,
    }
