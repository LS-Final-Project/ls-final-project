"""KAN-188 번호 빈자리 표시(모델 없음)와 경계만 받는 구조 보정(가짜 공급자). 블록은 합성이다."""

import json

import pytest

from backend.engine.documents.model import Block, DeletedValue, Position, build_document
from backend.engine.rfp.clauses import build_clause_tree, detect_structure_gaps
from backend.engine.rfp.structure_repair import REPAIRED, repair_tree
from backend.shared.ai import Generation, Usage


def p(text, *deleted, n=[0]):
    n[0] += 1
    return Block(
        block_id=f"s{n[0]:04d}",
        kind="PARAGRAPH",
        text=text,
        deleted_values=[DeletedValue(text=t, position=at, origin="STRIKE") for t, at in deleted],
        position=Position(paragraph_index=n[0]),
    )


def tree_of(*blocks):
    return build_clause_tree(build_document("DOCX", b"synthetic", list(blocks), [], tables=0))


def gaps_of(tree):
    return [(g.clause_no, g.reason, g.labels) for g in detect_structure_gaps(tree.clauses)]


def by_no(tree):
    return {c.clause_no: c for c in tree.clauses}


HEAD = (p("PART 2 - PRODUCTS"), p("2.1 SWITCHGEAR"), p("A. General."))


def late_start():
    # a.가 부모 문장 끝에 붙었지만 앞이 긴 문장이라 결정 코드(_inline_items)가 나누지 않는다.
    return tree_of(
        *HEAD,
        p(
            "B. Fuses shall be listed and the following apply to all switch fuse units a. Rated 15 kV."
        ),
        p("b. Indicator required."),
    )


def skip():
    # b.가 앞 항목에 붙었고 뒤가 소문자라 close()도 나누지 않는다(대문자면 결정 코드가 나눈다).
    return tree_of(
        *HEAD,
        p("B. Bus:"),
        p("a. Copper bus rated 1200 A b. silver plated joints."),
        p("c. Bolted connections."),
    )


def inline():
    return tree_of(
        *HEAD,
        p(
            "B. The breakers shall be rated as follows for the normal service named in this "
            "contract 1. Voltage. 2. Current."
        ),
    )


def twice():
    return tree_of(*HEAD, p("B. Bus:"), p("a. Copper bus b. tin plated b. tin plated."), p("c. X."))


def test_late_start_skip_and_inline_lists_are_flagged_with_the_clause_holding_the_items():
    assert gaps_of(late_start()) == [("2.1.B", "LATE_START", ["a"])]
    assert gaps_of(skip()) == [("2.1.B.a", "SKIP", ["b"])]
    assert gaps_of(inline()) == [("2.1.B", "INLINE_UNSPLIT", ["1", "2"])]
    # 기존 GAP은 그대로, 같은 자리의 STRUCTURE_GAP이 더해진다.
    kinds = [(i.clause_no, i.kind, i.detail) for i in skip().numbering_issues]
    assert ("2.1.B.c", "GAP", "a 다음 c") in kinds
    assert ("2.1.B.a", "STRUCTURE_GAP", "SKIP: b") in kinds


def test_complete_lists_far_skips_restarts_and_references_are_not_flagged():
    tree = tree_of(
        *HEAD,
        p("B. Bus:"),
        p("a. One."),
        p("b. Two."),
        p("C. Far skip:"),
        p("a. One."),
        p("f. Six is too far to be a buried item."),
        p("D. Refer to Section 1. General and Table 2. Ratings."),
        p("E. Restart:"),
        p("a. One."),
        p("b. Two."),
        p("a. New list."),
    )
    assert gaps_of(tree) == []


def test_parenthesized_inline_lists_keep_their_label_format():
    tree = tree_of(
        *HEAD,
        p("B. Bus:"),
        p("1. Ratings:"),
        p("a. Ratings apply as listed for this unit (1) Voltage. (2) Current."),
    )
    [gap] = detect_structure_gaps(tree.clauses)
    assert (gap.clause_no, gap.labels, gap.label_format, gap.depth) == (
        "2.1.B.1.a",
        ["1", "2"],
        "({})",
        6,
    )


def answer(*items, finish=()):
    calls = []

    def provider(prompt):
        calls.append(prompt)
        return Generation(
            text=json.dumps({"items": [{"label": label, "start_text": t} for label, t in items]}),
            usage=Usage(10, 2, 3, 0, 15),
            model_version="fake-1",
            finish_reasons=list(finish),
        )

    return provider, calls


def test_good_boundaries_split_the_clause_with_the_same_blocks():
    tree = skip()
    provider, calls = answer(("b", "b. silver plated"))
    result = repair_tree(tree, provider)
    clauses = by_no(result.tree)
    assert clauses["2.1.B.a"].text == "Copper bus rated 1200 A"
    assert clauses["2.1.B.b"].text == "silver plated joints."
    assert clauses["2.1.B.b"].label == "b." and clauses["2.1.B.b"].depth == 5
    assert clauses["2.1.B.b"].block_ids == clauses["2.1.B.a"].block_ids
    assert REPAIRED in clauses["2.1.B.b"].flags and REPAIRED in clauses["2.1.B.a"].flags
    numbers = [c.clause_no for c in result.tree.clauses]
    assert numbers.index("2.1.B.b") == numbers.index("2.1.B.a") + 1
    assert [c.ordinal for c in result.tree.clauses] == list(range(len(numbers)))
    assert ("2.1.B.a", REPAIRED) in [(i.clause_no, i.kind) for i in result.tree.numbering_issues]
    assert result.calls == len(calls) == 1 and result.rejected == []
    assert result.usage.input_tokens == 10 and result.model_versions == {"fake-1"}
    assert "Copper bus rated 1200 A b. silver plated joints." in calls[0]


def test_late_start_and_inline_repairs_add_children_under_the_parent():
    late = repair_tree(late_start(), answer(("a", "a. Rated 15 kV."))[0]).tree
    assert by_no(late)["2.1.B"].text.endswith("switch fuse units")
    assert by_no(late)["2.1.B.a"].text == "Rated 15 kV."
    items = repair_tree(inline(), answer(("1", "1. Voltage."), ("2", "2. Current."))[0]).tree
    assert [(c.clause_no, c.text) for c in items.clauses if c.parent_no == "2.1.B"] == [
        ("2.1.B.1", "Voltage."),
        ("2.1.B.2", "Current."),
    ]


@pytest.mark.parametrize(
    ("fixture", "items", "reason"),
    [
        (skip, [("x", "b. silver plated")], "LABELS_MISMATCH"),
        (skip, [("b", "b. silver plated"), ("c", "Bolted")], "LABELS_MISMATCH"),
        (skip, [("b", "b. silver-plated joints")], "NOT_SUBSTRING"),
        (skip, [("b", "silver plated joints.")], "LABEL_NOT_AT_START"),
        (skip, [], "NO_ITEMS"),
        (inline, [("2", "2. Current."), ("1", "1. Voltage.")], "LABELS_MISMATCH"),
        (inline, [("1", "2. Current."), ("2", "1. Voltage.")], "LABEL_NOT_AT_START"),
        (inline, [("1", "1. Voltage."), ("2", "rated as follows")], "OUT_OF_ORDER"),
        (inline, [("1", "1. Voltage. 2. Current"), ("2", "2. Current.")], "OVERLAP"),
        (twice, [("b", "b. tin")], "AMBIGUOUS_START"),
    ],
)
def test_bad_boundaries_keep_the_tree_unchanged(fixture, items, reason):
    tree = fixture()
    result = repair_tree(tree, answer(*items)[0])
    assert result.tree == tree
    assert [r.reason for r in result.rejected] == [reason]
    assert result.calls == 1 and result.usage.input_tokens == 10


def test_truncated_or_edited_targets_are_not_applied():
    tree = skip()
    truncated = repair_tree(tree, answer(("b", "b. silver plated"), finish=["MAX_TOKENS"])[0])
    assert truncated.tree == tree and truncated.rejected[0].reason == "TRUNCATED"
    edited = tree_of(
        *HEAD, p("B. Bus:"), p("a. Copper rated 1200 A b. silver.", ("old", 3)), p("c. X.")
    )
    provider, calls = answer(("b", "b. silver."))
    result = repair_tree(edited, provider)
    assert result.tree == edited and calls == []
    assert result.rejected[0].reason == "TARGET_HAS_DELETED_VALUES"
