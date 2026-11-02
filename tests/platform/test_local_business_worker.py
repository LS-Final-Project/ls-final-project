"""Offline SDK/permit/lease fakes only: no keys, provider clients or DB connections."""

import json
import os
import sys
from contextlib import nullcontext
from datetime import date
from decimal import Decimal
from types import ModuleType, SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest
from google.genai import types
from pydantic import SecretStr

from backend.agent import job_runtime
from backend.shared import ai
from scripts.local_business import worker

MODEL = "offline-model"
JOB = UUID("12345678-1234-4234-8234-123456789abc")
PRICES = {
    MODEL: ai.ModelPrice(
        input_per_million=Decimal("1"),
        output_per_million=Decimal("2"),
        cached_input_per_million=Decimal("0.5"),
        currency="USD",
        as_of=date(2026, 11, 2),
    )
}


class FakePermit:
    def __init__(self, *, denied=False, reason=None, settlement_error=False):
        self.reservations, self.settlements = [], []
        self.denied, self.reason, self.settlement_error = denied, reason, settlement_error

    def reserve(self, token, job_id, model):
        if self.denied:
            raise RuntimeError("private permission detail")
        self.reservations.append((token, job_id, model))
        return "offline-charge"

    def settle(self, charge, *, actual_usd, usage=None):
        if self.settlement_error:
            raise RuntimeError("private journal detail")
        self.settlements.append((charge, actual_usd, usage))
        return self.reason


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("Real DB and SDK client creation are forbidden in these tests")

    monkeypatch.setattr(psycopg, "connect", forbidden)

    class ForbiddenClient:
        def __init__(self, *args, **kwargs):
            forbidden()

    monkeypatch.setattr(ai.genai, "Client", ForbiddenClient)


def configuration(tmp_path):
    return worker.Configuration(
        "tft", JOB, "offline-token", tmp_path, tmp_path / "key", tmp_path / "prices.json"
    )


def response(**changes):
    usage = dict(
        prompt_token_count=100,
        candidates_token_count=20,
        thoughts_token_count=10,
        cached_content_token_count=5,
        total_token_count=130,
    )
    usage.update(changes)
    return types.GenerateContentResponse(
        usage_metadata=types.GenerateContentResponseUsageMetadata(**usage),
        candidates=[types.Candidate(content=types.Content(parts=[types.Part(text="offline")]))],
        model_version=MODEL,
    )


def client_for(meter, result=None, error=None):
    calls = []

    def generate(**kwargs):
        calls.append(kwargs)
        if error:
            raise error
        return result

    return worker.Client(
        SimpleNamespace(models=SimpleNamespace(generate_content=generate)), meter
    ), calls


def test_known_usage_settles_full_cost_and_preserves_sdk_response(tmp_path):
    permit = FakePermit()
    meter = worker.Meter(permit, configuration(tmp_path), PRICES)
    sdk_response = response()
    client, calls = client_for(meter, sdk_response)
    assert client.models.generate_content(model=MODEL, contents="offline") is sdk_response
    assert len(calls) == 1
    assert permit.reservations == [("offline-token", str(JOB), MODEL)]
    assert permit.settlements == [
        (
            "offline-charge",
            "0.0001575",
            {
                "input_tokens": 100,
                "output_tokens": 20,
                "thought_tokens": 10,
                "cached_input_tokens": 5,
                "total_tokens": 130,
            },
        )
    ]


def test_shared_generate_works_with_proxy_and_missing_cache_means_zero(tmp_path):
    permit = FakePermit()
    meter = worker.Meter(permit, configuration(tmp_path), PRICES)
    client, _ = client_for(meter, response(cached_content_token_count=None))
    result = ai.generate(
        client,
        ai.AISettings(_env_file=None, gemini_model=MODEL, gemini_transient_retries=0),
        "offline",
    )
    assert result.text == "offline"
    assert permit.settlements[0][1] == "0.00016"
    assert permit.settlements[0][2]["cached_input_tokens"] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("prompt_token_count", None),
        ("candidates_token_count", None),
        ("thoughts_token_count", None),
        ("total_token_count", None),
        ("prompt_token_count", -1),
        ("cached_content_token_count", 101),
        ("total_token_count", 129),
        ("total_token_count", 131),
    ],
)
def test_partial_or_invalid_usage_never_settles_partial_estimate(tmp_path, field, value):
    permit = FakePermit()
    meter = worker.Meter(permit, configuration(tmp_path), PRICES)
    client, calls = client_for(meter, response(**{field: value}))
    with pytest.raises(worker.LocalStop, match="LOCAL_COST_UNKNOWN"):
        client.models.generate_content(model=MODEL)
    assert permit.settlements == [("offline-charge", None, None)]
    with pytest.raises(worker.LocalStop):
        client.models.generate_content(model=MODEL)
    assert len(calls) == 1


def test_absent_or_boolean_usage_is_unknown(tmp_path):
    for raw in [
        None,
        SimpleNamespace(
            prompt_token_count=True,
            candidates_token_count=0,
            thoughts_token_count=0,
            total_token_count=1,
        ),
    ]:
        permit = FakePermit()
        client, _ = client_for(
            worker.Meter(permit, configuration(tmp_path), PRICES),
            SimpleNamespace(usage_metadata=raw),
        )
        with pytest.raises(worker.LocalStop, match="LOCAL_COST_UNKNOWN"):
            client.models.generate_content(model=MODEL)
        assert permit.settlements[0][1] is None


@pytest.mark.parametrize(
    "error",
    [TimeoutError("secret provider body"), ValueError("secret provider body"), KeyboardInterrupt()],
)
def test_provider_failure_keeps_reservation_and_blocks_fallback(tmp_path, error, capsys):
    permit = FakePermit()
    client, calls = client_for(worker.Meter(permit, configuration(tmp_path), PRICES), error=error)
    fallback = []
    with pytest.raises(worker.LocalStop, match="LOCAL_PROVIDER_CALL_FAILED"):
        try:
            client.models.generate_content(model=MODEL, contents="secret prompt")
        except Exception:
            fallback.append("success")
    assert not fallback
    assert permit.settlements == [("offline-charge", None, None)]
    with pytest.raises(worker.LocalStop):
        client.models.generate_content(model=MODEL)
    assert len(calls) == 1
    assert "secret" not in str(capsys.readouterr())


def test_unpriced_or_non_usd_model_is_denied_before_reservation_or_sdk(tmp_path):
    for prices in [{}, {MODEL: PRICES[MODEL].model_copy(update={"currency": "KRW"})}]:
        permit = FakePermit()
        client, calls = client_for(
            worker.Meter(permit, configuration(tmp_path), prices), response()
        )
        with pytest.raises(worker.LocalStop, match="LOCAL_MODEL_PRICE_REQUIRED"):
            client.models.generate_content(model=MODEL)
        assert not calls and not permit.reservations


def test_partial_cost_total_is_not_accepted_as_known(tmp_path, monkeypatch):
    permit = FakePermit()
    client, _ = client_for(worker.Meter(permit, configuration(tmp_path), PRICES), response())
    monkeypatch.setattr(
        ai,
        "estimate_cost",
        lambda *args: ai.Cost(input=Decimal("0.001"), total=Decimal("0.001"), currency="USD"),
    )
    with pytest.raises(worker.LocalStop, match="LOCAL_COST_UNKNOWN"):
        client.models.generate_content(model=MODEL)
    assert permit.settlements == [("offline-charge", None, None)]


@pytest.mark.parametrize("product", ["cs", "tft"])
def test_local_denial_escapes_actual_product_exception_fallback(tmp_path, product):
    permit = FakePermit(denied=True)
    client, calls = client_for(worker.Meter(permit, configuration(tmp_path), PRICES), response())

    def provider(item):
        return ai.generate(
            client,
            ai.AISettings(_env_file=None, gemini_model=MODEL, gemini_transient_retries=0),
            "offline",
        )

    with pytest.raises(worker.LocalStop, match="LOCAL_RESERVATION_DENIED"):
        if product == "cs":
            from backend.engine.cs.classification_call import run_classification_attempt

            run_classification_attempt(
                {
                    "channel": "EMAIL",
                    "body_language": "en",
                    "subject": "offline",
                    "body": "An offline test inquiry.",
                },
                sensitive_values={},
                allowed_info_codes=frozenset(),
                provider=provider,
                attempt_number=1,
            )
        else:
            from backend.engine.rfp.requirements import (
                ClauseInput,
                ExtractionInput,
                extract_section,
            )

            extract_section(
                ExtractionInput(
                    section_no="2.1",
                    section_title="Offline",
                    clauses=[
                        ClauseInput(
                            clause_no="2.1.A", path="Offline", text="The enclosure shall be coated."
                        )
                    ],
                ),
                provider,
            )
    assert not calls and not permit.settlements


@pytest.mark.parametrize(
    "permit,code",
    [
        (FakePermit(denied=True), "LOCAL_RESERVATION_DENIED"),
        (FakePermit(reason="LOCAL_RESERVATION_EXCEEDED"), "LOCAL_RESERVATION_EXCEEDED"),
        (FakePermit(reason="LOCAL_COST_UNKNOWN"), "LOCAL_COST_UNKNOWN"),
        (FakePermit(settlement_error=True), "LOCAL_SETTLEMENT_FAILED"),
    ],
)
def test_admission_and_settlement_failures_are_fatal(tmp_path, permit, code):
    client, calls = client_for(worker.Meter(permit, configuration(tmp_path), PRICES), response())
    with pytest.raises(worker.LocalStop, match=code):
        client.models.generate_content(model=MODEL)
    with pytest.raises(worker.LocalStop, match=code):
        client.models.generate_content(model=MODEL)
    assert len(calls) == (0 if permit.denied else 1)


@pytest.mark.parametrize(
    "access",
    [
        lambda c: c.aio,
        lambda c: c.files,
        lambda c: c.models.embed_content,
        lambda c: c.models.generate_content_stream,
    ],
)
def test_other_sdk_paths_cannot_bypass_meter(tmp_path, access):
    permit = FakePermit()
    client, calls = client_for(worker.Meter(permit, configuration(tmp_path), PRICES), response())
    with pytest.raises(worker.LocalStop, match="LOCAL_SDK_OPERATION_BLOCKED"):
        access(client)
    assert not calls and not permit.reservations


def test_factory_injects_key_only_in_settings_and_restores_patches(tmp_path, monkeypatch):
    made = []

    def factory(settings):
        made.append(settings)
        return SimpleNamespace()

    monkeypatch.setattr(ai, "create_client", factory)
    original_claim = job_runtime.claim
    meter = worker.Meter(FakePermit(), configuration(tmp_path), PRICES)
    settings = ai.AISettings(_env_file=None, gemini_model=MODEL, gemini_transient_retries=0)
    with worker.intercept(meter, SecretStr("offline-key"), job_runtime, []):
        assert isinstance(ai.create_client(settings), worker.Client)
        assert made[0].gemini_api_key.get_secret_value() == "offline-key"
        assert settings.gemini_api_key is None
        with pytest.raises(worker.LocalStop, match="LOCAL_SDK_OPERATION_BLOCKED"):
            ai.create_client(settings, embedding=True)
    assert ai.create_client is factory and job_runtime.claim is original_claim


@pytest.mark.parametrize(
    "owner,outcome",
    [
        ("tft", "known"),
        ("tft", "denied"),
        ("tft", "unknown"),
        ("tft", "lease_lost"),
        ("cs", "denied"),
        ("cs", "unknown"),
        ("cs", "lease_lost"),
    ],
)
def test_regular_main_uses_observed_lease_and_never_success_fallback(
    tmp_path, monkeypatch, capsys, owner, outcome
):
    """Run the real runpy worker main/execute_job with offline dependency boundaries."""
    key_file, prices_file = tmp_path / "key", tmp_path / "prices.json"
    key_file.write_text("offline-key")
    prices_file.write_text(json.dumps({MODEL: PRICES[MODEL].model_dump(mode="json")}))
    env = {
        "ENVIRONMENT": "local",
        "LOCAL_BUSINESS_MODE": "real",
        "LOCAL_JOB_OWNER": owner,
        "LOCAL_JOB_ID": str(JOB),
        "LOCAL_DRIVER_TOKEN": "offline-token",
        "LOCAL_BUSINESS_CONTROL": str(tmp_path),
        "LOCAL_MODEL_KEY_FILE": str(key_file),
        "LOCAL_PRICES_FILE": str(prices_file),
        "DATABASE_URL": "postgresql://unused/unused",
        f"{owner.upper()}_JOB_ID": str(JOB),
        f"{owner.upper()}_WORKER_ENABLED": "1",
        "CS_VECTOR_SEARCH_ENABLED": "false",
        "GEMINI_TRANSIENT_RETRIES": "0",
        "GEMINI_MODEL": MODEL,
    }
    monkeypatch.setattr(os, "environ", env)
    permit = FakePermit(denied=outcome == "denied")
    permit_module = ModuleType("scripts.local_business.permit")
    paths = []
    permit_module.Permit = lambda path: paths.append(path) or permit
    monkeypatch.setitem(sys.modules, permit_module.__name__, permit_module)
    lease = job_runtime.Lease(
        JOB,
        "cs.analyze" if owner == "cs" else "rfp.process",
        uuid4(),
        "offline",
        uuid4(),
        uuid4(),
        1,
    )
    monkeypatch.setattr(job_runtime, "connect", lambda settings: nullcontext(None))
    monkeypatch.setattr(job_runtime, "check_role", lambda *args: None)
    monkeypatch.setattr(job_runtime, "claim", lambda *args: lease)
    finishes = []

    def finish(settings, got, state, payload, **kwargs):
        assert got is lease
        if outcome == "lease_lost":
            raise job_runtime.LeaseLost
        finishes.append((state, kwargs.get("failure_code")))

    monkeypatch.setattr(job_runtime, "finish", finish)
    sdk_calls = []

    def factory(settings):
        def generate(**kwargs):
            sdk_calls.append(kwargs)
            return response(
                thoughts_token_count=None if outcome in {"unknown", "lease_lost"} else 10
            )

        return SimpleNamespace(models=SimpleNamespace(generate_content=generate))

    monkeypatch.setattr(ai, "create_client", factory)
    fallbacks = []

    def generate_or_fallback(*args):
        try:
            ai.create_client(ai.AISettings()).models.generate_content(model=MODEL)
        except Exception:
            fallbacks.append("success")
        return {}

    if owner == "tft":
        from backend.agent import processor_registry

        monkeypatch.setattr(
            processor_registry, "load_processor", lambda settings: generate_or_fallback
        )
    else:
        from backend.agent import ticket_source_registry

        # Local stops during input assembly must also escape its Exception-based failure path.
        monkeypatch.setattr(ticket_source_registry, "load_parts", generate_or_fallback)
    module = f"backend.agent.{owner}_worker"
    monkeypatch.delitem(sys.modules, module, raising=False)
    if outcome == "known":
        worker.main()
        assert finishes == [("SUCCEEDED", None)]
    else:
        code = "LOCAL_RESERVATION_DENIED" if outcome == "denied" else "LOCAL_COST_UNKNOWN"
        with pytest.raises(SystemExit, match=code):
            worker.main()
        assert finishes == ([] if outcome == "lease_lost" else [("FAILED", code)])
    assert not fallbacks
    assert paths == [tmp_path / "permit.sqlite3"]
    assert len(sdk_calls) == (0 if outcome == "denied" else 1)
    assert ai.create_client is factory
    output = capsys.readouterr()
    assert "offline-key" not in output.out + output.err
    if outcome == "lease_lost":
        assert "LOCAL_FAILURE_NOT_RECORDED_LEASE_LOST" in output.err


@pytest.mark.parametrize(
    "changed",
    [
        {"ENVIRONMENT": "production"},
        {"K_SERVICE": ""},
        {"LOCAL_BUSINESS_MODE": "fake"},
        {"LOCAL_JOB_OWNER": "platform"},
        {"GEMINI_TRANSIENT_RETRIES": "1"},
        {"CS_VECTOR_SEARCH_ENABLED": "true"},
    ],
)
def test_invalid_environment_rejected_before_opening_any_file(monkeypatch, changed):
    env = {
        "ENVIRONMENT": "local",
        "LOCAL_BUSINESS_MODE": "real",
        "LOCAL_JOB_OWNER": "tft",
        "GEMINI_TRANSIENT_RETRIES": "0",
        "CS_VECTOR_SEARCH_ENABLED": "false",
        "DATABASE_URL": "unused",
        **changed,
    }
    monkeypatch.setattr(os, "environ", env)
    with pytest.raises(worker.LocalStop, match="LOCAL_WORKER_CONFIGURATION_INVALID"):
        worker.configuration()


def test_no_claim_means_no_finish_and_only_fixed_diagnostic(capsys):
    worker.fail_claimed(job_runtime, [], "LOCAL_COST_UNKNOWN")
    assert capsys.readouterr().err == "LOCAL_FAILURE_NOT_RECORDED_NO_LEASE\n"
