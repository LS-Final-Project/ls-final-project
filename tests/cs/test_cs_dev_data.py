"""The development loaders reject invalid rows and gold; hashes do not prove separation.

Uses small synthetic files only; no provided data is read.
"""

import hashlib
import json

import pytest
from cs_dev_data import (
    DevDataError,
    has_entry,
    load_dev_tickets,
    load_partition_products,
    load_return_subtypes,
)

HEADER = "ticket_id,data_split,category_code\n"
TARGETS = {"T-1", "T-2"}


def write(tmp_path, tickets: str, gold: str | None = None, **changes) -> object:
    # Bytes, not text: newline translation on Windows would change the hash.
    (tmp_path / "tickets_dev.csv").write_bytes(tickets.encode())
    manifest: dict = {
        "tickets": {
            "path": "tickets_dev.csv",
            "sha256": hashlib.sha256(tickets.encode()).hexdigest(),
            "splits": ["train", "valid"],
            "rows": tickets.count("\n") - 1,
        }
    }
    if gold is not None:
        (tmp_path / "gold.csv").write_bytes(gold.encode())
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
    (tmp_path / "tickets_dev.csv").write_bytes((DEV + "T-3,test,DEFECT\n").encode())
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


@pytest.mark.parametrize(
    "tickets",
    [
        HEADER + "T-1,train,RETURN\nT-1,valid,RETURN\n",
        HEADER + "T-1,train,RETURN\nT-1,train,RETURN\n",
        HEADER + ",train,RETURN\n",
        HEADER + " T-1,train,RETURN\n",
        "data_split,category_code\ntrain,RETURN\n",
        "ticket_id,ticket_id,data_split\nA,B,train\n",
        HEADER + "T-1,train\n",
        HEADER + "T-1,train,RETURN,extra\n",
    ],
)
def test_invalid_development_identity_or_columns_are_rejected(tmp_path, tickets):
    """분모를 왜곡하는 중복·누락 ID와 모호한 CSV 열을 거부한다.

    Args:
        tmp_path: 합성 파일 전용 임시 디렉터리.
        tickets: 검증할 합성 CSV.

    Returns:
        None: 원문을 포함하지 않는 오류를 확인한다.
    """
    with pytest.raises(DevDataError, match="^CS_DEV_ROWS_INVALID$"):
        load_dev_tickets(write(tmp_path, tickets))


@pytest.mark.parametrize("entry_path", ["../outside.csv", "sub/../../outside.csv", "ABSOLUTE"])
def test_paths_outside_the_manifest_folder_are_refused(tmp_path, entry_path):
    folder = tmp_path / "package"
    folder.mkdir()
    outside = tmp_path / "outside.csv"
    outside.write_bytes(DEV.encode())
    if entry_path == "ABSOLUTE":
        entry_path = str(outside)
    path = write(folder, DEV, tickets__path=entry_path)
    with pytest.raises(DevDataError, match="inside the manifest folder"):
        load_dev_tickets(path)


def test_a_link_leading_outside_is_refused(tmp_path):
    folder = tmp_path / "package"
    folder.mkdir()
    (tmp_path / "outside.csv").write_bytes(DEV.encode())
    path = write(folder, DEV, tickets__path="link.csv")
    (folder / "link.csv").symlink_to(tmp_path / "outside.csv")
    with pytest.raises(DevDataError, match="inside the manifest folder"):
        load_dev_tickets(path)


def test_files_in_a_subfolder_are_allowed(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "tickets_dev.csv").write_bytes(DEV.encode())
    path = write(tmp_path, DEV, tickets__path="data/tickets_dev.csv")
    assert len(load_dev_tickets(path)) == 2


PARTITION = "ticket_id,category_code,product_code,data_split\nT-1,RETURN,PLC-01,valid\nT-2,QUOTE,UPS-02,valid\n"


def write_partition(tmp_path, data: str, **changes) -> object:
    (tmp_path / "tickets.valid.csv").write_bytes(data.encode())
    entry = {
        "path": "tickets.valid.csv",
        "sha256": hashlib.sha256(data.encode()).hexdigest(),
        "rows": data.count("\n") - 1,
    } | changes
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"partitions": {"valid": entry}}), encoding="utf-8")
    return path


def test_partition_products_keep_only_id_and_product(tmp_path):
    products, digest = load_partition_products(write_partition(tmp_path, PARTITION), "valid")
    assert products == {"T-1": "PLC-01", "T-2": "UPS-02"}
    assert digest == hashlib.sha256(PARTITION.encode()).hexdigest()
    assert "RETURN" not in repr(products)


@pytest.mark.parametrize(
    "data,changes,split",
    [
        (PARTITION, {}, "test"),
        (PARTITION, {}, "train"),
        (PARTITION.replace("T-2,QUOTE,UPS-02,valid", "T-2,QUOTE,UPS-02,test"), {}, "valid"),
        (PARTITION.replace("UPS-02", ""), {}, "valid"),
        (PARTITION.replace("T-2,", "T-1,"), {}, "valid"),
        (PARTITION.replace("product_code", "product"), {}, "valid"),
        (PARTITION, {"rows": 3}, "valid"),
        (PARTITION, {"sha256": "0" * 64}, "valid"),
    ],
)
def test_partition_products_reject_invalid_files(tmp_path, data, changes, split):
    with pytest.raises(DevDataError):
        load_partition_products(write_partition(tmp_path, data, **changes), split)
