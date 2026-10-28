"""Exercise the real SDK request and response path without credentials or network calls."""

import json
from datetime import UTC, date
from decimal import Decimal

import google.auth
import httpx
import pytest
from google.oauth2.credentials import Credentials

from backend.shared.ai import (
    PRICES,
    AISettings,
    ModelPrice,
    Usage,
    create_client,
    embed,
    estimate_cost,
    generate,
    load_price_table,
)
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
    settings = AISettings(ai_provider="vertex", google_cloud_project="fixture-project")
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
    assert result.usage.raw == {
        "promptTokenCount": 7,
        "candidatesTokenCount": 4,
        "thoughtsTokenCount": 11,
        "cachedContentTokenCount": 2,
        "totalTokenCount": 22,
    }
    assert result.usage.billable_input_tokens == 5
    assert result.model == settings.gemini_model
    assert result.attempts == 1
    assert result.elapsed_ms is not None and result.elapsed_ms >= 0
    assert result.finished_at is not None and result.finished_at.tzinfo is UTC
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
    provider = GenAIProvider(
        AISettings(ai_provider="vertex", google_cloud_project="fixture-project")
    )
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
    provider = GenAIProvider(
        AISettings(ai_provider="vertex", google_cloud_project="fixture-project")
    )
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
        ai_provider="vertex",
        google_cloud_project="fixture-project",
        google_cloud_embedding_location="us-central1",
    )
    with create_client(settings, embedding=True) as client:
        if valid:
            result = embed(client, "gemini-embedding-001", "synthetic", dimensions=2)
            assert result.values == values
            assert result.input_tokens == 3
            assert result.billable_characters == 8
            assert (result.model, result.attempts) == ("gemini-embedding-001", 1)
            assert result.elapsed_ms is not None and result.finished_at is not None
        else:
            with pytest.raises(ValueError):
                embed(client, "gemini-embedding-001", "synthetic", dimensions=2)
    assert requests[0].url.host == "us-central1-aiplatform.googleapis.com"
    assert requests[0].url.path.endswith("gemini-embedding-001:predict")


def test_default_provider_uses_gemini_key_without_adc(sdk_http):
    _, requests = sdk_http
    settings = AISettings(gemini_api_key="fixture-explicit-key")
    with create_client(settings) as client:
        generate(client, settings, "synthetic")
    assert requests[0].url.host == "generativelanguage.googleapis.com"
    assert requests[0].headers["x-goog-api-key"] == "fixture-explicit-key"
    assert "authorization" not in requests[0].headers


def test_fake_startup_needs_no_key_but_real_client_fails_before_network(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    settings = AISettings()
    assert settings.ai_provider == "gemini"
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        create_client(settings)


def test_gemini_embedding_uses_supported_config_and_checks_dimensions(sdk_http):
    state, requests = sdk_http
    state["response"] = {"embeddings": [{"values": [0.1, 0.2]}]}
    settings = AISettings(gemini_api_key="fixture-explicit-key")
    with create_client(settings, embedding=True) as client:
        result = embed(
            client,
            "gemini-embedding-001",
            "synthetic",
            dimensions=2,
            task_type="RETRIEVAL_DOCUMENT",
        )
        assert result.values == [0.1, 0.2]
        with pytest.raises(ValueError, match="dimension"):
            embed(client, "gemini-embedding-001", "synthetic", dimensions=3)
    assert all(r.url.host == "generativelanguage.googleapis.com" for r in requests)
    body = json.loads(requests[0].content)
    assert body["requests"][0]["outputDimensionality"] == 2
    assert body["requests"][0]["taskType"] == "RETRIEVAL_DOCUMENT"
    assert "autoTruncate" not in str(body)


def test_missing_usage_fields_stay_none_and_cache_omission_is_zero(sdk_http):
    state, _ = sdk_http
    # Gemini omits cachedContentTokenCount and thoughtsTokenCount when they are not used.
    state["response"]["usageMetadata"] = {"promptTokenCount": 9, "candidatesTokenCount": 2}
    settings = AISettings(gemini_api_key="fixture-explicit-key")
    with create_client(settings) as client:
        usage = generate(client, settings, "synthetic").usage
    assert usage.raw == {"promptTokenCount": 9, "candidatesTokenCount": 2}
    assert (usage.cached_input_tokens, usage.thought_tokens, usage.total_tokens) == (None,) * 3
    assert usage.billable_input_tokens == 9
    state["response"].pop("usageMetadata")
    with create_client(settings) as client:
        usage = generate(client, settings, "synthetic").usage
    assert usage == Usage() and usage.billable_input_tokens is None and usage.raw is None


# 테스트 전용 임의 단가다. 실제 공급자 단가가 아니다.
FAKE_PRICES = {
    "fixture-model": ModelPrice(
        input_per_million=Decimal("2"),
        output_per_million=Decimal("10"),
        cached_input_per_million=Decimal("0.5"),
        currency="USD",
        as_of=date(2026, 1, 1),
    )
}


def test_cost_counts_each_token_once_and_keeps_thought_separate():
    cost = estimate_cost(
        Usage(1_000_000, 200_000, 300_000, 400_000, 1_900_000), "fixture-model", FAKE_PRICES
    )
    # 청구 입력 600,000 × 2, 캐시 400,000 × 0.5, 답변 200,000 × 10, 추론 300,000 × 10.
    assert (cost.input, cost.cached_input, cost.output, cost.thought) == (
        Decimal("1.2"),
        Decimal("0.2"),
        Decimal("2"),
        Decimal("3"),
    )
    assert cost.total == Decimal("6.4")
    assert (cost.currency, cost.price_as_of) == ("USD", date(2026, 1, 1))


def test_cost_propagates_none():
    partial = estimate_cost(Usage(input_tokens=1_000_000), "fixture-model", FAKE_PRICES)
    assert (partial.input, partial.output, partial.cached_input, partial.thought) == (
        Decimal("2"),
        None,
        None,
        None,
    )
    assert partial.total == Decimal("2")
    assert estimate_cost(Usage(), "fixture-model", FAKE_PRICES).total is None
    unknown = estimate_cost(Usage(10, 2, 1, 0, 13), "other-model", FAKE_PRICES)
    assert all(value is None for value in vars(unknown).values())


def test_shipped_price_table_loads_and_empty_table_gives_none():
    # 저장소 단가표는 형식만 검증한다. 담당자가 공식 단가를 채워도 깨지지 않는다.
    load_price_table(PRICES)
    assert estimate_cost(Usage(10, 2, 1, 0, 13), "fixture-model", {}).total is None


def test_cache_larger_than_input_gives_no_billable_input():
    usage = Usage(input_tokens=3, cached_input_tokens=5)
    assert usage.billable_input_tokens is None
    cost = estimate_cost(usage, "fixture-model", FAKE_PRICES)
    assert cost.input is None and cost.total == Decimal("0.0000025")
