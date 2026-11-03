"""Context report is deterministic and uses only the captured review result."""

# ruff: noqa: F811
import io
import zipfile
from datetime import UTC, datetime
from uuid import uuid4
from xml.etree import ElementTree as ET

from backend.api.tft.rfp.context_schemas import ContextResponse, SourceReference
from backend.api.tft.rfp.draft_exports import W, render_docx
from backend.engine.rfp.context_models import (
    ContextAssignment,
    ContextGroup,
    ContextRequirement,
    ContextResult,
)
from backend.engine.rfp.draft import build_draft
from tests.tft.test_rfp_draft import snapshot  # noqa: F401


def context(project):
    r, d, v, c = map(str, (uuid4(), uuid4(), uuid4(), uuid4()))
    req = ContextRequirement(
        requirement_id=r,
        version=2,
        document_version_id=v,
        clause_id=c,
        clause_no="2.1",
        section_path=["송변전", "TR-1"],
        quote="TR-1 시험 보고서 제출",
        equipment="TR-1",
        status="SPEC_GAP",
        necessity="MANDATORY",
    )
    result = ContextResult(
        outcome="PARTIAL",
        groups=[
            ContextGroup(
                entity_id="g",
                name="TR-1",
                kind="EQUIPMENT",
                tag="TR-1",
                evidence_requirement_ids=[r],
            )
        ],
        assignments=[
            ContextAssignment(
                requirement_id=r,
                primary_category="DELIVERABLE",
                additional_categories=["QUALITY"],
                group_ids=["g"],
                reason="시험 보고서 원문",
                evidence_requirement_ids=[r],
                needs_review=True,
            )
        ],
    )
    return ContextResponse(
        project_id=project,
        version=2,
        status="STALE",
        source_fingerprint="0" * 64,
        current_source_fingerprint="1" * 64,
        provenance="ai_draft",
        created_at=datetime(2026, 11, 3, tzinfo=UTC),
        created_by=uuid4(),
        result=result,
        requirements=[req],
        source_refs=[
            SourceReference(
                requirement_id=r,
                version=2,
                code="R-001",
                document_id=d,
                document_version_id=v,
                clause_id=c,
                page=4,
                quote=req.quote,
            )
        ],
    )


def text(data):
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        assert package.testzip() is None
        return " ".join(
            n.text or "" for n in ET.fromstring(package.read("word/document.xml")).iter(W + "t")
        )


def test_frozen_report_preserves_categories_traceability_stale_and_legacy_sections(snapshot):
    draft = build_draft(**snapshot)
    saved = context(draft.project_id)
    first = render_docx(draft, 7, context=saved)
    assert first == render_docx(draft, 7, context=saved)
    rendered = text(first)
    for value in (
        "문맥 판본 2",
        "STALE",
        "8개 요구 유형별 구성",
        "설비·장비",
        "수행 업무",
        "제출물",
        "인터페이스·연계",
        "품질·시험",
        "일정",
        "자격·실적",
        "상업 조건",
        "미분류",
        "TR-1 시험 보고서 제출",
        "R-001 / v2",
        "사양 차이",
        "분류 근거: 시험 보고서 원문",
        "검토용 초안입니다.",
    ):
        assert value in rendered
    for part in draft.sections:
        for sentence in part.sentences:
            assert sentence.text in rendered
    newer = saved.model_copy(update={"version": 3})
    assert render_docx(draft, 7, context=newer) != first
    assert render_docx(draft, 7, context=saved) == first


def test_legacy_review_absence_is_explicit_and_not_latest_context(snapshot):
    rendered = text(render_docx(build_draft(**snapshot), 1))
    assert "이 검토본에는 문맥 분석 스냅샷이 없습니다" in rendered
    assert "이후 생성한 분석을 소급 적용하지 않았습니다" in rendered
