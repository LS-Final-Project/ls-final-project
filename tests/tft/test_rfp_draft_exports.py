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
    assert texts.count("검토용 초안입니다. 고객 제출 전 담당자 확인이 필요합니다.") == 2
    assert texts[0] == texts[-1] == DISCLAIMER
    assert '한글 <장비> & "조건"' in texts and "다음 줄" in texts and "탭" in texts
    assert len(list(root.iter(w + "br"))) >= 2 and list(root.iter(w + "tab"))
    assert "조건·미확인: 조건 <허용> & 미확약" in texts
    assert any("재검토: 예" in text for text in texts)
    numbers = iter(range(1, 1000))
    for section in draft.sections:
        assert draft_exports.SECTION_TITLES[section.section] in texts
        for sentence in section.sentences:
            assert f"문장 번호: S{next(numbers)}" in texts
            assert str(sentence.sentence_id) not in " ".join(texts)
            for line in draft_exports.evidence_lines(sentence, {}):
                assert line in texts


def test_evidence_is_grouped_by_kind_with_readable_numbers():
    """근거는 종류별 한 줄이고 번호를 모르는 ID도 빠지지 않는다."""
    row = s.Sentence.model_construct(
        evidence=[
            s.Evidence(type="requirement", id="r1"),
            s.Evidence(type="clause", id="c1"),
            s.Evidence(type="requirement", id="r2"),
        ]
    )
    assert draft_exports.evidence_lines(row, {"r1": "2.4.E-1", "c1": "2.4.E"}) == [
        "근거 요구 2건: 2.4.E-1, r2",
        "근거 조항 1건: 2.4.E",
    ]


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
    with (
        patch.object(jobs, "connection", side_effect=AssertionError("must not open a connection")),
        patch.object(
            jobs.commands, "replay", side_effect=AssertionError("must not touch commands")
        ),
        patch.object(jobs.commands, "audit", side_effect=AssertionError("must not audit PDF")),
        patch.object(draft_exports, "reserve", side_effect=AssertionError("must not reserve PDF")),
    ):
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
    assert not moved.needs_rereview and moved.provenance_kind == "human_input"
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


@pytest.mark.parametrize(
    ("operation", "result", "entity", "target"),
    [
        ("review_version.create", {"review_version": 3, "draft_version": 2}, "review_version", 3),
        (
            "export.create",
            {"export_id": str(UUID(int=9)), "job_id": str(UUID(int=8)), "status": "QUEUED"},
            "export",
            str(UUID(int=9)),
        ),
    ],
)
def test_command_receipt_audits_review_or_export_resource(operation, result, entity, target):
    """새 공통 감사 매핑이 job ID 대신 실제 검토본/내보내기를 기록한다(merge 회귀)."""
    from unittest.mock import AsyncMock

    from backend.api.tft.rfp import commands

    conn = AsyncMock()
    actor, project_id = UUID(int=1), UUID(int=2)
    with patch.object(commands, "audit", new_callable=AsyncMock) as record:
        asyncio.run(
            commands.remember(
                conn,
                actor=actor,
                operation=operation,
                key="receipt-key",
                digest="0" * 64,
                project_id=project_id,
                status_code=202 if operation == "export.create" else 201,
                result=result,
            )
        )
    record.assert_awaited_once()
    assert record.call_args.kwargs["entity"] == entity
    assert record.call_args.kwargs["entity_id"] == target
    assert record.call_args.kwargs["actor"] == actor


def test_export_retry_reuses_immutable_bytes_after_live_review_changes(snapshot, tmp_path):
    """파일 저장 후 중단해도 현재 요구의 검토 표시와 무관하게 같은 검토본 바이트를 재사용한다."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from backend.agent.processor_registry import TftJob
    from backend.api.tft.rfp import draft_repository as db
    from backend.api.tft.rfp.storage import LocalDocumentStore

    original = build_draft(**snapshot)
    changed = original.model_copy(deep=True)
    changed.sections[0].sentences[0].needs_rereview = True
    export_id = UUID(int=99)
    job = TftJob(
        job_id=UUID(int=98),
        kind="rfp.export",
        project_id=original.project_id,
        input_version=draft_exports.input_version(export_id),
        actor_id=UUID(int=97),
    )
    row = dict(
        id=export_id,
        review_version=1,
        job_id=None,
        created_by=job.actor_id,
        format="DOCX",
        completed_at=None,
    )
    conn = AsyncMock()

    async def execute(sql, params):
        if "FROM tft.exports" in sql:
            return SimpleNamespace(fetchall=AsyncMock(return_value=[row]))
        if "FROM tft.review_versions" in sql:
            return SimpleNamespace(
                fetchone=AsyncMock(return_value={"draft_version": original.version})
            )
        return AsyncMock()

    conn.execute.side_effect = execute
    store = LocalDocumentStore(tmp_path)
    events = []

    def interrupt_after_put(event):
        events.append(event)
        if len(events) == 2:
            raise RuntimeError("crash after object creation")

    with (
        patch.object(db, "load", new=AsyncMock(side_effect=[original, changed])) as live,
        patch.object(db, "load_snapshot", new=AsyncMock(return_value=original)) as frozen,
        patch.object(draft_exports, "audit", new_callable=AsyncMock) as audit,
    ):
        with pytest.raises(RuntimeError, match="crash after object"):
            asyncio.run(draft_exports.process(conn, job, interrupt_after_put, store))
        path = tmp_path / f"rfp/{job.project_id}/exports/{export_id}.docx"
        expected = draft_exports.render_docx(original, 1)
        assert path.read_bytes() == expected
        result = asyncio.run(draft_exports.process(conn, job, lambda event: None, store))
        assert result["needs_review"] is False and path.read_bytes() == expected
        assert frozen.await_count == 2 and live.await_count == 0
        audit.assert_awaited_once()


def test_edit_stores_snapshot_flags_and_returns_live_display(snapshot):
    """DB 없는 경계 검사: live 합산 flags를 편집 저장 입력으로 재사용하지 않는다."""
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from backend.api.tft.rfp import draft_repository as db

    stored = build_draft(**snapshot)
    row = stored.sections[0].sentences[0]
    row.needs_rereview = row.unconfirmed = row.evidence_missing = False
    live = stored.model_copy(deep=True)
    live.version += 1
    flagged = live.sections[0].sentences[0]
    flagged.needs_rereview = flagged.unconfirmed = flagged.evidence_missing = True
    payload = s.DraftEdit(
        version=stored.version,
        edits=[
            s.SentenceEdit(
                sentence_id=None,
                text="별도 문장 편집",
                reason="확인",
                section="CUSTOMER_GOAL_SCOPE",
                evidence=row.evidence,
            )
        ],
    )

    @asynccontextmanager
    async def connect(_settings):
        yield object()

    app = create_app(Settings(_env_file=None, environment="test"))
    with (
        patch.object(routes_draft, "connection", connect),
        patch.object(routes_draft, "visible", new_callable=AsyncMock),
        patch.object(db, "lock", new_callable=AsyncMock),
        patch.object(db, "head", new_callable=AsyncMock, return_value=stored.version),
        patch.object(db, "load_snapshot", new_callable=AsyncMock, return_value=stored) as frozen,
        patch.object(db, "load", new_callable=AsyncMock, return_value=live) as display,
        patch.object(db, "save", new_callable=AsyncMock) as save,
        patch.object(db, "evidence_labels", new_callable=AsyncMock, return_value={}),
        patch.object(
            drafts,
            "valid_references",
            new_callable=AsyncMock,
            return_value={(e.type, e.id) for e in row.evidence},
        ),
        patch.object(routes_draft.commands, "audit", new_callable=AsyncMock),
        patch.object(routes_draft.repository, "clear_sentence_rereview", new_callable=AsyncMock),
        TestClient(app) as client,
    ):
        result = client.patch(
            f"/api/v1/tft/projects/{stored.project_id}/draft",
            json=payload.model_dump(mode="json"),
            headers=principal_header(Principal(user_id=UUID(int=1), roles={"sales"})),
        )
    assert result.status_code == 200, result.text
    persisted = save.call_args.args[1].sections[0].sentences[0]
    assert (
        not persisted.needs_rereview
        and not persisted.unconfirmed
        and not persisted.evidence_missing
    )
    assert result.json()["data"]["sections"][0]["sentences"][0]["needs_rereview"] is True
    frozen.assert_awaited_once()
    display.assert_awaited_once()
