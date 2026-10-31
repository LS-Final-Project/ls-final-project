"""근거가 연결된 RFP 제안 검토 초안을 결정적 템플릿으로 만든다(KAN-188)."""

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import get_args
from uuid import UUID, uuid5

from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.integration import CHECK_ITEM, HOLDER_TEXT
from backend.engine.rfp.vocabulary import (
    ATTRIBUTE_KEYS,
    CONDITION_NAMES,
    check_text,
    condition_text,
)

DISCLAIMER = "검토용 초안입니다. 고객 제출 전 담당자 확인이 필요합니다."
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


MAX_SENTENCES_PER_SECTION = 8
MAX_REPRESENTATIVES = 3
REPRESENTATIVE_CHARS = 120
COMPARISON_REPRESENTATIVE_CHARS = 1000
COMPARISON_VALUE_CHARS = 900
OWNER_TEXT = {
    "SALES": "영업 담당",
    "TECH": "기술 검토 담당",
    "PURCHASE": "구매 담당",
    "CUSTOMER": "고객",
    "RND": "연구개발 담당",
    "OTHER": "담당 부서 확인",
}
RND_TEXT = {
    "NOT_REQUIRED": "해당 없음",
    "PENDING": "확인 대기",
    "CONFIRMED_FEASIBLE": "수행 가능 확인 기록",
    "CONFIRMED_NOT_FEASIBLE": "수행 불가 확인 기록",
    "CONFIRMED_SCHEDULE_MISMATCH": "일정 불일치 확인 기록",
    "WAIVED": "확인 절차 생략·사유 확인 필요",
}
GRADE_TEXT = {
    "MANDATORY_CONFIRMED": "필수 항목 확인 기록",
    "REVIEW": "검토 필요",
    "REFERENCE_ONLY": "참고용",
    "EXCLUDED": "제외",
}
CONFIRMATION_TEXT = {
    "UNCONFIRMED": "미확인",
    "CONTACT_PLANNED": "접촉 예정",
    "CONFIRMED": "확인 기록 있음",
}
# 알려진 내부 키에만 적용한다. 일반 원문·숫자·규격·제품/장비 이름을 추측해서 지우지 않는다.
DISPLAY_CODES = {key: key.replace("_", " ") for key in ATTRIBUTE_KEYS | CONDITION_NAMES}
DISPLAY_CODES.update(
    STATUS_TEXT | PATH_TEXT | OWNER_TEXT | RND_TEXT | GRADE_TEXT | CONFIRMATION_TEXT
)
DISPLAY_CODES.update(
    {"contract_scope": "계약 범위 확인", "MV_EQUIP": "고압 장비", "LV_BREAKER": "저압 차단기"}
)
DISPLAY_CODES = {key: label for key, label in DISPLAY_CODES.items() if "_" in key}
CODE_WORD = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    + "|".join(
        re.escape(key) for key in sorted(DISPLAY_CODES, key=lambda value: (-len(value), value))
    )
    + r")(?![A-Za-z0-9_])"
)


def _display(text: str) -> str:
    return CODE_WORD.sub(lambda match: DISPLAY_CODES[match[0]], text)


def _sample(text: str, limit: int = REPRESENTATIVE_CHARS, *, display_codes: bool = True) -> str:
    text = " ".join(_display(text).split()) if display_codes else text
    if len(text) <= limit:
        return text
    prefix = text[:limit].rsplit(" ", 1)[0] if " " in text[:limit] else text[: limit - 1]
    return prefix + "…"


def _comparison_sample(
    req: s.RequirementDetail, candidate: s.ComparisonCandidate
) -> tuple[str, list[str]]:
    """비교값을 먼저 보존한다. 예산보다 긴 값/식별자는 자르지 않고 원문 조건으로 연결한다."""
    match = candidate.attribute_match
    wanted = f"{req.spec.value or '미정'} {req.spec.unit or ''}".strip()
    offered = f"{match.our_value or '자료 없음'} {match.our_unit or ''}".strip()
    value_text = f"요구 {wanted}, 후보 {offered}"
    shortened = len(value_text) > COMPARISON_VALUE_CHARS
    if shortened:
        if len(wanted) <= len(offered):
            offered = "[긴 후보값: 조건 원문 확인]"
            if len(f"요구 {wanted}, 후보 {offered}") > COMPARISON_VALUE_CHARS:
                wanted = "[긴 요구값: 조건 원문 확인]"
        else:
            wanted = "[긴 요구값: 조건 원문 확인]"
            if len(f"요구 {wanted}, 후보 {offered}") > COMPARISON_VALUE_CHARS:
                offered = "[긴 후보값: 조건 원문 확인]"
        value_text = f"요구 {wanted}, 후보 {offered}"
    attribute = _display(req.spec.attribute or "비교 항목")
    attribute_note = "[긴 속성명: 조건 원문 확인]"
    minimum_attribute = attribute if len(attribute) <= len(attribute_note) else attribute_note
    product = candidate.product_code
    primary = f"제품 {product} · {value_text}"
    if len(primary + " · 속성 " + minimum_attribute) > COMPARISON_REPRESENTATIVE_CHARS:
        product = "[긴 제품 식별명: 근거·조건 원문 확인]"
        primary = f"제품 {product} · {value_text}"
        shortened = True
    if len(primary + " · 속성 " + attribute) > COMPARISON_REPRESENTATIVE_CHARS:
        attribute = attribute_note
        shortened = True
    raw = []
    if shortened:
        raw.append(
            f"자사 비교 원문: 제품 「{candidate.product_code}」; 속성 「{req.spec.attribute if req.spec.attribute is not None else '미정'}」; "
            f"요구값 「{req.spec.value if req.spec.value is not None else '미정'}」/단위 「{req.spec.unit if req.spec.unit is not None else '미정'}」; "
            f"후보값 「{match.our_value if match.our_value is not None else '자료 없음'}」/단위 「{match.our_unit if match.our_unit is not None else '미정'}」"
        )
    return primary + " · 속성 " + attribute, raw


@dataclass
class _Summary:
    key: tuple[str, ...]
    title: str
    priority: int = 9
    evidence: set[tuple[s.EvidenceType, str]] = field(default_factory=set)
    conditions: set[str] = field(default_factory=set)
    representatives: list[tuple[tuple[str, ...], str]] = field(default_factory=list)
    unconfirmed: bool = False
    rereview: bool = False

    def include(self, other: "_Summary") -> None:
        self.evidence.update(other.evidence)
        self.conditions.update(other.conditions)
        self.representatives.extend(other.representatives)
        self.unconfirmed |= other.unconfirmed
        self.rereview |= other.rereview
        self.priority = min(self.priority, other.priority)


def _requirement_evidence(req: s.RequirementDetail) -> list[s.Evidence]:
    result = [s.Evidence(type="requirement", id=str(req.requirement_id))]
    if req.origin.clause_id is not None:
        result.append(s.Evidence(type="clause", id=str(req.origin.clause_id)))
    return result


def _conditions(req: s.RequirementDetail) -> list[str]:
    result = [
        _display(f"{condition_text(c.name)}: {c.value} {c.unit or ''}".strip())
        for c in req.spec.conditions
    ]
    if req.spec.tolerance is not None:
        result.append(f"요구 허용 오차(원문): {req.spec.tolerance}")
    if req.needs_review:
        result.append("추출 결과의 원문·허용 오차 등 검토 필요")
    result.extend(_display(value) for value in req.spec.undetermined_items)
    result.extend(_display(value) for value in req.judgement.unresolved)
    if req.spec.necessity == "CONDITION_BOUND":
        result.append("조건부 필수 요구의 적용 조건 확인")
    if req.judgement.rnd_check in {"PENDING", "WAIVED"}:
        result.append(f"R&D 개발·수행 확인 미완료({RND_TEXT[req.judgement.rnd_check]})")
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
    replaced: Sequence[s.Section] = (),
    clause_ids: Collection[UUID] = (),
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
        replaced: 사람이 고친 기존 자동 문장. 같은 절의 포함 근거·조건 영역만 생성에서 제외한다.
        clause_ids: 프로젝트 문서의 조항 ID. 요구 없는 질문의 조항을 대조한다.

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
    # 요구가 뽑히지 않은 조항의 질문도 있다. 프로젝트 조항이면 유효하다.
    clauses = {r.origin.clause_id for r in requirements if r.origin.clause_id is not None}
    clauses |= set(clause_ids)
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
        # 승인은 판단 칸만 새 판본으로 만들고 비교는 이전 판본 것을 그대로 쓴다. 앞선 판본만 어긋남이다.
        if comparison_row.requirement_version > reqs[comparison_row.requirement_id].version:
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
            if option_set is None or option_set.customer_permission == "FORBIDDEN":
                raise ValueError("RFP_DRAFT_BLOCKED_SELECTION")
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
        if not question.question_text.strip():
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

    sections: dict[s.DraftSection, dict[tuple[str, ...], _Summary]] = {
        key: {} for key in get_args(s.DraftSection)
    }
    registry = (
        {("requirement", str(key)) for key in reqs}
        | {("clause", str(key)) for key in clauses}
        | {("product", key) for key in products}
        | {("partner", key) for key in candidates}
        | {("question", str(key)) for key in asks}
    )

    def order(req: s.RequirementDetail) -> tuple[str, ...]:
        number = re.sub(r"\d+", lambda match: match[0].zfill(12), req.origin.clause_no or "")
        return (
            number,
            req.spec.equipment or "",
            req.spec.attribute or req.spec.task or "",
            str(req.requirement_id),
        )

    def add(
        section: s.DraftSection,
        key: tuple[str, ...],
        title: str,
        evidence: Sequence[s.Evidence],
        representative: str,
        sort_key: tuple[str, ...],
        *,
        conditions: Sequence[str] = (),
        unconfirmed: bool = False,
        rereview: bool = False,
        priority: int = 9,
        display_codes: bool = True,
        representative_limit: int = REPRESENTATIVE_CHARS,
        literal_conditions: Sequence[str] = (),
    ) -> None:
        proofs = {(e.type, e.id) for e in evidence}
        shown_conditions = {_display(value) for value in conditions if value.strip()} | set(
            literal_conditions
        )
        if (
            key != ("integration",)
            and proofs
            and any(
                part.section == section
                and (row.template_text or row.text).startswith(title + ".")
                and proofs.issubset({(e.type, e.id) for e in row.evidence})
                and shown_conditions.issubset(row.conditions)
                for part in replaced
                for row in part.sentences
            )
        ):
            return
        summary = sections[section].setdefault(key, _Summary(key, title))
        summary.include(
            _Summary(
                key,
                title,
                priority,
                {(e.type, e.id) for e in evidence},
                {_display(value) for value in conditions if value.strip()}
                | set(literal_conditions),
                [
                    (
                        sort_key,
                        _sample(representative, representative_limit, display_codes=display_codes),
                    )
                ],
                unconfirmed or bool(conditions) or bool(literal_conditions) or rereview,
                rereview,
            )
        )

    for req in sorted(requirements, key=order):
        evidence = _requirement_evidence(req)
        conditions = _conditions(req)
        review = req.needs_rereview or req.needs_review
        unconfirmed = req.judgement.status != "EVIDENCE_FOUND" or bool(conditions) or review
        equipment_key = " ".join(
            (req.spec.equipment or ("수행 과업" if req.spec.task else "장비 미정")).split()
        )
        equipment = _sample(
            req.spec.equipment or ("수행 과업" if req.spec.task else "장비 미정"),
            80,
            display_codes=False,
        )
        status = req.judgement.status
        kind = "과업" if req.spec.task else "요구"
        base_group = (equipment_key, status, kind)
        priority = {
            "SPEC_GAP": 0,
            "CONDITIONAL": 1,
            "INSUFFICIENT_EVIDENCE": 2,
            "EVIDENCE_FOUND": 3,
        }[status]
        add(
            "CUSTOMER_GOAL_SCOPE",
            base_group,
            f"{equipment}의 고객 {kind} 검토 범위 · {STATUS_TEXT[status]}",
            evidence,
            req.origin.quote,
            order(req),
            conditions=conditions,
            unconfirmed=unconfirmed,
            rereview=review,
            priority=priority,
            display_codes=False,
        )
        comp = comps.get(req.requirement_id)
        if comp:
            for candidate in sorted(comp.candidates, key=lambda value: value.product_code):
                match = candidate.attribute_match
                if not any(e.document.strip() for e in match.evidence):
                    continue
                candidate_conditions = [*conditions, "제품의 실제 공급·납기·적용 모델은 별도 확인"]
                if comp.search_trace.search_failed:
                    candidate_conditions.append("문서 검색 실패: 문서 근거 추가 확인 필요")
                candidate_conditions.extend(
                    f"후보 조건 {condition_text(c.name)}: {c.value} {c.unit or ''}".strip()
                    for c in match.our_conditions
                )
                for proof in match.evidence:
                    candidate_conditions.extend(
                        value for value in (proof.applicability, proof.limits) if value
                    )
                representative, raw_comparison = _comparison_sample(req, candidate)
                add(
                    "OUR_RESPONSE",
                    (*base_group, match.result),
                    f"{equipment}의 자사 후보 비교 · {RESULT_TEXT[match.result]}. 전체 요구 충족이나 공급 확약이 아님",
                    [*evidence, s.Evidence(type="product", id=candidate.product_code)],
                    representative,
                    (*order(req), candidate.product_code),
                    conditions=candidate_conditions,
                    unconfirmed=True,
                    rereview=review,
                    priority=priority,
                    display_codes=False,
                    representative_limit=COMPARISON_REPRESENTATIVE_CHARS,
                    literal_conditions=raw_comparison,
                )
        if unconfirmed or status == "SPEC_GAP":
            destination: s.DraftSection = (
                "DEVIATIONS" if status == "SPEC_GAP" else "ASSUMPTIONS_EXCLUSIONS"
            )
            add(
                destination,
                (*base_group, "judgement"),
                f"{equipment}의 {kind} · {STATUS_TEXT[status]}",
                evidence,
                req.origin.quote,
                order(req),
                conditions=conditions,
                unconfirmed=True,
                rereview=review,
                priority=priority,
                display_codes=False,
            )
        option_set = opts.get(req.requirement_id)
        if option_set:
            selection = chosen.get(req.requirement_id)
            for option in sorted(option_set.options, key=lambda value: (value.rank, value.path)):
                if (
                    not option.allowed
                    or option.blocked_reason
                    or (
                        option.path in PARTNER_PATHS
                        and option_set.customer_permission == "FORBIDDEN"
                    )
                ):
                    continue
                is_selected = selection is not None and selection.path == option.path
                path_conditions = [*conditions, *option.conditions_to_confirm]
                if (
                    option.path == "ALTERNATIVE_SPEC"
                    or option_set.customer_permission == "UNSTATED"
                ):
                    path_conditions.append("고객 허용·승인 조건 확인")
                if option.gate.requires == "OWN_DEVELOPMENT_CHECK":
                    path_conditions.append(
                        f"R&D 개발·수행 확인 상태: {RND_TEXT[option.gate.status]}"
                    )
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
                destination = "PARTNER_SUPPLEMENT" if option.path in PARTNER_PATHS else "DEVIATIONS"
                if option.path == "NONE":
                    destination = "ASSUMPTIONS_EXCLUSIONS"
                path_key = (*base_group, option.path, str(is_selected))
                title = f"{equipment}의 {PATH_TEXT[option.path]} · {'사람 선택 기록 있음' if is_selected else '미선택 선택지'}. 이행·공급·납기 확약이 아님"
                add(
                    destination,
                    path_key,
                    title,
                    path_evidence,
                    req.origin.quote,
                    order(req),
                    conditions=path_conditions,
                    unconfirmed=True,
                    rereview=review,
                    priority=priority,
                    display_codes=False,
                )
                for code in sorted(option.candidate_codes if option.path in PARTNER_PATHS else []):
                    partner = candidates[code]
                    grade = next(
                        g for g in partner.grades if g.requirement_id == req.requirement_id
                    )
                    if partner.overall_grade == "EXCLUDED" or grade.grade == "EXCLUDED":
                        continue
                    add(
                        destination,
                        path_key,
                        title,
                        [*path_evidence, s.Evidence(type="partner", id=code)],
                        req.origin.quote,
                        (*order(req), code),
                        conditions=[
                            *path_conditions,
                            *map(check_text, partner.conditions_to_confirm),
                            *map(check_text, grade.mandatory_check.unknown),
                            f"가상 협력사 요구별 등급: {GRADE_TEXT[grade.grade]}",
                            f"가상 협력사 확인 상태: {CONFIRMATION_TEXT[partner.confirmation_status]}",
                        ],
                        unconfirmed=True,
                        rereview=review,
                        priority=priority,
                        display_codes=False,
                    )
        if req.judgement.next_owner_field:
            owner = req.judgement.next_owner_field
            add(
                "QUESTIONS_NEXT_ACTIONS",
                (equipment_key, "next_action", owner),
                f"{equipment}의 다음 확인 담당: {OWNER_TEXT[owner]}",
                evidence,
                req.origin.quote,
                order(req),
                conditions=conditions,
                unconfirmed=True,
                rereview=review,
                priority=priority,
                display_codes=False,
            )

    if integration.required:
        basis = [
            req for req in requirements if req.origin.clause_id in integration.basis_clause_ids
        ]
        evidence = [proof for req in basis for proof in _requirement_evidence(req)]
        evidence.extend(
            s.Evidence(type="partner", id=code) for code in integration.holder_candidate_codes
        )
        unresolved = integration.holder == "UNRESOLVED"
        conditions = [*integration.unresolved]
        if unresolved:
            conditions.append(CHECK_ITEM)
            conditions.extend(path.condition for path in integration.fallback_paths)
        add(
            "ASSUMPTIONS_EXCLUSIONS",
            ("integration",),
            f"유닛 전체의 조립·공장 시험·연동·보증을 맡을 통합 책임자: {HOLDER_TEXT[integration.holder]}. {'통합 책임 미확보로 유닛 공급 가능 여부를 확정하지 않음' if unresolved else '책임자 지정 기록이며 유닛 공급·납기 확약이 아님'}",
            evidence,
            "",
            (),
            conditions=conditions,
            unconfirmed=unresolved,
            rereview=any(req.needs_rereview or req.needs_review for req in basis),
            priority=-1,
        )
    for question in sorted(questions, key=lambda value: str(value.question_id)):
        evidence = [s.Evidence(type="question", id=str(question.question_id))]
        related = reqs.get(question.requirement_id) if question.requirement_id else None
        if related:
            evidence.extend(_requirement_evidence(related))
        elif question.origin.clause_id is not None:
            evidence.append(s.Evidence(type="clause", id=str(question.origin.clause_id)))
        target = (
            f"내부 다음 행동(담당 부서 {OWNER_TEXT.get(question.target_dept, '지정됨') if question.target_dept and question.target_dept.strip() else '미정'})"
            if question.target == "INTERNAL"
            else "고객 확인 질문"
        )
        state = {
            "DRAFT": "초안·미발송",
            "OPEN": "확인된 질문·발송 여부 별도 확인",
            "REPLIED": "회신 수신 기록·반영 여부 별도 확인",
        }[question.status]
        add(
            "QUESTIONS_NEXT_ACTIONS",
            (question.target, question.target_dept or "", question.status),
            f"{target} · {state}",
            evidence,
            question.question_text,
            order(related) if related else (str(question.question_id),),
            unconfirmed=question.status != "REPLIED",
            rereview=bool(related and (related.needs_rereview or related.needs_review)),
        )
        assumption = question.internal_assumption
        if assumption and assumption.shown_in_draft and assumption.value.strip():
            add(
                "ASSUMPTIONS_EXCLUSIONS",
                ("internal_assumption", question.target, question.status),
                "회신 전 내부 가정(충족 판정에 사용 금지)",
                evidence,
                assumption.value,
                order(related) if related else (str(question.question_id),),
                conditions=[assumption.value],
                unconfirmed=True,
                rereview=bool(related and (related.needs_rereview or related.needs_review)),
            )

    def sentence(section: s.DraftSection, group: _Summary) -> s.Sentence:
        if not group.evidence or not group.evidence.issubset(registry):
            raise ValueError("RFP_DRAFT_SENTENCE_EVIDENCE_MISSING")
        representatives = list(
            dict.fromkeys(text for _, text in sorted(group.representatives) if text)
        )[:MAX_REPRESENTATIVES]
        counts = []
        for kind, label in (
            ("requirement", "요구"),
            ("product", "자사 후보"),
            ("partner", "가상 협력사"),
            ("question", "질문"),
        ):
            count = sum(e_type == kind for e_type, _ in group.evidence)
            if count:
                counts.append(f"{label} {count}건")
        text = f"{group.title}. {' · '.join(counts)}."
        if representatives:
            text += " 대표 항목: " + " / ".join(f"「{value}」" for value in representatives) + "."
        unique, conditions = sorted(group.evidence), sorted(group.conditions)
        return s.Sentence(
            sentence_id=uuid5(
                project_id, repr((version, section, group.key, text, unique, conditions))
            ),
            text=text,
            template_text=text,
            evidence=[s.Evidence(type=kind, id=ref) for kind, ref in unique],
            conditions=conditions,
            unconfirmed=group.unconfirmed or bool(conditions) or group.rereview,
            needs_rereview=group.rereview,
            evidence_missing=False,
            provenance_kind="system_record",
        )

    output = []
    for section, groups in sections.items():
        ordered = sorted(
            groups.values(), key=lambda group: (not group.rereview, group.priority, group.key)
        )
        reserved = [group for group in ordered if group.key == ("integration",)]
        ordered = [group for group in ordered if group.key != ("integration",)]
        slots = MAX_SENTENCES_PER_SECTION - len(reserved)
        if len(ordered) > slots:
            overflow = _Summary(
                ("overflow",),
                "혼합 항목 추가 검토: 서로 다른 장비·조건·선택 여부의 묶음이며 동일 항목이나 공급 가능 판단이 아님",
            )
            for group in ordered[slots - 1 :]:
                overflow.include(group)
            ordered = [*ordered[: slots - 1], overflow]
        output.append(
            s.Section(
                section=section,
                sentences=[sentence(section, group) for group in [*reserved, *ordered]],
            )
        )
    return s.Draft(
        project_id=project_id,
        version=version,
        data_origin=data_origin,
        known_at=known_at,
        derivation_kind="DERIVED",
        sections=output,
        disclaimer=DISCLAIMER,
        evidence_missing_count=sum(
            row.evidence_missing for part in output for row in part.sentences
        ),
    )
