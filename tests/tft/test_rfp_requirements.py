"""KAN-185 요구 추출: 입력 구성, 근거 검사, 빠진 문장 보완 호출, 채점. 모델은 가짜 함수다."""

import json

import pytest

from backend.engine.documents.model import Block, DeletedValue, Position, build_document
from backend.engine.rfp.clauses import build_clause_tree
from backend.engine.rfp.edits import interpret_edits
from backend.engine.rfp.requirements import (
    CODE_ONLY_FIELDS,
    ExtractedRequirement,
    ExtractionOutput,
    ResponseSchema,
    build_inputs,
    coverage,
    designated_item,
    extract_section,
    prompt,
    requirement_sentences,
    split_input,
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

    # needs_review는 코드가 정한다(모델 응답 스키마에 없다).
    assert (
        RequirementSchema.model_fields.keys()
        == ExtractedRequirement.model_fields.keys() - CODE_ONLY_FIELDS
    )
    assert ConditionSchema.model_fields.keys() == Condition.model_fields.keys()
    # Gemini는 additionalProperties를 받지 않는다.
    assert "additionalProperties" not in json.dumps(ResponseSchema.model_json_schema())
    # Gemini 구조화 출력은 enum 둘 이상의 anyOf를 400 INVALID_ARGUMENT로 거절한다(2026-10-30 실측).
    # 어휘 + OTHER는 enum 하나로 펼친다. null과의 anyOf(enum 하나 + null)는 받는다.
    properties = ResponseSchema.model_json_schema()["$defs"]["RequirementSchema"]["properties"]
    for name, schema in properties.items():
        enums = [part for part in schema.get("anyOf", []) if "enum" in part]
        assert len(enums) <= 1, name
    assert "OTHER" in properties["attribute_key"]["enum"]


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
    kept, rejected, dropped, _ = validate_output(raw, item)
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


def metering_tree():
    """지정 모델이 있는 합성 절. 2.7 B는 취소선으로 지운 옛 모델명을 품는다."""
    blocks = [
        p("PART 2 - PRODUCTS"),
        p("2.6 SECONDARY SWITCHGEAR"),
        p("A. In addition to the integrated trip unit, provide a SEL-751A protection relay."),
        p("2.7 METERING"),
        p("A. Microprocessor-based power meter. Model # PowerLogic ION 9000 with Ethernet."),
        p("B. Feeder meter by Acme Meters, model AM-20.", ("AM-10 ", 39)),
    ]
    return build_clause_tree(build_document("DOCX", b"synthetic", blocks, [], tables=0))


def test_a_designated_model_and_maker_stay_only_when_the_clause_names_them():
    six, seven = build_inputs(metering_tree(), ["2.6", "2.7"])
    designated = {"kind": "DESIGNATED_PRODUCT", "operator": "TEXT"}
    relay = req(
        "2.6.A",
        "provide a SEL-751A protection relay",
        "protection relay",
        designated_model="SEL-751A",
        # 제조사 이름이 원문에 없다: 모델명에서 짐작한 값은 비운다.
        designated_manufacturer="Schweitzer Engineering Laboratories",
        **designated,
    )
    kept, _, _, cleared = validate_output(json.dumps({"requirements": [relay]}), six)
    assert designated_item(kept[0]).model_dump() == {
        "manufacturer": None,
        "model": "SEL-751A",
        "clause_no": "2.6.A",
    }
    assert cleared == 1
    meters = [
        req(
            "2.7.A",
            "Model # PowerLogic ION 9000",
            "power meter",
            designated_model="PowerLogic ION 9000",
            **designated,
        ),
        req(
            "2.7.B",
            "Feeder meter by Acme Meters, model AM-20",
            "feeder meter",
            designated_model="AM-20",
            designated_manufacturer="Acme Meters",
            **designated,
        ),
        # 지운 옛 모델(AM-10)과 원문에 없는 모델은 지정품이 아니다. 요구는 남긴다.
        req(
            "2.7.B",
            "Feeder meter by Acme Meters",
            "feeder meter",
            designated_model="AM-10",
            designated_manufacturer="Acme Meters",
            **designated,
        ),
        req("2.7.A", "Microprocessor-based power meter", "meter", designated_model="ION 8650"),
    ]
    kept, rejected, _, cleared = validate_output(json.dumps({"requirements": meters}), seven)
    assert rejected == [] and cleared == 2
    assert [designated_item(r) and designated_item(r).model_dump() for r in kept] == [
        {"manufacturer": None, "model": "PowerLogic ION 9000", "clause_no": "2.7.A"},
        {"manufacturer": "Acme Meters", "model": "AM-20", "clause_no": "2.7.B"},
        None,
        None,
    ]
    # 모델이 비면 제조사도 비운다(명세: designated_model이 null이면 designated_item도 null).
    assert kept[2].designated_manufacturer is None
    assert "Do not infer a maker" in prompt(six)


def test_the_section_result_counts_cleared_designations():
    six = build_inputs(metering_tree(), ["2.6"])[0]
    output = gen(
        [
            req(
                "2.6.A",
                "In addition to the integrated trip unit, provide a SEL-751A protection relay",
                "protection relay",
                operator="TEXT",
                designated_model="SEL-751B",
            )
        ]
    )
    result = extract_section(six, lambda _text: output)
    assert result.designated_dropped == 1 and result.requirements[0].designated_model is None


def long_tree():
    """첫 단계 조항 셋(A·B·C)이 각각 긴 합성 절."""
    filler = "The relay shall report status. " * 40
    blocks = [p("PART 2 - PRODUCTS"), p("2.6 SWITCHGEAR")]
    for letter in "ABC":
        blocks += [p(f"{letter}. Group {letter}:"), p(f"1. {filler}"), p(f"2. {filler}")]
    return build_clause_tree(build_document("DOCX", b"synthetic", blocks, [], tables=0))


def test_a_long_section_is_split_by_first_level_groups_and_merged():
    item = build_inputs(long_tree(), ["2.6"])[0]
    # 묶음 하나가 약 2.5천 자라 기본 상한(4000자)에서 묶음마다 한 부분이 된다.
    pieces = split_input(item)
    # 묶음(2.6.A와 그 하위)은 쪼개지 않고 순서를 지킨다.
    assert [[c.clause_no for c in piece.clauses] for piece in pieces] == [
        ["2.6.A", "2.6.A.1", "2.6.A.2"],
        ["2.6.B", "2.6.B.1", "2.6.B.2"],
        ["2.6.C", "2.6.C.1", "2.6.C.2"],
    ]
    assert split_input(section()) == [section()]
    calls = []

    def provider(text):
        calls.append(text)
        if "2.6.B.1" in text:
            raise TimeoutError("provider timeout")
        letter = "A" if "2.6.A.1" in text else "C"
        return gen(
            [
                req(f"2.6.{letter}.{n}", "The relay shall report status", "status", operator="TEXT")
                for n in (1, 2)
            ]
        )

    result = extract_section(item, provider)
    # 실패한 부분이 있으면 VALID가 아니지만, 성공한 부분의 요구는 남는다.
    assert (result.outcome, result.parts, result.parts_failed) == ("TRANSPORT_FAILURE", 3, 1)
    assert [r.clause_no for r in result.requirements] == [
        "2.6.A.1",
        "2.6.A.2",
        "2.6.C.1",
        "2.6.C.2",
    ]
    assert result.sentences == len(requirement_sentences(item))
    assert result.usage.input_tokens == 20 and len(calls) == 3


def breaker_section():
    """일반 표현의 합성 절(차단기). 의미 칸 검사에 쓴다."""
    blocks = [
        p("PART 2 - PRODUCTS"),
        p("2.1 LOW-VOLTAGE CIRCUIT BREAKERS"),
        p("A. Rated current shall be 630 A ±5%."),
        p("B. Breakers shall be listed and labeled to the applicable product standard."),
        p("C. Enclosure paint color shall be light grey."),
        p("D. Rated operational voltage shall be 415 V with a tolerance of +10/-15 percent."),
    ]
    tree = build_clause_tree(build_document("DOCX", b"synthetic", blocks, [], tables=0))
    return build_inputs(tree, ["2.1"])[0]


def semantic(clause_no, quote, attribute, value, unit, key, family, tolerance=None, **extra):
    return req(
        clause_no,
        quote,
        attribute,
        value,
        unit,
        attribute_key=key,
        equipment_family=family,
        tolerance=tolerance,
        certification_listed=extra.pop("listed", False),
        **extra,
    )


def test_semantic_fields_are_kept_when_they_are_in_the_lists_and_the_quote():
    """속성 키·계열·허용 오차·Listed는 목록과 인용에 맞으면 그대로 남고 검토 표시가 없다."""
    raw = json.dumps(
        {
            "requirements": [
                semantic(
                    "2.1.A",
                    "Rated current shall be 630 A ±5%",
                    "rated current",
                    "630",
                    "A",
                    "rated_current",
                    "LV_BREAKER",
                    "±5%",
                ),
                semantic(
                    "2.1.B",
                    "Breakers shall be listed and labeled",
                    "listing",
                    None,
                    None,
                    "OTHER",
                    "LV_BREAKER",
                    operator="TEXT",
                    listed=True,
                ),
            ]
        }
    )
    kept, rejected, _, _ = validate_output(raw, breaker_section())
    assert rejected == []
    assert [
        (r.attribute_key, r.equipment_family, r.tolerance, r.certification_listed, r.needs_review)
        for r in kept
    ] == [
        ("rated_current", "LV_BREAKER", "±5%", False, False),
        ("OTHER", "LV_BREAKER", None, True, False),
    ]
    # 원문 칸은 그대로다(정규화는 코드가 한다).
    assert (kept[0].attribute, kept[0].value, kept[0].unit) == ("rated current", "630", "A")


def test_names_outside_the_lists_become_other_and_are_reviewed():
    """목록 밖 키·계열은 OTHER로 바꾸고 검토로 둔다(모델의 추측을 믿지 않는다). 섹션 전체를 버리지 않는다."""
    raw = json.dumps(
        {
            "requirements": [
                semantic(
                    "2.1.C",
                    "Enclosure paint color shall be light grey",
                    "paint color",
                    "light grey",
                    None,
                    "paint_color",
                    "ENCLOSURE",
                    operator="TEXT",
                )
            ]
        }
    )
    [kept], _, _, _ = validate_output(raw, breaker_section())
    assert (kept.attribute_key, kept.equipment_family, kept.needs_review) == (
        "OTHER",
        "OTHER",
        True,
    )


def test_a_tolerance_outside_the_quote_is_cleared_and_a_missing_one_is_reviewed():
    """인용에 없는 허용 오차는 지우고 검토, 인용에 허용 오차가 있는데 비운 수치 요구도 검토다."""
    raw = json.dumps(
        {
            "requirements": [
                semantic(
                    "2.1.A",
                    "Rated current shall be 630 A",
                    "rated current",
                    "630",
                    "A",
                    "rated_current",
                    "LV_BREAKER",
                    "±10%",
                ),
                semantic(
                    "2.1.D",
                    "Rated operational voltage shall be 415 V with a tolerance of +10/-15 percent",
                    "rated operational voltage",
                    "415",
                    "V",
                    "rated_operational_voltage",
                    "LV_BREAKER",
                ),
            ]
        }
    )
    invented, missing = validate_output(raw, breaker_section())[0]
    assert (invented.tolerance, invented.needs_review) == (None, True)
    assert (missing.tolerance, missing.needs_review) == (None, True)


def test_the_prompt_lists_the_vocabulary_without_rfp_wording():
    """지시문은 단일 원본의 어휘·계열 목록을 싣고 모르면 OTHER라고 말한다."""
    text = prompt(breaker_section())
    assert "rated_short_circuit_breaking_current" in text and "LV_BREAKER" in text
    assert '"OTHER"' in text and "Never compute a range" in text
    # 계기용 변압기는 전력 변압기 계열이 아니다(검증 세트 실측 오분류, 일반 지침).
    assert "Instrument transformers" in text


def test_the_vocabulary_is_one_source_for_spec_facts_and_extraction():
    """협력사·자사 사양 속성 형식과 추출 키가 같은 원본이고, 자사 사양 파일은 그 부분집합이다."""
    from backend.api.tft.partners.schemas import Attribute
    from backend.api.tft.rfp.comparison import load_own_products
    from backend.engine.rfp.vocabulary import (
        ATTRIBUTE_KEYS,
        EQUIPMENT_FAMILIES,
        AttributeKey,
    )

    assert Attribute is AttributeKey
    products = load_own_products()
    assert {f.attribute for p in products for f in p.spec_facts} <= ATTRIBUTE_KEYS
    assert {p.family for p in products} <= EQUIPMENT_FAMILIES


def assembly_section():
    """조립품 공급 요구(양성)와 장비 나열(반례)의 합성 절. 일반 표현만 쓴다."""
    blocks = [
        p("PART 2 - PRODUCTS"),
        p("2.1 POWER DISTRIBUTION CENTER"),
        p("A. Breakers, relays and meters shall be factory-assembled and tested as one unit."),
        p(
            "B. The drive and motor shall be furnished by one manufacturer with unit responsibility."
        ),
        p("C. Provide circuit breakers, relays and meters."),
    ]
    tree = build_clause_tree(build_document("DOCX", b"synthetic", blocks, [], tables=0))
    return build_inputs(tree, ["2.1"])[0]


def test_assembly_clause_is_kept_for_a_single_unit_and_not_for_a_list():
    """조립품·단일 공급 책임 요구는 true, 장비 나열은 false로 남고 통합 책임 판별 입력이 된다."""
    raw = json.dumps(
        {
            "requirements": [
                req(
                    "2.1.A",
                    "factory-assembled and tested as one unit",
                    "assembly",
                    operator="TEXT",
                    assembly_clause=True,
                ),
                req(
                    "2.1.B",
                    "furnished by one manufacturer with unit responsibility",
                    "supply responsibility",
                    operator="TEXT",
                    assembly_clause=True,
                ),
                req(
                    "2.1.C",
                    "Provide circuit breakers, relays and meters",
                    "included equipment",
                    operator="TEXT",
                    assembly_clause=False,
                ),
            ]
        }
    )
    kept, rejected, _, _ = validate_output(raw, assembly_section())
    assert rejected == []
    assert [(r.clause_no, r.assembly_clause) for r in kept] == [
        ("2.1.A", True),
        ("2.1.B", True),
        ("2.1.C", False),
    ]
    from backend.api.tft.rfp.integration import Requirement, integration_responsibility

    result = integration_responsibility(
        [
            Requirement(clause_no=r.clause_no, task=r.task, assembly_clause=r.assembly_clause)
            for r in kept
        ]
    )
    assert (result.required, result.basis_clause_nos) == (True, ["2.1.A", "2.1.B"])


def test_a_model_answer_without_assembly_clause_stays_false_and_the_prompt_explains_it():
    """옛 응답처럼 칸이 없으면 기본 false다. 응답 스키마는 필수이고 지시문은 나열은 false라고 말한다."""
    from backend.engine.rfp.requirements import RequirementSchema

    raw = json.dumps({"requirements": [req("2.1.C", "Provide circuit breakers", "equipment")]})
    assert validate_output(raw, assembly_section())[0][0].assembly_clause is False
    assert RequirementSchema.model_fields["assembly_clause"].is_required()
    text = prompt(assembly_section())
    assert "assembly_clause" in text and "merely listed together is false" in text
