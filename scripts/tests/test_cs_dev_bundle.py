"""제공 원본 대신 임시 합성 CSV로 개발 분리와 소비자 호환성을 검증한다."""

import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cs_dev_bundle", ROOT / "scripts/cs_dev_bundle.py")
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)
spec = importlib.util.spec_from_file_location("cs_dev_data", ROOT / "tests/cs/cs_dev_data.py")
consumer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(consumer)


class DevelopmentBundleTests(unittest.TestCase):
    def test_repository_output_must_be_ignored(self):
        """저장소에서는 Git 제외 경로만 허용해 공개 커밋 실수를 방지한다.

        Args:
            없음.

        Returns:
            None: 일반 출력 거부와 artifacts 허용을 합성 저장소에서 검증한다.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            (root / ".gitignore").write_text("artifacts/\n")
            source = root / "source.csv"
            source.write_text("ticket_id,data_split\nA,train\nB,valid\n")
            with self.assertRaisesRegex(ValueError, "CS_SPLIT_OUTPUT_NOT_IGNORED"):
                bundle.create_bundle(source, root / "out", "source", "approval", (1, 1))
            self.assertFalse((root / "out").exists())
            (root / "artifacts").mkdir()
            result = bundle.create_bundle(
                source, root / "artifacts" / "dev", "source", "approval", (1, 1)
            )
            self.assertEqual(result["tickets"]["rows"], 2)

    def test_only_development_rows_are_saved_and_consumer_accepts_them(self):
        """시험 표식 비출력·원본 보존·개발 분리본 hash와 소비자를 확인한다.

        Args:
            없음.

        Returns:
            None: 합성 자료로 파일 권한과 검수 미완료 표시까지 검증한다.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            (root / ".gitignore").write_text("development/\n")
            source = root / "source.csv"
            raw = b'ticket_id,data_split,body\nA,train,"first\nsecond"\nB,test,DO_NOT_EXPORT\nC,valid,ok\n'
            source.write_bytes(raw)
            output = root / "development"
            manifest = bundle.create_bundle(
                source, output, "synthetic-source", "test-approval", (1, 1)
            )
            self.assertEqual(source.read_bytes(), raw)
            self.assertEqual(
                manifest["provenance"]["source_sha256"], hashlib.sha256(raw).hexdigest()
            )
            self.assertEqual(manifest["provenance"]["test_rows_discarded"], 1)
            self.assertEqual(manifest["provenance"]["review_status"], "pending")
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            for path in output.iterdir():
                self.assertNotIn(b"DO_NOT_EXPORT", path.read_bytes())
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            rows = consumer.load_dev_tickets(output / "manifest.json")
            self.assertEqual([r["ticket_id"] for r in rows], ["A", "C"])
            for split, entry in manifest["partitions"].items():
                self.assertEqual(
                    hashlib.sha256((output / entry["path"]).read_bytes()).hexdigest(),
                    entry["sha256"],
                )
                single = output / f"{split}.json"
                single.write_text(json.dumps({"tickets": {**entry, "splits": [split]}}))
                self.assertEqual(len(consumer.load_dev_tickets(single)), 1)

    def test_invalid_input_creates_no_bundle_and_existing_directory_is_preserved(self):
        """불명 split·중복 ID·건수 불일치와 덮어쓰기를 거부한다.

        Args:
            없음.

        Returns:
            None: 실패 시 원본과 기존 사용자 파일이 보존됨을 확인한다.
        """
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            (root / ".gitignore").write_text("development/\n")
            source = root / "source.csv"
            output = root / "development"
            for rows in ("A,train\nA,valid\n", "A,other\nC,valid\n", "A,train\n"):
                raw = "ticket_id,data_split\n" + rows
                source.write_text(raw)
                with self.assertRaises(ValueError):
                    bundle.create_bundle(source, output, "source", "approval", (1, 1))
                self.assertFalse(output.exists())
                self.assertEqual(source.read_text(), raw)
            output.mkdir()
            sentinel = output / "keep"
            sentinel.write_text("keep")
            with self.assertRaises(ValueError):
                bundle.create_bundle(source, output, "source", "approval", (1, 1))
            self.assertEqual(sentinel.read_text(), "keep")
            with self.assertRaises(ValueError):
                bundle.create_bundle(source, root / "data", "source", "approval", (1, 1))
            self.assertFalse((root / "data").exists())


if __name__ == "__main__":
    unittest.main()
