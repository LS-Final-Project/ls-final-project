"""KAN-99 3차 문서 색인 적재 CLI의 실제 적재. cs_database 픽스처와 cs_app 권한으로 소형 DOCX 하나를 왕복한다."""

import io
import zipfile

import psycopg
from psycopg.conninfo import make_conninfo

from backend.agent.cs_search import fts_searcher_from_dsn
from backend.engine.search.chunking import INDEX_VERSION
from backend.engine.search.model import SearchFilters
from scripts.cs_index_docs import index_documents

NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def docx(text):
    """문단 하나짜리 최소 DOCX.

    Args:
        text: 문단 문장.

    Returns:
        bytes: DOCX 바이트.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f"<w:document {NS}><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>",
        )
    return buffer.getvalue()


def test_index_documents_round_trip(cs_database, tmp_path):
    """DOCX 하나를 적재하고 FTS로 찾으며, 같은 문서를 다시 적재해도 행이 늘지 않는다.

    Args:
        cs_database: 관리자 DSN.
        tmp_path: 임시 디렉터리.

    Returns:
        None: 적재 수·검색·재적재를 확인한다.
    """
    dsn = make_conninfo(cs_database, user="cs_app", password="test-only")
    (tmp_path / "manual.docx").write_bytes(
        docx("Breaker trip curve settings for KAN99 index test.")
    )
    try:
        [row] = index_documents(
            tmp_path, execute=True, dsn=dsn, data_origin="synthetic", document_kind="CS_MANUAL"
        )
        assert row["reason"] is None and row["stored"] == row["chunks"] == 1
        filters = SearchFilters(document_sha256s=[row["sha256"]], index_version=INDEX_VERSION)
        [candidate] = fts_searcher_from_dsn(dsn, INDEX_VERSION)("trip curve", filters)
        assert candidate.document_sha256 == row["sha256"]
        [again] = index_documents(
            tmp_path, execute=True, dsn=dsn, data_origin="synthetic", document_kind="CS_MANUAL"
        )
        assert again["stored"] == 1
        with psycopg.connect(cs_database) as conn:
            count, kind = conn.execute(
                "SELECT count(*), min(document_kind) FROM cs.document_chunks WHERE document_sha256=%s",
                (row["sha256"],),
            ).fetchone()
        assert (count, kind) == (1, "CS_MANUAL")
    finally:
        with psycopg.connect(cs_database, autocommit=True) as conn:
            conn.execute(
                "DELETE FROM cs.document_chunks WHERE document_kind='CS_MANUAL' AND data_origin='synthetic'"
            )
