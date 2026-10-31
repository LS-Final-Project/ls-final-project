"""처리 함수 추출 모델 설정과 lease 길이 검사(KAN-199). 모델·DB를 부르지 않는다."""

import pytest

from backend.api.tft import worker_processor
from backend.api.tft.worker_processor import ProcessorSettings, extraction_settings
from backend.engine.rfp.requirements import ResponseSchema
from backend.shared import ai
from backend.shared.settings import Settings


def test_extraction_defaults_reach_generate(monkeypatch):
    """추출 호출은 공통 기본(출력 1024)이 아니라 평가와 같은 모델·출력 상한, 120초 제한으로 간다."""
    seen = {}
    monkeypatch.setattr(ai, "create_client", lambda settings: "client")

    def generate(client, settings, text, *, response_schema):
        seen.update(client=client, settings=settings, schema=response_schema)
        return "generation"

    monkeypatch.setattr(ai, "generate", generate)
    provider = worker_processor.gemini_provider(
        extraction_settings(ProcessorSettings(_env_file=None))
    )
    assert provider("prompt") == "generation"
    settings = seen["settings"]
    assert (
        settings.gemini_model,
        settings.gemini_max_output_tokens,
        settings.gemini_timeout_ms,
    ) == (
        "gemini-3.6-flash",
        16384,
        120000,
    )
    assert seen["client"] == "client" and seen["schema"] is ResponseSchema


@pytest.mark.parametrize(("lease", "refused"), [("200", True), ("300", False)])
def test_a_lease_shorter_than_one_model_call_refuses_to_start(monkeypatch, lease, refused):
    """lease가 호출 하나의 최악(120초×2+1초=241초)보다 짧으면 모델 클라이언트를 만들기 전에 시작을 거부한다."""
    for name, value in {
        "TFT_EXTRACTION_PROVIDER": "gemini",
        "TFT_EXTRACTION_TIMEOUT_MS": "120000",
        "GEMINI_TRANSIENT_RETRIES": "1",
        "TFT_WORKER_LEASE_SECONDS": lease,
    }.items():
        monkeypatch.setenv(name, value)
    created = []
    monkeypatch.setattr(ai, "create_client", lambda settings: created.append(settings))
    settings = Settings(_env_file=None, environment="test")
    if refused:
        with pytest.raises(ValueError, match="INVALID_WORKER_CONFIGURATION"):
            worker_processor.build(settings)
        assert created == []
    else:
        worker_processor.build(settings)
        assert len(created) == 1


def test_rewrite_provider_is_lazy_uses_array_schema_and_disables_transport_retries(monkeypatch):
    from backend.engine.rfp.draft_rewrite import RewriteOutput

    created, seen = [], {}
    monkeypatch.setattr(ai, "create_client", lambda settings: created.append(settings) or "client")

    def generate(client, settings, text, *, response_schema):
        seen.update(schema=response_schema, settings=settings)
        return "generation"

    monkeypatch.setattr(ai, "generate", generate)
    provider = worker_processor.rewrite_provider(
        ProcessorSettings(_env_file=None, draft_rewrite_provider="gemini")
    )
    assert created == []
    assert provider("fake prompt") == "generation"
    assert seen["schema"] is RewriteOutput
    assert (
        seen["settings"].gemini_max_output_tokens,
        seen["settings"].gemini_timeout_ms,
        seen["settings"].gemini_transient_retries,
    ) == (8192, 120000, 0)
    assert provider("another fake prompt") == "generation" and len(created) == 1


def test_rewrite_invalid_lease_fails_at_start_before_client_or_model(monkeypatch):
    monkeypatch.setenv("TFT_WORKER_LEASE_SECONDS", "60")
    monkeypatch.setattr(ai, "create_client", lambda _: pytest.fail("client must stay lazy"))
    monkeypatch.setattr(ai, "generate", lambda *a, **k: pytest.fail("model must not run"))
    with pytest.raises(ValueError, match="INVALID_WORKER_CONFIGURATION"):
        worker_processor.rewrite_provider(
            ProcessorSettings(
                _env_file=None, draft_rewrite_provider="gemini", draft_rewrite_timeout_ms=120000
            )
        )


def test_build_rejects_rewrite_lease_before_real_client_creation(monkeypatch):
    monkeypatch.setenv("TFT_EXTRACTION_PROVIDER", "fake")
    monkeypatch.setenv("TFT_DRAFT_REWRITE_PROVIDER", "gemini")
    monkeypatch.setenv("TFT_DRAFT_REWRITE_TIMEOUT_MS", "120000")
    monkeypatch.setenv("TFT_WORKER_LEASE_SECONDS", "60")
    monkeypatch.setattr(ai, "create_client", lambda _: pytest.fail("must not create client"))
    with pytest.raises(ValueError, match="INVALID_WORKER_CONFIGURATION"):
        worker_processor.build(Settings(_env_file=None, environment="test"))


def test_fake_rewrite_configuration_does_not_need_real_ai_settings(monkeypatch):
    monkeypatch.setattr(
        worker_processor, "AISettings", lambda **_: pytest.fail("fake needs no AI settings")
    )
    provider = worker_processor.rewrite_provider(
        ProcessorSettings(_env_file=None, draft_rewrite_provider="fake")
    )
    assert callable(provider)
