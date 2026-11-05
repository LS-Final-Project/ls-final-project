"""Single-line graph contracts with authored geometry and fake provider output only."""

import base64
import hashlib
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.engine.documents.diagrams import DiagramGraph
from backend.engine.documents.router import extract
from backend.engine.documents.vision import (
    CheckedTranscription,
    ProviderTranscription,
    Transcription,
    append_ocr_clauses,
    gemini_image_provider,
    image_inputs,
    transcribe,
)
from backend.engine.rfp.clauses import build_clause_tree
from backend.shared.ai import AISettings
from tests.test_ai_client import sdk_http as sdk_http
from tests.tft.test_rfp_image_transcription import IMAGE, PNG, docx, generation


def diagram():
    return {
        "diagram_type": "SINGLE_LINE",
        "title": "Single line — review candidate",
        "nodes": [
            {
                "id": "N1",
                "tag": "TR-01",
                "label": "TR-01",
                "kind": "TRANSFORMER",
                "bbox": {"x_min": 100, "y_min": 100, "x_max": 220, "y_max": 220},
                "ratings": ["22.9 kV / 380 V", "1000 kVA"],
                "evidence": "TR-01 printed beside symbol",
            },
            {
                "id": "N2",
                "tag": "CB-01",
                "label": "CB-01",
                "kind": "BREAKER",
                "bbox": {"x_min": 100, "y_min": 500, "x_max": 220, "y_max": 580},
                "evidence": "CB-01 printed beside breaker symbol",
            },
            {
                "id": "N3",
                "label": "미식별 표기",
                "kind": "UNKNOWN",
                "bbox": None,
                "evidence": "partly legible symbol at lower right",
                "uncertainties": ["symbol unclear"],
            },
        ],
        "edges": [
            {
                "id": "E1",
                "source": "N1",
                "target": "N2",
                "connection": "CONTINUOUS_LINE",
                "path": [{"x": 160, "y": 220}, {"x": 160, "y": 500}],
                "evidence": "single continuous vertical line between visible symbols",
            },
        ],
        "uncertainties": [],
        "review_required": True,
    }


def test_single_line_candidates_retain_labels_geometry_and_unknown_nodes():
    parsed = DiagramGraph.model_validate(diagram())
    assert parsed.review_required is True
    assert parsed.nodes[0].ratings == ["22.9 kV / 380 V", "1000 kVA"]
    assert parsed.nodes[2].kind == "UNKNOWN" and parsed.nodes[2].bbox is None
    assert parsed.edges[0].target == "N2"
    assert parsed.edges[0].path[0].x == 160
    assert DiagramGraph.model_validate_json(parsed.model_dump_json()) == parsed


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["nodes"].append(deepcopy(d["nodes"][0])),
        lambda d: d["edges"].append(deepcopy(d["edges"][0])),
        lambda d: d["edges"][0].update(target="MISSING"),
        lambda d: d["edges"][0].update(source="MISSING"),
        lambda d: d["edges"][0].update(target="N1"),
        lambda d: d["nodes"][0]["bbox"].update(x_min=300),
        lambda d: d["nodes"][0]["bbox"].update(y_max=1001),
        lambda d: d["nodes"][0]["bbox"].update(x_min=float("nan")),
        lambda d: d["edges"][0]["path"][0].update(y=-1),
        lambda d: d["edges"][0]["path"][0].update(x=float("inf")),
        lambda d: d["edges"][0].update(path=[{"x": 1, "y": 2}]),
        lambda d: d["edges"][0].update(connection="UNRESOLVED"),
        lambda d: d["edges"][0].update(connection="CROSS_PAGE", target=None),
        lambda d: d["edges"][0].update(connection="CROSS_PAGE", off_page_reference="sheet 2"),
        lambda d: d.update(diagram_type="OTHER"),
        lambda d: d.update(diagram_type="UNKNOWN"),
        lambda d: d.update(review_required=False),
        lambda d: d.update(review_required=1),
        lambda d: d.update(confirmed=True),
        lambda d: d["nodes"][0].update(verified=True),
        lambda d: d["edges"][0].update(confidence=1),
    ],
)
def test_untrusted_graph_cannot_create_invalid_or_confirmed_connections(change):
    value = diagram()
    change(value)
    with pytest.raises(ValidationError):
        DiagramGraph.model_validate(value)


def test_crossing_and_cross_page_stay_explicitly_unresolved():
    value = diagram()
    value["edges"] = [
        {
            "id": "E1",
            "source": "N1",
            "target": "N2",
            "connection": "UNRESOLVED",
            "path": [],
            "evidence": "two lines cross without a visible dot",
            "uncertainties": ["crossing is not proof of a junction"],
        },
        {
            "id": "E2",
            "source": "N2",
            "target": None,
            "connection": "CROSS_PAGE",
            "path": [],
            "evidence": "printed continuation arrow",
            "off_page_reference": "SEE SHT 02 A3",
        },
    ]
    graph = DiagramGraph.model_validate(value)
    assert graph.edges[0].connection == "UNRESOLVED"
    assert graph.edges[1].target is None
    assert graph.edges[1].off_page_reference == "SEE SHT 02 A3"
    assert graph.review_required is True


def test_other_drawing_preserves_observations_without_connections():
    value = diagram()
    value.update(diagram_type="OTHER", edges=[])
    assert DiagramGraph.model_validate(value).nodes[0].tag == "TR-01"


def test_one_image_call_keeps_original_bytes_and_separate_graph_without_new_requirements():
    source = docx(IMAGE)
    extracted = extract(source)
    plan = image_inputs(source, extracted)
    prior_sha = hashlib.sha256(plan.inputs[0].data).hexdigest()
    calls = []

    def provider(item):
        calls.append(item)
        return generation(["TR-01"], contains_diagram=True, diagram=diagram())

    result = transcribe(plan, provider)
    assert len(calls) == 1 and calls[0].data == PNG
    assert hashlib.sha256(result.items[0].source.data).hexdigest() == prior_sha
    graph = result.items[0].diagram
    assert graph is not None and graph.nodes[0].tag == "TR-01"
    tree = append_ocr_clauses(build_clause_tree(extracted), result)
    texts = [c.text for c in tree.clauses]
    assert "TR-01" in texts
    assert all("1000 kVA" not in text and "CONTINUOUS_LINE" not in text for text in texts)
    assert any(x.reason == "OCR_REQUIRES_SOURCE_REVIEW" for x in result.skipped)


def test_invalid_graph_preserves_valid_literal_ocr_and_explicit_failure():
    value = diagram()
    value["edges"][0]["target"] = "NOT_FOUND"
    plan = image_inputs(docx(IMAGE), extract(docx(IMAGE)))
    result = transcribe(plan, lambda _: generation(["TR-01"], contains_diagram=True, diagram=value))
    assert result.items[0].lines == ["TR-01"] and result.items[0].diagram is None
    assert any(x.reason == "OCR_DIAGRAM_INVALID" for x in result.skipped)


def test_graph_without_declared_drawing_is_not_accepted():
    data = docx(IMAGE)
    plan = image_inputs(data, extract(data))
    result = transcribe(
        plan, lambda _: generation(["text"], contains_diagram=False, diagram=diagram())
    )
    assert result.items[0].diagram is None
    assert any(x.reason == "OCR_DIAGRAM_INVALID" for x in result.skipped)


def test_legacy_drawing_response_keeps_text_and_explicitly_marks_unanalyzed_connections():
    data = docx(IMAGE)
    plan = image_inputs(data, extract(data))
    result = transcribe(plan, lambda _: generation(["TR-01"], contains_diagram=True))
    assert result.items[0].diagram is None
    assert any(x.reason == "OCR_DIAGRAM_NOT_ANALYZED" for x in result.skipped)


@pytest.mark.parametrize(
    "transform",
    [
        '<a:srcRect l="12000"/>',
        '<a:xfrm rot="5400000"/>',
        '<a:xfrm flipH="1"/>',
    ],
)
def test_cropped_or_rotated_media_is_preserved_but_not_read_as_visible_drawing(transform):
    nodes = IMAGE.replace('<a:blip r:embed="r1"/>', '<a:blip r:embed="r1"/>' + transform)
    data = docx(nodes)
    plan = image_inputs(data, extract(data))
    assert len(plan.inputs) == 1 and plan.inputs[0].data == PNG
    assert plan.inputs[0].presentation_warning == "OCR_IMAGE_PRESENTATION_UNSUPPORTED"
    result = transcribe(plan, lambda _: pytest.fail("hidden/untransformed original was sent"))
    assert result.items == []
    assert result.skipped[0].reason == "OCR_IMAGE_PRESENTATION_UNSUPPORTED"


def test_provider_schema_uses_real_sdk_without_network_and_preserves_inline_image(sdk_http):
    state, requests = sdk_http
    state["response"]["candidates"][0]["content"]["parts"][0]["text"] = generation(
        ["TR-01"], contains_diagram=True, diagram=diagram()
    ).text
    settings = AISettings(gemini_api_key="fixture-explicit-key", gemini_transient_retries=0)
    plan = image_inputs(docx(IMAGE), extract(docx(IMAGE)))
    result = transcribe(plan, gemini_image_provider(settings))
    assert result.items[0].diagram is not None
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    schema = body["generationConfig"]["responseJsonSchema"]
    assert "diagram" in schema["properties"]
    assert "additionalProperties" not in json.dumps(schema)
    for constraint in (
        "maxItems",
        "minItems",
        "maxLength",
        "minLength",
        "minimum",
        "maximum",
        "pattern",
    ):
        assert f'"{constraint}"' not in json.dumps(schema)
    inline = body["contents"][0]["parts"][0]["inlineData"]
    assert inline["mime_type"] == "image/png"
    assert base64.b64decode(inline["data"]) == PNG
    assert schema["$defs"]["DiagramGraph"]["properties"]["review_required"]["const"] is True
    assert "diagram" in Transcription.model_json_schema()["properties"]
    prompt = body["contents"][0]["parts"][1]["text"]
    assert "Crossing lines" in prompt and "do not join another page automatically" in prompt


@pytest.mark.parametrize("hidden", ["vanish", "webHidden"])
@pytest.mark.parametrize("value", [None, "1", "true", "0", "false", "off"])
def test_hidden_run_preserves_asset_and_honors_explicit_on_off(hidden, value):
    attr = "" if value is None else f' w:val="{value}"'
    nodes = IMAGE.replace("<w:r>", f"<w:r><w:rPr><w:{hidden}{attr}/></w:rPr>")
    data = docx(nodes)
    plan = image_inputs(data, extract(data))
    assert plan.inputs[0].data == PNG
    calls = []
    result = transcribe(plan, lambda image: calls.append(image) or generation())
    if value in {"0", "false", "off"}:
        assert len(calls) == 1 and result.items
    else:
        assert calls == [] and not result.items
        assert result.skipped[0].reason == "OCR_IMAGE_PRESENTATION_UNSUPPORTED"


def test_vml_visibility_with_whitespace_is_not_sent_as_visible_image():
    nodes = IMAGE.replace("<w:drawing>", '<w:drawing style="visibility: hidden">')
    data = docx(nodes)
    plan = image_inputs(data, extract(data))
    assert plan.inputs[0].data == PNG
    result = transcribe(plan, lambda _: pytest.fail("hidden image provider call"))
    assert result.items == []
    assert result.skipped[0].reason == "OCR_IMAGE_PRESENTATION_UNSUPPORTED"


def test_provider_projection_preserves_typed_shape_and_does_not_mutate_runtime_schema():
    original = Transcription.model_json_schema()
    projected = ProviderTranscription.model_json_schema()
    assert projected["properties"]["lines"]["type"] == "array"
    assert projected["properties"]["lines"]["items"] == {"type": "string"}
    assert projected["required"] == original["required"]
    assert projected["properties"]["diagram"] == original["properties"]["diagram"]
    for name, schema in original["$defs"].items():
        reduced = projected["$defs"][name]
        assert reduced["type"] == schema["type"]
        assert reduced.get("required") == schema.get("required")
        assert set(reduced["properties"]) == set(schema["properties"])
        for field, value in schema["properties"].items():
            for keyword in ("type", "enum", "const", "$ref", "default"):
                if keyword in value:
                    assert reduced["properties"][field][keyword] == value[keyword]
    assert Transcription.model_json_schema() == original
    assert original["properties"]["lines"]["maxItems"] == 128
    assert original["$defs"]["DiagramGraph"]["properties"]["nodes"]["maxItems"] == 80
    assert CheckedTranscription.model_json_schema()["additionalProperties"] is False


@pytest.mark.parametrize(
    "change,reason",
    [
        (lambda d: d.update(lines=["x"] * 129), "OCR_RESPONSE_INVALID"),
        (lambda d: d.update(uncertain=["x"] * 33), "OCR_RESPONSE_INVALID"),
        (lambda d: d["diagram"]["nodes"][0].update(id="1bad"), "OCR_DIAGRAM_INVALID"),
        (lambda d: d["diagram"]["nodes"][0].update(label="x" * 321), "OCR_DIAGRAM_INVALID"),
        (lambda d: d["diagram"]["nodes"][0]["bbox"].update(x_min=-1), "OCR_DIAGRAM_INVALID"),
        (
            lambda d: d["diagram"]["nodes"].extend([d["diagram"]["nodes"][0]] * 80),
            "OCR_DIAGRAM_INVALID",
        ),
        (
            lambda d: d["diagram"]["edges"].extend([d["diagram"]["edges"][0]] * 160),
            "OCR_DIAGRAM_INVALID",
        ),
        (
            lambda d: d["diagram"]["edges"][0].update(path=[{"x": 1, "y": 2}] * 33),
            "OCR_DIAGRAM_INVALID",
        ),
    ],
)
def test_lightweight_provider_does_not_relax_runtime_acceptance(sdk_http, change, reason):
    state, requests = sdk_http
    payload = {"lines": ["TR-01"], "uncertain": [], "contains_diagram": True, "diagram": diagram()}
    change(payload)
    state["response"]["candidates"][0]["content"]["parts"][0]["text"] = json.dumps(payload)
    settings = AISettings(gemini_api_key="fixture-explicit-key", gemini_transient_retries=0)
    plan = image_inputs(docx(IMAGE), extract(docx(IMAGE)))
    result = transcribe(plan, gemini_image_provider(settings))
    assert len(requests) == 1
    assert any(issue.reason == reason for issue in result.skipped)
    if reason == "OCR_RESPONSE_INVALID":
        assert result.items == []
    else:
        assert result.items[0].lines == ["TR-01"] and result.items[0].diagram is None
