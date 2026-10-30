"""초안 편집과 실제 OOXML 구조, 미지원 PDF의 무부수효과 경계를 검사한다(KAN-188)."""


# ruff: noqa: F811
# pytest의 기존 합성 fixture를 가져와 인자 이름으로 재사용한다.

import asyncio
import io
import zipfile
from copy import deepcopy
from unittest.mock import patch
from uuid import UUID
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from backend.api.tft.main import create_app
from backend.api.tft.rfp import draft_exports, drafts, jobs, routes_draft
from backend.api.tft.rfp import schemas as s
from backend.engine.rfp.draft import DISCLAIMER, build_draft
from backend.shared.access import Principal, principal_header
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings
from tests.tft.test_rfp_draft import snapshot  # noqa: F401


def test_docx_is_valid_ooxml_with_all_sections_proofs_conditions_and_unicode(snapshot):
    draft = build_draft(**snapshot)
    row = draft.sections[0].sentences[0]
    row.text = '한글 <장비> & "조건"\n다음 줄\t탭'
    row.conditions.append("조건 <허용> & 미확약\n둘째 조건")
    row.needs_rereview = True
    data = draft_exports.render_docx(draft, 7)
    assert data == draft_exports.render_docx(draft, 7)
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        assert package.testzip() is None
        assert set(package.namelist()) == {
            "[Content_Types].xml",
            "_rels/.rels",
            "word/document.xml",
        }
        for name in package.namelist():
            ET.fromstring(package.read(name))
        root = ET.fromstring(package.read("word/document.xml"))
        content_types = package.read("[Content_Types].xml").decode()
        assert "wordprocessingml.document.main+xml" in content_types
    w = draft_exports.W
    texts = [node.text or "" for node in root.iter(w + "t")]
    assert texts.count(DISCLAIMER) == 2
    assert '한글 <장비> & "조건"' in texts and "다음 줄" in texts and "탭" in texts
    assert len(list(root.iter(w + "br"))) >= 2 and list(root.iter(w + "tab"))
    assert "조건·미확인: 조건 <허용> & 미확약" in texts
    assert any("재검토: True" in text for text in texts)
    for section in draft.sections:
        assert draft_exports.SECTION_TITLES[section.section] in texts
        for sentence in section.sentences:
            assert f"문장 ID: {sentence.sentence_id}" in texts
            for evidence in sentence.evidence:
                assert f"근거 [{evidence.type}]: {evidence.id}" in texts


@pytest.mark.parametrize("invalid", ["\x00", "\ud800", "\ufffe"])
def test_docx_rejects_invalid_xml_characters_instead_of_losing_content(snapshot, invalid):
    draft = build_draft(**snapshot)
    draft.sections[0].sentences[0].text = f"한글{invalid}누락 금지"
    with pytest.raises(ValueError, match="INVALID_CHARACTER"):
        draft_exports.render_docx(draft, 1)


@pytest.mark.parametrize("entry", ["route", "admit"])
def test_pdf_is_rejected_before_reservation_connection_or_platform_call(entry):
    payload = s.ExportRequest(review_version=1, format="PDF")
    user = Principal(user_id=UUID(int=1), roles=frozenset({"sales"}))
    with patch.object(jobs, "connection", side_effect=AssertionError("must not open a connection")):
        with pytest.raises(ApplicationError) as caught:
            if entry == "route":
                asyncio.run(routes_draft.export(None, user, UUID(int=2), payload, "export-pdf-key"))
            else:
                asyncio.run(
                    jobs.admit(None, user, UUID(int=2), "rfp.export", payload, "export-pdf-key")
                )
    assert caught.value.status_code == 422
    assert caught.value.code == "TFT_EXPORT_FORMAT_UNAVAILABLE"


def test_pdf_http_preserves_enum_and_returns_specific_error_without_database():
    app = create_app(Settings(_env_file=None, environment="test"))
    user = Principal(user_id=UUID(int=1), roles=frozenset({"sales"}))
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/tft/projects/{UUID(int=2)}/draft/exports",
            json={"review_version": 1, "format": "PDF"},
            headers={**principal_header(user), "Idempotency-Key": "export-pdf-test"},
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "TFT_EXPORT_FORMAT_UNAVAILABLE"
    assert "PDF" in s.ExportRequest.model_json_schema()["properties"]["format"]["enum"]


def test_edit_keeps_conditions_review_flags_ids_and_disclaimer_in_new_version(snapshot):
    current = build_draft(**snapshot)
    old = current.sections[0].sentences[0]
    old.needs_rereview = True
    before = current.model_dump()
    payload = s.DraftEdit(
        version=current.version,
        edits=[
            s.SentenceEdit(
                sentence_id=old.sentence_id,
                text="사람이 수정한 검토 문장",
                reason="원문을 확인한 문구 수정",
                evidence=old.evidence,
                section="DEVIATIONS",
            )
        ],
    )
    refs = {(e.type, e.id) for e in old.evidence}
    result, reasons = drafts.edit(current, payload, refs)
    moved = next(
        v for part in result.sections for v in part.sentences if v.sentence_id == old.sentence_id
    )
    assert result.version == current.version + 1 and current.model_dump() == before
    assert moved.conditions == old.conditions and moved.unconfirmed == old.unconfirmed
    assert moved.needs_rereview and moved.provenance_kind == "human_input"
    assert result.disclaimer == DISCLAIMER
    assert reasons == {old.sentence_id: "원문을 확인한 문구 수정"}
    assert moved in next(part.sentences for part in result.sections if part.section == "DEVIATIONS")


@pytest.mark.parametrize("kind", ["empty", "foreign"])
def test_human_sentence_missing_or_foreign_evidence_is_counted(snapshot, kind):
    current = build_draft(**snapshot)
    old = current.sections[0].sentences[0]
    evidence = [] if kind == "empty" else [s.Evidence(type="requirement", id=str(UUID(int=999)))]
    payload = s.DraftEdit(
        version=current.version,
        edits=[
            s.SentenceEdit(
                sentence_id=old.sentence_id, text="검토할 문장", reason="편집", evidence=evidence
            )
        ],
    )
    result, _ = drafts.edit(current, payload, {(e.type, e.id) for e in old.evidence})
    rows = [v for part in result.sections for v in part.sentences]
    assert result.evidence_missing_count == sum(row.evidence_missing for row in rows) == 1


def test_stale_edit_has_no_mutation(snapshot):
    current = build_draft(**snapshot)
    before = deepcopy(current)
    payload = s.DraftEdit(
        version=current.version + 1,
        edits=[
            s.SentenceEdit(sentence_id=None, section="DEVIATIONS", text="새 문장", reason="편집")
        ],
    )
    with pytest.raises(ApplicationError) as caught:
        drafts.edit(current, payload, set())
    assert caught.value.code == "TFT_VERSION_CONFLICT" and current == before
