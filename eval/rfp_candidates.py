"""RFP 후보 비교 하네스(KAN-191). 문서마다 처리 함수와 같은 엔진 경로로 요구를 뽑고 판정해 정답과 비교한다.

처리 함수(`backend/api/tft/worker_processor.py`)와 같은 매핑(`to_comparison`·`to_grading`)·절 규칙(`sections_of` 대신
정답이 고정한 평가 절)·비교·보완·등급·통합 책임 함수를 부른다. DB·파일·모델에 직접 접근하지 않고, 조항 트리와
모델 호출은 호출자가 넘긴다(`scripts/rfp_candidate_run.py`).

- 정답(`GoldDocument`)은 저장소 밖 파일이다. 평가 절, 정답 요구(상태·필수 여부·경로·사유), 협력사 후보 정답 등급과
  검토 후보가 표시해야 할 미확인 항목, 조립품 공급 조항, 사람이 프로젝트에 입력하는 RFP 맥락을 담는다.
- 결과 파일은 `eval_adopt --task rfp` 입력 형식이다(meta에 `documents`, 후보 행에 게이트 키 9개). 측정 도구가 없는
  키(근거 정확성·초안 근거 추적)는 None이라 NOT_EVALUABLE이다.
- 결과에는 원문·인용·값을 넣지 않는다. 키(조항 번호·속성)와 건수만 남긴다.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from backend.api.tft.partners import grading
from backend.api.tft.partners.schemas import PartnerProfile
from backend.api.tft.rfp import comparison, integration, supplement
from backend.api.tft.rfp.schemas import Necessity, RequirementStatus, SupplementPath
from backend.api.tft.worker_processor import to_comparison, to_grading
from backend.engine.rfp.clauses import ClauseTree
from backend.engine.rfp.requirements import (
    EXTRACTION_VERSION,
    ExtractedRequirement,
    build_inputs,
    extract_section,
)
from backend.shared.ai import Generation, Usage
from eval.rfp_metrics import (
    GoldRequirement,
    PredictedRequirement,
    gate_values,
    gold_fingerprint,
    summarize_candidate_grades,
    summarize_integration_responsibility,
    summarize_requirement_extraction,
    unconfirmed_items,
)

RunLabel = Literal["FIRST", "REMEASURE"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GoldRequirementIn(Strict):
    clause_no: str
    attribute: str | None
    expected_status: RequirementStatus
    necessity: Necessity | None = None
    expected_path: SupplementPath | None = None
    expected_reason: str | None = None


class GoldCandidate(Strict):
    grade: grading.Grade
    # 정답 REVIEW 후보가 '확인 필요'로 표시해야 할 항목 이름(eval.rfp_metrics.unconfirmed_items와 같은 이름).
    unconfirmed: list[str] | None = None


class GoldDocument(Strict):
    """문서 하나의 정답. sections는 측정 전에 고정한 평가 절이다(기획서 3.2: 문서마다 장비 1~2개 절)."""

    document_number: int
    sections: list[str]
    requirements: list[GoldRequirementIn]
    candidates: dict[str, GoldCandidate] = {}
    assembly_clauses: list[str] = []
    # 사람이 프로젝트에 입력하는 RFP 맥락(고객 제조사 목록·목록 외 허용·공급 지역·조립품 공급 여부).
    context: grading.RfpContext = grading.RfpContext(
        manufacturer_list=None,
        off_list_permission="UNSTATED",
        supply_region=None,
        requires_integrator=False,
    )


class GoldFile(Strict):
    gold_version: str
    documents: list[GoldDocument]

    @model_validator(mode="after")
    def _unique_documents(self) -> "GoldFile":
        # 번호가 겹치면 뒤 항목이 앞 항목을 조용히 덮어 점수가 바뀐다(#318 리뷰 P3).
        numbers = [d.document_number for d in self.documents]
        if len(numbers) != len(set(numbers)):
            raise ValueError("EVAL_GOLD_DUPLICATE_DOCUMENT")
        return self


@dataclass
class DocumentRun:
    """문서 하나의 실행 결과(원문 없음)."""

    document_number: int
    predicted: list[PredictedRequirement] = field(default_factory=list)
    grades: dict[str, grading.Grade] = field(default_factory=dict)
    unconfirmed: dict[str, list[str]] = field(default_factory=dict)
    integration: dict | None = None
    section_outcomes: dict[str, str] = field(default_factory=dict)
    usage: Usage = field(default_factory=lambda: Usage(0, 0, 0, 0, 0))
    model_versions: set[str] = field(default_factory=set)


def _add(a: Usage, b: Usage) -> Usage:
    def plus(x: int | None, y: int | None) -> int | None:
        return None if x is None or y is None else x + y

    return Usage(
        plus(a.input_tokens, b.input_tokens),
        plus(a.output_tokens, b.output_tokens),
        plus(a.thought_tokens, b.thought_tokens),
        plus(a.cached_input_tokens, b.cached_input_tokens),
        plus(a.total_tokens, b.total_tokens),
    )


def _predicted(item: ExtractedRequirement, outcome: comparison.Outcome) -> PredictedRequirement:
    """추출 요구와 비교 결과 → 채점 입력. 경로는 시스템이 1순위로 내미는 열린 선택지다(사람이 고르기 전 제안)."""
    options = supplement.options_for(
        outcome, "UNSTATED", designated=item.designated_model is not None
    )
    allowed = sorted((o for o in options.options if o.allowed), key=lambda o: o.rank)
    evidence = frozenset(
        f"{e.document}:{e.clause}" for c in outcome.candidates for e in c.evidence if c.comparable
    )
    return PredictedRequirement(
        clause_no=item.clause_no,
        attribute=item.attribute,
        status=outcome.status,
        necessity=item.necessity,
        path=allowed[0].path if allowed and outcome.status != "EVIDENCE_FOUND" else None,
        evidence_block_ids=evidence,
        value_text=item.value,
        reason=outcome.reason,
    )


def run_document(
    tree: ClauseTree,
    gold: GoldDocument,
    provider: Callable[[str], Generation],
    products: Sequence[comparison.Product],
    profiles: Sequence[PartnerProfile],
    today: date,
) -> DocumentRun:
    """문서 하나를 평가 절만 처리 함수와 같은 경로로 돌린다.

    Args:
        tree: 원문 조항 트리.
        gold: 그 문서의 정답(평가 절·RFP 맥락을 쓴다. 정답 요구는 여기서 보지 않는다).
        provider: 추출 모델 호출.
        products: 자사 제품.
        profiles: 가상 협력사 프로필.
        today: 인증 유효기한 기준일.

    Returns:
        DocumentRun: 채점 입력과 절별 결과·사용량. 너무 긴 절은 RFP_SECTION_TOO_LONG으로 남기고 건너뛴다.
    """
    run = DocumentRun(document_number=gold.document_number)
    extracted: list[ExtractedRequirement] = []
    for section in gold.sections:
        try:
            [item] = build_inputs(tree, [section])
        except ValueError as error:
            run.section_outcomes[section] = str(error)
            continue
        result = extract_section(item, provider)
        run.section_outcomes[section] = result.outcome
        run.usage = _add(run.usage, result.usage)
        if result.model_version:
            run.model_versions.add(result.model_version)
        extracted += result.requirements
    # 운영 처리 함수(worker_processor)와 같은 호출이다: 요구 장비 계열의 후보 안에서만 비교한다.
    outcomes = [
        comparison.compare_in_family(to_comparison(r), r.equipment_family, products)
        for r in extracted
    ]
    run.predicted = [_predicted(r, o) for r, o in zip(extracted, outcomes, strict=True)]
    targets = [
        g
        for index, (r, o) in enumerate(zip(extracted, outcomes, strict=True))
        if o.status == "SPEC_GAP" and (g := to_grading(UUID(int=index + 1), r)) is not None
    ]
    if targets:
        for profile in profiles:
            summary, grades = grading.grade_candidate(profile, targets, gold.context, today)
            run.grades[profile.candidate_code] = summary.overall_grade
            run.unconfirmed[profile.candidate_code] = sorted(
                unconfirmed_items(summary.model_dump(), [g.model_dump() for g in grades])
            )
    run.integration = integration.integration_responsibility(
        [
            integration.Requirement(
                clause_no=r.clause_no, task=r.task, assembly_clause=r.assembly_clause
            )
            for r in extracted
        ]
    ).model_dump()
    return run


def summarize(gold: GoldFile, runs: Sequence[DocumentRun], run_label: RunLabel) -> dict:
    """정답 전체와 실행 결과를 지표로 모은다. 사례 키는 'D{문서 번호}'다.

    Args:
        gold: 정답 파일.
        runs: 문서별 실행 결과. 정답에 없는 문서는 받지 않는다.
        run_label: FIRST(평가 문서 첫 실행, 공식) 또는 REMEASURE(결함 수정 뒤 재측정, 참고값).

    Returns:
        dict: extraction·grades·integration 요약과 게이트 값 9개(gate_values), gold_fingerprint.
    """
    by_number = {d.document_number: d for d in gold.documents}
    if any(r.document_number not in by_number for r in runs):
        raise ValueError("EVAL_INVALID_INPUT")
    case = {r.document_number: f"D{r.document_number}" for r in runs}
    gold_requirements = {
        case[r.document_number]: [
            GoldRequirement(**g.model_dump()) for g in by_number[r.document_number].requirements
        ]
        for r in runs
    }
    extraction = summarize_requirement_extraction(
        gold_requirements, {case[r.document_number]: r.predicted for r in runs}, run_label=run_label
    )
    gold_grades = {
        case[r.document_number]: {
            code: c.grade for code, c in by_number[r.document_number].candidates.items()
        }
        for r in runs
    }
    # 정답에 없는 후보(프로필은 있지만 정답을 만들지 않은 후보)는 채점하지 않는다.
    predicted_grades = {
        case[r.document_number]: {
            code: grade
            for code, grade in r.grades.items()
            if code in gold_grades[case[r.document_number]]
        }
        for r in runs
    }
    gold_unconfirmed = {
        case[r.document_number]: {
            code: c.unconfirmed
            for code, c in by_number[r.document_number].candidates.items()
            if c.unconfirmed is not None
        }
        for r in runs
    }
    grades = summarize_candidate_grades(
        gold_grades,
        predicted_grades,
        gold_unconfirmed,
        {case[r.document_number]: r.unconfirmed for r in runs},
    )
    integration_summary = summarize_integration_responsibility(
        {case[r.document_number]: by_number[r.document_number].assembly_clauses for r in runs},
        {case[r.document_number]: r.integration or {} for r in runs},
    )
    return {
        "gold_fingerprint": gold_fingerprint(gold_requirements),
        "extraction": extraction,
        "grades": grades,
        "integration": integration_summary,
        "gates": gate_values(extraction, grades, integration_summary),
    }


def result_file(
    summary: Mapping,
    runs: Sequence[DocumentRun],
    *,
    name: str,
    model: str,
    run_label: RunLabel,
    split: str,
    manifest_version: str,
    gold_version: str,
    started_at: str,
    git_sha: str,
) -> dict:
    """eval_adopt --task rfp 입력 형식의 결과 파일. 원문·인용·값은 넣지 않는다.

    Args:
        summary: summarize 결과.
        runs: 문서별 실행 결과.
        name: 후보 이름.
        model: 추출 모델 ID.
        run_label: FIRST 또는 REMEASURE(참고값).
        split: 문서 용도(manifest split). 한 실행의 문서는 용도가 같다(회귀 15번은 따로 돌린다).
        manifest_version: 문서 manifest 판본.
        gold_version: 정답 파일 판본.
        started_at: 실행 시작 시각(ISO 8601).
        git_sha: 실행한 커밋.

    Returns:
        dict: {"meta": {...}, "result": {"candidates": [{"name", 게이트 키 9개}]}, "report": {...}}.
    """
    usage = Usage(0, 0, 0, 0, 0)
    for run in runs:
        usage = _add(usage, run.usage)
    extraction, grades = summary["extraction"], summary["grades"]
    return {
        "meta": {
            "documents": len(runs),
            "document_numbers": [r.document_number for r in runs],
            "candidates": [
                {"name": name, "model": model, "extraction_version": EXTRACTION_VERSION}
            ],
            "run_label": run_label,
            "split": split,
            # 평가 문서 첫 실행만 공식 점수다. 결함 수정 뒤 재측정과 개발·회귀 문서 실행은 참고값이다(KAN-191).
            "official": run_label == "FIRST" and split == "evaluation",
            "manifest_version": manifest_version,
            "gold_version": gold_version,
            "gold_fingerprint": summary["gold_fingerprint"],
            "started_at": started_at,
            "git_sha": git_sha,
            "model_versions": sorted({v for r in runs for v in r.model_versions}),
            "usage": {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "thought_tokens": usage.thought_tokens,
            },
            "counts": {
                "recall": extraction["extraction"]["recall_count"],
                "precision": extraction["extraction"]["precision_count"],
                "status_match": extraction["status_match"]["count"],
                "partner_mandatory_confirmed": grades["mandatory_confirmed_count"],
                "partner_review_listed": (grades["review_unconfirmed_listed"] or {}).get("count"),
                "integration": summary["integration"]["count"],
            },
        },
        "result": {"candidates": [{"name": name, **summary["gates"]}]},
        "report": {
            "sections": {str(r.document_number): r.section_outcomes for r in runs},
            "status": extraction["status"],
            "critical_errors": extraction["critical_errors"],
            "grades": grades["grades"],
            "review_unconfirmed_missing": (grades["review_unconfirmed_listed"] or {}).get(
                "missing"
            ),
            "excluded_recommended": grades["excluded_recommended"],
            "integration_missed": summary["integration"]["missed"],
        },
    }
