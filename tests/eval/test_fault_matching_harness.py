from datetime import UTC, datetime, timedelta

from eval.fault_matching import (
    NEW,
    CaseRow,
    Result,
    build_tasks,
    downstream,
    nearest_group,
    result_of,
    summarize,
    summarize_by,
)

T0 = datetime(2025, 10, 1, tzinfo=UTC)


def row(
    case_id: str,
    day: int,
    fault: str,
    *,
    product: str = "P1",
    customer: str | None = None,
    split: str = "valid",
    language: str = "en",
) -> CaseRow:
    return CaseRow(
        case_id=case_id,
        ticket_id=f"T-{case_id}",
        product_code=product,
        product_name=f"{product} name",
        fault_id=fault,
        customer_id=customer or f"C-{case_id}",
        known_at=T0 + timedelta(days=day),
        split=split,
        language=language,
        text=f"text {case_id}",
        symptom_in_text=language == "en",
    )


def test_tasks_use_only_earlier_same_product_history_with_masked_refs() -> None:
    cases = [
        row("a", 0, "F1", split="train"),
        row("b", 1, "F2", split="train"),
        row("x", 1, "F9", product="P2", split="train"),
        row("c", 2, "F1"),
        row("d", 2, "F3"),  # 같은 시각 기록은 이력이 아니다
        row("e", 3, "F2"),
    ]
    tasks = {t.case.case_id: t for t in build_tasks(cases, "valid")}
    assert set(tasks) == {"c", "d", "e"}
    c = tasks["c"]
    assert [g.ref for g in c.payload.groups] == ["G1", "G2"]
    assert c.ref_to_fault == {"G1": "F1", "G2": "F2"}
    assert c.expected == "G1"
    assert tasks["d"].expected == NEW
    assert tasks["e"].expected == "G2"
    assert len(tasks["e"].payload.groups) == 3  # 이력 a·b·c·d의 고장 F1·F2·F3
    dumped = c.payload.model_dump_json()
    assert "F1" not in dumped and "F2" not in dumped


def test_groups_hold_at_most_two_examples() -> None:
    cases = [row(str(i), i, "F1", split="train") for i in range(5)] + [row("q", 9, "F1")]
    (task,) = build_tasks(cases, "valid")
    assert len(task.payload.groups) == 1
    assert task.payload.groups[0].examples == ["text 0", "text 1"]


def test_summary_separates_split_and_merge() -> None:
    results = [
        Result("1", "G1", "EXISTING", "G1", "en", True),
        Result("2", "G1", "EXISTING", "G2", "es", False),
        Result("3", "G2", "NEW", None, "es", False),
        Result("4", NEW, "EXISTING", "G1", "en", True),
        Result("5", NEW, "NEW", None, "en", True),
        Result("6", "G1", "UNSURE", None, "en", True),
    ]
    s = summarize(results)
    assert s["existing"] == {
        "total": 4,
        "correct": 1,
        "merged_into_other": 1,
        "split_as_new": 1,
        "unsure": 1,
        "error": 0,
        "accuracy": 0.25,
        "split_rate": 0.25,
    }
    assert s["first_seen"]["correct_new"] == 1
    assert s["first_seen"]["merged_into_existing"] == 1
    assert s["answered_existing"] == 3
    assert s["merge_rate"] == 2 / 3


def test_downstream_reproduces_baseline_only_with_right_picks() -> None:
    cases = [
        row("a", 0, "F1", split="train"),
        row("b", 10, "F1", split="train"),
        row("c", 20, "F1"),
    ]
    tasks = build_tasks(cases, "valid")
    right = [result_of(tasks[0], "EXISTING", "G1")]
    assert downstream(cases, tasks, right) == {
        "baseline_reached": 1,
        "reproduced_same_time": 1,
        "reproduced_other_time": 0,
        "missed": 0,
        "ai_only": 0,
    }
    split = [result_of(tasks[0], "NEW", None)]
    assert downstream(cases, tasks, split)["missed"] == 1
    unsure = [result_of(tasks[0], "UNSURE", None)]
    assert downstream(cases, tasks, unsure)["missed"] == 1
    assert downstream(cases, tasks, unsure, human_resolves_unsure=True)["reproduced_same_time"] == 1


def test_breakdown_separates_fault_reports() -> None:
    results = [
        Result("1", "G1", "EXISTING", "G1", "es", False, "DEFECT"),
        Result("2", "G1", "UNSURE", None, "en", False, "INSTALL"),
    ]
    by = summarize_by(results)
    assert by["inquiry=fault_report"]["existing"]["accuracy"] == 1.0
    assert by["inquiry=other"]["existing"]["unsure"] == 1


def test_embedding_baseline_picks_nearest_centroid() -> None:
    cases = [row("a", 0, "F1", split="train"), row("b", 1, "F2", split="train"), row("q", 2, "F2")]
    (task,) = build_tasks(cases, "valid")
    vectors = {"text a": [1.0, 0.0], "text b": [0.0, 1.0], "text q": [0.1, 0.9]}
    assert nearest_group(task, vectors.__getitem__) == ("EXISTING", "G2")
    assert nearest_group(task, vectors.__getitem__, new_below=0.999) == ("NEW", None)
