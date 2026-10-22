"""Exercise the real SDK request and response path without credentials or network calls."""

import json

import google.auth
import httpx
import pytest
from google.oauth2.credentials import Credentials

from backend.shared.ai import AISettings, create_client, embed, generate
from eval.smoke import GenAIProvider, Prediction, evaluate, save


@pytest.fixture
def sdk_http(monkeypatch):
    requests = []
    response = {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": '{"result":"ready"}'}]},
                "finishReason": "STOP",
            }
        ],
        "modelVersion": "fixture-version",
        "usageMetadata": {
            "promptTokenCount": 7,
            "candidatesTokenCount": 4,
            "thoughtsTokenCount": 11,
            "cachedContentTokenCount": 2,
            "totalTokenCount": 22,
        },
    }
    state = {"status": 200, "response": response}
    monkeypatch.setenv("GEMINI_API_KEY", "fixture-key-must-not-be-used")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(
        google.auth,
        "default",
        lambda **_: (Credentials(token="fixture-access-token"), "fixture-project"),
    )

    def send(_client, request, **kwargs):
        requests.append(request)
        return httpx.Response(state["status"], json=state["response"], request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    return state, requests


def test_vertex_uses_oauth_and_preserves_schema_and_all_token_categories(sdk_http):
    _, requests = sdk_http
    settings = AISettings(google_cloud_project="fixture-project")
    with create_client(settings) as client:
        result = generate(client, settings, "synthetic", response_schema=Prediction)
    request = requests[0]
    assert request.url.host == "aiplatform.googleapis.com"
    assert "/locations/global/" in request.url.path
    assert request.headers["authorization"] == "Bearer fixture-access-token"
    assert "x-goog-api-key" not in request.headers
    assert "fixture-key-must-not-be-used" not in str(request.url)
    body = json.loads(request.content)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert "result" in body["generationConfig"]["responseSchema"]["properties"]
    assert result.usage.input_tokens == 7
    assert result.usage.output_tokens == 4
    assert result.usage.thought_tokens == 11
    assert result.usage.cached_input_tokens == 2
    assert result.usage.total_tokens == 22
    assert result.model_version == "fixture-version"
    assert result.finish_reasons == ["STOP"]
    assert "fixture-key-must-not-be-used" not in repr(settings)


def test_vertex_failure_does_not_retry_or_fall_back_to_key_and_report_redacts_error(sdk_http):
    state, requests = sdk_http
    state.update(
        status=429,
        response={
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "message": "fixture-sensitive-provider-error",
            }
        },
    )
    provider = GenAIProvider(AISettings(google_cloud_project="fixture-project"))
    try:
        report = evaluate(provider, "vertex", "fixture")
    finally:
        provider.client.close()
    assert len(requests) == 2  # One request per synthetic case, no retry or fallback.
    assert all(r.url.host == "aiplatform.googleapis.com" for r in requests)
    assert all("x-goog-api-key" not in r.headers for r in requests)
    assert all(row["http_status"] == 429 for row in report["cases"])
    assert "fixture-sensitive-provider-error" not in str(report)
    assert "fixture-key-must-not-be-used" not in str(report)


def test_empty_answer_retains_usage_and_finish_reason_in_failed_schema_report(sdk_http, tmp_path):
    state, _ = sdk_http
    state["response"]["candidates"][0].update(
        content={"role": "model", "parts": []}, finishReason="MAX_TOKENS"
    )
    provider = GenAIProvider(AISettings(google_cloud_project="fixture-project"))
    try:
        report = evaluate(provider, "vertex", "fixture", {"location": "global"})
    finally:
        provider.client.close()
    assert not report["cases"][0]["schema_valid"]
    assert report["cases"][0]["usage"]["thought_tokens"] == 11
    assert report["cases"][0]["finish_reasons"] == ["MAX_TOKENS"]
    path = save(report, tmp_path)
    assert json.loads(path.read_text())["configuration"]["location"] == "global"
    page = path.with_suffix(".html").read_text()
    assert "global" in page and "총 토큰" in page and "캐시 입력 토큰" in page


@pytest.mark.parametrize(
    "values,truncated,valid",
    [([0.1, 0.2], False, True), ([0.1], False, False), ([0.1, 0.2], True, False)],
)
def test_embedding_location_and_dimension_or_truncation_contract(
    sdk_http, values, truncated, valid
):
    state, requests = sdk_http
    state["response"] = {
        "predictions": [
            {
                "embeddings": {
                    "values": values,
                    "statistics": {"token_count": 3, "truncated": truncated},
                }
            }
        ],
        "metadata": {"billableCharacterCount": 8},
    }
    settings = AISettings(
        google_cloud_project="fixture-project", google_cloud_embedding_location="us-central1"
    )
    with create_client(settings, embedding=True) as client:
        if valid:
            result = embed(client, "gemini-embedding-001", "synthetic", dimensions=2)
            assert result.values == values
            assert result.input_tokens == 3
            assert result.billable_characters == 8
        else:
            with pytest.raises(ValueError):
                embed(client, "gemini-embedding-001", "synthetic", dimensions=2)
    assert requests[0].url.host == "us-central1-aiplatform.googleapis.com"
    assert requests[0].url.path.endswith("gemini-embedding-001:predict")


def test_developer_api_is_only_used_when_explicitly_selected(sdk_http):
    _, requests = sdk_http
    settings = AISettings(ai_provider="gemini", gemini_api_key="fixture-explicit-key")
    with create_client(settings) as client:
        generate(client, settings, "synthetic")
    assert requests[0].url.host == "generativelanguage.googleapis.com"
    assert requests[0].headers["x-goog-api-key"] == "fixture-explicit-key"
    assert "authorization" not in requests[0].headers
