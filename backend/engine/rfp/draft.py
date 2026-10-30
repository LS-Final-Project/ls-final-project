"""근거가 연결된 RFP 제안 검토 초안을 결정적 템플릿으로 만든다(KAN-188)."""

from collections.abc import Sequence
from datetime import datetime
from typing import get_args
from uuid import UUID, uuid5

from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.integration import CHECK_ITEM, HOLDER_TEXT

DISCLAIMER = "시연용 검토본이며 실제 제안서가 아니다."
PATH_TEXT: dict[s.SupplementPath, str] = {
    "OTHER_OWN_PRODUCT": "다른 자사 제품 검토",
    "OWN_DEVELOPMENT": "자사 개발·수행 가능 여부 확인",
    "OWN_TERMS_REVIEW": "자사 상업 조건 변경 검토",
    "ALTERNATIVE_SPEC": "고객 승인 조건의 대체 사양 검토",
    "EXTERNAL_PRODUCT": "외부 제품 조달 검토",
    "TASK_SUBCONTRACT": "과업 위탁 검토",
    "JOINT_PROPOSAL": "공동 제안 검토",
    "CUSTOMER_SPECIFIED_PROCUREMENT": "고객 지정품 조달 검토",
    "NONE": "참여 보류 및 자료 보완 검토",
}
PARTNER_PATHS = frozenset({"EXTERNAL_PRODUCT", "TASK_SUBCONTRACT", "JOINT_PROPOSAL"})
STATUS_TEXT: dict[s.RequirementStatus, str] = {
    "EVIDENCE_FOUND": "해당 요구의 근거 있음(전체 공급 가능 확정 아님)",
    "CONDITIONAL": "조건부 검토",
    "SPEC_GAP": "사양 차이",
    "INSUFFICIENT_EVIDENCE": "근거 부족(미충족 확정 아님)",
}
RESULT_TEXT: dict[s.ComparisonResult, str] = {
    "MEETS": "해당 비교 항목의 근거 일치",
    "DIFFERS": "해당 비교 항목의 차이",
    "NOT_COMPARABLE": "단위·조건·적용 범위 확인 필요",
    "NO_DATA": "비교 자료 부족",
}


def _requirement_evidence(req: s.RequirementDetail) -> list[s.Evidence]:
    result = [s.Evidence(type="requirement", id=str(req.requirement_id))]
    if req.origin.clause_id is not None:
        result.append(s.Evidence(type="clause", id=str(req.origin.clause_id)))
    return result


def _conditions(req: s.RequirementDetail) -> list[str]:
    result = [f"{c.name}: {c.value} {c.unit or ''}".strip() for c in req.spec.conditions]
    result.extend(req.spec.undetermined_items)
    result.extend(req.judgement.unresolved)
    if req.spec.necessity == "CONDITION_BOUND":
        result.append("조건부 필수 요구의 적용 조건 확인")
    if req.judgement.rnd_check in {"PENDING", "WAIVED"}:
        result.append(f"R&D 개발·수행 확인 미완료({req.judgement.rnd_check})")
    return list(dict.fromkeys(result))


def build_draft(
    *,
    project_id: UUID,
    version: int,
    data_origin: s.DataOrigin,
    known_at: datetime,
    requirements: Sequence[s.RequirementDetail],
    integration: s.IntegrationResponsibility,
    comparisons: Sequence[s.Comparison] = (),
    options: Sequence[s.SupplementOptions] = (),
    selections: Sequence[s.SupplementSelected] = (),
    partners: Sequence[s.PartnerCandidate] = (),
    questions: Sequence[s.Question] = (),
) -> s.Draft:
    """동일 프로젝트의 최신 공개 스냅샷에서 근거가 있는 검토 문장만 만든다.

    Args:
        project_id: 호출자가 접근과 소속을 확인한 프로젝트 ID.
        version: 만들 초안 판본. 시간·판본은 호출자가 제공한다.
        data_origin: 프로젝트의 데이터 출처.
        known_at: 초안 기준 시각.
        requirements: 최신 요구 판본 전부. 현행 원문 인용은 상위 처리에서 검증한다.
        integration: 서버가 계산한 통합 책임과 사람이 지정한 책임자 스냅샷.
        comparisons: 요구 판본과 일치하는 자사 비교.
        options: 요구 판본과 일치하는 보완 선택지.
        selections: 최신 사람 선택. 선택 판본은 요구 판본과 독립적이다.
        partners: 근거로 참조할 가상 협력사와 요구별 등급.
        questions: 최신 고객·내부 질문. 질문 판본은 요구 판본과 독립적이다.

    Returns:
        Draft: 여섯 절과 고정 고지를 가진 검토 초안. 모델·DB·시계·난수 호출은 없다.

    Raises:
        ValueError: 중복·누락 참조, 판본 불일치, 근거 없는 요구 또는 모순된 선택 스냅샷.
    """
    reqs = {r.requirement_id: r for r in requirements}
    comps = {c.requirement_id: c for c in comparisons}
    opts = {o.requirement_id: o for o in options}
    chosen = {v.requirement_id: v for v in selections}
    candidates = {p.candidate_code: p for p in partners}
    asks = {q.question_id: q for q in questions}
    if any(
        len(index) != len(rows)
        for index, rows in (
            (reqs, requirements),
            (comps, comparisons),
            (opts, options),
            (chosen, selections),
            (candidates, partners),
            (asks, questions),
        )
    ):
        raise ValueError("RFP_DRAFT_DUPLICATE_REFERENCE")
    clauses = {r.origin.clause_id for r in requirements if r.origin.clause_id is not None}
    for row in (*requirements, *comparisons, *options, *selections, *questions):
        if row.data_origin != data_origin:
            raise ValueError("RFP_DRAFT_DATA_ORIGIN_MISMATCH")
    for req in requirements:
        if not req.origin.quote.strip() or not req.code.strip():
            raise ValueError("RFP_DRAFT_REQUIREMENT_EVIDENCE_MISSING")
        if not set(req.links.question_ids).issubset(asks) or not set(
            req.links.partner_candidate_ids
        ).issubset(candidates):
            raise ValueError("RFP_DRAFT_UNKNOWN_REFERENCE")
        if any(asks[key].requirement_id != req.requirement_id for key in req.links.question_ids):
            raise ValueError("RFP_DRAFT_UNKNOWN_REFERENCE")
    for comparison_row in comparisons:
        if comparison_row.requirement_id not in reqs:
            raise ValueError("RFP_DRAFT_UNKNOWN_REFERENCE")
        if comparison_row.requirement_version != reqs[comparison_row.requirement_id].version:
            raise ValueError("RFP_DRAFT_VERSION_MISMATCH")
        codes = [c.product_code for c in comparison_row.candidates]
        if len(set(codes)) != len(codes) or any(not code.strip() for code in codes):
            raise ValueError("RFP_DRAFT_DUPLICATE_REFERENCE")
        if any(
            not c.attribute_match.comparable and c.attribute_match.result in {"MEETS", "DIFFERS"}
            for c in comparison_row.candidates
        ):
            raise ValueError("RFP_DRAFT_COMPARISON_INCONSISTENT")
    products = {c.product_code for comp in comparisons for c in comp.candidates}
    for partner in partners:
        if not partner.candidate_code.strip():
            raise ValueError("RFP_DRAFT_UNKNOWN_REFERENCE")
        grade_ids = [g.requirement_id for g in partner.grades]
        if len(set(grade_ids)) != len(grade_ids) or not set(grade_ids).issubset(reqs):
            raise ValueError("RFP_DRAFT_INVALID_PARTNER_GRADES")
    for req in requirements:
        if any(
            not any(g.requirement_id == req.requirement_id for g in candidates[code].grades)
            for code in req.links.partner_candidate_ids
        ):
            raise ValueError("RFP_DRAFT_INVALID_PARTNER_GRADES")
    for opt in options:
        if opt.requirement_id not in reqs:
            raise ValueError("RFP_DRAFT_UNKNOWN_REFERENCE")
        if opt.version != reqs[opt.requirement_id].version:
            raise ValueError("RFP_DRAFT_VERSION_MISMATCH")
        if opt.permission_clause_id is not None and opt.permission_clause_id not in clauses:
            raise ValueError("RFP_DRAFT_UNKNOWN_REFERENCE")
        paths = [v.path for v in opt.options]
        if len(set(paths)) != len(paths):
            raise ValueError("RFP_DRAFT_DUPLICATE_REFERENCE")
        for option in opt.options:
            own_comparison = comps.get(opt.requirement_id)
            available = (
                candidates.keys()
                if option.path in PARTNER_PATHS
                else {c.product_code for c in own_comparison.candidates}
                if own_comparison
                else set()
            )
            if not set(option.candidate_codes).issubset(available):
                raise ValueError("RFP_DRAFT_UNKNOWN_REFERENCE")
            if option.path in PARTNER_PATHS:
                if option.allowed and opt.customer_permission == "FORBIDDEN":
                    raise ValueError("RFP_DRAFT_BLOCKED_SELECTION")
                for code in option.candidate_codes:
                    if not any(
                        g.requirement_id == opt.requirement_id for g in candidates[code].grades
                    ):
                        raise ValueError("RFP_DRAFT_INVALID_PARTNER_GRADES")
    for selection_row in selections:
        option_set = opts.get(selection_row.requirement_id)
        selected_option = (
            next((v for v in option_set.options if v.path == selection_row.path), None)
            if option_set
            else None
        )
        if selected_option is None or not selected_option.allowed or selected_option.blocked_reason:
            raise ValueError("RFP_DRAFT_BLOCKED_SELECTION")
        if not set(selection_row.candidate_codes).issubset(selected_option.candidate_codes):
            raise ValueError("RFP_DRAFT_UNKNOWN_REFERENCE")
        if selected_option.path in PARTNER_PATHS:
            for code in selection_row.candidate_codes:
                partner = candidates[code]
                grade = next(
                    g for g in partner.grades if g.requirement_id == selection_row.requirement_id
                )
                if partner.overall_grade == "EXCLUDED" or grade.grade == "EXCLUDED":
                    raise ValueError("RFP_DRAFT_BLOCKED_SELECTION")
    for question in questions:
        if question.requirement_id is not None and question.requirement_id not in reqs:
            raise ValueError("RFP_DRAFT_UNKNOWN_REFERENCE")
        if question.origin.clause_id is not None and question.origin.clause_id not in clauses:
            raise ValueError("RFP_DRAFT_UNKNOWN_REFERENCE")
        if question.requirement_id is not None and question.origin.clause_id is not None:
            if question.origin.clause_id != reqs[question.requirement_id].origin.clause_id:
                raise ValueError("RFP_DRAFT_QUESTION_CLAUSE_MISMATCH")
        if not question.question_text.strip() or (question.target == "INTERNAL") != bool(
            question.target_dept
        ):
            raise ValueError("RFP_DRAFT_INVALID_QUESTION")
    if len(integration.basis_clause_ids) != len(integration.basis_clause_nos):
        raise ValueError("RFP_DRAFT_INTEGRATION_BASIS_MISMATCH")
    if integration.required != bool(integration.basis_clause_ids):
        raise ValueError("RFP_DRAFT_INTEGRATION_BASIS_MISMATCH")
    for clause_id, clause_no in zip(
        integration.basis_clause_ids, integration.basis_clause_nos, strict=True
    ):
        if not any(
            r.origin.clause_id == clause_id and r.origin.clause_no == clause_no
            for r in requirements
        ):
            raise ValueError("RFP_DRAFT_INTEGRATION_BASIS_MISMATCH")
    if not set(integration.holder_candidate_codes).issubset(candidates):
        raise ValueError("RFP_DRAFT_UNKNOWN_REFERENCE")

    sections: dict[s.DraftSection, list[s.Sentence]] = {key: [] for key in get_args(s.DraftSection)}
    registry = (
        {("requirement", str(key)) for key in reqs}
        | {("clause", str(key)) for key in clauses}
        | {("product", key) for key in products}
        | {("partner", key) for key in candidates}
        | {("question", str(key)) for key in asks}
    )

    def emit(
        section: s.DraftSection,
        text: str,
        evidence: list[s.Evidence],
        *,
        conditions: Sequence[str] = (),
        unconfirmed: bool = False,
        rereview: bool = False,
    ) -> None:
        unique = sorted({(e.type, e.id) for e in evidence})
        if not unique or not set(unique).issubset(registry):
            raise ValueError("RFP_DRAFT_SENTENCE_EVIDENCE_MISSING")
        sections[section].append(
            s.Sentence(
                sentence_id=uuid5(project_id, repr((version, section, text, unique))),
                text=text,
                evidence=[s.Evidence(type=kind, id=ref) for kind, ref in unique],
                conditions=list(dict.fromkeys(c for c in conditions if c.strip())),
                unconfirmed=unconfirmed or bool(conditions) or rereview,
                needs_rereview=rereview,
                evidence_missing=False,
                provenance_kind="system_record",
            )
        )

    for req in sorted(requirements, key=lambda r: str(r.requirement_id)):
        evidence = _requirement_evidence(req)
        conditions = _conditions(req)
        unconfirmed = (
            req.judgement.status != "EVIDENCE_FOUND" or bool(conditions) or req.needs_rereview
        )
        label = f"요구 {req.code}"
        emit(
            "CUSTOMER_GOAL_SCOPE",
            f"{label}의 제안 검토 범위(고객 요구 인용): 「{req.origin.quote}」.",
            evidence,
            conditions=conditions,
            unconfirmed=unconfirmed,
            rereview=req.needs_rereview,
        )
        comp = comps.get(req.requirement_id)
        if comp:
            for candidate in sorted(comp.candidates, key=lambda c: c.product_code):
                match = candidate.attribute_match
                if not any(e.document.strip() for e in match.evidence):
                    continue
                candidate_conditions = list(conditions)
                if comp.search_trace.search_failed:
                    candidate_conditions.append("문서 검색 실패: 문서 근거 추가 확인 필요")
                candidate_conditions.extend(
                    f"후보 조건 {c.name}: {c.value} {c.unit or ''}".strip()
                    for c in match.our_conditions
                )
                for proof in match.evidence:
                    candidate_conditions.extend(v for v in (proof.applicability, proof.limits) if v)
                candidate_conditions.append("제품의 실제 공급·납기·적용 모델은 별도 확인")
                emit(
                    "OUR_RESPONSE",
                    f"{label}의 자사 후보 「{candidate.product_code}」: 「{match.matched_attribute or req.spec.attribute or '비교 항목'}」의 {RESULT_TEXT[match.result]}. 요구값 「{req.spec.value or '미정'} {req.spec.unit or ''}」, 후보값 「{match.our_value or '자료 없음'} {match.our_unit or ''}」. 전체 요구 충족이나 공급 확약이 아님.",
                    [*evidence, s.Evidence(type="product", id=candidate.product_code)],
                    conditions=candidate_conditions,
                    unconfirmed=True,
                    rereview=req.needs_rereview,
                )
        if unconfirmed or req.judgement.status == "SPEC_GAP":
            section: s.DraftSection = (
                "DEVIATIONS" if req.judgement.status == "SPEC_GAP" else "ASSUMPTIONS_EXCLUSIONS"
            )
            emit(
                section,
                f"{label}: {STATUS_TEXT[req.judgement.status]}. 판단 근거 기록: 「{req.judgement.reason}」.",
                evidence,
                conditions=conditions,
                unconfirmed=True,
                rereview=req.needs_rereview,
            )
        option_set = opts.get(req.requirement_id)
        if option_set:
            selection = chosen.get(req.requirement_id)
            for option in sorted(option_set.options, key=lambda v: (v.rank, v.path)):
                if not option.allowed or option.blocked_reason:
                    continue
                is_selected = selection is not None and selection.path == option.path
                path_conditions = [*conditions, *option.conditions_to_confirm]
                if (
                    option.path == "ALTERNATIVE_SPEC"
                    or option_set.customer_permission == "UNSTATED"
                ):
                    path_conditions.append("고객 허용·승인 조건 확인")
                if option.gate.requires == "OWN_DEVELOPMENT_CHECK":
                    path_conditions.append(f"R&D 개발·수행 확인 상태: {option.gate.status}")
                if is_selected and selection is not None:
                    path_conditions.extend(selection.conditions)
                    path_conditions.append(
                        f"사람 선택의 역할 분담 기록(이행 확약 아님): {selection.role_split}"
                    )
                path_evidence = list(evidence)
                if option_set.permission_clause_id is not None:
                    path_evidence.append(
                        s.Evidence(type="clause", id=str(option_set.permission_clause_id))
                    )
                destination: s.DraftSection = (
                    "PARTNER_SUPPLEMENT" if option.path in PARTNER_PATHS else "DEVIATIONS"
                )
                if option.path == "NONE":
                    destination = "ASSUMPTIONS_EXCLUSIONS"
                emit(
                    destination,
                    f"{label}의 보완 경로 검토안: {PATH_TEXT[option.path]}. {'사람 선택 기록 있음' if is_selected else '미선택 선택지'}; 이행·공급·납기 확약이 아님.",
                    path_evidence,
                    conditions=path_conditions,
                    unconfirmed=True,
                    rereview=req.needs_rereview,
                )
                for code in sorted(option.candidate_codes if option.path in PARTNER_PATHS else []):
                    partner = candidates[code]
                    grade = next(
                        g for g in partner.grades if g.requirement_id == req.requirement_id
                    )
                    if partner.overall_grade == "EXCLUDED" or grade.grade == "EXCLUDED":
                        continue
                    emit(
                        "PARTNER_SUPPLEMENT",
                        f"{label}의 {PATH_TEXT[option.path]} 경로의 가상 협력사 검토 후보 「{code}」: 요구별 등급 {grade.grade}, 확인 상태 {partner.confirmation_status}. 실제 공급·납기 확약이 아님.",
                        [*path_evidence, s.Evidence(type="partner", id=code)],
                        conditions=[
                            *path_conditions,
                            *partner.conditions_to_confirm,
                            *grade.mandatory_check.unknown,
                        ],
                        unconfirmed=True,
                        rereview=req.needs_rereview,
                    )
        if req.judgement.next_owner_field:
            emit(
                "QUESTIONS_NEXT_ACTIONS",
                f"{label}의 다음 확인 담당: {req.judgement.next_owner_field}.",
                evidence,
                conditions=conditions,
                unconfirmed=True,
                rereview=req.needs_rereview,
            )

    if integration.required:
        basis = [r for r in requirements if r.origin.clause_id in integration.basis_clause_ids]
        evidence = [e for r in basis for e in _requirement_evidence(r)]
        evidence.extend(
            s.Evidence(type="partner", id=code) for code in integration.holder_candidate_codes
        )
        unresolved = integration.holder == "UNRESOLVED"
        conditions = [*integration.unresolved]
        if unresolved:
            conditions.append(CHECK_ITEM)
            conditions.extend(p.condition for p in integration.fallback_paths)
        emit(
            "ASSUMPTIONS_EXCLUSIONS",
            f"유닛 전체의 조립·공장 시험·연동·보증을 맡을 통합 책임자: {HOLDER_TEXT[integration.holder]}. {'통합 책임 미확보로 유닛 공급 가능 여부를 확정하지 않음' if unresolved else '책임자 지정 기록이며 유닛 공급·납기 확약이 아님'}.",
            evidence,
            conditions=conditions,
            unconfirmed=unresolved,
            rereview=any(r.needs_rereview for r in basis),
        )
    for question in sorted(questions, key=lambda q: str(q.question_id)):
        question_evidence = [s.Evidence(type="question", id=str(question.question_id))]
        related = reqs.get(question.requirement_id) if question.requirement_id else None
        if related:
            question_evidence.extend(_requirement_evidence(related))
        elif question.origin.clause_id is not None:
            question_evidence.append(s.Evidence(type="clause", id=str(question.origin.clause_id)))
        target = (
            f"내부 다음 행동(담당 부서 {question.target_dept})"
            if question.target == "INTERNAL"
            else "고객 확인 질문"
        )
        state = {
            "DRAFT": "초안·미발송",
            "OPEN": "확인된 질문·발송 여부 별도 확인",
            "REPLIED": "회신 수신 기록·반영 여부 별도 확인",
        }[question.status]
        emit(
            "QUESTIONS_NEXT_ACTIONS",
            f"{target} [{state}]: 「{question.question_text}」.",
            question_evidence,
            unconfirmed=question.status != "REPLIED",
            rereview=bool(related and related.needs_rereview),
        )
        assumption = question.internal_assumption
        if assumption and assumption.shown_in_draft and assumption.value.strip():
            emit(
                "ASSUMPTIONS_EXCLUSIONS",
                f"회신 전 내부 가정(충족 판정에 사용 금지): 「{assumption.value}」.",
                question_evidence,
                conditions=[assumption.value],
                unconfirmed=True,
                rereview=bool(related and related.needs_rereview),
            )
    return s.Draft(
        project_id=project_id,
        version=version,
        data_origin=data_origin,
        known_at=known_at,
        derivation_kind="DERIVED",
        sections=[s.Section(section=key, sentences=rows) for key, rows in sections.items()],
        disclaimer=DISCLAIMER,
        evidence_missing_count=sum(v.evidence_missing for rows in sections.values() for v in rows),
    )
