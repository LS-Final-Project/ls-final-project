"""고장 코드 없이 같은 고장 판단 평가 하네스(KAN-204). 파일·DB·네트워크에 접근하지 않는다.

해결 기록을 적재 시각(해결 시각 대리값) 순으로 다시 흘리며, 평가 대상 기록마다 그 시점까지 알려진 같은 제품
기록을 고장 ID로 묶어 가린 참조(G1..)로 보여 준다. 정답은 그 기록의 고장 ID가 가리키는 참조, 처음 나온 고장이면
NEW다. 모델에는 문의 원문만 가며 고장 ID·해결 기록 문장은 가지 않는다(engine/cs/fault_matching).

지표는 기획서 4.4의 두 오류를 따로 센다. 같은 고장을 새 고장으로 쪼갠 것(split)과 다른 고장에 합친 것(merge)이다.
영어 원문 대부분에는 해결 기록의 증상 문장이 그대로 들어 있어 쉬우므로 언어·증상 문장 포함 여부로 나눠 보고한다.
하류 지표는 AI 배정으로 다시 만든 묶음에 반복 후보 규칙(improvement_candidates, 90일·3곳)을 적용해 기준 도달
묶음·도달일이 고장 ID 기준과 얼마나 같은지 본다.
"""

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from backend.engine.cs.fault_matching import (
    MAX_EXAMPLES_PER_GROUP,
    GroupView,
    MatchInput,
)
from backend.engine.cs.improvement_candidates import CaseRecord, fault_candidates

NEW = "NEW"
# 고장 신고·펌웨어 문의는 원문이 증상을 설명한다(영어 원문은 전부 해결 기록 증상 문장을 포함, 2026-11-02 집계).
# 설치·보증·반품 문의는 증상 없이 요청만 있는 경우가 많아 원문만으로는 같은 고장을 가릴 수 없다.
FAULT_REPORTS = frozenset({"DEFECT", "FW_UPDATE"})
Predicted = Literal["EXISTING", "NEW", "UNSURE", "ERROR"]


@dataclass(frozen=True)
class CaseRow:
    """해결 기록 한 건과 그 문의의 모델 입력. fault_id·symptom_in_text는 채점용이며 모델에 가지 않는다."""

    case_id: str
    ticket_id: str
    product_code: str
    product_name: str
    fault_id: str
    customer_id: str | None
    known_at: datetime
    split: str
    language: str
    text: str
    symptom_in_text: bool
    category: str = ""


@dataclass(frozen=True)
class Task:
    case: CaseRow
    payload: MatchInput
    ref_to_fault: Mapping[str, str]
    expected: str


@dataclass(frozen=True)
class Result:
    case_id: str
    expected: str
    decision: Predicted
    group_ref: str | None
    language: str
    symptom_in_text: bool
    category: str = ""


def build_tasks(cases: Iterable[CaseRow], split: str, *, limit: int | None = None) -> list[Task]:
    """평가 대상 split의 기록마다 판단 입력과 정답을 만든다.

    Args:
        cases: 개발 split(train·valid) 해결 기록 전부. 평가 대상이 아닌 기록도 이력으로 쓴다.
        split: 평가할 split.
        limit: 앞에서부터(적재 시각 순) 이만큼만. None이면 전부.

    Returns:
        list[Task]: 적재 시각 순. 이력은 대상보다 먼저 알려진 같은 제품 기록뿐이다(같은 시각은 넣지 않는다).
    """
    ordered = sorted(cases, key=lambda c: (c.known_at, c.case_id))
    tasks: list[Task] = []
    for case in ordered:
        if case.split != split:
            continue
        history = [
            c for c in ordered if c.product_code == case.product_code and c.known_at < case.known_at
        ]
        members: dict[str, list[CaseRow]] = {}
        for c in history:
            members.setdefault(c.fault_id, []).append(c)
        ref_to_fault = {f"G{i}": fault for i, fault in enumerate(members, 1)}
        groups = [
            GroupView(ref=ref, examples=[c.text for c in members[fault][:MAX_EXAMPLES_PER_GROUP]])
            for ref, fault in ref_to_fault.items()
        ]
        fault_to_ref = {fault: ref for ref, fault in ref_to_fault.items()}
        tasks.append(
            Task(
                case=case,
                payload=MatchInput(product_name=case.product_name, query=case.text, groups=groups),
                ref_to_fault=ref_to_fault,
                expected=fault_to_ref.get(case.fault_id, NEW),
            )
        )
        if limit is not None and len(tasks) >= limit:
            break
    return tasks


def result_of(task: Task, decision: Predicted, group_ref: str | None) -> Result:
    """판단 하나를 채점용 결과로 만든다."""
    return Result(
        task.case.case_id,
        task.expected,
        decision,
        group_ref,
        task.case.language,
        task.case.symptom_in_text,
        task.case.category,
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def summarize(results: list[Result]) -> dict[str, object]:
    """두 오류를 나눠 센다.

    Args:
        results: 판단 결과.

    Returns:
        dict: 기존 고장(정답이 G 참조) 대상의 맞음·잘못 합침·새 고장으로 쪼갬·보류·오류, 처음 나온 고장(정답 NEW)
            대상의 맞음·잘못 합침·보류·오류와 비율. merge_rate의 분모는 EXISTING으로 답한 전체다.
    """
    existing = [r for r in results if r.expected != NEW]
    new = [r for r in results if r.expected == NEW]
    correct = sum(r.decision == "EXISTING" and r.group_ref == r.expected for r in existing)
    wrong_ref = sum(r.decision == "EXISTING" and r.group_ref != r.expected for r in existing)
    split_new = sum(r.decision == "NEW" for r in existing)
    new_merged = sum(r.decision == "EXISTING" for r in new)
    answered_existing = sum(r.decision == "EXISTING" for r in results)
    return {
        "total": len(results),
        "existing": {
            "total": len(existing),
            "correct": correct,
            "merged_into_other": wrong_ref,
            "split_as_new": split_new,
            "unsure": sum(r.decision == "UNSURE" for r in existing),
            "error": sum(r.decision == "ERROR" for r in existing),
            "accuracy": _rate(correct, len(existing)),
            "split_rate": _rate(split_new, len(existing)),
        },
        "first_seen": {
            "total": len(new),
            "correct_new": sum(r.decision == "NEW" for r in new),
            "merged_into_existing": new_merged,
            "unsure": sum(r.decision == "UNSURE" for r in new),
            "error": sum(r.decision == "ERROR" for r in new),
        },
        "merge_rate": _rate(wrong_ref + new_merged, answered_existing),
        "answered_existing": answered_existing,
    }


def summarize_by(results: list[Result]) -> dict[str, dict[str, object]]:
    """언어(영어·비영어), 증상 문장 포함 여부, 문의 유형(고장 신고·펌웨어 대 그 외)으로 나눠 summarize한다."""
    groups: dict[str, list[Result]] = defaultdict(list)
    for r in results:
        groups["language=en" if r.language == "en" else "language=non_en"].append(r)
        groups[f"symptom_in_text={str(r.symptom_in_text).lower()}"].append(r)
        kind = "fault_report" if r.category in FAULT_REPORTS else "other"
        groups[f"inquiry={kind}"].append(r)
    return {key: summarize(value) for key, value in sorted(groups.items())}


def downstream(
    cases: Iterable[CaseRow],
    tasks: list[Task],
    results: list[Result],
    *,
    human_resolves_unsure: bool = False,
) -> dict[str, object]:
    """AI 배정으로 다시 만든 묶음에 반복 후보 규칙을 적용해 고장 ID 기준과 비교한다.

    평가 대상이 아닌 기록은 고장 ID를 그대로 쓰고, 대상 기록은 고른 묶음의 고장 ID로 바꾼다. 새 고장·보류·오류는
    혼자인 묶음이 된다. 펌웨어 예외는 빼고(배포 이력 없음) 90일·3곳 일반 규칙만 본다.

    human_resolves_unsure가 True면 보류·오류 기록은 사람이 확인해 올바른 묶음에 넣었다고 본다(운영의 사람 확인
    게이트). AI가 EXISTING·NEW로 답한 기록만 AI 판단을 따른다.

    Returns:
        dict: 고장 ID 기준 도달 묶음 수, 같은 도달 시각으로 재현된 수, 시각만 다른 수, 놓친 수, AI 쪽에만 생긴 수.
    """
    rows = list(cases)
    by_case = {t.case.case_id: t for t in tasks}
    chosen = {r.case_id: r for r in results}

    def label(c: CaseRow) -> str:
        r = chosen.get(c.case_id)
        if r is None or (human_resolves_unsure and r.decision in {"UNSURE", "ERROR"}):
            return c.fault_id
        if r.decision == "EXISTING" and r.group_ref is not None:
            return by_case[c.case_id].ref_to_fault[r.group_ref]
        return f"AI-NEW-{c.case_id}"

    def reached(labels: Callable[[CaseRow], str]) -> dict[tuple[str, str], datetime]:
        records = [
            CaseRecord(
                case_id=c.case_id,
                ticket_id=c.ticket_id,
                product_code=c.product_code,
                fault_id=labels(c),
                customer_id=c.customer_id,
                known_at=c.known_at,
            )
            for c in rows
        ]
        as_of = max(c.known_at for c in rows)
        return {
            (f.product_code, f.fault_id): f.reached_at
            for f in fault_candidates(records, [], as_of=as_of)
            if f.reached_at is not None
        }

    base = reached(lambda c: c.fault_id)
    ai = reached(label)
    same = sum(ai.get(k) == v for k, v in base.items())
    moved = sum(k in ai and ai[k] != v for k, v in base.items())
    return {
        "baseline_reached": len(base),
        "reproduced_same_time": same,
        "reproduced_other_time": moved,
        "missed": len(base) - same - moved,
        "ai_only": sum(k not in base for k in ai),
    }


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return 0.0 if norm == 0 else dot / norm


def nearest_group(
    task: Task, vector: Callable[[str], list[float]], new_below: float | None = None
) -> tuple[Predicted, str | None]:
    """임베딩 기준선: 묶음 예시 벡터 평균과 코사인이 가장 큰 묶음을 고른다.

    Args:
        task: 판단 입력.
        vector: 문자열 → 임베딩(호출자가 캐시).
        new_below: 최고 유사도가 이 값보다 작으면 NEW. None이면 묶음이 있을 때 항상 고른다.

    Returns:
        (decision, group_ref): 묶음이 없으면 NEW.
    """
    if not task.payload.groups:
        return "NEW", None
    query = vector(task.payload.query)
    best_ref, best = None, -2.0
    for group in task.payload.groups:
        vectors = [vector(e) for e in group.examples]
        centroid = [sum(values) / len(vectors) for values in zip(*vectors, strict=True)]
        score = cosine(query, centroid)
        if score > best:
            best_ref, best = group.ref, score
    if new_below is not None and best < new_below:
        return "NEW", None
    return "EXISTING", best_ref
