"""KAN-99 3차 문서 색인 적재 CLI의 실제 적재. cs_database 픽스처와 cs_app 권한으로 소형 DOCX 하나를 왕복한다."""

import io
import zipfile

import psycopg
from psycopg.conninfo import make_conninfo

from backend.agent.cs_search import fts_searcher_from_dsn
from backend.engine.search.chunking import INDEX_VERSION
from backend.engine.search.model import SearchFilters

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
    # 백엔드 검사 이미지에는 scripts/가 check_backend.py만 있어, DB가 있을 때(cs_database 통과 뒤)만 가져온다.
    from scripts.cs_index_docs import index_documents

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


def test_embed_mode_fills_only_missing_chunks_and_stops_on_provider_error(
    cs_database, tmp_path, monkeypatch, capsys
):
    """--embed는 적재 뒤 임베딩이 없는 청크만 넣고, 단독 재실행은 0건이며, 공급자 오류면 사유 코드로 멈춘다(KAN-99)."""
    import json

    from scripts.cs_index_docs import main

    dsn = make_conninfo(cs_database, user="cs_app", password="test-only")
    monkeypatch.setenv("DATABASE_URL", dsn)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "embed.docx").write_bytes(docx("Embedding probe KAN99 vector chunk."))
    calls = []
    inputs = []

    def fake(text):
        calls.append(len(text))
        inputs.append(text)
        return [0.5] * 768

    def broken(text):
        raise RuntimeError("PRIVATE_PROVIDER_MESSAGE")

    base = [
        "--docs",
        str(tmp_path / "docs"),
        "--data-origin",
        "synthetic",
        "--document-kind",
        "CS_EMBED_TEST",
    ]
    try:
        out = tmp_path / "m.json"
        assert main([*base, "--execute", "--embed", "--manifest-out", str(out)], embedder=fake) == 0
        embedding = json.loads(out.read_text())["embedding"]
        assert embedding["embedding_version"] == "gemini-embedding-2:768:RETRIEVAL_DOCUMENT"
        # 임베딩 입력은 맥락 머리(여기서는 문서 종류) 뒤에 본문이 온다.
        assert all(text.startswith("CS_EMBED_TEST") for text in inputs)
        assert (
            embedding["embedded"] >= 1
            and embedding["calls"] == len(calls)
            and embedding["reason"] is None
        )
        assert embedding["approx_input_cost"] is not None
        calls.clear()
        assert main([*base, "--embed", "--manifest-out", str(out)], embedder=fake) == 0
        again = json.loads(out.read_text())
        assert again["mode"] == "embed" and again["documents"] == []
        assert again["embedding"]["embedded"] == 0
        with psycopg.connect(cs_database, autocommit=True) as conn:
            conn.execute(
                "DELETE FROM cs.chunk_embeddings e USING cs.document_chunks c WHERE c.id = e.chunk_row_id AND c.document_kind = 'CS_EMBED_TEST'"
            )
        capsys.readouterr()
        assert main([*base, "--embed", "--manifest-out", str(out)], embedder=broken) == 1
        failed = json.loads(out.read_text())["embedding"]
        assert failed["reason"] == "CS_EMBED_PROVIDER_ERROR" and failed["embedded"] == 0
        assert "PRIVATE_PROVIDER_MESSAGE" not in capsys.readouterr().out + out.read_text()
        assert main([*base, "--dry-run", "--embed"], embedder=fake) == 1
    finally:
        with psycopg.connect(cs_database, autocommit=True) as conn:
            conn.execute("DELETE FROM cs.document_chunks WHERE document_kind='CS_EMBED_TEST'")


def test_embed_client_setup_failure_is_a_code_and_is_skipped_when_nothing_is_missing(
    cs_database, tmp_path, monkeypatch, capsys
):
    """임베딩 클라이언트를 만들지 못하면 CS_INDEX_EMBED_FAILED·종료 1(원문·키 미노출)이고, 대상이 0건이면 만들지 않는다."""
    import scripts.cs_index_docs as cli

    dsn = make_conninfo(cs_database, user="cs_app", password="test-only")
    monkeypatch.setenv("DATABASE_URL", dsn)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "setup.docx").write_bytes(docx("Embedding setup probe KAN99."))
    made = []

    def no_key(*args):
        made.append(args)
        raise RuntimeError("PRIVATE_KEY_AIzaSECRET")

    monkeypatch.setattr(cli, "default_embedder", no_key)
    base = [
        "--docs",
        str(tmp_path / "docs"),
        "--data-origin",
        "synthetic",
        "--document-kind",
        "CS_EMBED_SETUP",
    ]
    try:
        assert cli.main([*base, "--execute", "--embed"]) == 1
        err = capsys.readouterr().err
        assert "CS_INDEX_EMBED_FAILED" in err and "PRIVATE_KEY" not in err and "AIza" not in err
        assert len(made) == 1
        assert cli.main([*base, "--embed"], embedder=lambda text: [0.25] * 768) == 0
        made.clear()
        assert cli.main([*base, "--embed"]) == 0
        assert made == []
    finally:
        with psycopg.connect(cs_database, autocommit=True) as conn:
            conn.execute("DELETE FROM cs.document_chunks WHERE document_kind='CS_EMBED_SETUP'")


def test_embed_limit_stops_without_calls_when_too_many_chunks_are_missing(
    cs_database, tmp_path, monkeypatch, capsys
):
    """--embed-limit보다 대상이 많으면 호출 없이 CS_EMBED_OVER_LIMIT·종료 1이고, 상한 안이면 넣는다(KAN-99 비용 상한)."""
    import json

    from scripts.cs_index_docs import main

    monkeypatch.setenv(
        "DATABASE_URL", make_conninfo(cs_database, user="cs_app", password="test-only")
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "limit.docx").write_bytes(docx("Embedding limit probe KAN99."))
    calls = []

    def fake(text):
        calls.append(text)
        return [0.125] * 768

    base = [
        "--docs",
        str(tmp_path / "docs"),
        "--data-origin",
        "synthetic",
        "--document-kind",
        "CS_EMBED_LIMIT",
    ]
    out = tmp_path / "m.json"
    try:
        assert main([*base, "--execute"]) == 0
        assert (
            main(
                [*base, "--embed", "--embed-limit", "0", "--manifest-out", str(out)], embedder=fake
            )
            == 1
        )
        stopped = json.loads(out.read_text())["embedding"]
        assert stopped["reason"] == "CS_EMBED_OVER_LIMIT" and stopped["missing"] >= 1
        assert stopped["calls"] == 0 and calls == []
        assert main([*base, "--embed", "--embed-limit", "100000"], embedder=fake) == 0
        assert calls
    finally:
        with psycopg.connect(cs_database, autocommit=True) as conn:
            conn.execute("DELETE FROM cs.document_chunks WHERE document_kind='CS_EMBED_LIMIT'")
