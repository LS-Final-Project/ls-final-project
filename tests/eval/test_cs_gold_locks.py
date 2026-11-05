import hashlib
from pathlib import Path

from eval.cs_gold_locks import (
    GOLD_BUNDLE_ID,
    GOLD_LOCKS,
    REQUIRED_INFO_PATH,
    REQUIRED_INFO_ROWS,
    REQUIRED_INFO_SHA256,
    REREQUEST_LOCK,
)

DOC = Path(__file__).resolve().parents[2] / "docs" / "evaluation.md"
SECTION = "## C/S 정답 묶음 판본 (KAN-88)"


def split_doc() -> tuple[str, str]:
    text = DOC.read_text(encoding="utf-8")
    head, tail = text.split(SECTION, 1)
    section, _, rest = tail.partition("\n## ")
    return section, head + rest


def test_bundle_table_matches_locked_constants_exactly():
    section, _ = split_doc()
    rows = [
        tuple(cell.strip().strip("`") for cell in line.strip().strip("|").split("|"))
        for line in section.splitlines()
        if line.startswith("| `")
    ]
    expected = [
        (
            metric,
            lock.gold_version,
            lock.gold_sha256,
            ",".join(lock.columns),
            scope,
            f"{train:,}",
            f"{valid:,}",
        )
        for metric, lock in GOLD_LOCKS.items()
        for scope, train, valid in lock.denominators
    ]
    assert rows == expected
    assert f"- 묶음 ID: `{GOLD_BUNDLE_ID}`" in section


def test_each_bundle_entry_is_recorded_in_its_lock_section():
    _, rest = split_doc()
    for lock in GOLD_LOCKS.values():
        assert f"`{lock.gold_version}`" in rest
        assert f"`{lock.gold_sha256}`" in rest


def test_rerequest_lock_and_rule_table_match_the_definition_section():
    text = DOC.read_text(encoding="utf-8")
    section = text.split("## C/S 이미 제공한 정보 재요청 지표 (KAN-95)", 1)[1].partition("\n## ")[0]
    (train, valid), (train_items, valid_items), (train_need, valid_need) = (
        REREQUEST_LOCK.targets,
        REREQUEST_LOCK.gold_items,
        REREQUEST_LOCK.request_needed,
    )
    assert f"3,204건(학습 {train:,}·검증 {valid:,})" in section
    assert f"9,519개(학습 {train_items:,}·검증 {valid_items:,})" in section
    assert f"706건(학습 {train_need:,}·검증 {valid_need:,})" in section
    assert (
        train + valid == 3204
        and train_items + valid_items == 9519
        and train_need + valid_need == 706
    )
    assert f"{REQUIRED_INFO_ROWS}행" in section and f"`{REQUIRED_INFO_SHA256}`" in section
    data = REQUIRED_INFO_PATH.read_bytes()
    assert hashlib.sha256(data).hexdigest() == REQUIRED_INFO_SHA256
    assert data.count(b"\r\n") == REQUIRED_INFO_ROWS + 1
