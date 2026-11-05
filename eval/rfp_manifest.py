"""RFP 개발·평가 입력 파일의 바이트 해시만 검증한다(KAN-197). 본문을 추출하지 않는다."""

import argparse
import hashlib
import json
from pathlib import Path

DEFAULT_MANIFEST = Path(__file__).with_name("manifests") / "rfp-documents-20261029-v1.json"
EXPECTED = {
    10: "evaluation",
    11: "evaluation",
    12: "evaluation",
    14: "development",
    17: "development",
    18: "evaluation",
}


def verify(manifest: dict, directory: Path) -> list[str]:
    """번호·용도·경로·해시를 검증하고 불일치 목록을 반환한다. 파일은 읽기만 한다."""
    errors = []
    documents = manifest["documents"]
    numbers = [row["document_number"] for row in documents]
    if len(numbers) != len(set(numbers)) or set(numbers) != set(EXPECTED):
        errors.append("Expected unique documents 10, 11, 12, 14, 17, 18")
    for row in documents:
        number = row["document_number"]
        name = row["filename"]
        if row["split"] != EXPECTED.get(number) or row["document_id"] != f"D{number:02d}":
            errors.append(f"D{number:02d}: number/split mismatch")
        if Path(name).name != name or not name.endswith(".docx"):
            errors.append(f"D{number:02d}: invalid filename")
            continue
        path = directory / name
        if not path.is_file():
            errors.append(f"D{number:02d}: missing file")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != row["sha256"]:
            errors.append(f"D{number:02d}: SHA-256 mismatch")
    return errors


def main() -> None:
    """원문 폴더를 명시적으로 받아 검증한다. 평가나 LLM 호출은 하지 않는다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    errors = verify(json.loads(args.manifest.read_text()), args.directory)
    if errors:
        raise SystemExit("\n".join(errors))
    print("Verified 6 document mappings and SHA-256 hashes; no evaluation performed.")


if __name__ == "__main__":
    main()
