"""회신 반영 분석 경로(KAN-230): 설정·꺼짐·입력 판본(DB·모델 없음)."""

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.api.cs.main import create_app
from backend.api.cs.reply_analysis.routes import (
    CsReplyAnalysisSettings,
    configure_reply_analysis,
    input_sha256,
)
from backend.engine.cs.reply import CustomerReply, ReplyContext, build_reply_input
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

AGENT = Principal(user_id=uuid4(), roles={"cs_agent"})


def payload(**context):
    values = {
        "ticket_product_code": "TRF-OIL-1000",
        "fw_version_reported": None,
        "requested_info_codes": ["photos"],
        "already_provided_codes": [],
        "prior_options": None,
        **context,
    }
    return build_reply_input(
        ReplyContext(**values),
        CustomerReply(
            channel="EMAIL",
            body="Photos attached.",
            body_language="en",
            received_at=datetime(2026, 11, 5, tzinfo=UTC),
        ),
        confirmed_unit_serial=None,
        sensitive_values={},
    ).payload


def test_the_feature_is_off_by_default_and_both_routes_are_404(monkeypatch):
    monkeypatch.delenv("CS_REPLY_ANALYSIS_ENABLED", raising=False)
    settings = CsReplyAnalysisSettings(_env_file=None)
    assert (settings.enabled, settings.model, settings.max_output_tokens) == (
        False,
        "gemini-3.6-flash",
        8192,
    )
    app = create_app(Settings(_env_file=None, environment="test"))
    configure_reply_analysis(app, settings)
    assert app.state.reply_analysis_provider is None
    path = f"/api/v1/cs/tickets/{uuid4()}/reply-analysis"
    with TestClient(app, headers=principal_header(AGENT)) as client:
        assert client.get(path).status_code == 404
        assert client.post(path).status_code == 404


def test_the_input_hash_follows_the_context():
    """요청 코드·제공 정보가 바뀌면 판본이 바뀌어 저장된 해석이 stale이 된다."""
    base = input_sha256(payload())
    assert base == input_sha256(payload())
    assert base != input_sha256(payload(already_provided_codes=["photos"]))
    assert base != input_sha256(payload(requested_info_codes=["photos", "serial_no"]))
