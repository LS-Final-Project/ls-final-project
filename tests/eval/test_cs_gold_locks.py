from pathlib import Path

from eval.cs_gold_locks import GOLD_BUNDLE_ID, GOLD_LOCKS

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
