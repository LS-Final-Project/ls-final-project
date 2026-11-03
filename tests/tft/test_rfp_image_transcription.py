"""Authored XML/PDF and fake model responses only. No reference/evaluation documents."""

import io
import json
import zipfile

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from backend.engine.documents.model import build_document
from backend.engine.documents.router import extract
from backend.engine.documents.vision import (
    MAX_IMAGE_BYTES,
    MAX_IMAGES,
    ImageInput,
    VisionIssue,
    VisionPlan,
    VisionResult,
    append_ocr_clauses,
    gemini_image_provider,
    image_inputs,
    transcribe,
)
from backend.engine.rfp.clauses import build_clause_tree
from backend.shared.ai import AISettings, Generation, Usage

PNG = b"\x89PNG\r\n\x1a\nfixture"


def docx(nodes, rels=None, image=PNG):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
            xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"><w:body>{nodes}</w:body></w:document>""",
        )
        archive.writestr(
            "word/_rels/document.xml.rels",
            rels
            or """<Relationships><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/test.png"/></Relationships>""",
        )
        archive.writestr("word/media/test.png", image)
    return data.getvalue()


IMAGE = '<w:p><w:r><w:drawing><a:blip r:embed="r1"/></w:drawing></w:r></w:p>'


def generation(lines=None, **changes):
    body = {
        "lines": lines or ["Rated voltage shall be 15 kV."],
        "uncertain": [],
        "contains_diagram": False,
        **changes,
    }
    return Generation(json.dumps(body), Usage(), "fake", ["STOP"])


def test_docx_embedded_image_mapping_excludes_deleted_and_duplicate_fallback():
    nodes = (
        IMAGE
        + f"<w:del>{IMAGE}</w:del>"
        + f"<mc:AlternateContent><mc:Choice>{IMAGE}</mc:Choice><mc:Fallback>{IMAGE}</mc:Fallback></mc:AlternateContent>"
    )
    data = docx(nodes)
    plan = image_inputs(data, extract(data))
    assert [x.source_id for x in plan.inputs] == ["OCR-DOCX-0001", "OCR-DOCX-0002"]
    assert all(x.data == PNG and x.mime == "image/png" and x.page is None for x in plan.inputs)
    assert not plan.skipped


@pytest.mark.parametrize(
    "target,extra,reason",
    [
        (
            "https://example.invalid/picture.png",
            'TargetMode="External"',
            "OCR_EXTERNAL_OR_MISSING_IMAGE",
        ),
        ("../media/test.png", "", "OCR_IMAGE_PATH_UNSUPPORTED"),
        ("/word/media/test.png", "", "OCR_IMAGE_PATH_UNSUPPORTED"),
    ],
)
def test_external_and_escape_relations_never_fetch(target, extra, reason):
    relations = f'<Relationships><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="{target}" {extra}/></Relationships>'
    data = docx(IMAGE, relations)
    plan = image_inputs(data, extract(data))
    assert not plan.inputs
    assert plan.skipped[0].reason == reason


def test_limits_are_explicit_and_zero_images_make_zero_calls():
    data = docx(IMAGE * (MAX_IMAGES + 2))
    plan = image_inputs(data, extract(data))
    assert len(plan.inputs) == MAX_IMAGES
    assert [x.reason for x in plan.skipped] == ["OCR_IMAGE_LIMIT"] * 2
    text = docx("<w:p><w:r><w:t>Ordinary text.</w:t></w:r></w:p>")
    empty = image_inputs(text, extract(text))
    result = transcribe(empty, lambda _: pytest.fail("text-only document called a model"))
    assert result == VisionResult()
    with pytest.raises(ValueError, match="IDENTITY"):
        image_inputs(data + b"different", extract(data))


def pdf_with_image(native=False):
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    stream = DecodedStreamObject()
    stream.set_data(b"\x00")
    stream.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(1),
            NameObject("/Height"): NumberObject(1),
            NameObject("/ColorSpace"): NameObject("/DeviceGray"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): writer._add_object(stream)})}
    )
    writer.add_blank_page(width=200, height=200)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_scanned_pdf_is_split_with_actual_page_numbers_without_raster_dependency():
    data = pdf_with_image()
    plan = image_inputs(data, extract(data))
    assert [x.page for x in plan.inputs] == [1]
    assert [x.source_id for x in plan.inputs] == ["OCR-PDF-P0001"]
    assert all(len(PdfReader(io.BytesIO(x.data)).pages) == 1 for x in plan.inputs)


def test_vector_drawing_page_is_selected_but_empty_page_is_not():
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    stream = DecodedStreamObject()
    stream.set_data(b"10 10 m 100 100 l S")
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_blank_page(width=200, height=200)
    out = io.BytesIO()
    writer.write(out)
    data = out.getvalue()
    plan = image_inputs(data, extract(data))
    assert [x.page for x in plan.inputs] == [1]


def test_nested_form_vector_drawing_is_selected_without_page_level_paint():
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    inner = DecodedStreamObject()
    inner.set_data(b"10 10 m 100 100 l S")
    inner[NameObject("/Subtype")] = NameObject("/Form")
    outer = DecodedStreamObject()
    outer.set_data(b"/Nested Do")
    outer[NameObject("/Subtype")] = NameObject("/Form")
    outer[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/XObject"): DictionaryObject(
                {NameObject("/Nested"): writer._add_object(inner)}
            )
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/XObject"): DictionaryObject({NameObject("/Fm0"): writer._add_object(outer)})}
    )
    page_stream = DecodedStreamObject()
    page_stream.set_data(b"/Fm0 Do")
    page[NameObject("/Contents")] = writer._add_object(page_stream)
    out = io.BytesIO()
    writer.write(out)
    data = out.getvalue()
    # Native text extraction is independent of visual-object traversal.
    plan = image_inputs(data, build_document("PDF", data, [], [], tables=0))
    assert [x.page for x in plan.inputs] == [1]
    assert plan.skipped == []


def test_nested_form_cycle_and_resource_limit_remain_bounded():
    from backend.engine.documents.vision import _has_visual

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    data = io.BytesIO()
    writer.write(data)
    reader = PdfReader(io.BytesIO(data.getvalue()))
    root = DictionaryObject()
    cyclic = DecodedStreamObject()
    cyclic.set_data(b"/Self Do")
    cyclic[NameObject("/Subtype")] = NameObject("/Form")
    cyclic[NameObject("/Resources")] = root
    root[NameObject("/XObject")] = DictionaryObject({NameObject("/Self"): cyclic})
    assert _has_visual(root, reader) is False
    # Count Forms as well as resource dictionaries, so a wide graph is also bounded.
    wide = DictionaryObject()
    children = DictionaryObject()
    for index in range(256):
        child = DecodedStreamObject()
        child.set_data(b"q Q")
        child[NameObject("/Subtype")] = NameObject("/Form")
        children[NameObject(f"/F{index}")] = child
    wide[NameObject("/XObject")] = children
    with pytest.raises(ValueError, match="OCR_PDF_RESOURCE_LIMIT"):
        _has_visual(wide, reader)


def test_inline_pdf_image_without_xobject_is_selected():
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    stream = DecodedStreamObject()
    stream.set_data(b"q 1 0 0 1 0 0 cm BI /W 1 /H 1 /CS /RGB /BPC 8 ID \xff\x00\x00 EI Q")
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    data = out.getvalue()
    reader = PdfReader(io.BytesIO(data))
    assert b"INLINE IMAGE" in [op for _, op in reader.pages[0].get_contents().operations]
    assert "/XObject" not in reader.pages[0]["/Resources"]
    plan = image_inputs(data, build_document("PDF", data, [], [], tables=0))
    assert [x.page for x in plan.inputs] == [1]


def test_model_cannot_change_location_or_escape_schema_and_native_text_is_not_duplicated():
    item = ImageInput(
        "OCR-PDF-P0007", "PDF 7쪽", "application/pdf", b"fake", 7, "Native shall be retained."
    )
    result = transcribe(
        VisionPlan([item]),
        lambda _: generation(
            ["Native shall be retained.", "Image shall be 15 kV."],
            uncertain=["Unreadable cell"],
            contains_diagram=True,
        ),
    )
    assert result.items[0].source.page == 7
    assert result.items[0].lines == ["Image shall be 15 kV."]
    assert result.skipped[0].reason == "OCR_REQUIRES_SOURCE_REVIEW"
    bad = transcribe(VisionPlan([item]), lambda _: generation(source_id="new source"))
    assert not bad.items and bad.skipped[0].reason == "OCR_RESPONSE_INVALID"
    with pytest.raises(TimeoutError):
        transcribe(VisionPlan([item]), lambda _: (_ for _ in ()).throw(TimeoutError()))


def test_ocr_clauses_preserve_original_and_page_source_and_build_inputs():
    from backend.api.tft.worker_processor import document_inputs

    data = docx(IMAGE)
    doc = extract(data)
    tree = build_clause_tree(doc)
    result = transcribe(image_inputs(data, doc), lambda _: generation())
    updated = append_ocr_clauses(tree, result)
    assert tree.clauses == []
    assert updated.clauses[0].title == "본문 이미지 1 · OCR 원문 대조 필요"
    assert updated.clauses[1].text == "Rated voltage shall be 15 kV."
    assert updated.clauses[1].clause_no == "OCR-DOCX-0001.1"
    assert updated.clauses[1].flags == ["OCR_TRANSCRIPTION"]
    inputs, skipped = document_inputs(updated)
    assert len(inputs) == 1 and not skipped
    assert inputs[0].clauses[0].clause_no == "OCR-DOCX-0001.1"
    with pytest.raises(ValueError, match="COLLISION"):
        append_ocr_clauses(updated, result)


def test_lazy_provider_calls_shared_generate_with_inline_media(monkeypatch):
    from backend.shared import ai

    called = []
    monkeypatch.setattr(ai, "create_client", lambda settings: called.append("client") or object())
    monkeypatch.setattr(
        ai,
        "generate",
        lambda client, settings, prompt, **kwargs: called.append(kwargs) or generation(),
    )
    provider = gemini_image_provider(AISettings())
    assert called == []
    item = ImageInput("OCR-DOCX-0001", "본문 이미지1", "image/png", PNG)
    provider(item)
    assert called[0] == "client"
    assert called[1]["inline_media"] == ("image/png", PNG)
    provider(item)
    assert called.count("client") == 1


def test_truncation_and_output_limit_are_not_successful_transcriptions():
    item = ImageInput("OCR-DOCX-0001", "본문 이미지1", "image/png", PNG)
    incomplete = generation()
    incomplete.finish_reasons = ["MAX_TOKENS"]
    result = transcribe(VisionPlan([item]), lambda _: incomplete)
    assert not result.items and result.skipped[0].reason == "OCR_RESPONSE_INCOMPLETE"
    result = transcribe(VisionPlan([item]), lambda _: generation(["x" * 2001]))
    assert not result.items and result.skipped[0].reason == "OCR_RESPONSE_INVALID"
    plan = VisionPlan()
    plan.add(ImageInput("OCR-DOCX-0001", "large", "image/png", b"x" * (MAX_IMAGE_BYTES + 1)))
    assert not plan.inputs and plan.skipped[0].reason == "OCR_IMAGE_SIZE_LIMIT"


def test_ocr_warnings_are_persistable_clauses_but_never_extraction_inputs():
    from backend.api.tft.worker_processor import sections_of

    data = docx(IMAGE)
    tree = build_clause_tree(extract(data))
    result = VisionResult(skipped=[VisionIssue("OCR-PDF-P0020", "OCR_IMAGE_LIMIT")])
    updated = append_ocr_clauses(tree, result)
    assert len(updated.clauses) == 1
    assert updated.clauses[0].clause_no == "OCR-REVIEW-0001"
    assert updated.clauses[0].page == 20
    assert "상한 초과" in updated.clauses[0].text
    assert sections_of(updated) == []


@pytest.mark.parametrize(
    "lines,native", [([], ""), (["Already extracted 15 kV."], "Already extracted 15 kV.")]
)
def test_empty_or_native_only_transcription_preserves_source_warning_without_requirements(
    lines, native
):
    from backend.api.tft.worker_processor import sections_of

    item = ImageInput("OCR-PDF-P0007", "PDF 7쪽", "application/pdf", b"fake", 7, native)
    response = Generation(
        json.dumps({"lines": lines, "uncertain": [], "contains_diagram": False}),
        Usage(),
        "fake",
        ["STOP"],
    )
    result = transcribe(VisionPlan([item]), lambda _: response)
    assert result.items[0].lines == []
    assert result.skipped == [VisionIssue(item.source_id, "OCR_NO_NEW_TEXT")]
    data = docx(IMAGE)
    tree = append_ocr_clauses(build_clause_tree(extract(data)), result)
    assert len(tree.clauses) == 1
    assert tree.clauses[0].clause_no == "OCR-REVIEW-0001"
    assert tree.clauses[0].page == 7
    assert "추가로 전사한 문자가 없어 원문 확인 필요" in tree.clauses[0].text
    assert sections_of(tree) == []
