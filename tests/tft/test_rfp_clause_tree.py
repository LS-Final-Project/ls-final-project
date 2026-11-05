"""KAN-185 조항 트리 복원과 편집 흔적 해석. 블록은 합성이다(실제 RFP 원문 없음).

14번(유닛 변전소 규격서)에서 본 배치를 흉내 낸다: 번호가 본문 글자, 페이지 머리말·바닥글이 본문 문단,
표 칸에 번호, 절 번호가 표 칸에 떠 있음, 한 문단에 항목 두 개, 취소선 값.
"""

from backend.engine.documents.docx import extract_docx
from backend.engine.documents.model import Block, DeletedValue, Hints, Position, build_document
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


def np(text, list_text, *deleted):
    """Word 자동 번호 문단: 본문 글자에 번호가 없고 DOCX 층이 계산한 list_text만 있다(평가 18번 유형)."""
    block = p(text, *deleted)
    return block.model_copy(
        update={
            "hints": Hints(
                style_name=None,
                numbering_level=0,
                list_text=list_text,
                is_heading=False,
                numbering_raw=None,
            )
        }
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
    # 발췌가 C.3부터라 늦게 시작한 목록만 남는다(본문 나누기 문제는 없다).
    assert [(i.clause_no, i.kind, i.detail) for i in tree.numbering_issues] == [
        ("2.4", "STRUCTURE_GAP", "LATE_START: A,B"),
        ("2.4.C", "STRUCTURE_GAP", "LATE_START: 1,2"),
    ]


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


def test_korean_reference_paragraphs_do_not_open_fake_articles():
    references = [
        "제3조 제2항에 따라 자료를 확인한다.",
        "제2조(정의)에 따른 조건을 확인한다.",
        "제 4 조 (범위)의 내용을 확인한다.",
        "제5조 제1호를 참조한다.",
    ]
    tree = tree_of(
        p("제1장 일반"),
        p("제1조(목적) 자료를 보존한다."),
        *(p(s) for s in references),
        p("제2조 절차"),
    )
    assert [c.clause_no for c in tree.clauses] == ["제1장", "제1조", "제2조"]
    assert all(reference in by_no(tree)["제1조"].text for reference in references)


def test_korean_decimal_headings_follow_actual_numeric_parents():
    tree = tree_of(
        p("1. 일반"),
        p("1.1 범위"),
        p("본문 자료를 보존한다."),
        p("1.1.1 세부 범위"),
        p("확인 이력을 기록한다."),
        p("1.2 자료"),
        p("원문 자료를 보존한다."),
        p("2. 절차"),
        p("2.1 검토"),
        p("담당자가 검토한다."),
    )
    assert [c.clause_no for c in tree.clauses] == ["1", "1.1", "1.1.1", "1.2", "2", "2.1"]
    assert [c.parent_no for c in tree.clauses] == [None, "1", "1.1", "1", None, "2"]
    assert [c.depth for c in tree.clauses] == [2, 3, 4, 3, 2, 3]
    assert by_no(tree)["1.1"].title == "범위"
    assert by_no(tree)["1.1.1"].text == "확인 이력을 기록한다."


def test_korean_decimal_quantity_reference_sentence_and_orphan_stay_body():
    body = [
        "본문의 1.1 항목을 확인한다.",
        "1.1 밀리미터 범위를 확인한다.",
        "1.2 조에 따른 자료",
        "1.3 자료를 확인한다.",
        "8.1 다른 번호",
        "1.1.1 부모 없는 항목",
        "1.4 kV 기준",
        "1.5 개 부품",
    ]
    tree = tree_of(p("1. 일반"), *(p(s) for s in body))
    assert [c.clause_no for c in tree.clauses] == ["1"]
    assert all(line in tree.clauses[0].text for line in body)


def test_korean_chapter_decimal_context_restores_english_after_part():
    tree = tree_of(
        p("제1장 일반"),
        p("1.1 범위"),
        p("1.1.1 세부 범위"),
        p("본문을 보존한다."),
        p("PART 2 - PRODUCTS"),
        p("2.1 EQUIPMENT"),
        p("A. Enclosure shall be painted."),
    )
    assert [c.clause_no for c in tree.clauses] == [
        "제1장",
        "1.1",
        "1.1.1",
        "PART 2",
        "2.1",
        "2.1.A",
    ]
    assert by_no(tree)["1.1"].parent_no == "제1장"
    assert by_no(tree)["2.1.A"].depth == 3


def test_korean_decimal_titles_do_not_split_nouns_into_unit_and_particle():
    tree = tree_of(
        p("1. 일반"),
        p("1.1 회의 절차"),
        p("검토 자료를 기록한다."),
        p("1.2 명의 변경"),
        p("이력 자료를 보존한다."),
    )
    assert [c.clause_no for c in tree.clauses] == ["1", "1.1", "1.2"]
    assert [c.title for c in tree.clauses] == ["일반", "회의 절차", "명의 변경"]
    assert [c.parent_no for c in tree.clauses] == [None, "1", "1"]


def test_korean_decimal_number_and_standalone_unit_are_quantities_not_titles():
    quantities = [
        "1.1 밀리미터",
        "1.2 볼트 범위",
        "1.3 회",
        "1.4 명",
        "1.5 개 부품",
        "1.6 암페어 조건",
    ]
    tree = tree_of(p("1. 일반"), *(p(line) for line in quantities))
    assert [c.clause_no for c in tree.clauses] == ["1"]
    assert all(line in tree.clauses[0].text for line in quantities)


def test_auto_numbered_paragraphs_use_the_computed_list_text():
    """자동 번호 문단은 DOCX 층의 list_text로 번호를 읽는다(KAN-207). 글자 번호가 있는 문단과 글머리표는 그대로다."""
    tree = tree_of(
        p("PART 2 - PRODUCTS"),
        np("METAL-CLAD SWITCHGEAR", "2.1"),
        np("General requirement.", "A."),
        np("Rated 50% value.", "1.", ("40", 6)),
        np("Bullet note.", "•"),
        p("B. Literal item."),
        np("C. Already numbered.", "C."),
        np("Sub item.", "1)"),
    )
    clauses = by_no(tree)
    assert list(clauses) == ["PART 2", "2.1", "2.1.A", "2.1.A.1", "2.1.B", "2.1.C", "2.1.C.1"]
    assert clauses["2.1"].title == "METAL-CLAD SWITCHGEAR"
    # 글머리표 문단은 번호가 아니라 바로 앞 조항에 붙는다.
    assert "Bullet note." in clauses["2.1.A.1"].text
    # 붙인 번호를 뗀 뒤 삭제 값 위치는 원래 자리("50%" 앞)로 돌아온다.
    item = clauses["2.1.A.1"]
    assert item.text[item.deleted_values[0].position :].startswith("50%")
    # 글자 번호와 list_text가 같이 있으면 번호를 두 번 붙이지 않는다.
    assert clauses["2.1.C"].text == "Already numbered."
    assert tree.numbering_issues == []


def test_docx_auto_numbering_reaches_the_clause_tree():
    """numPr 자동 번호만 있는 DOCX(평가 18번 유형)도 PART·절·항목 트리가 복원된다. 원문은 합성이다."""
    from tests.tft.test_document_extraction import NUMBERING, STYLES, docx, para, run

    def styled(style, text):
        return para(run(text), props=f'<w:pStyle w:val="{style}"/>')

    body = "".join(
        styled(style, text)
        for style, text in [
            ("PRT", "PRODUCTS"),
            ("ART", "METAL-CLAD SWITCHGEAR"),
            ("PR1", "Rated maximum voltage shall be 15 kV."),
            ("PR1", "BIL shall be 95 kV."),
        ]
    )
    document = extract_docx(docx(body, word_styles_xml=STYLES, word_numbering_xml=NUMBERING))
    assert [b.text for b in document.blocks][1] == "METAL-CLAD SWITCHGEAR"  # 본문 글자에 번호 없음
    tree = build_clause_tree(document)
    clauses = by_no(tree)
    assert list(clauses) == ["PART 1", "1.1", "1.1.A", "1.1.B"]
    assert clauses["PART 1"].title == "PRODUCTS" and clauses["1.1"].title == "METAL-CLAD SWITCHGEAR"
    assert clauses["1.1.B"].text == "BIL shall be 95 kV."


def test_part_without_a_dash_opens_the_part_only_with_a_capital_title():
    """대시 없는 "PART 1 GENERAL"도 PART다(평가 11번 유형, KAN-207). 본문의 "PART 2 of …"는 PART가 아니다."""
    tree = tree_of(
        p("PART 1 GENERAL"),
        p("1.01 SUMMARY"),
        p("A. See PART 2 of the drawings for details."),
        p("PART 2 PRODUCTS"),
        p("2.04 DRY-TYPE TRANSFORMER"),
        p("A. Rated 1000 kVA."),
    )
    clauses = by_no(tree)
    assert list(clauses) == ["PART 1", "1.01", "1.01.A", "PART 2", "2.04", "2.04.A"]
    assert clauses["PART 2"].title == "PRODUCTS"
    assert "PART 2 of the drawings" in clauses["1.01.A"].text


def test_article_and_item_numbers_in_separate_cells_read_like_one_cell():
    """표 행 "2.1 | A. | 본문"(평가 18번 유형)은 같은 칸 "2.1 A."와 같게 절과 항목을 연다(KAN-207)."""
    tree = tree_of(
        p("PART 2 - PRODUCTS"),
        cell(1, 0, 0, "2.1"),
        cell(1, 0, 1, "A."),
        cell(1, 0, 2, "Rated maximum voltage shall be 15 kV."),
        cell(1, 1, 0, ""),
        cell(1, 1, 1, "B."),
        cell(1, 1, 2, "BIL shall be 95 kV."),
        cell(2, 0, 0, "2.2"),
        cell(2, 0, 1, "A."),
        cell(2, 0, 2, "Provide a meter."),
        p("2.10 FIELD TESTS"),
    )
    clauses = by_no(tree)
    assert list(clauses) == ["PART 2", "2.1", "2.1.A", "2.1.B", "2.2", "2.2.A", "2.10"]
    assert clauses["2.1.A"].text == "Rated maximum voltage shall be 15 kV."
    assert clauses["2.1.B"].text == "BIL shall be 95 kV."


def test_compact_korean_spec_uses_independent_decimal_sections_and_preserves_body():
    tree = tree_of(
        p("제8장 설비 기준"),
        p("1.1적용 범위 1.1.1본 설비는 정격 전압을 만족하여야 한다."),
        p("1.1.2부속 장치를 공급하여야 한다."),
        p("1)보호 장치는 별도로 공급하여야 한다."),
        p("1.2공급 범위"),
        p("1.2.1시험 기록을 제공하여야 한다."),
        p("2.1구성"),
        p("2.1.1제어 장치를 제공하여야 한다."),
    )
    rows = by_no(tree)
    assert {"1.1", "1.1.2", "1.2", "1.2.1", "2.1", "2.1.1"} <= rows.keys()
    assert rows["1.1"].parent_no == "제8장"
    assert rows["1.1.2"].parent_no == "1.1"
    assert "정격 전압을 만족" in rows["1.1.1"].text
    assert rows["1.1.1"].parent_no == "1.1"
    assert "부속 장치를 공급" in rows["1.1.2"].text
    assert any(
        c.parent_no == "1.1.2" and c.text == "보호 장치는 별도로 공급하여야 한다."
        for c in tree.clauses
    )
    assert rows["2.1.1"].parent_no == "2.1"


def test_compact_korean_spec_does_not_promote_quantities_or_orphan_references():
    tree = tree_of(
        p("제8장 설비 기준"),
        p("1.1범위"),
        p("1.1.1자료를 제출하여야 한다."),
        p("1.1.2기록을 보존하여야 한다."),
        p("13.8kV 정격으로 한다."),
        p("0.5 밀리미터 공차를 적용한다."),
        p("3.7.2조에 따른 확인을 한다."),
    )
    rows = by_no(tree)
    assert "13.8" not in rows and "0.5" not in rows and "3.7.2" not in rows
    assert all(s in rows["1.1.2"].text for s in ("13.8kV", "0.5 밀리미터", "3.7.2조"))


def test_compact_split_retains_deleted_value_and_original_block_source():
    text = "1.1범위 1.1.1정격은 22.9kV 이상이어야 한다."
    original = p(text, ("13.8", text.index("22.9")))
    tree = tree_of(
        p("제8장 기준"),
        original,
        p("1.1.2장치를 공급하여야 한다."),
        p("1.1.3시험을 수행하여야 한다."),
    )
    row = by_no(tree)["1.1.1"]
    assert row.block_ids == [original.block_id]
    assert row.deleted_values[0].text == "13.8"
    assert row.text[row.deleted_values[0].position :].startswith("22.9kV")


def test_compact_next_sibling_retains_attached_rating_without_renumbering():
    tree = tree_of(
        p("제8장 기준"),
        p("4.1범위"),
        p("4.1.1자료를 제출한다."),
        p("4.1.222.9kV 이상으로 공급한다."),
        p("4.1.3IEC 기준을 적용한다."),
        p("4.1.4시험을 수행한다."),
    )
    rows = by_no(tree)
    assert rows["4.1.2"].text == "22.9kV 이상으로 공급한다."
    assert rows["4.1.3"].text == "IEC 기준을 적용한다."
    assert "4.1.222.9" not in rows
    assert rows["4.1.2"].parent_no == "4.1"


def test_compact_korean_detection_does_not_change_following_english_part():
    tree = tree_of(
        p("제8장 기준"),
        p("1.1범위"),
        p("1.1.1자료를 제출한다."),
        p("1.1.2기록을 보존한다."),
        p("PART 2 - PRODUCTS"),
        p("2.1 EQUIPMENT"),
        p("A. Provide a meter."),
        p("1) Check ratings."),
    )
    rows = by_no(tree)
    assert rows["2.1.A"].text == "Provide a meter."
    assert rows["2.1.A.1"].text == "Check ratings."


def test_compact_child_without_sentence_ending_is_requirement_body():
    tree = tree_of(
        p("제8장 기준"), p("1.1범위"), p("1.1.1시험 기록 제출"), p("1.1.2공급 부품 목록 제출")
    )
    rows = by_no(tree)
    assert rows["1.1.1"].title is None
    assert rows["1.1.1"].text == "시험 기록 제출"
    assert rows["1.1.2"].text == "공급 부품 목록 제출"


def test_pdf_paragraph_lines_open_section_articles_without_part():
    """KAN-229: 도면 PDF 글자층은 줄들을 한 문단으로 준다. PART 없이 SECTION 뒤 대문자 절과 항목을 읽는다."""
    blocks = [
        p("SECTION 26 12 19 - POWER EQUIPMENT\n2.1 EQUIPMENT REQUIREMENTS"),
        p(
            "A. TR-01 transformer shall have a 1000 kVA rating.\n"
            "B. CB-01 circuit breaker shall have a 630 A rating."
        ),
        p("2.2 SINGLE-LINE DIAGRAM - SHEET 1"),
        p("CB-01\n630 A"),
    ]
    tree = build_clause_tree(doc(blocks, kind="PDF"))
    assert [c.clause_no for c in tree.clauses] == ["2.1", "2.1.A", "2.1.B", "2.2"]
    assert tree.clauses[1].text == "TR-01 transformer shall have a 1000 kVA rating."
    # 나눈 조각은 원래 블록 하나를 가리킨다.
    assert tree.clauses[1].block_ids == tree.clauses[2].block_ids == [blocks[1].block_id]
    assert tree.clauses[3].text == "CB-01\n630 A"
    assert tree.preamble_block_ids == [blocks[0].block_id]


def test_pdf_wrapped_decimal_line_is_not_split():
    """끊긴 줄(앞 줄이 문장 끝이 아님)의 소수는 나누지 않는다."""
    blocks = [
        p("PART 2 - PRODUCTS"),
        p("2.1 SWITCHGEAR"),
        p("A. Switchgear shall be rated\n13.8 kV class and 1200 A.\nB. Bus shall be copper."),
    ]
    pdf = build_clause_tree(doc(blocks, kind="PDF"))
    assert [c.clause_no for c in pdf.clauses] == ["PART 2", "2.1", "2.1.A", "2.1.B"]
    assert pdf.clauses[2].text == "Switchgear shall be rated\n13.8 kV class and 1200 A."


def test_parent_title_and_first_child_in_one_paragraph_split_into_children():
    """변환 문서는 '2. 제목 a. 첫 항목 b. 다음'이 한 문단이다. 첫 하위 항목(.a·.1)을 상위에 묻지 않는다."""
    tree = tree_of(
        p("PART 3 - EXECUTION"),
        p("3.4 SNUBBERS"),
        p("L. Snubber Lighting Arresters 1. Located in the air terminal chamber."),
        p(
            "2. Enclosure a. The snubber shall be enclosed. b. The enclosure shall have a hinged door."
        ),
        p("3. Connections a. The snubber shall come fully assembled."),
        p("4. Main Fuses a. Fuses shall be current limiting."),
        p("b. Fuses shall have blown fuse indicators."),
    )
    clauses = by_no(tree)
    assert clauses["3.4.L"].text.startswith("Snubber Lighting Arresters")
    assert clauses["3.4.L.1"].text == "Located in the air terminal chamber."
    assert clauses["3.4.L.2"].text == "Enclosure"
    assert clauses["3.4.L.2.a"].text == "The snubber shall be enclosed."
    assert clauses["3.4.L.2.b"].text == "The enclosure shall have a hinged door."
    assert clauses["3.4.L.3.a"].text == "The snubber shall come fully assembled."
    assert clauses["3.4.L.4.a"].text == "Fuses shall be current limiting."
    assert clauses["3.4.L.4.b"].text == "Fuses shall have blown fuse indicators."
    assert not tree.numbering_issues


def test_inline_numbers_that_are_references_or_out_of_sequence_stay_in_the_text():
    tree = tree_of(
        p("PART 2 - PRODUCTS"),
        p("2.1 SWITCHGEAR"),
        # 참조 단어 뒤 번호, 1·a로 시작하지 않는 번호, 긴 문장 뒤 번호는 나누지 않는다.
        p("A. Refer to Section 1. General for submittals."),
        p("B. The breaker shall interrupt 25 kA. 2. Second is not a child list."),
        p(
            "C. The switchgear shall be designed and manufactured in accordance with all of the "
            "applicable standards listed here 1. IEEE C37.20.2"
        ),
        p("D. Ratings 1. Voltage 2. Current", ("old", 3)),
    )
    clauses = by_no(tree)
    assert "2.1.A.1" not in clauses and clauses["2.1.A"].text.endswith("for submittals.")
    assert "2.1.B.2" not in clauses
    assert "2.1.C.1" not in clauses
    # 삭제값이 있는 문단은 위치를 지키려고 나누지 않는다.
    assert "2.1.D.1" not in clauses


def test_lowercase_roman_items_sit_under_the_letter_item():
    """a. 아래 i.·ii.·iii.는 로마 숫자 단계다. i.는 앞 형제가 h(빠진 번호 포함 f·g)면 영문자 항목이다."""
    tree = tree_of(
        p("PART 2 - PRODUCTS"),
        p("2.4 SWITCHGEAR"),
        p("G. Ratings:"),
        p("3. Circuit Breakers: a. Same as the assembly, and as follows:"),
        p("i. Rated Continuous Current: 4000 A"),
        p("ii. Closing current is 2.6 times the short-circuit current."),
        # 표 칸에 뜬 다음 절 번호 뒤의 번호 칸도 같은 목록의 이어짐이다.
        cell(0, 0, 0, "2.5"),
        cell(0, 0, 1, "A."),
        cell(0, 0, 2, "iii."),
        cell(0, 0, 3, "Rated Closing Current: 104 kA."),
        cell(0, 1, 0, "iv."),
        cell(0, 1, 1, "Rated Interrupting Time: 50 msec."),
        cell(0, 2, 0, "v."),
        cell(0, 2, 1, "Rated Voltage: 15 kV."),
        # 미뤄 둔 절은 제목 행에서 연다.
        cell(0, 3, 0, "STATION POWER"),
        cell(0, 4, 0, "Provide two load interrupter switches."),
        p("2.6 INSTRUMENTS"),
        p("A. Meter:"),
        p("1. Functions:"),
        p("a. Volts."),
        p("b. Amps."),
        p("c. Watts."),
        p("d. Vars."),
        p("e. VA."),
        p("f. Frequency."),
        p("h. Power factor."),
        p("i. Harmonics."),
        p("2. Relay:"),
        p("g. Trip."),
        p("i. Close."),
    )
    clauses = by_no(tree)
    assert clauses["2.4.G.3.a.i"].text == "Rated Continuous Current: 4000 A"
    assert clauses["2.4.G.3.a.ii"].depth == 6
    assert clauses["2.4.G.3.a.iii"].text == "Rated Closing Current: 104 kA."
    assert clauses["2.4.G.3.a.iv"].text == "Rated Interrupting Time: 50 msec."
    assert clauses["2.4.G.3.a.v"].text == "Rated Voltage: 15 kV."
    assert "DEFERRED_ARTICLE" in clauses["2.5.A"].flags
    assert clauses["2.5.A"].table_rows == [
        ["STATION POWER"],
        ["Provide two load interrupter switches."],
    ]
    assert clauses["2.6.A.1.i"].depth == 5
    assert clauses["2.6.A.2.i"].depth == 5
    assert "2.4.G.3.i" not in clauses


def test_list_items_glued_without_a_sentence_end_or_a_space():
    """값 목록은 마침표 없이 다음 소문자 항목이 붙고(`4000 A c. Rated`), 번호 뒤 공백이 빠지기도 한다(`b.Prevent`)."""
    tree = tree_of(
        p("PART 2 - PRODUCTS"),
        p("2.4 SWITCHGEAR"),
        p("D. Cable compartments:"),
        p("2. Clearances:"),
        p("a. Provide stress cone distance."),
        p("b.Prevent cross-phasing."),
        p("c. Rated Continuous Current: 4000 A d. Rated Short-Time Current: 40 kA"),
        p("e. Remote panel see appendix C for layout f. One racking device. g. Test set."),
        p("h. Mount per Type i. Notes apply."),
        p("3. The quantity of spares shall be 3. Each spare is boxed."),
    )
    clauses = by_no(tree)
    assert clauses["2.4.D.2.b"].text == "Prevent cross-phasing."
    assert clauses["2.4.D.2.c"].text == "Rated Continuous Current: 4000 A"
    assert clauses["2.4.D.2.d"].text == "Rated Short-Time Current: 40 kA"
    assert clauses["2.4.D.2.e"].text == "Remote panel see appendix C for layout"
    assert clauses["2.4.D.2.f"].text == "One racking device."
    assert clauses["2.4.D.2.g"].text == "Test set."
    # 참조 단어 뒤 글자와 문장 속 숫자는 번호가 아니다.
    assert clauses["2.4.D.2.h"].text == "Mount per Type i. Notes apply."
    assert "2.4.D.4" not in clauses
    assert not [i for i in tree.numbering_issues if i.kind in ("GAP", "DUPLICATE")]


def test_next_parent_item_glued_into_a_child_paragraph_opens_the_parent_sibling():
    """하위 항목 문단 끝에 상위 목록의 다음 번호가 붙어 있으면(`… MIN) 5. AUTOMATIC MODE: a. …`) 그 자리에서 나눈다."""
    tree = tree_of(
        p("PART 3 - EXECUTION"),
        p("3.05 SWITCHGEAR SECTION"),
        p("L. Main and Tie Breakers 1. Main breakers shall be rated as follows: a. Rating 4000 A."),
        p("4. Sequence of operation: a. Definitions: 1) SOURCE A 2) SOURCE B"),
        p(
            "b. Default delays: 1) TDNE = 5 SECONDS 2) TDEN = 5 MINUTES (RANGE 0-60 MIN) "
            "5. AUTOMATIC MODE for substations: a. Normal operation: mains closed."
        ),
        p("b. Loss of source A: the tie closes."),
        p("c. Return of source A: the tie opens."),
        p("r. Cold load pickup 6. Field-Selectable Relay Settings: Required by the study."),
        p("a. Phase Protection: inverse curves."),
        p("b. The number of relays shall be 7. Each relay is drawout."),
    )
    clauses = by_no(tree)
    assert clauses["3.05.L.1.a"].text == "Rating 4000 A."
    assert clauses["3.05.L.4.b"].text.endswith("(RANGE 0-60 MIN)")
    assert clauses["3.05.L.5"].text == "AUTOMATIC MODE for substations:"
    assert clauses["3.05.L.5.a"].text == "Normal operation: mains closed."
    assert clauses["3.05.L.5.b"].parent_no == "3.05.L.5"
    assert clauses["3.05.L.5.r"].text == "Cold load pickup"
    assert clauses["3.05.L.6"].text.startswith("Field-Selectable Relay Settings:")
    assert clauses["3.05.L.6.a"].parent_no == "3.05.L.6"
    # 짧은 대문자 제목과 쌍점이 없으면 문장 속 숫자다.
    assert "3.05.L.7" not in clauses
    assert not [i for i in tree.numbering_issues if i.kind == "DUPLICATE"]


def test_table_rows_with_several_numbers_a_held_number_and_a_leaked_page_header():
    """표로 바뀐 쪽에서 번호 칸이 어긋난 배치(개발 문서 유형)를 원문 번호대로 읽는다."""
    tree = tree_of(
        p("PART 2 - PRODUCTS"),
        p("2.6 SWITCHGEAR"),
        p("C. Switchgear Bus:"),
        p("1. Use bus bars to connect compartments."),
        # 번호 칸 하나에 2. 3. 4., 본문 칸에 세 항목. 삭제 값은 해당 항목으로 간다.
        cell(0, 0, 0, "2. 3. 4."),
        cell(
            0,
            0,
            1,
            "Main Phase Bus: Uniform capacity. Neutral Bus: Refer to the Drawings. "
            "Vertical Section Bus Size: Comply with IEEE C37.20.1, including allowance for spare",
            ("None", 47),
        ),
        p("circuit breakers."),
        p("D. Breakers:"),
        p("3. Operating Mechanism:"),
        p("c. Stored-Energy Mechanism: Electrically charged."),
        p("1) Operating Handle: One for each breaker. 2) Electric Close Button: One for each."),
        p("4. Trip Devices:"),
        p("g. Ground-fault protection:"),
        # 5.는 다음 항목 번호, 1)은 g.의 이어짐. 5.는 번호 없는 다음 행에서 연다.
        cell(1, 0, 0, "5."),
        cell(1, 0, 1, "1)"),
        cell(1, 0, 2, "Arrange to provide protection for four-wire circuit."),
        cell(1, 1, 0, "2)"),
        cell(1, 1, 1, "Provide zone interlocking."),
        cell(1, 2, 1, "In addition to the trip unit, provide a protection relay for the"),
        p("Main Circuit Breaker"),
        p("6. MCCB Features:"),
        p("g. Undervoltage Trip: Set to operate at 35 percent."),
        # 쪽 머리말 조각이 다음 번호 칸 앞과 본문 칸 앞에 섞였다.
        cell(2, 0, 0, "PROJECT 2300096 h."),
        cell(2, 0, 1, "[LOCATION REDACTED] Auxiliary Contacts: Two SPDT switches."),
    )
    clauses = by_no(tree)
    assert clauses["2.6.C.2"].text == "Main Phase Bus: Uniform capacity."
    assert clauses["2.6.C.3"].text == "Neutral Bus: Refer to the Drawings."
    assert [(v.text, v.position) for v in clauses["2.6.C.3"].deleted_values] == [("None", 13)]
    assert clauses["2.6.C.4"].text.endswith("allowance for spare circuit breakers.")
    assert clauses["2.6.C.1"].table_rows == []
    assert clauses["2.6.D.3.c.1"].text == "Operating Handle: One for each breaker."
    assert clauses["2.6.D.3.c.2"].text == "Electric Close Button: One for each."
    assert clauses["2.6.D.3.c.2"].label == "2)"
    assert clauses["2.6.D.4.g.1"].text == "Arrange to provide protection for four-wire circuit."
    assert clauses["2.6.D.4.g.2"].text == "Provide zone interlocking."
    assert clauses["2.6.D.5"].text.startswith("In addition to the trip unit")
    assert clauses["2.6.D.5"].text.endswith("Main Circuit Breaker")
    assert clauses["2.6.D.6.h"].text == "Auxiliary Contacts: Two SPDT switches."
    assert not [i for i in tree.numbering_issues if i.kind in ("GAP", "DUPLICATE")]
