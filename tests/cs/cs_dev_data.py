"""Guarded loaders for the C/S development data (docs/data-contract.md, KAN-83 인수 조건).

관리자가 물리적으로 분리한 개발 파일과 검수 manifest를 제공해야 한다. hash는 무결성
검사이며 분리 승인이 아니다. 잘못 승인된 파일의 시험 행은 파싱 후 거부하므로 시험
비접근을 보장하지 않는다. 이 로더는 원본을 분리하거나 사람의 승인을 대신하지 않는다.

Manifest (JSON, paths relative to the manifest and inside its folder):

    {
      "tickets": {"path": "...", "sha256": "...", "splits": ["train", "valid"], "rows": 3403},
      "return_subtype_gold": {"path": "...", "sha256": "...", "review_version": "..."}
    }

`return_subtype_gold` is optional until KAN-95 locks it. Its CSV has the columns
`ticket_id,return_subtype` with `DEFECT`, `NON_DEFECT` or an empty value (unconfirmed).
"""

import csv
import hashlib
import io
import json
from pathlib import Path

DEV_SPLITS = frozenset({"train", "valid"})
SUBTYPES = frozenset({"DEFECT", "NON_DEFECT", ""})


class DevDataError(ValueError):
    """The provided files do not match what the manifest promises."""


def _entry(manifest_path: Path, name: str, section: str | None = None) -> tuple[bytes, dict]:
    """The file's bytes, checked against the manifest. Callers parse these same bytes, so the
    file cannot change between the hash check and the read. `section` reads a nested entry
    such as `partitions.train`."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = (manifest.get(section) or {}).get(name) if section else manifest.get(name)
    if not isinstance(entry, dict):
        raise DevDataError(f"The manifest has no {name} entry")
    root = manifest_path.parent.resolve()
    try:
        path = (root / entry["path"]).resolve()
        expected = str(entry["sha256"]).lower()
    except KeyError as missing:
        raise DevDataError(f"{name}: manifest field {missing} is missing") from None
    # The manifest only vouches for files in its own folder (docs/data-contract.md).
    if not path.is_relative_to(root):
        raise DevDataError(f"{name}: path must stay inside the manifest folder")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise DevDataError(f"{name}: SHA-256 differs from the manifest")
    return data, entry


def _rows(data: bytes) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
    return list(reader.fieldnames or []), list(reader)


def has_entry(manifest_path: Path, name: str) -> bool:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return isinstance(manifest.get(name), dict)


def load_dev_tickets(manifest_path: Path) -> list[dict[str, str]]:
    """개발 문의의 split·건수·ID 유일성과 CSV 구조를 검증한다.

    Args:
        manifest_path: 관리자가 검수한 개발 파일 목록 경로.

    Returns:
        list: 인수 검사를 통과한 개발 문의 행.
    """
    data, entry = _entry(manifest_path, "tickets")
    splits = set(entry.get("splits", []))
    if not splits or not splits <= DEV_SPLITS:
        raise DevDataError(f"Only train/valid files are allowed, got {sorted(splits)}")
    fields, rows = _rows(data)
    if "data_split" not in fields:
        raise DevDataError("The tickets file has no data_split column")
    if "ticket_id" not in fields or len(fields) != len(set(fields)) or not all(fields):
        raise DevDataError("CS_DEV_ROWS_INVALID")
    seen = set()
    for row in rows:
        if None in row or any(value is None for value in row.values()):
            raise DevDataError("CS_DEV_ROWS_INVALID")
        ticket_id = row["ticket_id"]
        if not ticket_id or ticket_id != ticket_id.strip() or ticket_id in seen:
            raise DevDataError("CS_DEV_ROWS_INVALID")
        seen.add(ticket_id)
    outside = {row["data_split"] for row in rows} - splits
    if outside:
        raise DevDataError(f"The tickets file holds rows from {sorted(outside)}")
    if len(rows) != entry.get("rows"):
        raise DevDataError(f"Expected {entry.get('rows')} rows, found {len(rows)}")
    return rows


def load_return_subtypes(manifest_path: Path, targets: set[str]) -> dict[str, str | None]:
    """Reviewed return subtypes for exactly `targets`. Empty means unconfirmed (None)."""
    data, entry = _entry(manifest_path, "return_subtype_gold")
    if not entry.get("review_version"):
        raise DevDataError("The return subtype gold needs a review version")
    fields, rows = _rows(data)
    if fields != ["ticket_id", "return_subtype"]:
        raise DevDataError(f"Unexpected columns: {fields}")
    gold: dict[str, str | None] = {}
    for row in rows:
        ticket_id, subtype = row["ticket_id"], row["return_subtype"]
        if ticket_id in gold:
            raise DevDataError(f"Duplicate ticket_id {ticket_id}")
        if subtype not in SUBTYPES:
            raise DevDataError(f"{ticket_id}: unknown return_subtype {subtype!r}")
        gold[ticket_id] = subtype or None
    if set(gold) != targets:
        missing, extra = sorted(targets - set(gold)), sorted(set(gold) - targets)
        raise DevDataError(f"Gold rows differ from targets: missing={missing} extra={extra}")
    return gold


def load_partition_products(manifest_path: Path, split: str) -> tuple[dict[str, str], str]:
    """한 개발 분할 파일에서 ticket_id·product_code만 꺼낸다(재요청 지표의 제품군 입력).

    Args:
        manifest_path: 관리자가 검수한 개발 파일 목록 경로.
        split: train 또는 valid. manifest의 partitions 항목을 쓴다.

    Returns:
        tuple[dict[str, str], str]: 문의 ID별 제품 코드와 manifest가 잠근 파일 SHA-256.
            다른 열(정답 포함)은 결과에 남기지 않는다. 분할 밖 행·중복·빈 값·행 수 불일치는 거부한다.
    """
    if split not in DEV_SPLITS:
        raise DevDataError("CS_DEV_SPLIT_INVALID")
    data, entry = _entry(manifest_path, split, "partitions")
    reader = csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""))
    header = next(reader, [])
    try:
        ids, codes, splits = (
            header.index(name) for name in ("ticket_id", "product_code", "data_split")
        )
    except ValueError:
        raise DevDataError("CS_DEV_ROWS_INVALID") from None
    products: dict[str, str] = {}
    for row in reader:
        if len(row) != len(header):
            raise DevDataError("CS_DEV_ROWS_INVALID")
        ticket_id, code = row[ids], row[codes]
        if (
            row[splits] != split
            or not ticket_id
            or ticket_id != ticket_id.strip()
            or not code
            or ticket_id in products
        ):
            raise DevDataError("CS_DEV_ROWS_INVALID")
        products[ticket_id] = code
    if len(products) != entry.get("rows"):
        raise DevDataError("CS_DEV_ROWS_INVALID")
    return products, str(entry["sha256"]).lower()
