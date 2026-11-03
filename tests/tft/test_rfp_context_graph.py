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
    assert len(result.groups) == 2
    assert {g.entity_id.split(":")[0] for g in result.groups} == {"b0", "b1"}


def test_fake_provider_is_unclassified_explicit_offline_not_semantic_pass():
    result = asyncio.run(graph.run_context([requirement()], graph.fake_provider))
    assert result.outcome == "PARTIAL"
    assert result.assignments[0].primary_category == "UNCLASSIFIED"
    assert result.assignments[0].needs_review


def test_gemini_shared_factory_and_runtime_schema_validation(monkeypatch):
    captured = []
    client = object()
    monkeypatch.setattr(graph.ai, "create_client", lambda settings: client)

    def generate(actual_client, settings, prompt, *, response_schema):
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
    assert len(result.groups) == 2
    assert all(len(g.entity_id) <= 160 for g in result.groups)
    roots = [g for g in result.groups if g.parent_id is None]
    children = [g for g in result.groups if g.parent_id is not None]
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
