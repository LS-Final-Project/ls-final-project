"""원문 다운로드의 저장 형식·파일명 헤더 회귀. 합성 응답만 사용하며 DB/원문/모델 없음."""

import asyncio
import hashlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import quote, unquote
from uuid import uuid4

import pytest

from backend.api.tft.rfp import routes_read
from backend.engine.documents.model import DOCX_MIME
from backend.shared.access import Principal


@pytest.mark.parametrize(
    "title,mime,filename",
    [
        ("한글 RFP 원문", DOCX_MIME, "한글 RFP 원문.docx"),
        ("입찰 사양 v1.2", "application/pdf", "입찰 사양 v1.2.pdf"),
        ("원문.docx", DOCX_MIME, "원문.docx"),
        ("원문.PDF", "application/pdf", "원문.PDF"),
        ("원문.DOCX", DOCX_MIME, "원문.DOCX"),
        ("제목.pdf", DOCX_MIME, "제목.docx"),
        ("제목.docx", "application/pdf", "제목.pdf"),
        (
            '한글 "공백"; 100% #검토 %2F\r\nX-Injected: value',
            DOCX_MIME,
            '한글 "공백"; 100% #검토 %2F\r\nX-Injected: value.docx',
        ),
        ("기존 형식", "text/html", "기존 형식"),
    ],
)
def test_original_download_filename_matches_stored_format(monkeypatch, title, mime, filename):
    body = b"authored download fixture"
    settings, conn, store = object(), object(), object()
    document_id = uuid4()
    principal = Principal(user_id=uuid4(), roles=frozenset({"sales"}))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
    row = {
        "title": title,
        "mime": mime,
        "storage_key": "opaque-version-key",
        "sha256": hashlib.sha256(body).hexdigest(),
        "size": len(body),
    }

    @asynccontextmanager
    async def connection(actual_settings):
        assert actual_settings is settings
        yield conn

    visible = AsyncMock(return_value=row)
    read = AsyncMock(return_value=body)
    monkeypatch.setattr(routes_read, "connection", connection)
    monkeypatch.setattr(routes_read, "visible_document", visible)
    monkeypatch.setattr(routes_read.storage, "document_store", lambda _: store)
    monkeypatch.setattr(routes_read.storage, "read_document", read)
    response = asyncio.run(routes_read.download_document(request, principal, document_id))

    visible.assert_awaited_once_with(conn, principal, document_id)
    read.assert_awaited_once_with(store, row["storage_key"], row["sha256"], row["size"])
    disposition = response.headers["content-disposition"]
    assert disposition == "attachment; filename*=UTF-8''" + quote(filename, safe="")
    assert unquote(disposition.split("UTF-8''", 1)[1]) == filename
    assert disposition.isascii() and "\r" not in disposition and "\n" not in disposition
    assert response.headers["content-type"].split(";", 1)[0] == mime
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.body == body
    assert row["title"] == title  # 표시 제목이나 원문 데이터는 바꾸지 않는다.
