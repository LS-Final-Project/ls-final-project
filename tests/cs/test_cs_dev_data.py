"""The development data loaders refuse the integrated file and loose gold files.

Uses small synthetic files only; no provided data is read.
"""

import hashlib
import json

import pytest
from cs_dev_data import DevDataError, has_entry, load_dev_tickets, load_return_subtypes

HEADER = "ticket_id,data_split,category_code\n"
TARGETS = {"T-1", "T-2"}


def write(tmp_path, tickets: str, gold: str | None = None, **changes) -> object:
    (tmp_path / "tickets_dev.csv").write_text(tickets, encoding="utf-8")
    manifest: dict = {
        "tickets": {
            "path": "tickets_dev.csv",
            "sha256": hashlib.sha256(tickets.encode()).hexdigest(),
            "splits": ["train", "valid"],
            "rows": tickets.count("\n") - 1,
        }
    }
    if gold is not None:
        (tmp_path / "gold.csv").write_text(gold, encoding="utf-8")
        manifest["return_subtype_gold"] = {
            "path": "gold.csv",
            "sha256": hashlib.sha256(gold.encode()).hexdigest(),
            "review_version": "kan95-v1",
        }
    for name, value in changes.items():
        section, field = name.split("__")
        manifest[section][field] = value
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


DEV = HEADER + "T-1,train,RETURN\nT-2,valid,RETURN\n"


def test_split_dev_file_is_loaded(tmp_path):
    rows = load_dev_tickets(write(tmp_path, DEV))
    assert [r["ticket_id"] for r in rows] == ["T-1", "T-2"]


def test_a_file_with_test_rows_is_refused(tmp_path):
    integrated = DEV + "T-3,test,DEFECT\n"
    with pytest.raises(DevDataError, match="test"):
        load_dev_tickets(write(tmp_path, integrated))


def test_a_manifest_listing_the_test_split_is_refused(tmp_path):
    with pytest.raises(DevDataError, match="train/valid"):
        load_dev_tickets(write(tmp_path, DEV, tickets__splits=["train", "test"]))


def test_a_file_that_differs_from_the_manifest_is_not_parsed(tmp_path):
    path = write(tmp_path, DEV)
    (tmp_path / "tickets_dev.csv").write_text(DEV + "T-3,test,DEFECT\n", encoding="utf-8")
    with pytest.raises(DevDataError, match="SHA-256"):
        load_dev_tickets(path)


def test_row_count_must_match(tmp_path):
    with pytest.raises(DevDataError, match="rows"):
        load_dev_tickets(write(tmp_path, DEV, tickets__rows=3))


GOLD = "ticket_id,return_subtype\nT-1,DEFECT\nT-2,\n"


def test_gold_maps_empty_to_unconfirmed(tmp_path):
    path = write(tmp_path, DEV, GOLD)
    assert has_entry(path, "return_subtype_gold")
    assert load_return_subtypes(path, TARGETS) == {"T-1": "DEFECT", "T-2": None}


@pytest.mark.parametrize(
    ("gold", "message"),
    [
        ("ticket_id,return_subtype\nT-1,DEFECT\nT-1,NON_DEFECT\nT-2,\n", "Duplicate"),
        ("ticket_id,return_subtype\nT-1,DEFECT\n", "missing=\\['T-2'\\]"),
        ("ticket_id,return_subtype\nT-1,DEFECT\nT-2,\nT-9,DEFECT\n", "extra=\\['T-9'\\]"),
        ("ticket_id,return_subtype\nT-1,defect\nT-2,\n", "unknown return_subtype"),
        ("ticket_id,subtype\nT-1,DEFECT\nT-2,\n", "columns"),
    ],
)
def test_loose_gold_files_are_refused(tmp_path, gold, message):
    with pytest.raises(DevDataError, match=message):
        load_return_subtypes(write(tmp_path, DEV, gold), TARGETS)


def test_gold_needs_its_hash_and_review_version(tmp_path):
    path = write(tmp_path, DEV, GOLD, return_subtype_gold__review_version="")
    with pytest.raises(DevDataError, match="review version"):
        load_return_subtypes(path, TARGETS)
    path = write(tmp_path, DEV, GOLD, return_subtype_gold__sha256="0" * 64)
    with pytest.raises(DevDataError, match="SHA-256"):
        load_return_subtypes(path, TARGETS)


def test_upper_case_hash_is_accepted(tmp_path):
    digest = hashlib.sha256(DEV.encode()).hexdigest().upper()
    assert len(load_dev_tickets(write(tmp_path, DEV, tickets__sha256=digest))) == 2


def test_missing_manifest_fields_and_columns_are_reported(tmp_path):
    path = write(tmp_path, DEV)
    manifest = json.loads(path.read_text())
    del manifest["tickets"]["sha256"]
    path.write_text(json.dumps(manifest))
    with pytest.raises(DevDataError, match="sha256"):
        load_dev_tickets(path)
    with pytest.raises(DevDataError, match="data_split"):
        load_dev_tickets(write(tmp_path, "ticket_id,category_code\nT-1,RETURN\n"))
