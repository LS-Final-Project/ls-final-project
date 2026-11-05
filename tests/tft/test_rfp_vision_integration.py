"""Image transcription is traceable but never a verified specification (fake model only)."""

import pytest

from backend.api.tft import worker_processor as processor
from backend.api.tft.rfp import comparison
from backend.engine.documents.model import DOCX_MIME
from backend.engine.rfp.requirements import ExtractedRequirement
from tests.tft.test_rfp_image_transcription import IMAGE, docx, generation
from tests.tft.test_worker_processor_db import REQUIREMENTS


def test_image_only_docx_reaches_existing_section_inputs_and_preserves_source():
    calls, events = [], []

    def provider(item):
        calls.append(item.source_id)
        return generation()

    tree, review = processor.parse_with_images(docx(IMAGE), DOCX_MIME, provider, events.append, "v")
    inputs, skipped = processor.document_inputs(tree)
    assert review and not skipped
    assert calls == ["OCR-DOCX-0001"]
    assert len(events) == 2 and all(e["step"] == "parse" for e in events)
    assert inputs[0].clauses[0].clause_no == "OCR-DOCX-0001.1"
    assert inputs[0].clauses[0].text == "Rated voltage shall be 15 kV."


def test_image_disabled_is_not_silently_text_only_success():
    tree, review = processor.parse_with_images(docx(IMAGE), DOCX_MIME, None, lambda _: None, "v")
    assert review and any(c.clause_no.startswith("OCR-REVIEW-") for c in tree.clauses)
    with pytest.raises(ValueError, match="TFT_DOCUMENT_EXTRACTION_INPUT_INVALID"):
        processor.document_inputs(tree)


def test_ocr_match_is_conditioned_without_changing_original_requirement():
    item = ExtractedRequirement.model_validate({**REQUIREMENTS[0], "clause_no": "OCR-PDF-P0002.1"})
    result = comparison.compare_in_family(
        processor.to_comparison(item), "TRANSFORMER", comparison.load_own_products()
    )
    result = result.model_copy(update={"status": "EVIDENCE_FOUND"})
    reviewed = processor.image_comparison_review(item, result)
    assert reviewed.status == "CONDITIONAL"
    assert reviewed.next_owner == "TECH"
    assert "이미지 전사 원문 확인 필요" in reviewed.unresolved
    assert result.status == "EVIDENCE_FOUND"
    assert item.clause_no == "OCR-PDF-P0002.1"
    assert (
        processor.image_comparison_review(item.model_copy(update={"clause_no": "2.1.A"}), result)
        is result
    )


def test_no_image_provider_call_after_lease_cancellation():
    called = []

    def emit(_):
        raise RuntimeError("LEASE_LOST")

    with pytest.raises(RuntimeError, match="LEASE_LOST"):
        processor.parse_with_images(docx(IMAGE), DOCX_MIME, lambda x: called.append(x), emit, "v")
    assert called == []


@pytest.mark.parametrize("denied", [False, True])
def test_document_warning_lookup_follows_visibility_and_exact_version(monkeypatch, denied):
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from backend.api.tft.rfp import routes_read
    from backend.shared.errors import ApplicationError

    conn, settings = object(), object()
    version, document_id = uuid4(), uuid4()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))

    @asynccontextmanager
    async def connection(_):
        yield conn

    visible = AsyncMock(return_value={"id": version, "clauses_count": 1, "deleted_values_count": 0})
    if denied:
        visible.side_effect = ApplicationError(404, "TFT_DOCUMENT_NOT_FOUND", "not found")
    warnings = AsyncMock(return_value=["OCR-PDF-P0002: 이미지 전사 원문 확인 필요"])
    monkeypatch.setattr(routes_read, "connection", connection)
    monkeypatch.setattr(routes_read, "visible_document", visible)
    monkeypatch.setattr(routes_read.repository, "extraction_warnings", warnings)
    monkeypatch.setattr(routes_read, "document_out", lambda _: {})
    monkeypatch.setattr(routes_read.s, "DocumentDetail", lambda **data: data)
    if denied:
        with pytest.raises(ApplicationError):
            asyncio.run(routes_read.get_document(request, object(), document_id))
        warnings.assert_not_awaited()
    else:
        result = asyncio.run(routes_read.get_document(request, object(), document_id))
        warnings.assert_awaited_once_with(conn, version)
        assert result["extraction_warnings"] == warnings.return_value
