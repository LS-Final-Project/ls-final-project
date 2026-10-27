"""명시 승인 후 원본을 보존하고 C/S train/valid 사본만 생성하는 관리 도구."""

import argparse
import csv
import hashlib
import io
import json
import os
import shutil
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path


def csv_bytes(fields: Sequence[str], rows: list[dict[str, str]]) -> bytes:
    """개발 행을 UTF-8 CSV로 직렬화한다.

    Args:
        fields: 원본 열 순서.
        rows: 이미 분리·검증한 개발 행.

    Returns:
        bytes: 원본 정답을 포함하는 검수 전용 CSV. 모델 입력이 아니다.
    """
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def create_bundle(
    source: Path,
    output: Path,
    source_ref: str,
    authorization_ref: str,
    expected: tuple[int, int] = (2800, 603),
) -> dict:
    """승인된 원본을 한 번 읽고 시험 행을 저장하지 않는 개발 묶음을 만든다.

    Args:
        source: 읽기 전용 통합 CSV.
        output: 존재하지 않는 출력 디렉터리. data 하위는 금지한다.
        source_ref: 원천 판본·위치의 비공개 기록 참조.
        authorization_ref: 기계적 분리 작업 승인 기록. 정답 검수 승인이 아니다.
        expected: 예상 train/valid 건수. 다르면 사본을 생성하지 않는다.

    Returns:
        dict: 원문 없이 hash·건수·처리 이력을 담은 manifest.
    """
    output = output.resolve()
    if (
        output.exists()
        or "data" in output.parts
        or not source_ref.strip()
        or not authorization_ref.strip()
    ):
        raise ValueError("CS_SPLIT_CONFIGURATION_INVALID")
    with source.open("rb") as file:
        raw = file.read(32 * 1024 * 1024 + 1)
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError("CS_SPLIT_SOURCE_TOO_LARGE")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
    fields = reader.fieldnames
    if (
        not fields
        or not {"ticket_id", "data_split"} <= set(fields)
        or len(fields) != len(set(fields))
        or not all(fields)
    ):
        raise ValueError("CS_SPLIT_COLUMNS_INVALID")
    parts: dict[str, list[dict[str, str]]] = {"train": [], "valid": []}
    seen: set[str] = set()
    skipped = 0
    for row in reader:
        split = row.get("data_split")
        if split == "test":
            skipped += 1
            continue
        if split not in parts or None in row or any(value is None for value in row.values()):
            raise ValueError("CS_SPLIT_ROWS_INVALID")
        ticket_id = row["ticket_id"]
        if not ticket_id or ticket_id != ticket_id.strip() or ticket_id in seen:
            raise ValueError("CS_SPLIT_ROWS_INVALID")
        seen.add(ticket_id)
        parts[split].append(row)
    if tuple(len(parts[s]) for s in ("train", "valid")) != expected:
        raise ValueError("CS_SPLIT_COUNTS_MISMATCH")
    payloads = {f"tickets.{s}.csv": csv_bytes(fields, rows) for s, rows in parts.items()}
    payloads["tickets.dev.csv"] = csv_bytes(fields, parts["train"] + parts["valid"])
    manifest = {
        "tickets": {
            "path": "tickets.dev.csv",
            "sha256": hashlib.sha256(payloads["tickets.dev.csv"]).hexdigest(),
            "splits": ["train", "valid"],
            "rows": sum(expected),
        },
        "partitions": {
            s: {
                "path": f"tickets.{s}.csv",
                "sha256": hashlib.sha256(payloads[f"tickets.{s}.csv"]).hexdigest(),
                "rows": len(rows),
            }
            for s, rows in parts.items()
        },
        "provenance": {
            "source_ref": source_ref,
            "source_sha256": hashlib.sha256(raw).hexdigest(),
            "authorization_ref": authorization_ref,
            "created_at": datetime.now(UTC).isoformat(),
            "procedure": "cs-dev-split-v1",
            "data_origin": "synthetic",
            "derivation_kind": "DERIVED",
            "test_rows_discarded": skipped,
            "review_status": "pending",
            "purpose": "development_review_and_rules_only",
        },
    }
    # 새로 만든 디렉터리만 정리하며 기존 사용자 파일은 건드리지 않는다.
    output.mkdir(mode=0o700, parents=False)
    try:
        payloads["manifest.json"] = (
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        ).encode()
        for name, content in payloads.items():
            with os.fdopen(
                os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
            ) as file:
                file.write(content)
    except BaseException:
        shutil.rmtree(output)
        raise
    return manifest


def main() -> None:
    """명시한 승인 참조로 관리 작업을 실행하고 안전한 집계만 출력한다.

    Args:
        없음. 경로와 승인 참조는 명령행에서 받는다.

    Returns:
        None: 성공 시 건수·hash만 출력하고 실패 시 원문 없는 코드로 종료한다.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--authorization-ref", required=True)
    args = parser.parse_args()
    try:
        result = create_bundle(args.source, args.output, args.source_ref, args.authorization_ref)
    except (ValueError, OSError, csv.Error):
        raise SystemExit("CS_SPLIT_FAILED: 원본·출력 경로·형식·건수를 확인하세요.") from None
    print(
        json.dumps(
            {
                "rows": result["tickets"]["rows"],
                "sha256": result["tickets"]["sha256"],
                "review_status": "pending",
            }
        )
    )


if __name__ == "__main__":
    main()
