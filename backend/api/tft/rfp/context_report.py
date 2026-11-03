"""Deterministic OOXML context appendix from a frozen review snapshot; no live queries/model."""

from collections import Counter
from xml.etree import ElementTree as ET

from backend.api.tft.rfp import docx_layout as layout
from backend.api.tft.rfp.context_schemas import ContextResponse

CATEGORIES = {
    "EQUIPMENT": "설비·장비",
    "TASK": "수행 업무",
    "DELIVERABLE": "제출물",
    "INTERFACE": "인터페이스·연계",
    "QUALITY": "품질·시험",
    "SCHEDULE": "일정",
    "QUALIFICATION": "자격·실적",
    "COMMERCIAL": "상업 조건",
    "UNCLASSIFIED": "미분류",
}
STATUS = {
    "EVIDENCE_FOUND": "근거 있음",
    "CONDITIONAL": "조건부",
    "SPEC_GAP": "사양 차이",
    "INSUFFICIENT_EVIDENCE": "근거 부족",
}


def append_context_report(body: ET.Element, snapshot: ContextResponse | None) -> None:
    layout.paragraph(body, "문맥별 RFP 분석 · 검토본 고정 기록", "Heading1")
    if snapshot is None:
        layout.paragraph(
            body,
            "이 검토본에는 문맥 분석 스냅샷이 없습니다. 이후 생성한 분석을 소급 적용하지 않았습니다.",
            "Notice",
        )
        return
    layout.paragraph(
        body,
        f"문맥 판본 {snapshot.version or '없음'} / 검토본 저장 시 상태: {snapshot.status}",
        "Meta",
    )
    layout.paragraph(
        body,
        "이 내용은 분류·묶음 검토 후보입니다. 미해결 표시가 없더라도 적합성·공급 가능·승인 완료를 의미하지 않습니다.",
        "Notice",
    )
    if snapshot.status == "STALE":
        layout.paragraph(
            body, "검토본 저장 당시 원문·요구 판본이 변경돼 재분석이 필요한 상태였습니다.", "Notice"
        )
    if snapshot.result is None:
        layout.paragraph(body, "검토본 저장 당시 문맥 분석 결과가 없었습니다.", "Meta")
        return
    result = snapshot.result
    requirements = {r.requirement_id: r for r in snapshot.requirements}
    refs = {str(r.requirement_id): r for r in snapshot.source_refs}
    groups = {g.entity_id: g for g in result.groups}
    counts: Counter[str] = Counter(row.primary_category for row in result.assignments)
    unresolved = sum(
        row.needs_review or row.primary_category == "UNCLASSIFIED" for row in result.assignments
    )
    layout.paragraph(
        body,
        f"요구 {len(requirements)}건 / 묶음 {len(groups)}개 / 분류·연결 확인 필요 {unresolved}건 / 기록된 이슈 {len(result.issues)}건",
        "Meta",
    )
    status_counts = Counter(req.status for req in requirements.values())
    layout.paragraph(
        body,
        "기존 요구 판정(고정 판본): "
        + " / ".join(f"{label} {status_counts[code]}건" for code, label in STATUS.items()),
        "Meta",
    )
    layout.paragraph(body, "8개 요구 유형별 구성", "Heading2")
    layout.paragraph(
        body,
        "막대는 가장 많은 주 유형 대비 상대 길이입니다. 추가 유형은 중복 합산하지 않습니다.",
        "Meta",
    )
    peak = max(counts.values(), default=1) or 1
    widths: tuple[int, ...] = (3800, 1200, 4480)
    table = layout.table(body, ("주 유형", "요구 수", "상대 분포"), widths)
    for category, name in CATEGORIES.items():
        cells = layout.table_row(table, widths)
        for cell, text in zip(
            cells,
            (
                name,
                str(counts[category]),
                "■" * round(counts[category] / peak * 16) + f" {counts[category]}건",
            ),
            strict=True,
        ):
            layout.paragraph(cell, text, "TableText")
    layout.paragraph(body, "설비·시스템·업무 계층", "Heading2")
    if not groups:
        layout.paragraph(body, "저장된 묶음 없음 · 원문 검토 필요", "Meta")
    for group in result.groups:
        parent = groups.get(group.parent_id or "")
        layout.paragraph(
            body,
            f"{group.name} [{group.kind}] / 상위: {parent.name if parent else '없음·미지정'}",
            "Heading3",
        )
        layout.paragraph(
            body,
            f"태그: {group.tag or '미확인'} / 위치: {group.site or '미확인'} / 검토 필요: {'예' if group.needs_review else '표식 없음(승인 아님)'}",
            "Meta",
        )
        layout.paragraph(
            body,
            "묶음 근거: "
            + ", ".join(refs[r].code if r in refs else r for r in group.evidence_requirement_ids),
            "Meta",
        )
        if group.diagram_source_ids:
            layout.paragraph(body, "도면 원본 참조: " + ", ".join(group.diagram_source_ids), "Meta")
    layout.paragraph(body, "요구별 추적표 · 원문과 판단", "Heading2")
    widths = (1000, 1800, 2480, 4100)
    table = layout.table(
        body, ("요구·판본", "유형·묶음", "기존 판정·검토", "원문 위치·인용·분류 근거"), widths
    )
    for row in result.assignments:
        req, ref = requirements.get(row.requirement_id), refs.get(row.requirement_id)
        cells = layout.table_row(table, widths)
        layout.paragraph(
            cells[0],
            f"{ref.code if ref else row.requirement_id} / v{req.version if req else '미확인'}",
            "TableText",
        )
        layout.paragraph(cells[1], CATEGORIES[row.primary_category], "TableText")
        if row.additional_categories:
            layout.paragraph(
                cells[1],
                "추가 유형: " + ", ".join(CATEGORIES[c] for c in row.additional_categories),
                "TableText",
            )
        layout.paragraph(
            cells[1],
            ", ".join(groups[g].name for g in row.group_ids if g in groups) or "미지정",
            "TableText",
        )
        for relation in row.relations:
            layout.paragraph(
                cells[1],
                f"관계 후보: {relation.relation} / {groups[relation.group_id].name if relation.group_id in groups else relation.group_id}",
                "TableText",
            )
        layout.paragraph(
            cells[2], STATUS.get(req.status, req.status) if req else "판정 확인 불가", "TableText"
        )
        layout.paragraph(
            cells[2],
            "분류 검토 필요: " + ("예" if row.needs_review else "표식 없음(승인 아님)"),
            "TableText",
        )
        if ref:
            layout.paragraph(
                cells[3],
                f"문서 {ref.document_id} / 판본 {ref.document_version_id} / 조항 {ref.clause_id or '미연결'} / 쪽 {ref.page or '미확인'}",
                "TableText",
            )
            layout.paragraph(cells[3], ref.quote, "TableText")
        if req:
            layout.paragraph(cells[3], " > ".join(req.section_path), "TableText")
        layout.paragraph(cells[3], "분류 근거: " + row.reason, "TableText")
        layout.paragraph(
            cells[3],
            "근거 요구: "
            + ", ".join(refs[r].code if r in refs else r for r in row.evidence_requirement_ids),
            "TableText",
        )
    layout.paragraph(body, "미해결·확인할 사항", "Heading2")
    for issue in result.issues:
        layout.paragraph(body, f"{issue.code}: {issue.message}", "Meta")
        layout.paragraph(
            body,
            "대상 요구: "
            + ", ".join(refs[r].code if r in refs else r for r in issue.requirement_ids),
            "Meta",
        )
    if not result.issues:
        layout.paragraph(
            body, "기록된 분류 이슈 없음 · 원문 요구의 승인·해결 완료를 뜻하지 않음", "Meta"
        )
