"""Deterministic OOXML context appendix from a frozen review snapshot; no live queries/model."""

from collections import Counter
from xml.etree import ElementTree as ET

from backend.api.tft.rfp import docx_layout as layout
from backend.api.tft.rfp.bid_outputs import clause_label, requirement_label
from backend.api.tft.rfp.context_schemas import ContextResponse

# 검토용 부록에도 내부 코드·영문 열거값을 그대로 쓰지 않는다(40444 흐름 점검, 검토본 DOCX).
STATUS = {"CURRENT": "최신", "STALE": "원문·요구가 바뀌어 다시 정리 필요", "UNKNOWN": "확인 불가"}
KIND = {"SYSTEM": "계통", "EQUIPMENT": "설비", "TASK": "업무", "COMMON": "공통"}


def diagram_label(source_id: str) -> str:
    return clause_label(source_id.rsplit(":", 1)[-1])


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
REQ_STATUS = {
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
        f"문맥 판본 {snapshot.version or '없음'} / 검토본 저장 시 상태: {STATUS.get(snapshot.status, snapshot.status)}",
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
        + " / ".join(f"{label} {status_counts[code]}건" for code, label in REQ_STATUS.items()),
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
        # 검토자가 훑는 한 줄. 묶음 근거 목록과 요구별 추적은 XLSX 요구별 시트에 있다(2026-11-05 분량 정리).
        parent = groups.get(group.parent_id or "")
        parts = [
            f"{group.name} [{KIND.get(group.kind, group.kind)}]",
            f"태그 {group.tag or '미확인'}",
            f"요구 {len(group.evidence_requirement_ids)}건",
        ]
        if parent:
            parts.append(f"상위 {parent.name}")
        if group.diagram_source_ids:
            parts.append("도면 " + ", ".join(diagram_label(d) for d in group.diagram_source_ids))
        if group.needs_review:
            parts.append("검토 필요")
        layout.paragraph(body, " · ".join(parts), "Meta")
    layout.paragraph(
        body,
        "요구별 원문 위치·판정·분류 근거는 대응 현황표(XLSX)의 요구별 시트에 있습니다.",
        "Meta",
    )
    layout.paragraph(body, "미해결·확인할 사항", "Heading2")
    for issue in result.issues:
        targets = ", ".join(
            requirement_label(refs[r].code) if r in refs else r for r in issue.requirement_ids
        )
        layout.paragraph(body, f"{issue.message} (대상 요구: {targets})", "Meta")
    if not result.issues:
        layout.paragraph(
            body, "기록된 분류 이슈 없음 · 원문 요구의 승인·해결 완료를 뜻하지 않음", "Meta"
        )
