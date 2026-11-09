"""Synthetic-only context graph contracts; no documents, DB, network or real models."""

import asyncio
import json
from collections import Counter

import pytest
from pydantic import ValidationError

from backend.engine.rfp import context_graph as graph
from backend.engine.rfp.context_models import (
    ContextAssignment,
    ContextGroup,
    ContextProposal,
    ContextRelation,
    ContextRequirement,
    ContextResult,
)
from backend.shared.ai import AISettings, Generation, Usage


def requirement(key="r1", **changes):
    values = dict(
        requirement_id=key,
        version=1,
        document_version_id="d1",
        clause_id="c1",
        clause_no="1.1",
        section_path=["Transformer"],
        quote="Transformer shall be tested.",
        equipment="Transformer",
        status="REVIEW_REQUIRED",
        necessity="MANDATORY",
    )
    return ContextRequirement(**(values | changes))


def assignment(key="r1", **changes):
    values = dict(
        requirement_id=key,
        primary_category="QUALITY",
        additional_categories=[],
        group_ids=[],
        relations=[],
        reason="시험 요구",
        evidence_requirement_ids=[key],
        needs_review=False,
    )
    return ContextAssignment(**(values | changes))


def group(key="g1", **changes):
    return ContextGroup(
        **(
            dict(
                entity_id=key, name="Transformer", kind="EQUIPMENT", evidence_requirement_ids=["r1"]
            )
            | changes
        )
    )


def validate(rows, groups=None, assignments=None, diagrams=None):
    result = ContextResult(outcome="VALID", groups=groups or [], assignments=assignments or [])
    return graph.validate_result(rows, result, diagram_source_ids=diagrams)


def test_whitelist_read_only_and_nullable_clause():
    row = requirement(clause_id=None)
    original = row.model_dump()
    payload = graph.build_input(row)
    assert "status" not in payload and "necessity" not in payload
    assert "priority" not in payload and "product_family" not in payload
    assert payload["clause_id"] is None
    assert row.model_dump() == original
    with pytest.raises(ValidationError):
        ContextRequirement.model_validate(original | {"gold": "secret"})


def test_inventory_excerpt_is_explicit_and_link_preserves_full_quote():
    row = requirement(quote="x" * 900)
    assert graph.build_input(row, inventory=True)["quote_is_excerpt"] is True
    assert len(graph.build_input(row, inventory=True)["quote"]) == 600
    assert graph.build_input(row)["quote"] == row.quote


def test_one_assignment_shared_by_multiple_groups_counts_once():
    groups = [group("g1"), group("g2", kind="SYSTEM")]
    a = assignment(
        group_ids=["g1", "g2"],
        relations=[
            ContextRelation(group_id="g1", relation="APPLIES_TO"),
            ContextRelation(group_id="g2", relation="PART_OF"),
        ],
        additional_categories=["EQUIPMENT", "EQUIPMENT", "QUALITY"],
    )
    result = validate([requirement()], groups, [a])
    assert result.outcome == "VALID"
    assert len(result.assignments) == 1
    assert result.assignments[0].group_ids == ["g1", "g2"]
    assert result.assignments[0].additional_categories == ["EQUIPMENT"]
    assert all(g.needs_review for g in result.groups)


def test_missing_duplicate_and_foreign_ids_never_invent_requirement():
    result = validate(
        [requirement(), requirement("r2")],
        assignments=[assignment(), assignment(), assignment("foreign")],
    )
    assert [a.requirement_id for a in result.assignments] == ["r1", "r2"]
    assert all(a.primary_category == "UNCLASSIFIED" for a in result.assignments)
    assert {i.code for i in result.issues} == {
        "UNKNOWN_REQUIREMENT",
        "MISSING_OR_DUPLICATE_ASSIGNMENT",
    }
    with pytest.raises(ValueError, match="DUPLICATE"):
        validate([requirement(), requirement()])


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "invented generator"},
        {"tag": "invented-tag"},
        {"site": "invented-site"},
        {"evidence_requirement_ids": ["missing"]},
        {"diagram_source_ids": ["other-document:image1"]},
    ],
)
def test_unsupported_group_labels_and_references_are_explicitly_flagged(changes):
    result = validate(
        [requirement()], [group(**changes)], [assignment()], diagrams=["other-document:image1"]
    )
    assert result.groups == [] and result.outcome == "PARTIAL"
    assert result.issues[0].code == "INVALID_GROUP_EVIDENCE"


@pytest.mark.parametrize("suffix", ["tag: T2 site: North", "tag: T1 site: South"])
def test_different_explicit_tag_or_site_cannot_merge(suffix):
    rows = [
        requirement(quote="Transformer tag: T1 site: North"),
        requirement("r2", quote="Transformer " + suffix),
    ]
    result = validate(
        rows, [group(evidence_requirement_ids=["r1", "r2"])], [assignment(), assignment("r2")]
    )
    assert not result.groups
    assert any(i.code == "INVALID_GROUP_EVIDENCE" for i in result.issues)


def test_same_equipment_needs_physical_identity_not_family_name():
    a = assignment(
        group_ids=["g1"], relations=[ContextRelation(group_id="g1", relation="SAME_EQUIPMENT")]
    )
    result = validate([requirement()], [group()], [a])
    assert result.assignments[0].group_ids == []
    assert result.assignments[0].needs_review
    grounded = validate([requirement(quote="Transformer tag: T1")], [group(tag="T1")], [a])
    assert grounded.assignments[0].group_ids == ["g1"]


def test_cross_document_generic_identity_and_parent_cycles_are_not_accepted():
    rows = [requirement(), requirement("r2", document_version_id="other")]
    result = validate(
        rows,
        [group("g1", parent_id="g2"), group("g2", parent_id="g1")],
        [
            assignment(
                "r2",
                group_ids=["g1"],
                relations=[ContextRelation(group_id="g1", relation="RELATED")],
            )
        ],
    )
    assert any(i.code == "INVALID_PARENT" for i in result.issues)
    assert result.assignments[1].group_ids == []


def test_invalid_assignment_evidence_loses_classification_not_original_row():
    result = validate(
        [requirement()],
        assignments=[assignment(evidence_requirement_ids=["other"], primary_category="COMMERCIAL")],
    )
    assert result.assignments[0].primary_category == "UNCLASSIFIED"
    assert result.assignments[0].requirement_id == "r1"
    assert result.outcome == "PARTIAL"


def test_1196_requirements_chunked_max_six_and_progress_without_duplicate_assignments():
    active = 0
    peak = 0
    calls = Counter()
    sizes = {"inventory": [], "link": []}
    seen_progress = []

    async def provider(stage, payload):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        calls[stage] += 1
        sizes[stage].append(len(payload["requirements"]))
        await asyncio.sleep(0)
        active -= 1
        if stage == "inventory":
            return ContextProposal()
        return ContextProposal(
            assignments=[assignment(r["requirement_id"]) for r in payload["requirements"]]
        )

    rows = [requirement(f"r{i}") for i in range(1196)]
    result = asyncio.run(graph.run_context(rows, provider, progress=seen_progress.append))
    assert calls == {"inventory": 15, "link": 50}
    assert peak == 6 and active == 0
    assert max(sizes["inventory"]) <= 80 and max(sizes["link"]) <= 24
    assert len(result.assignments) == 1196
    assert {a.requirement_id for a in result.assignments} == {r.requirement_id for r in rows}
    assert result.outcome == "VALID"
    assert set(seen_progress) == {"context_map", "context_link", "context_validate"}


@pytest.mark.parametrize("count,expected_peak", [(1, 1), (24, 1), (25, 2), (120, 5), (121, 6)])
def test_short_inputs_only_start_needed_batches(count, expected_peak):
    active = 0
    peak = 0
    calls = Counter()

    async def provider(stage, payload):
        nonlocal active, peak
        assert payload["requirements"]
        active += 1
        peak = max(peak, active)
        calls[stage] += 1
        await asyncio.sleep(0)
        active -= 1
        if stage == "inventory":
            return ContextProposal()
        return ContextProposal(
            assignments=[assignment(r["requirement_id"]) for r in payload["requirements"]]
        )

    result = asyncio.run(graph.run_context([requirement(f"r{i}") for i in range(count)], provider))
    assert peak == expected_peak and active == 0
    assert calls == {"inventory": (count + 79) // 80, "link": (count + 23) // 24}
    assert len(result.assignments) == count
    assert result.outcome == "VALID"


def test_ambiguous_repair_is_once_bounded_and_cannot_modify_other_requirements():
    calls = Counter()

    async def provider(stage, payload):
        calls[stage] += 1
        if stage == "repair":
            assert len(payload["requirements"]) == 24
            return ContextProposal(assignments=[assignment("outside")])
        return ContextProposal()

    rows = [requirement(f"r{i}") for i in range(60)]
    result = asyncio.run(graph.run_context(rows, provider))
    assert calls["repair"] == 1
    assert len(result.assignments) == 60
    assert all(a.primary_category == "UNCLASSIFIED" for a in result.assignments)
    assert any(i.code == "REPAIR_SCOPE_VIOLATION" for i in result.issues)


def test_schema_failure_partial_is_explicit_and_provider_failure_is_not_fallback():
    async def bad_schema(stage, payload):
        raise graph.ContextOutputError("synthetic invalid output")

    result = asyncio.run(graph.run_context([requirement()], bad_schema))
    assert result.outcome == "PARTIAL"
    assert {i.code for i in result.issues} >= {
        "INVENTORY_INVALID_OUTPUT",
        "LINK_INVALID_OUTPUT",
        "REPAIR_INVALID_OUTPUT",
    }

    class AccountingStop(BaseException):
        pass

    async def provider_failure(stage, payload):
        raise AccountingStop()

    with pytest.raises(AccountingStop):
        asyncio.run(graph.run_context([requirement()], provider_failure))


def test_provider_failure_waits_started_calls_and_blocks_pending_batches():
    running = 0
    started = 0

    async def provider(stage, payload):
        nonlocal running, started
        started += 1
        running += 1
        try:
            await asyncio.sleep(0)
            if payload["requirements"][0]["requirement_id"] == "r0":
                raise RuntimeError("synthetic provider error")
            await asyncio.sleep(0.01)
            return ContextProposal()
        finally:
            running -= 1

    with pytest.raises(RuntimeError, match="synthetic"):
        asyncio.run(graph.run_context([requirement(f"r{i}") for i in range(500)], provider))
    assert started == 6 and running == 0


def test_tracing_and_input_limits_block_before_any_provider(monkeypatch):
    calls = []

    async def provider(stage, payload):
        calls.append(stage)
        return ContextProposal()

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    with pytest.raises(ValueError, match="LangSmith"):
        asyncio.run(graph.run_context([], provider))
    monkeypatch.delenv("LANGSMITH_TRACING")
    with pytest.raises(ValueError, match="LIMIT"):
        asyncio.run(graph.run_context([requirement(f"r{i}") for i in range(4001)], provider))
    with pytest.raises(ValueError, match="DIAGRAM"):
        asyncio.run(graph.run_context([requirement(diagram_source_ids=["x"])], provider))
    assert not calls


def test_inventory_namespaced_same_tag_different_document_never_merges():
    async def provider(stage, payload):
        if stage == "inventory":
            refs = [r["requirement_id"] for r in payload["requirements"]]
            return ContextProposal(
                groups=[group("shared", tag="T1", evidence_requirement_ids=refs)]
            )
        return ContextProposal(
            assignments=[assignment(r["requirement_id"]) for r in payload["requirements"]]
        )

    rows = [
        requirement(
            f"r{i}", quote="Transformer tag: T1", document_version_id="d1" if i < 80 else "d2"
        )
        for i in range(160)
    ]
    result = asyncio.run(graph.run_context(rows, provider))
    equipment = [g for g in result.groups if g.kind == "EQUIPMENT"]
    assert len(equipment) == 2
    assert {g.entity_id.split(":")[0] for g in equipment} == {"b0", "b1"}


def test_fake_provider_is_unclassified_explicit_offline_not_semantic_pass():
    result = asyncio.run(graph.run_context([requirement()], graph.fake_provider))
    assert result.outcome == "PARTIAL"
    assert result.assignments[0].primary_category == "UNCLASSIFIED"
    assert result.assignments[0].needs_review


def test_gemini_shared_factory_and_runtime_schema_validation(monkeypatch):
    captured = []
    client = object()
    monkeypatch.setattr(graph.ai, "create_client", lambda settings: client)

    def generate(actual_client, settings, prompt, *, response_schema, thinking_level=None):
        assert actual_client is client and settings.gemini_transient_retries == 0
        schema = response_schema.model_json_schema()
        encoded = json.dumps(schema)
        assert "additionalProperties" not in encoded and "maxItems" not in encoded
        assert "COMMERCIAL" in encoded and "primary_category" in encoded
        captured.append(prompt)
        return Generation(
            text=json.dumps(
                {
                    "assignments": [
                        {
                            "requirement_id": "r1",
                            "primary_category": "INVENTED",
                            "reason": "x",
                            "needs_review": False,
                        }
                    ]
                }
            ),
            usage=Usage(),
            model_version="fake",
            finish_reasons=["STOP"],
        )

    monkeypatch.setattr(graph.ai, "generate", generate)
    provider = graph.create_gemini_context_provider(AISettings())
    with pytest.raises(graph.ContextOutputError):
        asyncio.run(provider("link", {"requirements": [graph.build_input(requirement())]}))
    assert len(provider.calls) == 1
    assert '"status"' not in captured[0] and '"necessity"' not in captured[0]
    assert graph.PROMPT_VERSION in captured[0] or "v1" in captured[0]


def test_all_eight_categories_and_unclassified_are_distinct_and_preserve_original_ids():
    categories = [
        "EQUIPMENT",
        "TASK",
        "DELIVERABLE",
        "INTERFACE",
        "QUALITY",
        "SCHEDULE",
        "QUALIFICATION",
        "COMMERCIAL",
        "UNCLASSIFIED",
    ]
    rows = [requirement(f"r{i}") for i in range(len(categories))]
    result = validate(
        rows,
        assignments=[
            assignment(r.requirement_id, primary_category=category)
            for r, category in zip(rows, categories, strict=True)
        ],
    )
    assert [a.primary_category for a in result.assignments] == categories
    assert result.assignments[-1].needs_review and result.outcome == "PARTIAL"


def test_same_scoped_tag_merges_inventory_and_untagged_candidates_remain_available_cross_batch():
    async def provider(stage, payload):
        if stage == "inventory":
            refs = [r["requirement_id"] for r in payload["requirements"]]
            return ContextProposal(
                groups=[group("transformer", tag="T1", evidence_requirement_ids=refs)]
            )
        groups = payload["groups"]
        assert len(groups) == 1
        return ContextProposal(
            assignments=[
                assignment(
                    r["requirement_id"],
                    group_ids=[groups[0]["entity_id"]],
                    relations=[
                        ContextRelation(group_id=groups[0]["entity_id"], relation="APPLIES_TO")
                    ],
                )
                for r in payload["requirements"]
            ]
        )

    rows = [requirement(f"r{i}", quote="Transformer tag: T1") for i in range(100)]
    result = asyncio.run(graph.run_context(rows, provider))
    assert len(result.groups) == 1
    assert len(result.groups[0].evidence_requirement_ids) == 100
    assert len(result.assignments) == 100
    assert all(a.group_ids == [result.groups[0].entity_id] for a in result.assignments)


def test_diagram_reference_requires_both_global_and_requirement_evidence():
    row = requirement(diagram_source_ids=["d1:image1"])
    valid = validate(
        [row], [group(diagram_source_ids=["d1:image1"])], [assignment()], ["d1:image1"]
    )
    assert valid.groups[0].diagram_source_ids == ["d1:image1"]
    invalid = validate(
        [row], [group(diagram_source_ids=["d2:image1"])], [assignment()], ["d1:image1", "d2:image1"]
    )
    assert not invalid.groups


def test_empty_input_has_no_provider_calls():
    async def provider(stage, payload):
        raise AssertionError("provider must not run")

    result = asyncio.run(graph.run_context([], provider))
    assert result.outcome == "VALID" and result.assignments == [] and result.groups == []


def test_tag_substring_is_not_identity_evidence():
    result = validate(
        [requirement(quote="Transformer tag: T10")], [group(tag="T1")], [assignment()]
    )
    assert result.groups == []
    assert result.outcome == "PARTIAL"


@pytest.mark.parametrize("changes", [{"tag": "Transformer"}, {"site": "Transformer"}])
def test_general_literal_is_not_an_explicit_physical_identifier(changes):
    result = validate(
        [requirement()],
        [group(**changes)],
        [
            assignment(
                group_ids=["g1"],
                relations=[ContextRelation(group_id="g1", relation="SAME_EQUIPMENT")],
            )
        ],
    )
    assert result.groups == []
    assert result.assignments[0].group_ids == []
    assert result.assignments[0].needs_review
    assert any(issue.code == "INVALID_GROUP_EVIDENCE" for issue in result.issues)


def test_max_length_provider_ids_are_remapped_with_parent_links_and_roundtrip_safely():
    parent = "p" * 160
    child = "c" * 160

    async def provider(stage, payload):
        if stage == "inventory":
            return ContextProposal(
                groups=[group(parent, kind="SYSTEM"), group(child, parent_id=parent)]
            )
        return ContextProposal(assignments=[assignment()])

    result = asyncio.run(graph.run_context([requirement()], provider))
    provided = [g for g in result.groups if not g.entity_id.startswith("article:")]
    assert len(provided) == 2
    assert all(len(g.entity_id) <= 160 for g in result.groups)
    roots = [g for g in provided if g.parent_id is None]
    children = [g for g in provided if g.parent_id is not None]
    assert len(roots) == len(children) == 1
    assert children[0].parent_id == roots[0].entity_id
    assert ContextResult.model_validate_json(result.model_dump_json()) == result


def test_provider_schema_requires_all_assignment_fields_and_self_evidence_instruction():
    from google.genai import types

    schema = graph._ProviderSchema.model_json_schema()
    assignment_schema = schema["$defs"]["ContextAssignment"]
    assert set(assignment_schema["required"]) == set(assignment_schema["properties"])
    for field in (
        "evidence_requirement_ids",
        "group_ids",
        "relations",
        "additional_categories",
        "needs_review",
    ):
        assert field in assignment_schema["required"]
    config = types.GenerateContentConfig(
        response_mime_type="application/json", response_json_schema=schema
    )
    assert config.response_json_schema == schema
    assert "own requirement_id" in graph.INSTRUCTION
    assert "Do not substitute clause_id" in graph.INSTRUCTION


def test_evidence_error_codes_distinguish_missing_self_and_foreign_without_echoing_values():
    missing = validate([requirement()], assignments=[assignment(evidence_requirement_ids=[])])
    assert [issue.code for issue in missing.issues] == ["MISSING_SELF_EVIDENCE"]
    foreign = validate(
        [requirement()],
        assignments=[assignment(evidence_requirement_ids=["r1", "secret-unrecognized-id"])],
    )
    assert [issue.code for issue in foreign.issues] == ["UNKNOWN_EVIDENCE_REFERENCE"]
    for result in (missing, foreign):
        assert result.assignments[0].primary_category == "UNCLASSIFIED"
        assert result.assignments[0].needs_review and result.outcome == "PARTIAL"
        assert "secret-unrecognized-id" not in result.model_dump_json()


def test_v1_validation_is_stable_for_human_edits_while_new_results_use_v2():
    legacy = ContextResult(
        outcome="VALID", groups=[], assignments=[assignment()], prompt_version="rfp-context-v1"
    )
    assert graph.validate_result([requirement()], legacy) == legacy
    assert (
        ContextResult.model_validate_json(legacy.model_dump_json()).prompt_version
        == "rfp-context-v1"
    )
    assert (
        asyncio.run(graph.run_context([requirement()], graph.fake_provider)).prompt_version
        == "rfp-context-v2"
    )


# KAN-216·KAN-223: 원문 제목 "이름 (태그)"로 묶음을 합치고, 같은 태그·속성·값 요구를 중복으로 묶는다.
HEADINGS = graph.heading_tags(
    [
        {
            "document_version_id": "d1",
            "clause_no": "제6조",
            "title": None,
            "text": "중전압 개폐반 (MV-SWG-01)",
        },
        {
            "document_version_id": "d1",
            "clause_no": "제7조",
            "title": None,
            "text": "건식 변압기 (TR-01)",
        },
        {"document_version_id": "d1", "clause_no": "제2조", "title": None, "text": "공급 범위"},
    ]
)


def kr(key, clause, equipment, attribute=None, value=None, unit=None, quote="원문"):
    return requirement(
        key,
        clause_no=clause,
        section_path=["제1장", clause.split(".")[0], clause],
        equipment=equipment,
        attribute=attribute,
        value=value,
        unit=unit,
        quote=quote,
    )


def test_heading_tags_merge_split_groups_and_link_by_source_tag():
    rows = [
        kr("a", "제7조.2", "건식 변압기", "정격 용량", "630", "kVA"),
        kr("b", "OCR-DOCX-0001.18", "TR-01", "정격 용량", "630", "kVA"),
        kr("c", "제1조.2", "변압기", "2차 전압", "0.415", "kV"),
        kr("d", "제7조.3", None, "2차 전압", "415", "V"),
        kr("e", "제6조.2", "중전압 개폐반", "정격 최고 전압", "12", "kV"),
        kr("f", "제11조.2", "중전압 개폐반과 변압기", quote="중전압 개폐반과 변압기의 성적서"),
    ]
    result = ContextResult(
        outcome="PARTIAL",
        groups=[
            group("g1", name="건식 변압기", evidence_requirement_ids=["a"]),
            group("g2", name="TR-01", evidence_requirement_ids=["b"]),
        ],
        assignments=[assignment(k) for k in "abcdef"],
    )
    after = graph.consolidate(result, rows, HEADINGS)
    tagged = {g.tag: g for g in after.groups if g.tag}
    # 이름이 다른 두 묶음(건식 변압기, TR-01)이 TR-01 하나로 합쳐지고, MV-SWG-01 묶음은 제목에서 새로 생긴다.
    assert set(tagged) == {"TR-01", "MV-SWG-01"}
    assert tagged["TR-01"].name == "건식 변압기" and not tagged["TR-01"].needs_review
    linked = {a.requirement_id: a.group_ids for a in after.assignments}
    assert linked["c"] == [
        tagged["TR-01"].entity_id
    ]  # 일반 이름 "변압기"는 선언된 이름 하나에만 들어간다
    assert linked["f"] == []  # 두 설비를 함께 말하는 요구는 정하지 않는다
    dups = [set(d.requirement_ids) for d in after.duplicates]
    assert {"a", "b"} in dups
    assert {"c", "d"} in dups  # 0.415 kV와 415 V는 같은 값
    assert after.duplicates[0].canonical_requirement_id != "b"  # 도면 OCR은 대표가 아니다
    assert after.value_conflicts == []


def test_safety_validation_drops_heading_groups_so_saving_reapplies_consolidation():
    # 저장 직전 validate_context는 근거 문장의 태그만 보므로 제목 태그 묶음을 지운다(40444 P26-0002, 2026-11-05).
    # context_repository.save_generated는 검사 뒤 consolidate를 다시 적용한다. 같은 순서를 여기서 재현한다.
    rows = [
        kr("a", "제7조.2", "건식 변압기", "정격 용량", "630", "kVA"),
        kr("b", "OCR-DOCX-0001.18", "TR-01", "정격 용량", "630", "kVA"),
        kr("e", "제6조.2", "중전압 개폐반", "정격 최고 전압", "12", "kV"),
    ]
    result = ContextResult(outcome="PARTIAL", groups=[], assignments=[assignment(k) for k in "abe"])
    first = graph.consolidate(result, rows, HEADINGS)
    checked = graph.validate_context(first, rows)
    assert not [g for g in checked.groups if g.tag]
    saved = graph.consolidate(checked, rows, HEADINGS)
    assert {g.tag for g in saved.groups if g.tag} == {"TR-01", "MV-SWG-01"}
    assert [set(d.requirement_ids) for d in saved.duplicates] == [{"a", "b"}]
    # 저장된 결과를 사람이 그대로 다시 보내도 같은 결과여야 한다(context_routes의 human 비교).
    again = graph.consolidate(graph.validate_context(saved, rows), rows, HEADINGS)
    assert again == saved


def test_numeric_value_difference_is_a_conflict_not_a_duplicate():
    rows = [
        kr("a", "제7조.2", "건식 변압기", "정격 용량", "630", "kVA"),
        kr("b", "OCR-DOCX-0001.18", "TR-01", "정격 용량", "1000", "kVA"),
        kr("c", "제7조.1", "건식 변압기", "형식", "몰드 건식"),
        kr("d", "OCR-DOCX-0001.18", "TR-01", "형식", "몰드 변압기"),
    ]
    result = ContextResult(
        outcome="PARTIAL", groups=[], assignments=[assignment(k) for k in "abcd"]
    )
    after = graph.consolidate(result, rows, HEADINGS)
    assert after.duplicates == []
    [conflict] = after.value_conflicts  # 문장형 값(형식) 차이는 충돌로 보지 않는다
    assert conflict.tag == "TR-01" and conflict.drawing
    assert {v.value for v in conflict.values} == {"630", "1000"}


def test_documents_without_declared_tags_are_left_unchanged():
    rows = [requirement("r1"), requirement("r2")]
    result = ContextResult(
        outcome="PARTIAL", groups=[group()], assignments=[assignment("r1"), assignment("r2")]
    )
    assert graph.consolidate(result, rows, {}) == result
    assert graph.consolidate(result, rows, graph.heading_tags([])) == result


def test_batches_merge_same_normalized_equipment_name_but_not_different_tags():
    # KAN-255: 80개 배치마다 따로 만든 'Meters'·'meter'는 같은 문서 안이면 한 묶음이다. 태그가 다르면 합치지 않는다.
    rows = [
        requirement(f"r{i}", equipment="Meter", quote="Meter shall read kWh.") for i in range(4)
    ]
    source = {r.requirement_id: r for r in rows}
    groups = [
        group("a", name="Meters", evidence_requirement_ids=["r0"]),
        group("b", name="the meter", evidence_requirement_ids=["r1"]),
        group("c", name="Meter", tag="M-01", evidence_requirement_ids=["r2"]),
        group("d", name="Meter", kind="COMMON", evidence_requirement_ids=["r3"]),
    ]
    merged, aliases = graph.merge_by_name(groups, source)
    assert [g.entity_id for g in merged] == ["a", "c", "d"]
    assert merged[0].evidence_requirement_ids == ["r0", "r1"]
    assert aliases == {"b": "a"}


def test_test_requirement_in_part3_links_to_part2_equipment_by_name():
    # 같은 설비면 절(PART)이 달라도 연결한다. 이름이 다르면 여전히 거부한다.
    product = requirement("r1", section_path=["PART 2", "2.3"], equipment="Circuit Breakers")
    test = requirement("r2", section_path=["PART 3", "3.6"], equipment="circuit breaker")
    other = requirement("r3", section_path=["PART 3", "3.6"], equipment="Relay")
    link = lambda key: assignment(  # noqa: E731
        key, group_ids=["g1"], relations=[ContextRelation(group_id="g1", relation="APPLIES_TO")]
    )
    result = validate(
        [product, test, other],
        [group(name="Circuit Breakers", evidence_requirement_ids=["r1"])],
        [link("r1"), link("r2"), link("r3")],
    )
    by_id = {a.requirement_id: a for a in result.assignments}
    assert by_id["r2"].group_ids == ["g1"]
    assert by_id["r3"].group_ids == []


def test_unlinked_requirements_go_to_their_article_and_survive_revalidation():
    rows = [
        requirement("r1", clause_no="2.3.A", section_path=["PART 2", "2.3", "2.3.A"]),
        requirement("r2", clause_no="2.3.B", section_path=["PART 2", "2.3", "2.3.B"]),
        requirement("r3", clause_no="3.1", section_path=["PART 3", "3.1"]),
        requirement("r4", section_path=[]),
    ]
    result = validate(rows, [], [assignment(r.requirement_id) for r in rows])
    titled = graph.attach_to_articles(result, rows, {("d1", "2.3"): "MOLDED-CASE BREAKERS"})
    names = {g.name: g.evidence_requirement_ids for g in titled.groups}
    assert names == {"2.3 MOLDED-CASE BREAKERS": ["r1", "r2"], "3.1": ["r3"]}
    assert all(g.kind == "COMMON" and g.needs_review for g in titled.groups)
    by_id = {a.requirement_id: a for a in titled.assignments}
    assert by_id["r4"].group_ids == []
    assert by_id["r1"].primary_category == "QUALITY"
    # 저장 직전 다시 검사(checked_result)해도 조 묶음과 연결이 그대로 남는다.
    assert graph.validate_context(titled, rows).model_dump(exclude={"outcome", "issues"}) == (
        titled.model_dump(exclude={"outcome", "issues"})
    )
