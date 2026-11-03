"""Offline summary bridge integration: real HTTP + permission ledger, fake model only."""

import json
import threading
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from backend.engine.cs.summary import build_summary_input, render_prompt
from backend.shared import ai
from backend.shared.errors import ApplicationError
from scripts.local_business import summary
from scripts.local_business.permit import Denied, Permit
from scripts.local_business.worker import Configuration, Meter


@pytest.fixture(autouse=True)
def local_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.delenv("K_SERVICE", raising=False)


@pytest.fixture
def ledger(tmp_path):
    permit = Permit(tmp_path / "permit.sqlite3")
    token = permit.authorize(max_jobs=2, max_calls=2, reserve_usd=".15")
    return permit, token


@pytest.fixture
def prompt():
    return render_prompt(
        build_summary_input(
            {
                "channel": "EMAIL",
                "body_language": "en",
                "body": "The inverter is noisy.",
                "subject": "Noise",
            }
        )
    )


def fake_factory(permit, token, control, model):
    job = uuid4()
    permit.grant(token, str(job))
    config = Configuration("cs", job, token, control, Path("/unused"), Path("/unused"))
    prices = {
        model: ai.ModelPrice(
            currency="USD",
            as_of=date(2026, 11, 3),
            input_per_million=Decimal(1),
            output_per_million=Decimal(1),
            cached_input_per_million=Decimal(0),
        )
    }
    meter = Meter(permit, config, prices)

    def provide(prompt):
        response = meter.generate(
            lambda **kw: SimpleNamespace(
                usage_metadata=SimpleNamespace(
                    prompt_token_count=10,
                    candidates_token_count=4,
                    thoughts_token_count=0,
                    total_token_count=14,
                )
            ),
            model=model,
        )
        assert response
        return ai.Generation(
            '{"summary_ko":"인버터 소음 문의임."}',
            ai.Usage(10, 4, 0, 0, 14),
            "fake-model-version",
            [],
            model=model,
            elapsed_ms=1,
        )

    return provide


def post(bridge, prompt, auth=None):
    return httpx.post(
        f"http://127.0.0.1:{bridge.server.server_port}/summary",
        headers={"Authorization": (auth or "Bearer " + bridge.capability).encode("latin1")},
        json={"prompt": prompt},
        trust_env=False,
    )


def test_authenticated_summary_and_analysis_share_jobs_calls_and_usage(ledger, tmp_path, prompt):
    permit, token = ledger
    # One analysis job already consumed the shared grant/call budget.
    fake_factory(permit, token, tmp_path, "fake-model")(prompt)
    bridge = summary.SummaryBridge(
        permit,
        token,
        tmp_path,
        "fake-model",
        threading.Lock(),
        address=("127.0.0.1", 0),
        factory=fake_factory,
    )
    try:
        assert post(bridge, prompt, auth="Bearer wrong").status_code == 403
        assert post(bridge, "arbitrary prompt").status_code == 400
        result = post(bridge, prompt)
        assert result.status_code == 200
        assert result.json()["usage"]["total_tokens"] == 14
        assert post(bridge, prompt).status_code == 503
        assert not bridge.failed
        with permit.transaction() as db:
            assert db.execute("SELECT count(*) FROM grants").fetchone()[0] == 2
            rows = db.execute("SELECT * FROM charges").fetchall()
        assert len(rows) == 2 and all(r["state"] == "SETTLED" for r in rows)
    finally:
        bridge.close()


def test_worker_lock_blocks_summary_without_grant_and_old_capability_rejected(
    ledger, tmp_path, prompt
):
    permit, token = ledger
    lock = threading.Lock()
    lock.acquire()
    bridge = summary.SummaryBridge(
        permit, token, tmp_path, "fake-model", lock, address=("127.0.0.1", 0), factory=fake_factory
    )
    try:
        assert post(bridge, prompt).status_code == 503
        assert post(bridge, prompt, auth="Bearer é").status_code == 403
        with permit.transaction() as db:
            assert db.execute("SELECT count(*) FROM grants").fetchone()[0] == 0
        old_capability = bridge.capability
    finally:
        lock.release()
        bridge.close()
    new = summary.SummaryBridge(
        permit, token, tmp_path, "fake-model", lock, address=("127.0.0.1", 0), factory=fake_factory
    )
    try:
        assert post(new, prompt, auth="Bearer " + old_capability).status_code == 403
        assert post(new, prompt).status_code == 200
    finally:
        new.close()


def test_unknown_spend_halts_bridge_and_future_driver(ledger, tmp_path, prompt):
    permit, token = ledger

    def unknown(permit, token, control, model):
        job = str(uuid4())
        permit.grant(token, job)
        charge = permit.reserve(token, job, model)

        def call(prompt):
            permit.settle(charge, actual_usd=None)
            raise RuntimeError("private provider payload")

        return call

    bridge = summary.SummaryBridge(
        permit,
        token,
        tmp_path,
        "fake-model",
        threading.Lock(),
        address=("127.0.0.1", 0),
        factory=unknown,
    )
    try:
        result = post(bridge, prompt)
        assert result.status_code == 503
        assert "private" not in result.text
        assert bridge.failed
        assert post(bridge, prompt).status_code == 503
    finally:
        bridge.close()
    permit.close_driver(token)
    with pytest.raises(Denied, match="LOCAL_COST_UNKNOWN_REVIEW_REQUIRED"):
        permit.authorize(max_jobs=1, max_calls=1, reserve_usd=".15")


def test_provider_disabled_or_stale_marker_never_opens_transport(tmp_path, monkeypatch):
    monkeypatch.setattr(httpx, "Client", lambda **kw: pytest.fail("No connection allowed"))
    provider = summary.bridge_provider(tmp_path)
    with pytest.raises(ApplicationError) as error:
        provider("unused")
    assert error.value.status_code == 503
    (tmp_path / "control").mkdir()
    (tmp_path / "control/enabled.json").write_text(
        json.dumps({"mode": "real", "owner": "tft", "remaining_jobs": 1})
    )
    with pytest.raises(ApplicationError):
        provider("unused")


@pytest.mark.parametrize("failure", [False, True])
def test_real_metered_provider_fake_sdk_only(ledger, tmp_path, prompt, monkeypatch, failure):
    from scripts.local_business.worker import LocalStop

    permit, token = ledger
    key = tmp_path / "fake-key"
    key.write_text("synthetic-test-key")
    original_config = summary.Configuration
    monkeypatch.setattr(
        summary, "Configuration", lambda *args: original_config(*args[:4], key, tmp_path / "prices")
    )
    price = ai.ModelPrice(
        currency="USD",
        as_of=date(2026, 11, 3),
        input_per_million=Decimal(1),
        output_per_million=Decimal(1),
        cached_input_per_million=Decimal(0),
    )
    monkeypatch.setattr(ai, "load_price_table", lambda p: {"fake-model": price})
    calls = []

    def generate(**kwargs):
        calls.append(kwargs)
        if failure:
            raise TimeoutError("private payload")
        usage = SimpleNamespace(
            prompt_token_count=10,
            candidates_token_count=4,
            thoughts_token_count=0,
            total_token_count=14,
            cached_content_token_count=0,
            model_dump=lambda **kw: {},
        )
        return SimpleNamespace(
            text='{"summary_ko":"소음 문의임."}',
            usage_metadata=usage,
            model_version="fake-model-001",
            candidates=[],
        )

    monkeypatch.setattr(
        ai,
        "create_client",
        lambda s: SimpleNamespace(models=SimpleNamespace(generate_content=generate)),
    )
    provider = summary.metered_provider(permit, token, tmp_path, "fake-model")
    if failure:
        with pytest.raises(LocalStop, match="LOCAL_PROVIDER_CALL_FAILED"):
            provider(prompt)
    else:
        result = provider(prompt)
        assert result.model == "fake-model" and result.usage.total_tokens == 14
    assert len(calls) == 1
    with permit.transaction() as db:
        row = db.execute("SELECT * FROM charges").fetchone()
    assert row["state"] == ("UNKNOWN" if failure else "SETTLED")


def test_local_runtime_cached_summary_get_and_post_need_no_driver(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    from fastapi.testclient import TestClient

    from backend.api.cs.summary import routes
    from backend.engine.cs.summary import (
        SUMMARY_INPUT_VERSION,
        SUMMARY_PROMPT_VERSION,
        input_sha256,
    )
    from backend.shared.access import Principal, current_principal
    from scripts.local_business import runtime

    # Reuse the real runtime fixture; its synthetic signing keys never leave this test.
    from tests.platform.test_local_business_integration import local_state

    state = local_state.__wrapped__(tmp_path, monkeypatch)
    app = runtime.app_for("cs", state)
    principal = Principal(user_id=uuid4(), roles={"cs_agent"})
    app.dependency_overrides[current_principal] = lambda: principal
    ticket_id = uuid4()
    ticket = {
        "channel": "EMAIL",
        "body_language": "en",
        "subject": "Noise",
        "body": "Noisy inverter.",
    }
    row = {
        "id": uuid4(),
        "ticket_id": ticket_id,
        "summary_ko": "인버터 소음 문의임.",
        "input_sha256": input_sha256(build_summary_input(ticket)),
        "prompt_version": SUMMARY_PROMPT_VERSION,
        "input_version": SUMMARY_INPUT_VERSION,
        "requested_model": "fake-model",
        "model_version": "fake-model-001",
        "outcome": "VALID",
        "error_code": None,
        "created_at": datetime.now(UTC),
    }

    @asynccontextmanager
    async def connection(*a):
        yield object()

    monkeypatch.setattr(routes, "connection", connection)
    monkeypatch.setattr(routes, "load", AsyncMock(return_value=ticket))
    monkeypatch.setattr(routes.repository, "matching", AsyncMock(return_value=row))
    monkeypatch.setattr(routes.repository, "latest", AsyncMock(return_value=row))
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: pytest.fail("Cached route must not call bridge")
    )
    # TestClient itself subclasses httpx.Client and was imported before the transport guard.
    with TestClient(app) as client:
        path = f"/api/v1/cs/tickets/{ticket_id}/summary"
        got = client.get(path)
        assert got.status_code == 200, got.text
        reused = client.post(path)
        assert reused.status_code == 200, reused.text
        assert reused.json()["data"]["summary"]["summary_ko"] == row["summary_ko"]
        monkeypatch.setattr(routes.repository, "matching", AsyncMock(return_value=None))
        denied = client.post(path)
        assert denied.status_code == 503
        assert denied.json()["error"]["code"] == "LOCAL_SUMMARY_UNAVAILABLE"
        # A connected local provider uses the driver-selected model in the saved attempt.
        saved = []

        async def insert(conn, values):
            saved.append(values.copy())
            return {**row, **values}

        monkeypatch.setattr(routes.repository, "insert", insert)
        app.state.summary_provider = lambda prompt: ai.Generation(
            '{"summary_ko":"인버터 소음 문의임."}',
            ai.Usage(10, 4, 0, 0, 14),
            "selected-version",
            [],
            model="selected-model",
            elapsed_ms=1,
        )
        made = client.post(path)
        assert made.status_code == 201, made.text
        assert saved[-1]["requested_model"] == "selected-model"
        assert saved[-1]["input_tokens"] == 10
        app.state.summary_provider = lambda prompt: ai.Generation(
            '{"summary_ko":"99999대 교체 승인됨."}',
            ai.Usage(10, 4, 0, 0, 14),
            "selected-version",
            [],
            model="selected-model",
            elapsed_ms=1,
        )
        invalid = client.post(path)
        assert invalid.status_code == 502
        assert saved[-1]["outcome"] == "INVALID_OUTPUT"
        assert "summary_ko" not in saved[-1]


def test_production_summary_bridge_is_forbidden(ledger, tmp_path, monkeypatch):
    permit, token = ledger
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="LOCAL_RUNNER_REQUIRES_LOCAL_ENVIRONMENT"):
        summary.SummaryBridge(permit, token, tmp_path, "fake-model", threading.Lock())
    with permit.transaction() as db:
        assert db.execute("SELECT count(*) FROM grants").fetchone()[0] == 0


def test_tracing_rejected_before_summary_grant_or_key_access(ledger, tmp_path, monkeypatch):
    permit, token = ledger
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setattr(ai, "create_client", lambda *a: pytest.fail("No client allowed"))
    with pytest.raises(ValueError):
        summary.metered_provider(permit, token, tmp_path, "fake-model")
    with permit.transaction() as db:
        assert db.execute("SELECT count(*) FROM grants").fetchone()[0] == 0


def test_dispatcher_counts_summary_grant_before_admitting_analysis(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from datetime import UTC, datetime

    from scripts.local_business import dispatcher

    control = tmp_path / "control"
    control.mkdir()
    permit = Permit(control / "permit.sqlite3")
    token = permit.authorize(max_jobs=1, max_calls=1, reserve_usd=".15")
    credentials = tmp_path / "fake-credentials.json"
    credentials.write_text(json.dumps({"platform_app": "fake-dsn", "local_cs_worker": "fake-dsn"}))
    (tmp_path / "settings.json").write_text(json.dumps({"namespace": "team4-kan159-test"}))
    for k, v in {
        "LOCAL_BUSINESS_STATE": str(tmp_path),
        "LOCAL_BUSINESS_CONTROL": str(control),
        "LOCAL_DRIVER_TOKEN": token,
        "LOCAL_JOB_OWNER": "cs",
        "LOCAL_MODEL": "fake-model",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(
        dispatcher,
        "Path",
        lambda p: credentials if str(p) == "/local/secrets/worker-db.json" else Path(p),
    )
    monkeypatch.setattr(dispatcher, "Settings", lambda **kw: object())
    monkeypatch.setattr(dispatcher, "corpus_check", lambda *a: None)
    monkeypatch.setattr(dispatcher.signal, "signal", lambda *a: None)
    monkeypatch.setattr(
        dispatcher, "candidate", lambda *a: pytest.fail("Summary exhausted shared job limit")
    )

    @contextmanager
    def connect(*a, **kw):
        yield SimpleNamespace(
            execute=lambda *a: SimpleNamespace(fetchone=lambda: (datetime.now(UTC),))
        )

    monkeypatch.setattr(dispatcher.psycopg, "connect", connect)
    closed = []

    class Bridge:
        def __init__(self, p, t, c, m, lock):
            self.failed = False
            self.capability = "synthetic-capability"
            fake_factory(p, t, c, m)("unused")

        def close(self):
            # Driver token remains valid through bridge shutdown/settlement.
            with permit.transaction() as db:
                permit.driver(db, token)
                assert (
                    db.execute("SELECT count(*) FROM charges WHERE state='SETTLED'").fetchone()[0]
                    == 1
                )
            closed.append(True)

    monkeypatch.setattr(summary, "SummaryBridge", Bridge)
    dispatcher.main()
    assert closed == [True]
    assert not (control / "enabled.json").exists()
    with permit.transaction() as db:
        assert db.execute("SELECT token FROM driver").fetchone()[0] is None


@pytest.mark.parametrize("limit", ["jobs", "calls", "budget"])
def test_normal_summary_limits_are_not_cost_review_failures(tmp_path, prompt, limit):
    permit = Permit(tmp_path / "permit.sqlite3")
    token = permit.authorize(
        max_jobs=1 if limit == "jobs" else 4,
        max_calls=1 if limit == "calls" else 4,
        reserve_usd=".15",
        budget_usd=".15" if limit == "budget" else "1.50",
    )
    fake_factory(permit, token, tmp_path, "fake-model")(prompt)
    bridge = summary.SummaryBridge(
        permit,
        token,
        tmp_path,
        "fake-model",
        threading.Lock(),
        address=("127.0.0.1", 0),
        factory=fake_factory,
    )
    try:
        assert post(bridge, prompt).status_code == 503
        assert not bridge.failed
        assert post(bridge, prompt).status_code == 503
        assert not bridge.failed
        with permit.transaction() as db:
            charges = db.execute("SELECT * FROM charges").fetchall()
        assert len(charges) == 1 and charges[0]["state"] == "SETTLED"
    finally:
        bridge.close()
        permit.close_driver(token)
    # The next explicitly authorized driver needs no unknown-cost review.
    permit.authorize(max_jobs=1, max_calls=1, reserve_usd=".01")


@pytest.mark.parametrize("reason", ["unknown", "authorization"])
def test_non_limit_admission_denial_keeps_summary_fail_closed(ledger, tmp_path, prompt, reason):
    permit, token = ledger
    if reason == "unknown":
        job = str(uuid4())
        permit.grant(token, job)
        charge = permit.reserve(token, job, "fake-model")
        permit.settle(charge, actual_usd=None)
    else:
        permit.close_driver(token)
    bridge = summary.SummaryBridge(
        permit,
        token,
        tmp_path,
        "fake-model",
        threading.Lock(),
        address=("127.0.0.1", 0),
        factory=fake_factory,
    )
    try:
        assert post(bridge, prompt).status_code == 503
        assert bridge.failed
        with permit.transaction() as db:
            assert db.execute("SELECT count(*) FROM charges").fetchone()[0] == (
                1 if reason == "unknown" else 0
            )
    finally:
        bridge.close()


def draft_prompt():
    """KAN-115 개선요청 초안 경로가 받는 정확한 프롬프트(C/S 경로가 만드는 것과 같다)."""
    from datetime import UTC, datetime

    from backend.engine.cs.improvement_drafting import (
        ConditionSummary,
        EvidenceTicket,
        ImprovementDraftContext,
        build_improvement_draft_input,
    )
    from backend.engine.cs.improvement_drafting import render_prompt as render_draft

    at = datetime(2026, 10, 1, tzinfo=UTC)
    context = ImprovementDraftContext(
        request_type="DOCUMENT",
        product_code="TRF-OIL-1000",
        fault_id=None,
        doc_signal="NOT_FOUND",
        tickets=[
            EvidenceTicket(
                ticket_ref="T-1",
                received_at=at,
                product_code="TRF-OIL-1000",
                fw_version_reported=None,
                error_code_reported=None,
                summary="Tap range is missing from the catalogue.",
            )
        ],
        cases=[],
        condition=ConditionSummary(condition_status="UNKNOWN", summary=None),
        candidate_depts=["TS_TRF"],
        as_of=at,
    )
    return render_draft(build_improvement_draft_input(context, sensitive_values={}).payload)


def test_the_improvement_draft_path_takes_only_its_own_prompt_and_factory(ledger, tmp_path, prompt):
    """KAN-115: 같은 중계가 /improvement-draft에서 초안 프롬프트만 받고 초안 공급자로 부른다(같은 원장)."""
    permit, token = ledger
    used = []

    def draft_factory(*args):
        used.append("draft")
        return fake_factory(*args)

    bridge = summary.SummaryBridge(
        permit,
        token,
        tmp_path,
        "fake-model",
        threading.Lock(),
        address=("127.0.0.1", 0),
        factory=fake_factory,
        draft_factory=draft_factory,
    )
    url = f"http://127.0.0.1:{bridge.server.server_port}"
    headers = {"Authorization": "Bearer " + bridge.capability}
    try:
        # 요약 프롬프트는 초안 경로에서, 초안 프롬프트는 요약 경로에서 받지 않는다. 모르는 경로도 400.
        for path, body in (("/improvement-draft", prompt), ("/summary", draft_prompt())):
            response = httpx.post(
                url + path, headers=headers, json={"prompt": body}, trust_env=False
            )
            assert response.status_code == 400
        other = httpx.post(
            url + "/other", headers=headers, json={"prompt": prompt}, trust_env=False
        )
        assert other.status_code == 400
        assert used == []
        made = httpx.post(
            url + "/improvement-draft",
            headers=headers,
            json={"prompt": draft_prompt()},
            trust_env=False,
        )
        assert made.status_code == 200 and used == ["draft"]
        with permit.transaction() as db:
            assert db.execute("SELECT count(*) FROM charges").fetchone()[0] == 1
    finally:
        bridge.close()


def test_the_draft_bridge_provider_is_unavailable_without_a_driver(tmp_path):
    """초안 공급자도 요약과 같이 CS 실행기가 없으면 모델을 부르지 않고 503(LOCAL_SUMMARY_UNAVAILABLE)이다."""
    provider = summary.bridge_provider(tmp_path, "/improvement-draft")
    with pytest.raises(ApplicationError) as error:
        provider("prompt")
    assert error.value.code == "LOCAL_SUMMARY_UNAVAILABLE"
