"""Real parser/vision/store integration with a synthetic transactional repository, no DB/model."""

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import SecretStr

from backend.agent.processor_registry import TftJob
from backend.api.tft import worker_processor as processor
from backend.api.tft.rfp.storage import LocalDocumentStore
from backend.engine.documents.model import DOCX_MIME
from backend.engine.documents.router import extract
from backend.engine.documents.vision import VisionResult
from backend.engine.rfp.clauses import build_clause_tree
from backend.shared.ai import Generation, Usage
from backend.shared.settings import Settings
from tests.tft.test_rfp_diagrams import diagram
from tests.tft.test_rfp_image_transcription import IMAGE, PNG, docx
from tests.tft.test_worker_processor_db import SPEC


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    class Connection:
        in_tx = False
        manifest = None
        pending = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, *args):
            return None

        @asynccontextmanager
        async def transaction(self):
            self.in_tx = True
            try:
                yield
                if self.pending is not None:
                    self.manifest = self.pending
            finally:
                self.pending = None
                self.in_tx = False

    conn = Connection()
    version = uuid4()
    document = {
        "id": version,
        "latest": True,
        "kind": "DRAWING",
        "title": "Authored single-line fixture",
        "storage_key": "original/input.docx",
        "mime": DOCX_MIME,
        "data_origin": "synthetic",
    }
    store = LocalDocumentStore(tmp_path)
    monkeypatch.setattr(processor.psycopg.AsyncConnection, "connect", AsyncMock(return_value=conn))
    monkeypatch.setattr(
        processor.repository, "all_document_versions", AsyncMock(return_value=[document])
    )
    # Deliberately no saved clauses: replay must recognize a graph-only manifest itself.
    monkeypatch.setattr(processor.repository, "document_processed", AsyncMock(return_value=False))
    monkeypatch.setattr(processor.repository, "store_clauses", AsyncMock(return_value={}))

    async def read(_conn, _version):
        assert _conn is conn and _version == version
        return conn.manifest

    async def persist(_conn, _version, sha, payload):
        assert _conn is conn and _version == version and conn.in_tx
        conn.pending = {"source_sha256": sha, "payload": payload}
        return True

    monkeypatch.setattr(processor.diagram_repository, "read_diagrams", read)
    monkeypatch.setattr(processor.diagram_repository, "store_diagrams", persist)
    monkeypatch.setattr(processor, "store_requirements", AsyncMock(return_value=[]))
    monkeypatch.setattr(processor, "store_question_drafts", AsyncMock())
    monkeypatch.setattr(processor.draft_repository, "generated", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        processor.drafts, "generate", AsyncMock(return_value=SimpleNamespace(sections=[]))
    )
    image_calls, text_calls, events = [], [], []

    def image_provider(item):
        image_calls.append(item)
        return Generation(
            json.dumps(
                {"lines": [], "uncertain": [], "contains_diagram": True, "diagram": diagram()}
            ),
            Usage(),
            "fake",
            ["STOP"],
        )

    def provider(prompt):
        text_calls.append(prompt)
        return processor.fake_provider(prompt)

    settings = Settings(_env_file=None, environment="test", database_url=SecretStr("fixture"))
    job = TftJob(uuid4(), "rfp.process", uuid4(), processor.input_version([version]), uuid4())
    run = processor.make_processor(
        settings,
        provider=provider,
        image_provider=image_provider,
        store=store,
        products=[],
        today=lambda: date(2026, 11, 3),
    )

    def install(data, *, kind="DRAWING", mime=DOCX_MIME):
        document.update(
            kind=kind, mime=mime, sha256=hashlib.sha256(data).hexdigest(), size=len(data)
        )
        asyncio.run(store.put(document["storage_key"], data))

    return SimpleNamespace(
        conn=conn,
        document=document,
        store=store,
        run=lambda: run(job, events.append),
        install=install,
        image_calls=image_calls,
        text_calls=text_calls,
        events=events,
    )


def test_graph_only_drawing_preserves_source_and_manifest_without_text_model_then_reuses(pipeline):
    original = docx(IMAGE)
    pipeline.install(original)
    result = pipeline.run()
    assert result["counts"]["requirements"] == 0 and result["needs_review"]
    assert len(pipeline.image_calls) == 1 and pipeline.text_calls == []
    manifest = pipeline.conn.manifest["payload"]
    asset = manifest["items"][0]
    assert asset["graph"]["review_required"] is True
    assert len(asset["graph"]["nodes"]) == 3
    assert asyncio.run(pipeline.store.get(asset["storage_key"])) == PNG
    assert asyncio.run(pipeline.store.get(pipeline.document["storage_key"])) == original
    replay = pipeline.run()
    assert replay["needs_review"] is True
    assert len(pipeline.image_calls) == 1 and pipeline.text_calls == []
    assert pipeline.conn.manifest["payload"] == manifest


def test_specification_cannot_use_graph_as_substitute_for_text_inputs(pipeline):
    pipeline.install(docx(IMAGE), kind="RFP_SPEC")
    with pytest.raises(ValueError, match="TFT_DOCUMENT_EXTRACTION_INPUT_INVALID"):
        pipeline.run()
    assert len(pipeline.image_calls) == 1 and pipeline.text_calls == []
    assert pipeline.conn.manifest is None


def test_no_image_specification_records_an_empty_manifest_without_vision_call(pipeline):
    pipeline.install(SPEC, kind="RFP_SPEC", mime="text/markdown")
    pipeline.run()
    assert pipeline.image_calls == [] and pipeline.text_calls
    assert pipeline.conn.manifest["payload"] == {"schema_version": 1, "items": [], "warnings": []}


def test_late_workflow_failure_does_not_commit_graph_manifest(monkeypatch, pipeline):
    pipeline.install(docx(IMAGE))
    monkeypatch.setattr(
        processor.drafts, "generate", AsyncMock(side_effect=RuntimeError("DRAFT_FAILED"))
    )
    with pytest.raises(RuntimeError, match="DRAFT_FAILED"):
        pipeline.run()
    assert pipeline.conn.manifest is None and pipeline.conn.pending is None
    assert not pipeline.conn.in_tx
    assert len(pipeline.image_calls) == 1


def test_prior_graph_manifest_wrong_source_rejected_before_provider(pipeline):
    pipeline.install(docx(IMAGE))
    pipeline.conn.manifest = {"source_sha256": "0" * 64, "payload": {}}
    with pytest.raises(ValueError, match="TFT_DIAGRAM_SOURCE_MISMATCH"):
        pipeline.run()
    assert pipeline.image_calls == [] and pipeline.text_calls == []


def test_drawing_without_valid_graph_keeps_existing_empty_input_rejection():
    tree = build_clause_tree(extract(docx(IMAGE), mime=DOCX_MIME))
    with pytest.raises(ValueError, match="TFT_DOCUMENT_EXTRACTION_INPUT_INVALID"):
        processor.drawing_inputs("DRAWING", tree, VisionResult())
