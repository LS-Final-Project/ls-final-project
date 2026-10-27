"""Guarded loaders for the C/S development data (docs/data-contract.md, KAN-83 인수 조건).

The integrated tickets file contains the test split, so it is never parsed here. A data
manager provides physically split train/valid files and a manifest. Each file's SHA-256 is
compared with the manifest before a single row is parsed, and a manifest that lists the test
split is refused.

Manifest (JSON, paths relative to the manifest):

    {
      "tickets": {"path": "...", "sha256": "...", "splits": ["train", "valid"], "rows": 3403},
      "return_subtype_gold": {"path": "...", "sha256": "...", "review_version": "..."}
    }

`return_subtype_gold` is optional until KAN-95 locks it. Its CSV has the columns
`ticket_id,return_subtype` with `DEFECT`, `NON_DEFECT` or an empty value (unconfirmed).
"""

import csv
import hashlib
import json
from pathlib import Path

DEV_SPLITS = frozenset({"train", "valid"})
SUBTYPES = frozenset({"DEFECT", "NON_DEFECT", ""})


class DevDataError(ValueError):
    """The provided files do not match what the manifest promises."""


def _entry(manifest_path: Path, name: str) -> tuple[Path, dict]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = manifest.get(name)
    if not isinstance(entry, dict):
        raise DevDataError(f"The manifest has no {name} entry")
    path = (manifest_path.parent / entry["path"]).resolve()
    # Hash the bytes before parsing, so a wrong file is refused without reading its rows.
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != entry["sha256"]:
        raise DevDataError(f"{name}: SHA-256 differs from the manifest")
    return path, entry


def has_entry(manifest_path: Path, name: str) -> bool:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return isinstance(manifest.get(name), dict)


def load_dev_tickets(manifest_path: Path) -> list[dict[str, str]]:
    path, entry = _entry(manifest_path, "tickets")
    splits = set(entry.get("splits", []))
    if not splits or not splits <= DEV_SPLITS:
        raise DevDataError(f"Only train/valid files are allowed, got {sorted(splits)}")
    with open(path, encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    outside = {row["data_split"] for row in rows} - splits
    if outside:
        raise DevDataError(f"The tickets file holds rows from {sorted(outside)}")
    if len(rows) != entry["rows"]:
        raise DevDataError(f"Expected {entry['rows']} rows, found {len(rows)}")
    return rows


def load_return_subtypes(manifest_path: Path, targets: set[str]) -> dict[str, str | None]:
    """Reviewed return subtypes for exactly `targets`. Empty means unconfirmed (None)."""
    path, entry = _entry(manifest_path, "return_subtype_gold")
    if not entry.get("review_version"):
        raise DevDataError("The return subtype gold needs a review version")
    with open(path, encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != ["ticket_id", "return_subtype"]:
            raise DevDataError(f"Unexpected columns: {reader.fieldnames}")
        rows = list(reader)
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
