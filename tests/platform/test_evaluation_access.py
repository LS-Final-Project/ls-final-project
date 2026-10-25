"""평가자 지정과 운영 세션, 최초 주지표 및 잠금 상태의 HTTP 회귀 검사."""

from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from backend.api.platform.auth.http import SESSION_COOKIE
from backend.api.platform.auth.settings import CALLBACK_PATH, MicrosoftAuthSettings
from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.main import create_app
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

USER = UUID(int=90)
PATH = "/api/v1/platform/evaluations/policy"


def client_for(ids=(), *, enabled=True, dev=False):
    """네트워크 연결 없이 실제 라우터와 서버 전용 설정을 준비한다.

    Args:
        ids: 허용할 합성 내부 사용자 UUID 목록.
        enabled: 운영 인증 활성 여부.
        dev: 로컬 개발 인증 활성 여부.

    Returns:
        TestClient: 정책 접근 검사용 HTTPS 클라이언트.
    """
    auth = MicrosoftAuthSettings(
        _env_file=None,
        enabled=enabled,
        client_id=UUID(int=1),
        client_secret=SecretStr("synthetic-secret"),
        app_origin="https://app.example.invalid",
        redirect_uri="https://app.example.invalid" + CALLBACK_PATH,
    )
    return TestClient(
        create_app(
            Settings(_env_file=None, environment="test", dev_auth_enabled=dev),
            auth,
            EvaluationAccessSettings(evaluator_user_ids=ids),
        ),
        base_url="https://app.example.invalid",
    )


@pytest.mark.parametrize("role", ["manager", "sales", "cs_agent"])
def test_role_does_not_grant_evaluation_access(monkeypatch, role):
    """업무 역할과 위조된 사용자 헤더가 서버 지정 목록을 우회하지 못한다.

    Args:
        monkeypatch: 세션 조회 경계 대체 도구.
        role: 권한을 자동 부여해서는 안 되는 업무 역할.

    Returns:
        None: 기본 거부와 비캐시 응답을 확인한다.
    """
    user = Principal(user_id=USER, roles={role})
    loader = AsyncMock(return_value=user)
    monkeypatch.setattr("backend.api.platform.evaluations.access.load_session_principal", loader)
    client = client_for()
    client.cookies.set(SESSION_COOKIE, "synthetic-session")
    response = client.get(PATH, headers=principal_header(user))
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert str(USER) not in response.text


def test_designated_session_gets_policy_but_no_execution_or_raw_access(monkeypatch):
    """지정 평가자만 주지표 정책을 받으며 실행·시험·원시 접근은 잠겨 있다.

    Args:
        monkeypatch: 세션 조회 경계 대체 도구.

    Returns:
        None: 정책 응답과 매 요청 재검사를 확인한다.
    """
    loader = AsyncMock(return_value=Principal(user_id=USER, roles={"sales"}))
    monkeypatch.setattr("backend.api.platform.evaluations.access.load_session_principal", loader)
    client = client_for((USER,))
    client.cookies.set(SESSION_COOKIE, "synthetic-session")
    response = client.get(PATH)
    assert response.status_code == 200
    assert response.json()["data"] == {
        "primary_quality_view": "first_attempt",
        "final_result_reported_separately": True,
        "admission_available": False,
        "local_smoke_execution_available": False,
        "execution_available": False,
        "execution_unavailable_reason": "RUNNER_NOT_CONNECTED",
        "final_test_available": False,
        "raw_output_available": False,
    }
    loader.side_effect = HTTPException(401)
    revoked = client.get(PATH)
    assert revoked.status_code == 401
    assert revoked.headers["cache-control"] == "no-store"
    assert loader.await_count == 2


@pytest.mark.parametrize("dev", [False, True])
def test_disabled_operational_auth_cannot_use_development_identity(dev):
    """개발 모드와 임의 헤더만으로 평가 기능을 열 수 없다.

    Args:
        dev: 개발 계정 선택 기능의 활성 여부.

    Returns:
        None: 운영 세션 경계의 거절을 확인한다.
    """
    client = client_for((USER,), enabled=False, dev=dev)
    user = Principal(user_id=USER, roles={"manager"})
    assert client.get(PATH, headers=principal_header(user)).status_code == 401


def test_missing_cookie_and_unavailable_session_fail_closed(monkeypatch):
    """세션 없는 호출과 DB 조회 장애를 허용이나 성공으로 바꾸지 않는다.

    Args:
        monkeypatch: 세션 조회 장애 대체 도구.

    Returns:
        None: 무세션 401과 의존성 장애 503을 확인한다.
    """
    client = client_for((USER,))
    assert client.get(PATH).status_code == 401
    monkeypatch.setattr(
        "backend.api.platform.evaluations.access.load_session_principal",
        AsyncMock(side_effect=HTTPException(503)),
    )
    response = client.get(PATH)
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"


def test_allowlist_only_accepts_internal_uuids():
    """이메일이나 표시 이름을 UUID 허용 목록으로 오인하지 않는다.

    Args:
        없음.

    Returns:
        None: 잘못된 지정 값의 설정 검증 실패를 확인한다.
    """
    with pytest.raises(ValidationError):
        EvaluationAccessSettings(evaluator_user_ids=["user@example.invalid"])


@pytest.fixture
def writer(monkeypatch):
    """지정 평가자의 합성 운영 세션과 정상 변경 요청 헤더를 준비한다.

    Args:
        monkeypatch: 실제 인증 DB 조회를 대체하는 도구.

    Returns:
        TestClient: 세션과 CSRF 검사를 통과할 수 있는 클라이언트.
    """
    from backend.api.platform.auth.http import CSRF_HEADER, csrf_token

    monkeypatch.setattr(
        "backend.api.platform.evaluations.access.load_session_principal",
        AsyncMock(return_value=Principal(user_id=USER, roles={"sales"})),
    )
    client = client_for((USER,))
    client.cookies.set(SESSION_COOKIE, "synthetic-session")
    client.headers.update(
        {
            "Origin": "https://app.example.invalid",
            CSRF_HEADER: csrf_token("synthetic-session"),
            "Idempotency-Key": "synthetic-request-90",
        }
    )
    return client


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("Origin", None),
        ("Origin", "https://attacker.invalid"),
        ("X-CSRF-Token", None),
        ("X-CSRF-Token", "another-session"),
    ],
)
def test_run_request_rejects_cross_site_or_wrong_session(writer, header, value):
    """유효한 평가자라도 출처 또는 세션 결속이 틀리면 거절한다.

    Args:
        writer: 지정 평가자 클라이언트.
        header: 변조하거나 제거할 헤더.
        value: 변조 값 또는 제거를 의미하는 None.

    Returns:
        None: 요청이 403으로 거절되고 캐시되지 않음을 확인한다.
    """
    if value is None:
        del writer.headers[header]
    else:
        writer.headers[header] = value
    response = writer.post("/api/v1/platform/evaluations/runs", json={"experiment_id": str(USER)})
    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("key", [None, "k90!", "x" * 129])
def test_run_request_requires_contract_idempotency_key(writer, key):
    """접수 전 멱등 키 누락과 길이 위반을 검출한다.

    Args:
        writer: 지정 평가자 클라이언트.
        key: 부적합 키 또는 누락을 나타내는 None.

    Returns:
        None: 키 값 노출 없이 422를 반환하는지 확인한다.
    """
    if key is None:
        del writer.headers["Idempotency-Key"]
    else:
        writer.headers["Idempotency-Key"] = key
    response = writer.post("/api/v1/platform/evaluations/runs", json={"experiment_id": str(USER)})
    assert response.status_code == 422
    if key:
        assert key not in response.text


@pytest.mark.parametrize("extra", ["data_split", "configuration", "command", "model_id"])
def test_run_request_rejects_client_execution_overrides(writer, extra):
    """일반 실행 요청에 데이터 분할·모델·명령을 끼워 넣지 못하게 한다.

    Args:
        writer: 지정 평가자 클라이언트.
        extra: 허용되지 않은 요청 필드.

    Returns:
        None: 임의 값이 오류에 노출되지 않고 거절됨을 확인한다.
    """
    response = writer.post(
        "/api/v1/platform/evaluations/runs",
        json={"experiment_id": str(USER), extra: "sensitive-synthetic-value"},
    )
    assert response.status_code == 422
    assert "sensitive-synthetic-value" not in response.text


def test_run_request_never_reports_acceptance_before_runner_connection(writer, monkeypatch):
    """정상 요청과 반복 요청 모두 실행기 미연결을 접수 성공으로 숨기지 않는다.

    Args:
        writer: 지정 평가자 클라이언트.
        monkeypatch: 반복 요청 시 권한 철회를 재현하는 도구.

    Returns:
        None: 503 잠금과 권한 철회 후 403, 작업 식별자 미노출을 확인한다.
    """
    for _ in range(2):
        response = writer.post(
            "/api/v1/platform/evaluations/runs", json={"experiment_id": str(USER)}
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "EVALUATION_RUNNER_NOT_CONNECTED"
        assert "location" not in response.headers
        assert "job_id" not in response.text
        assert response.headers["cache-control"] == "no-store"
    monkeypatch.setattr(
        "backend.api.platform.evaluations.access.load_session_principal",
        AsyncMock(return_value=Principal(user_id=UUID(int=91), roles={"manager"})),
    )
    assert (
        writer.post(
            "/api/v1/platform/evaluations/runs", json={"experiment_id": str(USER)}
        ).status_code
        == 403
    )
