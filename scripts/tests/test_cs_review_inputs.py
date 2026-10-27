"""기존 입력 정제와 실제 개발 로더를 합성 CSV로 연결하는 관리 도구 회귀."""

import csv
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.cs_review_inputs import prepare_review, source_groups


class InputReviewTests(unittest.TestCase):
    def test_followup_group_uses_only_positive_ascii_counts(self):
        """회신 수의 특수 숫자나 매우 긴 문자열로 감사 작업이 중단되지 않는다.

        Args:
            없음.

        Returns:
            None: 유효한 양의 ASCII 정수 표기만 회신 그룹으로 표시한다.
        """
        for value, expected in (
            ("²", False),
            ("２", False),
            ("000", False),
            ("002", True),
            ("9" * 5000, True),
        ):
            with self.subTest(value=value[:8]):
                self.assertEqual(
                    "SOURCE_FOLLOWUP" in source_groups({"customer_followups": value}), expected
                )

    def setUp(self):
        """Git 제외 출력과 합성 파일만 있는 독립 작업 공간을 만든다.

        Args:
            없음.

        Returns:
            None: 외부 개발 자료는 열지 않는다.
        """
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True)
        (self.root / ".gitignore").write_text("out*/\n")

    def source(self, bodies, channels=None):
        """합성 개발 CSV와 호환 manifest를 기록한다.

        Args:
            bodies: 문의 본문 목록.
            channels: 본문별 채널. 생략하면 PHONE.

        Returns:
            tuple: manifest 경로와 원문 hash.
        """
        stream = io.StringIO(newline="")
        fields = [
            "ticket_id",
            "data_split",
            "channel",
            "body_language",
            "inquiry_subject",
            "inquiry_body",
            "category_code",
            "secondary_category",
            "urgency_flag",
            "priority",
            "final_reply",
            "reference_doc_id",
            "customer_followups",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for i, body in enumerate(bodies):
            writer.writerow(
                {
                    "ticket_id": f"CASE-{i}",
                    "data_split": "train" if i == 0 else "valid",
                    "channel": channels[i] if channels else "PHONE",
                    "body_language": "en",
                    "inquiry_subject": "",
                    "inquiry_body": body,
                    "category_code": "RETURN",
                    "secondary_category": "MANUAL",
                    "urgency_flag": "SITE_DOWN",
                    "priority": "GOLD_SECRET",
                    "final_reply": "FUTURE_SECRET",
                    "reference_doc_id": "FLT-042",
                    "customer_followups": "2",
                }
            )
        raw = stream.getvalue().encode()
        (self.root / "dev.csv").write_bytes(raw)
        manifest = self.root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "tickets": {
                        "path": "dev.csv",
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "splits": ["train", "valid"],
                        "rows": len(bodies),
                    }
                }
            )
        )
        return manifest, hashlib.sha256(manifest.read_bytes()).hexdigest()

    def test_candidates_and_holds_preserve_denominators_and_separate_labels(self):
        """정제·보류·모델 입력 비주입·패턴 마스킹을 실제 build_input으로 검증한다.

        Args:
            없음.

        Returns:
            None: 원본 보존과 전체 행의 감사 기록·검수 대기를 확인한다.
        """
        manifest, digest = self.source(
            [
                "Urgency: SAFETY\nserial: PRIVATE_SERIAL user@example.com please help",
                "Urgency: high please help",
                "Urgency: NONE",
                "Urgency: SAFETY user@example.com please help",
            ],
            ["PHONE", "PHONE", "PHONE", "EMAIL"],
        )
        before = (self.root / "dev.csv").read_bytes()
        summary = prepare_review(manifest, digest, self.root / "out")
        self.assertEqual(
            (summary["total"], summary["candidate"], summary["held"], summary["model_ready"]),
            (4, 2, 2, 0),
        )
        self.assertEqual(
            summary["hold_reasons"], {"CS_UNSUPPORTED_PHONE_ANNOTATION": 1, "CS_EMPTY_INPUT": 1}
        )
        self.assertEqual(summary["strata"]["train/PHONE"]["removed_annotations"], 1)
        candidates = [
            json.loads(line)
            for line in (self.root / "out/candidates.jsonl").read_text().splitlines()
        ]
        payloads = json.dumps([c["payload"] for c in candidates])
        for secret in (
            "PRIVATE_SERIAL",
            "user@example.com",
            "GOLD_SECRET",
            "FUTURE_SECRET",
            "SITE_DOWN",
            "FLT-042",
        ):
            self.assertNotIn(secret, payloads)
        self.assertNotIn("Urgency", candidates[0]["payload"]["body"])
        self.assertIn("Urgency: SAFETY", candidates[1]["payload"]["body"])
        self.assertIn("[SERIAL_PROVIDED]", candidates[0]["payload"]["body"])
        self.assertTrue(
            all(
                set(c["payload"]) == {"channel", "body_language", "subject", "body"}
                for c in candidates
            )
        )
        self.assertTrue(all(c["privacy_review_status"] == "pending" for c in candidates))
        audit = [
            json.loads(line) for line in (self.root / "out/audit.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len(audit), 4)
        self.assertEqual(len({a["case_id"] for a in audit}), 4)
        self.assertIn("SOURCE_BLOCKED_REFERENCE", audit[0]["source_groups"])
        self.assertNotIn("please help", json.dumps(audit))
        self.assertEqual((self.root / "dev.csv").read_bytes(), before)
        self.assertEqual((self.root / "out").stat().st_mode & 0o777, 0o700)
        for path in (self.root / "out").iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        for name, checksum in summary["files"].items():
            self.assertEqual(
                hashlib.sha256((self.root / "out" / name).read_bytes()).hexdigest(), checksum
            )

    def test_wrong_hash_or_external_path_is_rejected_before_loader(self):
        """승인 hash 불일치와 인수 폴더 밖 파일을 읽기 전에 거부한다.

        Args:
            없음.

        Returns:
            None: 개발 소비자가 호출되지 않았음을 확인한다.
        """
        manifest, digest = self.source(["help"])
        with patch("scripts.cs_review_inputs.load_dev_tickets") as loader:
            with self.assertRaisesRegex(ValueError, "CS_REVIEW_MANIFEST_MISMATCH"):
                prepare_review(manifest, "0" * 64, self.root / "out")
            loader.assert_not_called()
            value = json.loads(manifest.read_text())
            value["tickets"]["path"] = "../outside.csv"
            manifest.write_text(json.dumps(value))
            digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, "CS_REVIEW_SOURCE_OUTSIDE_BUNDLE"):
                prepare_review(manifest, digest, self.root / "out")
            loader.assert_not_called()
            value["tickets"]["path"] = "dev.csv"
            value["tickets"]["splits"] = ["test"]
            manifest.write_text(json.dumps(value))
            digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, "CS_REVIEW_SPLITS_INVALID"):
                prepare_review(manifest, digest, self.root / "out")
            loader.assert_not_called()
        self.assertFalse((self.root / "out").exists())

    def test_unexpected_failure_does_not_become_success_or_leak_details(self):
        """예상 밖 정제 오류는 안전한 코드로 전체 작업을 실패시킨다.

        Args:
            없음.

        Returns:
            None: 원문 오류 메시지나 부분 결과가 저장되지 않음을 확인한다.
        """
        manifest, digest = self.source(["help"])
        with patch("scripts.cs_review_inputs.build_input", side_effect=ValueError("RAW_SECRET")):
            with self.assertRaisesRegex(ValueError, "^CS_REVIEW_UNEXPECTED_FAILURE$"):
                prepare_review(manifest, digest, self.root / "out")
        self.assertFalse((self.root / "out").exists())


if __name__ == "__main__":
    unittest.main()
