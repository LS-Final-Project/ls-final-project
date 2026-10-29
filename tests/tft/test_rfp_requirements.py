"""KAN-185 요구 추출: 입력 구성, 근거 검사, 빠진 문장 보완 호출, 채점. 모델은 가짜 함수다."""

import json

import pytest

from backend.engine.documents.model import Block, DeletedValue, Position, build_document
from backend.engine.rfp.clauses import build_clause_tree
from backend.engine.rfp.edits import interpret_edits
from backend.engine.rfp.requirements import (
    ExtractedRequirement,
    ExtractionOutput,
    ResponseSchema,
    build_inputs,
    coverage,
    extract_section,
    prompt,
    validate_output,
)
from backend.engine.rfp.scoring import GoldRequirement, score
from backend.shared.ai import Generation, Usage


def p(text, *deleted, n=[0]):
    n[0] += 1
    return Block(
        block_id=f"q{n[0]:04d}",
        kind="PARAGRAPH",
        text=text,
        deleted_values=[DeletedValue(text=t, position=at, origin="STRIKE") for t, at in deleted],
        position=Position(paragraph_index=n[0]),
    )


def tree():
    blocks = [
        p("PART 2 - PRODUCTS"),
        p("2.5 EPOXY CAST RESIN, DRY-TYPE TRANSFORMER SECTION"),
        p("C. Cooling System shall contain fans for automatically increasing the kVA rating by:"),
        p("1. 480Y/277V Secondary: 33% with fans at 115 degree C rise.", ("50", 24)),
        p(
            "D. The controller shall be coated. The unit shall provide Fans, Alarm, and Trip output relays."
        ),
        p("K. BIL: 95 kV."),
        p("2.6 SWITCHBOARD"),
        p("A. Comply with UL 1558."),
    ]
    return build_clause_tree(build_document("DOCX", b"synthetic", blocks, [], tables=0))


def req(clause_no, quote, attribute, value=None, unit=None, operator="EQ", **extra):
    return {
        "clause_no": clause_no,
        "quote": quote,
        "kind": "SPEC",
        "equipment": "transformer",
        "task": None,
        "attribute": attribute,
        "value": value,
        "unit": unit,
        "operator": operator,
        "conditions": extra.pop("conditions", []),
        "standard_system": "NONE",
        "necessity": "MANDATORY",
        "designated_model": None,
        **extra,
    }


def gen(requirements):
    return Generation(
        json.dumps({"requirements": requirements}), Usage(10, 20, None, None, 30), "m", []
    )


def section():
    return build_inputs(tree(), ["2.5"])[0]


def test_response_schema_matches_the_strict_model_without_additional_properties():
    def fields(model):
        return {name: str(f.annotation) for name, f in model.model_fields.items()}

    assert fields(ResponseSchema).keys() == fields(ExtractionOutput).keys()
    from backend.engine.rfp.requirements import Condition, ConditionSchema, RequirementSchema

    assert RequirementSchema.model_fields.keys() == ExtractedRequirement.model_fields.keys()
    assert ConditionSchema.model_fields.keys() == Condition.model_fields.keys()
    # Gemini는 additionalProperties를 받지 않는다.
    assert "additionalProperties" not in json.dumps(ResponseSchema.model_json_schema())


def test_model_input_has_current_text_only_and_context():
    item = section()
    assert [c.clause_no for c in item.clauses] == ["2.5.C", "2.5.C.1", "2.5.D", "2.5.K"]
    c1 = next(c for c in item.clauses if c.clause_no == "2.5.C.1")
    assert c1.path.startswith("Cooling System shall contain")
    text = prompt(item)
    # 지운 값 "50"은 모델에 보내지 않는다(현행 33%만).
    assert "33%" in text and "5033" not in text and '"50"' not in text
    with pytest.raises(ValueError, match="RFP_SECTION_NOT_FOUND"):
        build_inputs(tree(), ["9.9"])


def test_quotes_and_values_must_come_from_the_clause_text():
    item = section()
    raw = json.dumps(
        {
            "requirements": [
                req(
                    "2.5.C.1",
                    "480Y/277V Secondary: 33% with fans at 115 degree C rise",
                    "kVA increase",
                    "33%",
                    conditions=[{"name": "secondary voltage", "value": "480Y/277V"}],
                ),
                req("2.5.C.1", "480Y/277V Secondary: 50%", "kVA increase", "50%"),
                req("2.5.K", "BIL: 95 kV", "BIL", "110", "kV"),
                req("2.5.Z", "BIL: 95 kV", "BIL", "95", "kV"),
                req(
                    "2.5.D",
                    "The controller shall be coated",
                    "coating",
                    "conformal coating",
                    operator="TEXT",
                ),
            ]
        }
    )
    kept, rejected, dropped = validate_output(raw, item)
    assert [(r.clause_no, r.value) for r in kept] == [("2.5.C.1", "33%"), ("2.5.D", None)]
    assert kept[0].conditions[0].value == "480Y/277V"
    assert [r.reason for r in rejected] == [
        "QUOTE_NOT_IN_CLAUSE",
        "VALUE_NOT_IN_QUOTE",
        "UNKNOWN_CLAUSE",
    ]
    assert dropped == 1
    with pytest.raises(ValueError, match="RFP_INVALID_OUTPUT"):
        validate_output("{not json", item)


def test_missing_requirement_sentences_get_one_gap_fill_call():
    item = section()
    calls = []

    def provider(text):
        calls.append(text)
        if len(calls) == 1:
            return gen(
                [
                    req("2.5.C.1", "33% with fans", "kVA increase", "33%"),
                    req("2.5.K", "BIL: 95 kV", "BIL", "95", "kV"),
                    req("2.5.D", "The controller shall be coated", "coating", operator="TEXT"),
                ]
            )
        return gen(
            [
                req(
                    "2.5.D",
                    "The unit shall provide Fans, Alarm, and Trip output relays",
                    "output relays",
                    operator="TEXT",
                )
            ]
        )

    result = extract_section(item, provider)
    assert result.outcome == "VALID" and len(calls) == 2
    # 보완 호출에는 빠진 문장만 들어간다.
    assert "output relays" in calls[1] and "BIL" not in calls[1]
    assert (result.covered_first, result.covered_final, result.sentences) == (3, 4, 4)
    assert result.gap_fill_added == 1 and len(result.requirements) == 4
    assert result.usage.input_tokens == 20


def test_full_coverage_needs_no_second_call_and_failures_are_reported():
    item = section()
    everything = gen(
        [
            req(
                "2.5.C",
                "Cooling System shall contain fans for automatically increasing the kVA rating by",
                "fans",
                operator="TEXT",
            ),
            req("2.5.C.1", "33% with fans", "kVA increase", "33%"),
            req("2.5.K", "BIL: 95 kV", "BIL", "95", "kV"),
            req("2.5.D", "The controller shall be coated", "coating", operator="TEXT"),
            req(
                "2.5.D",
                "The unit shall provide Fans, Alarm, and Trip output relays",
                "relays",
                operator="TEXT",
            ),
        ]
    )
    calls = []
    result = extract_section(item, lambda text: calls.append(text) or everything)
    assert len(calls) == 1 and result.covered_first == result.sentences
    total, missing = coverage(item, result.requirements)
    assert missing == []

    def broken(text):
        raise TimeoutError

    assert extract_section(item, broken).outcome == "TRANSPORT_FAILURE"
    bad = Generation("{}", Usage(), "m", [])
    assert extract_section(item, lambda text: bad).outcome == "SCHEMA_VIOLATION"


def test_scoring_reports_counts_strict_and_lenient():
    gold = [
        GoldRequirement(
            clause_no="2.4.E.1.d", attribute="momentary withstand", value="61", unit="kA"
        ),
        GoldRequirement(clause_no="2.4.A.1", attribute="arc-resistant construction"),
        GoldRequirement(clause_no="2.4.A.1", attribute="duplex fusible interrupter"),
    ]
    pred = [
        ExtractedRequirement.model_validate(
            req("2.4.E.1.d", "61 kA ASYM RMS", "momentary", "61", "kA ASYM RMS")
        ),
        ExtractedRequirement.model_validate(
            req(
                "2.4.A.1",
                "arc-resistant, duplex, fusible interrupter switchgear",
                "construction",
                operator="TEXT",
            )
        ),
        ExtractedRequirement.model_validate(
            req("2.4.A.1", "metal-enclosed", "enclosure", operator="TEXT")
        ),
    ]
    strict = score(gold, pred)
    # kA와 kA ASYM RMS는 같은 단위다. 값 없는 정답은 낱말이 가장 많이 겹치는 예측과 짝짓는다.
    assert (strict.recall, strict.precision) == ("2/3", "2/3")
    lenient = score(gold, pred, lenient=True)
    # 한 예측이 목록형 특징 두 개를 덮으면 관대 채점은 둘 다 인정한다.
    assert (lenient.recall, lenient.precision) == ("3/3", "2/3")


def test_title_and_body_strikes_are_kept_apart():
    blocks = [
        p("PART 3 - EXECUTION", (" (NOT IN CONTRACT)", 18)),
        p("3.1 INSTALLATION"),
        p("A. Install units.", ("x", 3)),
    ]
    tree_ = build_clause_tree(build_document("DOCX", b"s", blocks, [], tables=0))
    part = tree_.clauses[0]
    assert part.title == "EXECUTION" and part.title_deleted_values[0].position == 9
    assert part.deleted_values == []
    locations = [(t.clause_no, t.location) for t in interpret_edits(tree_).traces]
    assert locations == [("PART 3", "title"), ("3.1.A", "text")]


def test_a_second_section_in_one_file_restarts_numbering_and_is_reported():
    blocks = [
        p("SECTION 261116 - UNIT SUBSTATIONS"),
        p("PART 1 - GENERAL"),
        p("1.1 SUMMARY"),
        p("1.2 DEFINITIONS"),
        p("END OF SECTION 261116"),
        p("SECTION 262413 - SWITCHBOARDS"),
        p("PART 1 - GENERAL"),
        p("1.1 SUMMARY"),
        p("A. Section includes switchboards."),
    ]
    tree_ = build_clause_tree(build_document("DOCX", b"s", blocks, [], tables=0))
    numbers = [c.clause_no for c in tree_.clauses]
    assert numbers == ["PART 1", "1.1", "1.2", "PART 1~2", "1.1~2", "1.1~2.A"]
    assert "MULTIPLE_SECTIONS" in [i.kind for i in tree_.numbering_issues]
