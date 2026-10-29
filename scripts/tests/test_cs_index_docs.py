"""KAN-99 3차 문서 색인 적재 CLI. 코드로 만든 DOCX와 주입한 적재 함수로 DB 없이 검사한다."""

import contextlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts import cs_index_docs

NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def docx(*paragraphs: str) -> bytes:
    """문단만 있는 최소 DOCX.

    Args:
        paragraphs: 문단 문장.

    Returns:
        bytes: DOCX 바이트.
    """
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml", f"<w:document {NS}><w:body>{body}</w:body></w:document>"
        )
    return buffer.getvalue()


class IndexDocsTest(unittest.TestCase):
    """찾기·DSN 경계·dry-run·적재·manifest."""

    def setUp(self) -> None:
        """임시 문서 디렉터리를 만든다.

        Returns:
            None: 준비.
        """
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name) / "docs"
        (self.root / "faq").mkdir(parents=True)
        (self.root / ".cache").mkdir()
        (self.root / "faq" / "warranty.docx").write_bytes(docx("Warranty period is 18 months."))
        (self.root / "policy.DOCX").write_bytes(docx("Do not quote FLT-042 to customers."))
        (self.root / "broken.pdf").write_bytes(b"not a pdf at all")
        (self.root / "notes.txt").write_text("SECRET-NOTE", encoding="utf-8")
        (self.root / "faq" / "trip.md").write_text(
            "# Trip FAQ\n\nTrip reset steps.\n", encoding="utf-8"
        )
        (self.root / ".hidden.docx").write_bytes(docx("hidden"))
        (self.root / ".cache" / "x.docx").write_bytes(docx("hidden"))
        env = patch.dict(
            os.environ, {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}, clear=True
        )
        env.start()
        self.addCleanup(env.stop)

    def run_main(self, *args, **kwargs):
        """main을 부르고 (코드, 표준 출력, 표준 오류)를 돌려준다.

        Args:
            args: 명령행 인자.
            kwargs: main 키워드.

        Returns:
            tuple: (코드, stdout, stderr).
        """
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cs_index_docs.main(
                ["--docs", str(self.root), "--data-origin", "synthetic", *args], **kwargs
            )
        return code, out.getvalue(), err.getvalue()

    def test_finds_only_visible_docx_and_pdf(self) -> None:
        """재귀로 .docx·.pdf만 고르고 숨김 파일·폴더는 뺀다.

        Returns:
            None: 고른 상대 경로를 확인한다.
        """
        found = [str(p.relative_to(self.root)) for p in cs_index_docs.find_documents(self.root)]
        self.assertEqual(found, ["broken.pdf", "faq/trip.md", "faq/warranty.docx", "policy.DOCX"])

    def test_local_dsn_check(self) -> None:
        """로컬 호스트·소켓만 로컬로 본다.

        Returns:
            None: 판정 표를 확인한다.
        """
        for dsn, local in [
            ("postgresql://u:p@localhost:5432/x", True),
            ("postgresql://u:p@127.0.0.1/x", True),
            ("postgresql://u:p@db/x", True),
            ("host=/var/run/postgresql dbname=x", True),
            ("postgresql:///x", True),
            ("postgresql://u:p@10.0.0.5/x", False),
            ("postgresql://u:p@team4-sql.example.com/x", False),
            ("postgresql://u:p@localhost,10.0.0.5/x", False),
        ]:
            self.assertEqual(cs_index_docs.is_local(dsn), local, dsn)

    def test_dry_run_reports_counts_and_reasons_without_text(self) -> None:
        """dry-run은 적재하지 않고 문서별 수와 사유 코드만 manifest에 쓰며 입력을 바꾸지 않는다.

        Returns:
            None: manifest·출력·입력 불변을 확인한다.
        """
        before = sorted((p.name, p.stat().st_mtime_ns) for p in self.root.rglob("*"))
        manifest = Path(self.dir.name) / "manifest.json"
        code, out, err = self.run_main("--manifest-out", str(manifest))
        self.assertEqual(code, 0, err)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        rows = {row["source_name"]: row for row in data["documents"]}
        self.assertEqual(data["mode"], "dry-run")
        self.assertEqual(rows["broken.pdf"]["reason"], "CS_INDEX_EXTRACT_REJECTED")
        self.assertEqual(rows["faq/warranty.docx"]["chunks"], 1)
        self.assertIsNone(rows["faq/warranty.docx"]["stored"])
        self.assertEqual(rows["policy.DOCX"]["blocked_code_chunks"], 1)
        self.assertEqual(len(rows["policy.DOCX"]["sha256"]), 64)
        text = manifest.read_text(encoding="utf-8") + out + err
        self.assertEqual(rows["faq/trip.md"]["chunks"], 1)
        for secret in ("Warranty period", "Do not quote", "SECRET-NOTE", "Trip reset"):
            self.assertNotIn(secret, text)
        self.assertEqual(
            before, sorted((p.name, p.stat().st_mtime_ns) for p in self.root.rglob("*"))
        )

    def test_execute_stores_with_injected_store_and_records_failures(self) -> None:
        """--execute는 문서마다 적재하고, 적재 실패는 사유 코드로 남긴다.

        Returns:
            None: 적재 수·시각·실패 사유를 확인한다.
        """
        calls = []

        def store(dsn, result, metadata, origin, known_at):
            """적재를 흉내 낸다(정책 문서는 실패).

            Args:
                dsn: DSN.
                result: 청킹 결과.
                metadata: 메타데이터.
                origin: data_origin.
                known_at: 알게 된 시각.

            Returns:
                int: 저장 행 수.
            """
            calls.append((origin, metadata.document_kind))
            if "FLT-042" in result.chunks[0].text:
                raise RuntimeError("PRIVATE DB ERROR")
            return len(result.chunks)

        manifest = Path(self.dir.name) / "manifest.json"
        code, out, err = self.run_main(
            "--execute", "--document-kind", "CS_FAQ", "--manifest-out", str(manifest), store=store
        )
        self.assertEqual(code, 0, err)
        rows = {row["source_name"]: row for row in json.loads(manifest.read_text())["documents"]}
        self.assertEqual(rows["faq/warranty.docx"]["stored"], 1)
        self.assertIsNotNone(rows["faq/warranty.docx"]["stored_at"])
        self.assertEqual(rows["policy.DOCX"]["reason"], "CS_INDEX_STORE_FAILED")
        self.assertEqual(calls, [("synthetic", "CS_FAQ")] * 3)
        self.assertNotIn("PRIVATE DB ERROR", out + err + manifest.read_text())

    def test_doc_index_fills_metadata_by_path(self) -> None:
        """doc_index.csv의 path로 문서를 찾아 doc_type·family·revision을 메타데이터에 넣고, 없는 문서는 전역값을 쓴다.

        Returns:
            None: 적재 메타데이터와 manifest doc_id를 확인한다.
        """
        index = self.root / "doc_index.csv"
        index.write_text(
            "doc_id,title,doc_type,product_code,family,revision,path,chars\n"
            "D-01,Trip,FAQ,ACB-08,ACB,r2,faq/trip.md,20\n",
            encoding="utf-8",
        )
        seen = {}

        def store(dsn, result, metadata, origin, known_at):
            """메타데이터를 모은다.

            Args:
                dsn: DSN.
                result: 청킹 결과.
                metadata: 메타데이터.
                origin: data_origin.
                known_at: 알게 된 시각.

            Returns:
                int: 저장 행 수.
            """
            seen[result.chunks[0].document_sha256] = metadata
            return len(result.chunks)

        manifest = Path(self.dir.name) / "manifest.json"
        code, _, err = self.run_main(
            "--execute",
            "--document-kind",
            "CS_DOC",
            "--doc-index",
            str(index),
            "--manifest-out",
            str(manifest),
            store=store,
        )
        self.assertEqual(code, 0, err)
        rows = {row["source_name"]: row for row in json.loads(manifest.read_text())["documents"]}
        trip, warranty = (
            seen[rows["faq/trip.md"]["sha256"]],
            seen[rows["faq/warranty.docx"]["sha256"]],
        )
        self.assertEqual(rows["faq/trip.md"]["doc_id"], "D-01")
        self.assertEqual(
            (trip.document_kind, trip.product_family, trip.version), ("FAQ", "ACB", "r2")
        )
        self.assertEqual((warranty.document_kind, warranty.version), ("CS_DOC", None))
        self.assertIsNone(rows["faq/warranty.docx"]["doc_id"])
        index.write_text("doc_id,path\nD-01,faq/trip.md\n", encoding="utf-8")
        code, _, err = self.run_main("--doc-index", str(index))
        self.assertEqual(code, 1)
        self.assertIn("CS_INDEX_DOC_INDEX_INVALID", err)

    def test_execute_requires_local_database(self) -> None:
        """--execute는 DATABASE_URL이 없거나 로컬이 아니면(허용 플래그 없이) 1로 끝난다.

        Returns:
            None: 종료 코드·안내를 확인한다.
        """
        code, _, err = self.run_main("--execute")
        self.assertEqual(code, 1)
        self.assertIn("CS_INDEX_NEEDS_DB", err)
        os.environ["DATABASE_URL"] = "postgresql://cs_app:pw@team4-sql.example.com/team4"
        code, _, err = self.run_main("--execute")
        self.assertEqual(code, 1)
        self.assertIn("CS_INDEX_REMOTE_DB", err)
        self.assertNotIn("pw@", err)

    def test_bad_arguments_exit_1(self) -> None:
        """디렉터리가 아니거나 판본이 청킹 판본과 다르면 1이다.

        Returns:
            None: 종료 코드를 확인한다.
        """
        self.assertEqual(self.run_main("--index-version", "other-v9")[0], 1)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cs_index_docs.main(
                ["--docs", str(self.root / "missing"), "--data-origin", "demo"]
            )
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
