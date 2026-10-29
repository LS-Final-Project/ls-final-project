"""KAN-144 Gemini 공급자 어댑터. 실제 SDK 요청 경로를 쓰되 가짜 HTTP로 네트워크 없이 검사한다."""

import json

import google.auth
import httpx
import pytest
from google.oauth2.credentials import Credentials

from backend.engine.cs import prompt_registry
from backend.engine.cs.classification import ClassificationInput
from backend.engine.cs.classification_call import ClassificationCallError
from backend.engine.cs.drafting import DraftOutput
from backend.engine.cs.prompt_registry import get_prompt
from backend.engine.cs.providers import gemini_classification_provider, gemini_text_provider
from backend.shared.ai import AISettings, create_client
from eval.cs_candidates import Candidate, run_candidates

KEY = "fixture-key-must-not-leak"
ANSWER = (
    '{"category_code":"RETURN","secondary_category":null,"urgency_flag":"NONE",'
    '"provided_info":[],"return_subtype":null}'
)
CODES = frozenset(
    {"TEST_SERIAL", "TEST_PHOTO", "TEST_MODEL", "TEST_FW", "TEST_INVOICE", "TEST_SITE"}
)
PAYLOAD = ClassificationInput(
    channel="EMAIL", body_language="en", subject="Return", body="Please take the breaker back."
)


@pytest.fixture
def sdk_http(monkeypatch):
    """SDK의 httpx 전송만 가짜로 바꾼다.

    Args:
        monkeypatch: httpx.Client.send 교체.

    Returns:
        tuple[dict, list]: 응답 상태(바꿀 수 있음)와 보낸 요청 목록.
    """
    requests = []
    state = {
        "status": 200,
        "error": None,
        "response": {
            "candidates": [
                {"content": {"role": "model", "parts": [{"text": ANSWER}]}, "finishReason": "STOP"}
            ],
            "modelVersion": "fixture-version",
            "usageMetadata": {
                "promptTokenCount": 7,
                "candidatesTokenCount": 4,
                "totalTokenCount": 11,
            },
        },
    }
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(
        google.auth, "default", lambda **_: (Credentials(token="fixture-token"), "fixture-project")
    )

    def send(_client, request, **kwargs):
        requests.append(request)
        if state["error"] is not None:
            raise state["error"]
        return httpx.Response(state["status"], json=state["response"], request=request)

    monkeypatch.setattr(httpx.Client, "send", send)
    return state, requests


@pytest.fixture
def client(sdk_http):
    settings = AISettings(gemini_api_key=KEY)
    with create_client(settings) as sdk:
        yield settings, sdk


def sent_text(request) -> str:
    return json.loads(request.content)["contents"][0]["parts"][0]["text"]


def test_classification_prompt_has_version_schema_codes_and_input(sdk_http, client):
    """분류 어댑터가 판본 본문·스키마·허용 코드·입력을 보내고 사용량·모델을 보존한다."""
    _, requests = sdk_http
    settings, sdk = client
    provider = gemini_classification_provider(
        sdk, settings, model="gemini-test-a", allowed_info_codes=CODES
    )
    generation = provider(PAYLOAD)
    [request] = requests
    text = sent_text(request)
    assert text.startswith(get_prompt("classification", "v1"))
    assert '"category_code"' in text
    assert "허용 정보 코드: " + json.dumps(sorted(CODES)) in text
    assert text.endswith("입력:\n" + PAYLOAD.model_dump_json())
    assert "gemini-test-a" in request.url.path
    config = json.loads(request.content)["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert (generation.text, generation.model, generation.model_version) == (
        ANSWER,
        "gemini-test-a",
        "fixture-version",
    )
    assert (generation.usage.input_tokens, generation.usage.total_tokens) == (7, 11)


def test_text_provider_sends_the_prompt_and_optional_schema(sdk_http, client):
    """텍스트 어댑터는 엔진 프롬프트를 그대로 보내고 스키마가 있으면 JSON 응답을 요청한다."""
    _, requests = sdk_http
    settings, sdk = client
    plain = gemini_text_provider(sdk, settings, model="gemini-test-b")
    typed = gemini_text_provider(sdk, settings, model="gemini-test-b", response_schema=DraftOutput)
    assert plain("초안 프롬프트").usage.output_tokens == 4
    typed("초안 프롬프트")
    assert [sent_text(r) for r in requests] == ["초안 프롬프트", "초안 프롬프트"]
    configs = [json.loads(r.content).get("generationConfig", {}) for r in requests]
    assert "responseMimeType" not in configs[0]
    assert configs[1]["responseMimeType"] == "application/json"


@pytest.mark.parametrize(
    ("status", "error", "code", "http_status"),
    [
        (429, None, "CS_RATE_LIMITED", 429),
        (503, None, "CS_PROVIDER_ERROR", 503),
        (400, None, "CS_PROVIDER_ERROR", 400),
        (200, httpx.ReadTimeout("fixture-timeout " + KEY), "CS_PROVIDER_TIMEOUT", None),
        (200, httpx.ConnectError("fixture-connect " + KEY), "CS_PROVIDER_ERROR", None),
    ],
)
def test_failures_map_to_codes_without_messages(sdk_http, client, status, error, code, http_status):
    """429·5xx·4xx·타임아웃·연결 실패를 코드로만 올리고 키·제공자 원문을 버린다."""
    state, requests = sdk_http
    state.update(
        status=status,
        error=error,
        response={"error": {"code": status, "message": "fixture-provider-text " + KEY}},
    )
    settings, sdk = client
    for provider, argument in (
        (
            gemini_classification_provider(sdk, settings, model="m", allowed_info_codes=CODES),
            PAYLOAD,
        ),
        (gemini_text_provider(sdk, settings, model="m"), "prompt"),
    ):
        with pytest.raises(ClassificationCallError) as raised:
            provider(argument)
        assert (raised.value.code, raised.value.http_status) == (code, http_status)
        assert raised.value.__context__ is None and raised.value.__cause__ is None
        assert KEY not in repr(raised.value) and "fixture-provider-text" not in str(raised.value)
    assert len(requests) == 2


def test_unknown_version_and_bad_settings_fail_before_any_call(sdk_http, client):
    """모르는 판본·잘못된 모델·코드는 어댑터를 만들 때 거부하고 요청을 보내지 않는다."""
    _, requests = sdk_http
    settings, sdk = client
    with pytest.raises(ValueError, match="^CS_UNKNOWN_PROMPT_VERSION$"):
        gemini_classification_provider(
            sdk, settings, model="m", allowed_info_codes=CODES, prompt_version="v9"
        )
    with pytest.raises(ValueError, match="^CS_INVALID_MODEL$"):
        gemini_text_provider(sdk, settings, model="bad model")
    with pytest.raises(ValueError, match="^CS_INVALID_INPUT$"):
        gemini_classification_provider(sdk, settings, model="m", allowed_info_codes={"X"})
    assert requests == []


def test_adapters_hold_no_api_key(client):
    """어댑터 클로저가 쥔 설정에는 API 키가 없다(키는 client 안에만 있다)."""
    settings, sdk = client
    provider = gemini_text_provider(sdk, settings, model="m")
    held = [cell.cell_contents for cell in provider.__closure__ or ()]
    configured = next(value for value in held if isinstance(value, AISettings))
    assert configured.gemini_api_key is None and configured.gemini_model == "m"
    assert KEY not in repr(held)


def test_two_adapters_plug_into_candidate_comparison(sdk_http, client, monkeypatch):
    """v1과 검사용 판본 어댑터 두 개를 후보 비교 하네스에 꽂아 한 표를 만든다(네트워크 없음)."""
    _, requests = sdk_http
    settings, sdk = client
    monkeypatch.setitem(prompt_registry.PROMPTS["classification"], "test-v2", "검사용 분류 본문.\n")
    candidates = [
        Candidate(
            name=f"gemini-{version}",
            provider=gemini_classification_provider(
                sdk,
                settings,
                model="gemini-test-a",
                allowed_info_codes=CODES,
                prompt_version=version,
            ),
            model="gemini-test-a",
            prompt_version=version,
        )
        for version in ("v1", "test-v2")
    ]
    records = {
        f"syn-{i}": {
            "channel": "EMAIL",
            "body_language": "en",
            "subject": None,
            "body": f"Return {i}.",
        }
        for i in range(2)
    }
    gold = {
        "category": dict.fromkeys(records, "RETURN"),
        "urgency": dict.fromkeys(records, "NONE"),
    }
    result = run_candidates(
        records,
        gold=gold,
        candidates=candidates,
        sensitive_values={},
        allowed_info_codes=CODES,
    )
    assert result["targets"] == 2 and sorted(result["ranking"]) == ["gemini-test-v2", "gemini-v1"]
    rows = {row["name"]: row for row in result["candidates"]}
    assert all(row["category"]["accuracy"] == 1.0 for row in rows.values())
    assert rows["gemini-test-v2"]["prompt_version"] == "test-v2"
    assert len(requests) == 4
    assert sum(sent_text(r).startswith("검사용 분류 본문.\n") for r in requests) == 2
