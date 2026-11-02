"""Offline authored bytes; no Drive, supplied documents, auth, DB or model calls."""

import copy
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scripts.local_business import download as module


def file_row(path="docs/policy.md", data=b"authored fixture", **extra):
    return {
        "id": path,
        "relative_path": path,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "drive_id": "fixture-id",
        "mime_type": "text/markdown",
        **extra,
    }


def manifest(row=None):
    return {
        "schema_version": 1,
        "profiles": {"cs": ["corpus"]},
        "groups": {"corpus": [row or file_row()]},
        "missing": [],
    }


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.destination = self.root / "artifacts" / "inputs"

    def test_plan_is_offline_and_creates_nothing(self):
        with patch.object(module.subprocess, "run") as run:
            report = module.plan(manifest(), self.destination)
        self.assertTrue(report["ready"])
        self.assertEqual(report["counts"], {"files": 1, "skip": 0})
        run.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_exact_file_skips_and_conflict_fails_before_any_fetch(self):
        with patch.object(
            module, "_fetch", side_effect=lambda r, p: p.write_bytes(b"authored fixture")
        ) as fetch:
            first = module.download(manifest(), self.destination)
            second = module.download(manifest(), self.destination)
            self.assertEqual((first["downloaded"], second["downloaded"]), (1, 0))
            self.assertEqual(fetch.call_count, 1)
        (self.destination / "docs/policy.md").write_bytes(b"different")
        with patch.object(module, "_fetch") as fetch:
            with self.assertRaisesRegex(ValueError, "DOWNLOAD_EXISTING_CONFLICT"):
                module.download(manifest(), self.destination)
            fetch.assert_not_called()

    def test_missing_source_is_visible_and_blocks_before_writes(self):
        m = manifest()
        m["missing"] = [
            {"group": "corpus", "relative_path": "docs/unavailable.md", "reason": "no ID"}
        ]
        self.assertFalse(module.plan(m, self.destination)["ready"])
        with patch.object(module, "_fetch") as fetch:
            with self.assertRaisesRegex(ValueError, "DOWNLOAD_SOURCE_MISSING"):
                module.download(m, self.destination)
            fetch.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_bad_paths_duplicates_and_native_exports_rejected(self):
        for path in (
            ".",
            "../escape",
            "/absolute",
            "docs/../escape",
            "docs//file",
            "docs/./file",
            "docs\\file",
            "data/file",
            "gold/file.csv",
            "expected.json",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                module.plan(manifest(file_row(path)), self.destination)
        for change in (
            {"mime_type": "application/vnd.google-apps.document"},
            {"bytes": True},
            {"sha256": "A" * 64},
            {"drive_id": "../id"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                module.validate_manifest(manifest(file_row(**change)))
        m = manifest()
        m["groups"]["corpus"].append(copy.deepcopy(m["groups"]["corpus"][0]))
        with self.assertRaises(ValueError):
            module.validate_manifest(m)
        with self.assertRaisesRegex(ValueError, "DOWNLOAD_DESTINATION_INVALID"):
            module.plan(manifest(), self.root / "data")

    def test_symlink_ancestors_and_leaf_never_followed(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "artifacts").symlink_to(outside, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            module.download(manifest(), self.destination)
        (self.root / "artifacts").unlink()
        self.destination.mkdir(parents=True)
        (self.destination / "docs").symlink_to(outside, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            module.download(manifest(), self.destination)
        (self.destination / "docs").unlink()
        (self.destination / "docs").mkdir()
        (outside / "target").write_bytes(b"authored fixture")
        (self.destination / "docs/policy.md").symlink_to(outside / "target")
        with self.assertRaises((OSError, ValueError)):
            module.download(manifest(), self.destination)
        self.assertEqual((outside / "target").read_bytes(), b"authored fixture")

    def test_hash_failure_leaves_no_published_or_temporary_file(self):
        with patch.object(module, "_fetch", side_effect=lambda r, p: p.write_bytes(b"bad")):
            with self.assertRaisesRegex(ValueError, "DOWNLOAD_HASH_MISMATCH"):
                module.download(manifest(), self.destination)
        self.assertEqual(list(self.destination.rglob("*")), [self.destination / "docs"])

    def test_atomic_publication_does_not_clobber_concurrent_file(self):
        original = os.link

        def race(src, dst, **kwargs):
            fd = os.open(
                dst, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=kwargs["dst_dir_fd"]
            )
            os.write(fd, b"other writer")
            os.close(fd)
            return original(src, dst, **kwargs)

        with (
            patch.object(
                module, "_fetch", side_effect=lambda r, p: p.write_bytes(b"authored fixture")
            ),
            patch.object(module.os, "link", side_effect=race),
        ):
            with self.assertRaisesRegex(ValueError, "DOWNLOAD_EXISTING_CONFLICT"):
                module.download(manifest(), self.destination)
        self.assertEqual((self.destination / "docs/policy.md").read_bytes(), b"other writer")
        self.assertFalse(list(self.destination.rglob(".download-*")))

    def archive(self, *, duplicate=False, symlink=False):
        data = b"selected fixture"
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as z:
            info = zipfile.ZipInfo("bundle/manifest.json")
            if symlink:
                info.external_attr = 0o120777 << 16
            z.writestr(info, data)
            if duplicate:
                z.writestr(info, data)
            z.writestr("../escape", b"unselected")
            z.writestr("bundle/expected.json", b"unselected")
        raw = stream.getvalue()
        row = file_row("bundles/development.zip", raw, mime_type="application/zip")
        row["members"] = [
            {
                k: v
                for k, v in file_row("bundle/manifest.json", data).items()
                if k in {"relative_path", "sha256", "bytes"}
            }
            | {"archive_path": "bundle/manifest.json"}
        ]
        return raw, manifest(row)

    def test_zip_extracts_only_explicit_hash_pinned_members_and_reruns(self):
        raw, m = self.archive()
        with patch.object(module, "_fetch", side_effect=lambda r, p: p.write_bytes(raw)) as fetch:
            self.assertEqual(module.download(m, self.destination)["downloaded"], 2)
            self.assertEqual(module.download(m, self.destination)["downloaded"], 0)
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(
            (self.destination / "bundle/manifest.json").read_bytes(), b"selected fixture"
        )
        self.assertFalse((self.root / "escape").exists())
        self.assertFalse((self.destination / "bundle/expected.json").exists())

    def test_zip_duplicate_and_symlink_members_rejected(self):
        for options in ({"duplicate": True}, {"symlink": True}):
            raw, m = self.archive(**options)
            destination = self.destination / ("duplicate" if "duplicate" in options else "symlink")
            with patch.object(module, "_fetch", side_effect=lambda r, p: p.write_bytes(raw)):
                with self.assertRaisesRegex(ValueError, "DOWNLOAD_ARCHIVE_INVALID"):
                    module.download(m, destination)
            self.assertFalse((destination / "bundle/manifest.json").exists())

    def test_member_hash_failure_never_publishes_selected_file(self):
        raw, m = self.archive()
        m["groups"]["corpus"][0]["members"][0]["sha256"] = "0" * 64
        with patch.object(module, "_fetch", side_effect=lambda r, p: p.write_bytes(raw)):
            with self.assertRaisesRegex(ValueError, "DOWNLOAD_HASH_MISMATCH"):
                module.download(m, self.destination)
        self.assertFalse((self.destination / "bundle/manifest.json").exists())
        self.assertFalse(list(self.destination.rglob(".download-*")))

    def test_rfp_origins_and_native_mime_are_not_interchangeable(self):
        row = file_row(
            "rfp/document.docx",
            source_category="evaluation",
            origin="demo",
            project_name="fixture project",
            customer_code="FIXTURE",
            title="fixture",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        m = {"schema_version": 1, "profiles": {"rfp": ["rfp"]}, "groups": {"rfp": [row]}}
        module.validate_manifest(m)
        for change in (
            {"origin": "synthetic"},
            {"source_category": "unknown"},
            {"mime_type": "application/zip"},
        ):
            invalid = copy.deepcopy(m)
            invalid["groups"]["rfp"][0].update(change)
            with (
                self.subTest(change=change),
                self.assertRaisesRegex(ValueError, "DOWNLOAD_RFP_INVALID"),
            ):
                module.validate_manifest(invalid)
        row.update(source_category="generated_demo", origin="synthetic")
        module.validate_manifest(m)

    def test_gws_command_uses_media_file_and_suppresses_provider_output(self):
        with patch.object(module.subprocess, "run") as run:
            module._fetch(file_row(), Path("/tmp/private-file"))
        args, kwargs = run.call_args
        command = args[0]
        self.assertEqual(command[:4], ["gws", "drive", "files", "get"])
        self.assertEqual(
            json.loads(command[5]),
            {"fileId": "fixture-id", "alt": "media", "supportsAllDrives": True},
        )
        self.assertEqual(command[-2:], ["--output", "/tmp/private-file"])
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        with patch.object(
            module.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(1, [], stderr="TOKEN PRIVATE"),
        ):
            with self.assertRaisesRegex(ValueError, "^DOWNLOAD_GWS_FAILED$"):
                module._fetch(file_row(), Path("/tmp/private-file"))

    def test_committed_manifest_counts_and_rfp_registration_labels(self):
        m = json.loads(
            (Path(__file__).resolve().parents[2] / "docs/local-business-downloads.json").read_text()
        )
        module.validate_manifest(m)
        self.assertEqual(
            {k: len(v) for k, v in m["groups"].items()}, {"corpus": 108, "cs_history": 7, "rfp": 22}
        )
        self.assertEqual(
            {r["id"] for r in m["groups"]["rfp"] if r["source_category"] == "evaluation"},
            {"D10", "D11", "D12", "D18"},
        )
        self.assertEqual(len(m["missing"]), 1)
        members = next(r["members"] for r in m["groups"]["cs_history"] if "members" in r)
        self.assertEqual(
            {Path(r["relative_path"]).name for r in members},
            {"manifest.json", "tickets.train.csv", "tickets.valid.csv", "tickets.dev.csv"},
        )


if __name__ == "__main__":
    unittest.main()
