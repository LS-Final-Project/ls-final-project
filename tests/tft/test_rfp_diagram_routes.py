"""Auth/version/asset-integrity boundaries using synthetic bytes, never live documents."""

import asyncio
import hashlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.api.tft.rfp import routes_diagrams as routes
from backend.api.tft.rfp.diagram_artifacts import DiagramManifest, preserve_images
from backend.api.tft.rfp.storage import LocalDocumentStore
from backend.engine.documents.diagrams import DiagramGraph
from backend.engine.documents.vision import ImageInput, OCRText, VisionPlan, VisionResult
from backend.shared.access import Principal
from backend.shared.errors import ApplicationError
from tests.tft.test_rfp_diagrams import diagram


@pytest.fixture
def context(monkeypatch, tmp_path):
    document_id, version_id, project_id = uuid4(), uuid4(), uuid4()
    principal = Principal(user_id=uuid4(), roles=frozenset({"sales"}))
    row = {"document_id": document_id, "project_id": project_id, "sha256": "a" * 64}
    conn = SimpleNamespace(execute=AsyncMock())
    cursor = SimpleNamespace(fetchall=AsyncMock(return_value=[]))
    conn.execute.return_value = cursor
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=object())))

    @asynccontextmanager
    async def connection(_):
        yield conn

    monkeypatch.setattr(routes, "connection", connection)
    version = AsyncMock(return_value=row)
    visible = AsyncMock(return_value={})
    read = AsyncMock(return_value=None)
    monkeypatch.setattr(routes.repository, "document_version_row", version)
    monkeypatch.setattr(routes, "visible_project", visible)
    monkeypatch.setattr(routes.diagram_repository, "read_diagrams", read)
    store = LocalDocumentStore(tmp_path)
    monkeypatch.setattr(routes.storage, "document_store", lambda _: store)
    return SimpleNamespace(**locals())


def test_original_bytes_preserved_and_version_scoped_download(context):
    c = context
    raw = b"authored original PNG bytes"
    source = ImageInput("OCR-DOCX-0001", "paragraph 1 image", "image/png", raw)
    result = VisionResult([OCRText(source, [], [], True, DiagramGraph.model_validate(diagram()))])
    payload = asyncio.run(preserve_images(c.store, c.version_id, VisionPlan([source]), result))
    assert payload["items"][0]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert c.store._path(payload["items"][0]["storage_key"]).read_bytes() == raw
    c.read.return_value = {"source_sha256": c.row["sha256"], "payload": payload}
    response = asyncio.run(
        routes.image(c.request, c.principal, c.document_id, c.version_id, source.source_id)
    )
    assert response.body == raw
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    c.visible.assert_awaited_once()
    listed = asyncio.run(routes.list_diagrams(c.request, c.principal, c.document_id, c.version_id))
    assert "storage_key" not in listed.model_dump_json()
    assert listed.items[0].graph.review_required is True
    c.store._path(payload["items"][0]["storage_key"]).write_bytes(b"corrupt")
    with pytest.raises(ApplicationError) as error:
        asyncio.run(
            routes.image(c.request, c.principal, c.document_id, c.version_id, source.source_id)
        )
    assert error.value.status_code == 503


def test_missing_analysis_is_not_empty_success(context):
    c = context
    out = asyncio.run(routes.list_diagrams(c.request, c.principal, c.document_id, c.version_id))
    assert out.processing_status == "NOT_ANALYZED" and not out.items
    c.read.return_value = {
        "source_sha256": c.row["sha256"],
        "payload": {"schema_version": 1, "items": [], "warnings": []},
    }
    out = asyncio.run(routes.list_diagrams(c.request, c.principal, c.document_id, c.version_id))
    assert out.processing_status == "ANALYZED" and not out.items


def test_wrong_document_cannot_read_version_manifest(context):
    c = context
    with pytest.raises(ApplicationError) as error:
        asyncio.run(routes.list_diagrams(c.request, c.principal, uuid4(), c.version_id))
    assert error.value.status_code == 404
    c.read.assert_not_awaited()


def test_project_scope_denial_precedes_manifest_read(context):
    c = context
    c.visible.side_effect = ApplicationError(404, "NO_SCOPE", "not found")
    with pytest.raises(ApplicationError):
        asyncio.run(routes.list_diagrams(c.request, c.principal, c.document_id, c.version_id))
    c.read.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 2, "items": [], "warnings": []},
        {"schema_version": 1, "items": [], "warnings": [], "extra": True},
    ],
)
def test_invalid_manifest_is_unavailable(context, payload):
    c = context
    c.read.return_value = {"source_sha256": c.row["sha256"], "payload": payload}
    with pytest.raises(ApplicationError) as error:
        asyncio.run(routes.list_diagrams(c.request, c.principal, c.document_id, c.version_id))
    assert error.value.status_code == 503


def test_source_hash_mismatch_not_served(context):
    c = context
    c.read.return_value = {
        "source_sha256": "b" * 64,
        "payload": {"schema_version": 1, "items": [], "warnings": []},
    }
    with pytest.raises(ApplicationError):
        asyncio.run(routes.list_diagrams(c.request, c.principal, c.document_id, c.version_id))


def test_tag_only_links_preserve_ambiguity_and_reject_substrings():
    item = SimpleNamespace(graph=DiagramGraph.model_validate(diagram()))
    rows = [
        {"requirement_id": uuid4(), "version": 2, "code": str(i), "equipment": text}
        for i, text in enumerate(
            ["TR-01 transformer", "TR-01", "TR-010", "XTR-01", "tr-01", "변압기"]
        )
    ]
    links = routes.tag_links(item, rows)
    assert [link.requirement_id for link in links] == [
        rows[0]["requirement_id"],
        rows[1]["requirement_id"],
    ]
    assert all(link.requirement_version == 2 for link in links)


def test_no_images_manifest_avoids_model_and_asset_writes(tmp_path):
    payload = asyncio.run(
        preserve_images(LocalDocumentStore(tmp_path), uuid4(), VisionPlan(), VisionResult())
    )
    assert DiagramManifest.model_validate(payload).items == []
    assert not list(tmp_path.iterdir())
