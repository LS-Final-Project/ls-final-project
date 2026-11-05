"""합성 파일로 누락·변조·분할 오류를 검출한다. 실제 평가 문서를 읽지 않는다."""

import hashlib
import json

from eval.rfp_manifest import DEFAULT_MANIFEST, EXPECTED, verify


def test_manifest_split_and_exposure_policy():
    manifest = json.loads(DEFAULT_MANIFEST.read_text())
    assert {r["document_number"]: r["split"] for r in manifest["documents"]} == EXPECTED
    assert manifest["policy"]["evaluation_used_for_model_prompt_selection"] is False
    assert manifest["policy"]["independent_unexposed_holdout"] is False


def test_missing_tampered_duplicate_and_wrong_split(tmp_path):
    documents = []
    for number, split in EXPECTED.items():
        name = f"synthetic-{number}.docx"
        (tmp_path / name).write_bytes(b"synthetic")
        documents.append(
            dict(
                document_number=number,
                document_id=f"D{number:02d}",
                split=split,
                filename=name,
                sha256=hashlib.sha256(b"synthetic").hexdigest(),
            )
        )
    manifest = {"documents": documents}
    assert verify(manifest, tmp_path) == []
    (tmp_path / documents[0]["filename"]).write_bytes(b"changed")
    assert any("SHA-256 mismatch" in e for e in verify(manifest, tmp_path))
    (tmp_path / documents[1]["filename"]).unlink()
    assert any("missing file" in e for e in verify(manifest, tmp_path))
    documents[2]["split"] = "development"
    assert any("split mismatch" in e for e in verify(manifest, tmp_path))
    documents.append(documents[0])
    assert any("unique documents" in e for e in verify(manifest, tmp_path))
