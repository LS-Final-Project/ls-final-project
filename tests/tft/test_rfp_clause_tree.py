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
