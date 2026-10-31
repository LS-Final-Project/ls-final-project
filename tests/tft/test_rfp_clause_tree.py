"""KAN-185 조항 트리 복원과 편집 흔적 해석. 블록은 합성이다(실제 RFP 원문 없음).

14번(유닛 변전소 규격서)에서 본 배치를 흉내 낸다: 번호가 본문 글자, 페이지 머리말·바닥글이 본문 문단,
표 칸에 번호, 절 번호가 표 칸에 떠 있음, 한 문단에 항목 두 개, 취소선 값.
"""

from backend.engine.documents.model import Block, DeletedValue, Position, build_document
from backend.engine.rfp.clauses import build_clause_tree
from backend.engine.rfp.edits import classify, interpret_edits, mark, restore

HEADER = "[PROJECT REDACTED]"


def p(text, *deleted, n=[0]):
    n[0] += 1
    return Block(
        block_id=f"p{n[0]:04d}",
        kind="PARAGRAPH",
        text=text,
        deleted_values=[DeletedValue(text=t, position=at, origin="STRIKE") for t, at in deleted],
        position=Position(paragraph_index=n[0]),
    )


def cell(table, row, col, text, *deleted, n=[0]):
    n[0] += 1
    return Block(
        block_id=f"t{n[0]:04d}",
        kind="TABLE_CELL",
        text=text,
        deleted_values=[DeletedValue(text=t, position=at, origin="STRIKE") for t, at in deleted],
        position=Position(table_index=table, row=row, col=col),
    )


def doc(blocks, kind="DOCX"):
    return build_document(kind, b"synthetic", blocks, [], tables=0)


def tree_of(*blocks):
    return build_clause_tree(doc(list(blocks)))


def by_no(tree):
    return {c.clause_no: c for c in tree.clauses}


def test_numbering_patterns_become_a_tree_with_dotted_numbers():
    tree = tree_of(
        p("SECTION 261116.11 - SECONDARY UNIT SUBSTATIONS"),
        p("PART 2 - PRODUCTS"),
        p("2.5 EPOXY CAST RESIN, DRY-TYPE TRANSFORMER SECTION"),
        p("C. Cooling System shall contain all necessary components:"),
        p("1. 480Y/277V Secondary: 33% with fans.", ("50", 24)),
        p("a. Fan control."),
        p("1) Manual override."),
    )
    clauses = by_no(tree)
    assert list(clauses) == ["PART 2", "2.5", "2.5.C", "2.5.C.1", "2.5.C.1.a", "2.5.C.1.a.1"]
    assert [c.depth for c in tree.clauses] == [1, 2, 3, 4, 5, 6]
    assert clauses["PART 2"].title == "PRODUCTS"
    assert clauses["2.5"].title.startswith("EPOXY")
    assert clauses["2.5.C.1"].parent_no == "2.5.C"
    assert tree.section_title.startswith("SECTION 261116.11")
    # 번호를 뗀 본문 기준으로 삭제 값 위치를 옮긴다("480Y/277V Secondary: " 뒤).
    c1 = clauses["2.5.C.1"]
    assert c1.text[c1.deleted_values[0].position :].startswith("33%")


def test_broken_lines_rejoin_and_page_noise_is_dropped():
    blocks = [
        p("PART 1 - GENERAL"),
        p("1.1 SUMMARY"),
        p("A. Section includes secondary unit substations with"),
        p(HEADER),
        p("11/21/2023 DOC REL 09 - 261116.11 - 1"),
        p("incoming section and transformer section", ("s", 40)),
        p("B. Next item."),
        p(HEADER),
        p("11/21/2023 DOC REL 09 - 261116.11 - 2"),
        p(HEADER),
        p("11/21/2023 DOC REL 09 - 261116.11 - 3"),
        p("END OF SECTION 261116.11"),
    ]
    tree = tree_of(*blocks)
    a = by_no(tree)["1.1.A"]
    assert (
        a.text
        == "Section includes secondary unit substations with incoming section and transformer section"
    )
    assert "LINE_BREAK_REJOINED" in a.flags
    assert a.text[a.deleted_values[0].position - 1] == "n"
    assert len(tree.boilerplate_block_ids) == 7
    assert all("REDACTED" not in c.text for c in tree.clauses)


def test_numbered_items_laid_out_in_table_rows():
    tree = tree_of(
        p("PART 2 - PRODUCTS"),
        p("2.5 TRANSFORMER"),
        p("C. Cooling."),
        cell(3, 0, 0, "D.", ("C.", 0)),
        cell(3, 0, 1, "Cooling system shall include a temperature controller."),
        cell(5, 1, 0, ""),
        cell(5, 1, 1, "1."),
        cell(5, 1, 2, "Dual temperature sensors."),
        cell(9, 0, 0, "Bus"),
        cell(9, 0, 1, "Neutral Bus: Refer to the Contract Drawings", ("None", 13)),
    )
    clauses = by_no(tree)
    d = clauses["2.5.D"]
    assert d.text.startswith("Cooling system") and d.label_history == ["C."]
    assert clauses["2.5.D.1"].text == "Dual temperature sensors."
    # 번호 없는 표 행은 앞 조항의 표 행으로, 삭제 값은 칸 기준으로 남는다.
    row = clauses["2.5.D.1"]
    assert row.table_rows == [["Bus", "Neutral Bus: Refer to the Contract Drawings"]]
    assert row.table_deleted_values[0].col == 1


def test_article_number_floating_in_a_table_cell():
    tree = tree_of(
        p("PART 2 - PRODUCTS"),
        p("2.1 MANUFACTURERS"),
        p("A. Manufacturers include the following:"),
        # 2.2 A.가 떠 있고 같은 행 뒤는 앞 목록의 1번이다.
        cell(0, 0, 0, "2.2 A."),
        cell(0, 0, 1, "1."),
        cell(0, 0, 2, "ABB."),
        p("2. Eaton."),
        p("1. Electrical Components: Listed and labeled."),
        # 2.4 A. 뒤가 본문이면 그 자리에서 연다.
        cell(1, 0, 0, "2.4 A."),
        cell(1, 0, 1, "DUAL FUSIBLE LOAD INTERRUPTER SWITCHGEAR Construction:"),
        cell(1, 1, 0, ""),
        cell(1, 1, 1, "1."),
        cell(1, 1, 2, "Deadfront, metal-enclosed."),
    )
    clauses = by_no(tree)
    assert clauses["2.1.A.1"].text == "ABB." and clauses["2.1.A.2"].text == "Eaton."
    assert clauses["2.2.A.1"].text.startswith("Electrical Components")
    assert "DEFERRED_ARTICLE" in clauses["2.2.A"].flags
    assert clauses["2.4.A"].text.startswith("DUAL FUSIBLE")
    assert clauses["2.4.A.1"].text == "Deadfront, metal-enclosed."
    assert not tree.numbering_issues


def test_two_items_in_one_paragraph_split_only_on_the_next_label():
    tree = tree_of(
        p("PART 2 - PRODUCTS"),
        p("2.4 SWITCHGEAR"),
        p("C. Switches:"),
        p("3. Fuses:"),
        p("a. Installed on a single frame. b. Expulsion Fuses: In disconnect-type mountings."),
        p("c. Indicator integral with each fuse. Class F. 2. Not a split because 2 is not d."),
    )
    clauses = by_no(tree)
    assert clauses["2.4.C.3.a"].text == "Installed on a single frame."
    assert clauses["2.4.C.3.b"].text.startswith("Expulsion Fuses")
    assert "INLINE_SPLIT" in clauses["2.4.C.3.b"].flags
    assert clauses["2.4.C.3.c"].text.endswith("is not d.")
    assert not tree.numbering_issues


def test_gaps_and_repeats_are_reported_not_renumbered():
    tree = tree_of(
        p("PART 3 - EXECUTION"),
        p("3.7 INSPECTION"),
        p("B. Record:"),
        p("a. Description."),
        p("c. Temperature difference."),
        p("c. Repeated."),
    )
    kinds = [(i.clause_no, i.kind) for i in tree.numbering_issues]
    assert ("3.7.B.c", "GAP") in kinds and ("3.7.B.c~2", "DUPLICATE") in kinds


def test_strike_runs_restore_the_original_and_keep_the_current_value():
    text = "480Y/277V Secondary: 33% with fans"
    deleted = [DeletedValue(text="50", position=21, origin="STRIKE")]
    # 서식을 버린 추출은 "5033%"로 읽힌다. 원문 복원과 현행 해석을 나눈다.
    assert restore(text, deleted) == "480Y/277V Secondary: 5033% with fans"
    assert mark(text, deleted) == "480Y/277V Secondary: ~~50~~33% with fans"
    assert classify(text, deleted[0]) == "VALUE"
    assert (
        classify("SCADA system. The", DeletedValue(text="s", position=12, origin="STRIKE"))
        == "TYPO"
    )
    assert (
        classify("EXECUTION", DeletedValue(text=" (NOT IN CONTRACT)", position=9, origin="STRIKE"))
        == "SCOPE"
    )


def test_edit_groups_one_per_renumbering_article_and_one_per_content_edit():
    tree = tree_of(
        p("PART 2 - PRODUCTS"),
        p("2.5 TRANSFORMER"),
        p("C. Cooling:"),
        p("1. Secondary: 33% with fans.", ("50", 13)),
        p("E. Module.", ("D.", 0)),
        p("F. Wiring.", ("E.", 0)),
        p("G. Sensor wiring to the SCADA system.", ("s", 34)),
        p("H. Network.", ("G.", 0)),
        p("PART 3 - EXECUTION", (" (NOT IN CONTRACT)", 18)),
    )
    report = interpret_edits(tree)
    assert report.counts == {"VALUE": 1, "RENUMBER": 3, "TYPO": 1, "SCOPE": 1}
    assert [(g.kind, g.article, g.traces) for g in report.groups] == [
        ("VALUE", "2.5", 1),
        ("RENUMBER", "2.5", 3),
        ("TYPO", "2.5", 1),
        ("SCOPE", "PART 3", 1),
    ]
    scope = next(t for t in report.traces if t.kind == "SCOPE")
    assert scope.location == "title" and scope.marked == "EXECUTION~~ (NOT IN CONTRACT)~~"
    assert by_no(tree)["PART 3"].title == "EXECUTION"


def test_pdf_reports_edit_traces_as_unsupported_not_none():
    tree = build_clause_tree(doc([p("PART 1 - GENERAL"), p("1.1 SUMMARY")], kind="PDF"))
    report = interpret_edits(tree)
    assert (report.status, report.edit_trace_support) == ("UNSUPPORTED", False)


def test_a_broken_line_starting_with_a_decimal_is_not_an_article():
    tree = tree_of(
        p("PART 2 - PRODUCTS"),
        p("2.4 SWITCHGEAR"),
        p("C. Rated voltage shall be"),
        p("13.8 kV class, 3-phase."),
        p("1. Item under C."),
    )
    clauses = by_no(tree)
    assert "13.8" not in clauses
    assert clauses["2.4.C"].text == "Rated voltage shall be 13.8 kV class, 3-phase."
    assert clauses["2.4.C.1"].parent_no == "2.4.C"


def test_short_body_lines_repeated_alone_stay_in_their_clauses():
    blocks = [p("PART 1 - GENERAL"), p("1.1 SUMMARY")]
    for letter in "ABC":
        blocks += [p(f"{letter}. Item {letter}:"), p("Provide the following:")]
    blocks += [p("END OF SECTION 26"), p("11/21/2023 DOC REL 09 - 24")]
    tree = tree_of(*blocks)
    assert all(by_no(tree)[f"1.1.{x}"].text.endswith("Provide the following:") for x in "ABC")
    # END OF SECTION 뒤(마지막 페이지 바닥글)는 반복되지 않아도 절 밖이라 조항에 붙이지 않는다.
    assert [e.reason for e in tree.excluded] == ["END_OF_SECTION", "AFTER_END_OF_SECTION"]
    assert tree.excluded[1].text.startswith("11/21/2023")


def test_a_floating_article_that_never_restarts_is_opened_and_reported():
    tree = tree_of(
        p("PART 2 - PRODUCTS"),
        p("2.1 MANUFACTURERS"),
        p("A. Manufacturers:"),
        cell(0, 0, 0, "2.2 A."),
        cell(0, 0, 1, "1."),
        cell(0, 0, 2, "ABB."),
        p("2. Eaton."),
    )
    clauses = by_no(tree)
    assert "2.2.A" in clauses and "DEFERRED_ARTICLE" in clauses["2.2.A"].flags
    assert ("2.2.A", "UNPLACED_ARTICLE") in [(i.clause_no, i.kind) for i in tree.numbering_issues]
    head = tree.clauses[-1].block_ids
    assert any(b.startswith("t") for b in head)


def test_inline_split_keeps_strikes_in_the_gap_and_the_right_block():
    first = p("a. First item ends here. b. Second item starts", ("x", 25))
    second = p("and continues on the next line.")
    tree = tree_of(p("PART 3 - EXECUTION"), p("3.7 INSPECTION"), p("B. Record:"), first, second)
    clauses = by_no(tree)
    a, b = clauses["3.7.B.a"], clauses["3.7.B.b"]
    assert a.text == "First item ends here."
    assert b.text == "Second item starts and continues on the next line."
    # 나눈 자리(공백) 안의 삭제 값을 버리지 않는다.
    assert [v.text for v in a.deleted_values + b.deleted_values] == ["x"]
    assert a.block_ids == [first.block_id] and b.block_ids == [first.block_id, second.block_id]


def test_letter_parentheses_are_the_seventh_level():
    tree = tree_of(
        p("PART 3 - EXECUTION"),
        p("3.6 TESTS"),
        p("G. Relays:"),
        p("2. Electrical Tests:"),
        p("c. Protective relays:"),
        p("4) Pickup:"),
        p("a) Determine pickup."),
        p("b) Determine time delay."),
    )
    clauses = by_no(tree)
    assert clauses["3.6.G.2.c.4.b"].text == "Determine time delay."
    assert clauses["3.6.G.2.c.4.b"].depth == 7


def test_a_decimal_from_the_same_part_but_out_of_order_is_not_an_article():
    tree = tree_of(
        p("PART 2 - PRODUCTS"),
        p("2.4 SWITCHGEAR"),
        p("C. Minimum clearance shall be"),
        p("2.1 mm between phases."),
    )
    assert "2.1" not in by_no(tree)
    assert by_no(tree)["2.4.C"].text.endswith("2.1 mm between phases.")


def test_korean_chapter_article_and_numbered_items_keep_hierarchy_and_body():
    tree = tree_of(
        p("제 1 장 공통 기준"),
        p("제1조(목적) 필요한 자료를 제출한다."),
        p("1. 적용 범위"),
        p("가. 최초 자료를 확인한다."),
        p("나. 변경 이력을 보존한다."),
        p("제2조 절차"),
        p("1. 담당자가 검토한다."),
    )
    clauses = by_no(tree)
    assert list(clauses) == [
        "제1장",
        "제1조",
        "제1조.1",
        "제1조.1.가",
        "제1조.1.나",
        "제2조",
        "제2조.1",
    ]
    assert [c.depth for c in tree.clauses] == [1, 2, 3, 4, 4, 2, 3]
    assert clauses["제1조"].parent_no == "제1장"
    assert clauses["제1조.1.나"].parent_no == "제1조.1"
    assert clauses["제1장"].label == "제 1 장"
    assert clauses["제1조"].text == "(목적) 필요한 자료를 제출한다."
    assert tree.numbering_issues == []


def test_korean_article_can_start_without_a_chapter_or_english_part():
    head = p("머리말 자료")
    tree = tree_of(head, p("제7조(확인) 사람이 기록을 검토한다."))
    [article] = tree.clauses
    assert article.clause_no == "제7조" and article.parent_no is None
    assert article.text == "(확인) 사람이 기록을 검토한다."
    assert tree.preamble_block_ids == [head.block_id]


def test_korean_simple_sections_preserve_decimal_quantities_as_body():
    tree = tree_of(
        p("문서 안내"),
        p("1. 일반"),
        p("검토 결과를 기록한다."),
        p("13.8 kV 기준을 적용한다."),
        p("0.5 밀리미터 범위를 확인한다."),
        p("2.수행 절차"),
        p("가. 담당자가 확인한다."),
    )
    clauses = by_no(tree)
    assert list(clauses) == ["1", "2", "2.가"]
    assert clauses["1"].title == "일반" and clauses["2"].title == "수행 절차"
    assert "13.8 kV" in clauses["1"].text and "0.5 밀리미터" in clauses["1"].text
    assert clauses["2.가"].parent_no == "2"


def test_mixed_korean_and_english_parts_restore_english_depth_and_preamble():
    cover = p("1. 한글 안내")
    tree = tree_of(
        cover,
        p("PART 2 - PRODUCTS"),
        p("2.1 SCOPE"),
        p("A. Main assembly."),
        p("1. Supply item."),
        p("제3장 확인 절차"),
        p("제1조 기록"),
        p("1. 현행 값을 보존한다."),
        p("3.5 kV 조건을 적용한다."),
        p("제1장치를 확인한다."),
        p("제7조립품을 확인한다."),
        p("PART 4 - EXECUTION"),
        p("4.1 TEST"),
        p("A. Verify the record."),
    )
    clauses = by_no(tree)
    assert list(clauses) == [
        "PART 2",
        "2.1",
        "2.1.A",
        "2.1.A.1",
        "제3장",
        "제1조",
        "제1조.1",
        "PART 4",
        "4.1",
        "4.1.A",
    ]
    assert cover.block_id in tree.preamble_block_ids
    assert clauses["2.1.A.1"].depth == 4 and clauses["4.1.A"].depth == 3
    assert all(value in clauses["제1조.1"].text for value in ("3.5 kV", "제1장치를", "제7조립품을"))


def test_korean_table_items_and_deleted_values_keep_source_offsets():
    tree = tree_of(
        p("제1장 기록"),
        p("제1조 제출"),
        cell(0, 0, 0, "1."),
        cell(0, 0, 1, "현행 값 20을 보존한다.", ("10", 5)),
    )
    clause = by_no(tree)["제1조.1"]
    assert clause.depth == 3 and clause.parent_no == "제1조"
    assert clause.label == "1." and "FROM_TABLE_ROW" in clause.flags
    assert clause.deleted_values[0].text == "10"
    assert clause.deleted_values[0].position == 5


def test_korean_table_decimal_current_is_not_an_english_compound_article():
    tree = tree_of(
        p("제1장 확인"),
        p("제1조 전류"),
        p("1. 시험 값을 기록한다."),
        cell(0, 0, 0, "1.5 A."),
        cell(0, 0, 1, "상한 값"),
    )
    assert list(by_no(tree)) == ["제1장", "제1조", "제1조.1"]
    assert by_no(tree)["제1조.1"].table_rows == [["1.5 A.", "상한 값"]]
