"""공개 API를 열지 않고 로그인 연결 순서와 실패 전파를 검증한다."""

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException

from backend.api.platform.auth import login
from backend.api.platform.auth.callback import MicrosoftSubject, OAuthPurpose, PendingAuthorization
from backend.api.platform.auth.identity import identity
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings

NOW = datetime.now(UTC)
SETTINGS = Settings(_env_file=None, environment="test")
ROW = {
    "id": UUID(int=3),
    "tid": UUID(int=1),
    "oid": UUID(int=2),
    "email": "registered@example.invalid",
    "display_name": "등록 사용자",
    "dept": None,
    "roles": ["sales", "manager"],
}


class StartAdapter:
    """동기 공급자 호출의 목적·실행 스레드와 실패를 기록한다."""

    def __init__(self, error=None):
        """인가 시작 결과와 호출 기록을 준비한다.

        Args:
            error: 공급자 호출에서 발생시킬 오류. 없으면 합성 flow를 반환한다.

        Returns:
            None: 호출 기록과 합성 인가 시도를 초기화한다.
        """
        self.error = error
        self.calls = []
        self.pending = PendingAuthorization(
            purpose=OAuthPurpose.LOGIN,
            state="synthetic-state",
            browser_binding_hash=sha256(b"browser-binding").hexdigest(),
            expires_at=NOW + timedelta(minutes=5),
            provider_flow={"nonce": "private-nonce", "code_verifier": "private-verifier"},
        )

    def begin(self, purpose, *, browser_binding, now, owner_user_id=None):
        """호출 정보를 기록하고 Microsoft URL과 서버 전용 flow를 반환한다.

        Args:
            purpose: 호출자가 고정한 인가 목적.
            browser_binding: 브라우저 결속값.
            now: 인가 시작 시각.
            owner_user_id: 기존 서비스 사용자. 로그인에서는 None.

        Returns:
            tuple[str, PendingAuthorization]: 합성 URL과 서버 저장용 flow.
        """
        self.calls.append((purpose, browser_binding, now, owner_user_id, threading.get_ident()))
        if self.error:
            raise self.error
        return "https://login.microsoftonline.com/synthetic", self.pending


def test_start_login_stores_flow_before_returning_url_and_uses_worker_thread(monkeypatch):
    """로그인이 메일 동의를 요청하지 않고 저장 후 URL만 반환하는지 확인한다.

    Args:
        monkeypatch: 실제 DB 저장을 기록하는 비동기 함수로 바꾸는 pytest 도구.

    Returns:
        None: 로그인 목적·스레드 분리·서버 저장을 검증한다.
    """
    adapter = StartAdapter()
    saved = AsyncMock()
    monkeypatch.setattr(login, "save_authorization", saved)
    main_thread = threading.get_ident()
    result = asyncio.run(
        login.start_login(SETTINGS, browser_binding="browser-binding", now=NOW, adapter=adapter)
    )
    saved.assert_awaited_once_with(SETTINGS, adapter.pending)
    purpose, binding, now, owner, thread = adapter.calls[0]
    assert (purpose, binding, now, owner) == (OAuthPurpose.LOGIN, "browser-binding", NOW, None)
    assert thread != main_thread
    assert result == "https://login.microsoftonline.com/synthetic"


@pytest.mark.parametrize("phase", ["provider", "database"])
def test_start_login_propagates_failures_without_returning_url(monkeypatch, phase):
    """공급자 또는 저장 실패를 성공 URL로 바꾸지 않는지 확인한다.

    Args:
        monkeypatch: 실제 DB 쓰기를 합성 함수로 교체하는 pytest 도구.
        phase: 공급자 호출 또는 DB 저장 중 실패할 단계.

    Returns:
        None: 실패 전파와 후속 저장 중단을 검증한다.
    """
    error = ApplicationError(503, "AUTH_PROVIDER_UNAVAILABLE", "Unavailable")
    adapter = StartAdapter(error if phase == "provider" else None)
    saved = AsyncMock(side_effect=HTTPException(503) if phase == "database" else None)
    monkeypatch.setattr(login, "save_authorization", saved)
    with pytest.raises(ApplicationError if phase == "provider" else HTTPException):
        asyncio.run(
            login.start_login(SETTINGS, browser_binding="browser-binding", now=NOW, adapter=adapter)
        )
    assert saved.await_count == (phase == "database")


def test_complete_login_resolves_only_verified_subject_and_preserves_duplicate_query(monkeypatch):
    """원시 query를 검증 경계에 넘기고 검증된 식별자로만 DB를 조회하는지 확인한다.

    Args:
        monkeypatch: 이미 별도로 검증한 소비 경계와 DB 조회를 교체하는 pytest 도구.

    Returns:
        None: 목적·소유자 고정과 검증 결과만 사용하는 연결을 검증한다.
    """
    subject = MicrosoftSubject(ROW["tid"], ROW["oid"])
    exchanged = AsyncMock(return_value=subject)
    loaded = AsyncMock(return_value=identity(ROW))
    monkeypatch.setattr(login, "complete_stored_callback", exchanged)
    monkeypatch.setattr(login, "registered_identity", loaded)
    parameters = [("state", "one"), ("state", "two"), ("roles", "cs_agent")]
    adapter = object()
    result = asyncio.run(
        login.complete_login(
            SETTINGS, parameters, browser_binding="browser-binding", now=NOW, adapter=adapter
        )
    )
    exchanged.assert_awaited_once_with(
        SETTINGS,
        parameters,
        purpose=OAuthPurpose.LOGIN,
        browser_binding="browser-binding",
        session_user_id=None,
        now=NOW,
        adapter=adapter,
    )
    loaded.assert_awaited_once_with(SETTINGS, subject)
    assert result.roles == ["sales", "manager"]


@pytest.mark.parametrize("status", [400, 401, 503])
def test_failed_callback_never_resolves_a_user(monkeypatch, status):
    """callback·토큰·공급자 실패 후 사용자 조회가 실행되지 않는지 확인한다.

    Args:
        monkeypatch: 소비 경계의 실패와 후속 조회를 기록하는 pytest 도구.
        status: 기존 검증 경계가 반환할 오류 상태.

    Returns:
        None: 인증 실패를 그대로 전파하고 후속 조회를 중단하는지 검증한다.
    """
    error = ApplicationError(status, "AUTH_TEST_FAILURE", "Authentication failed.")
    monkeypatch.setattr(login, "complete_stored_callback", AsyncMock(side_effect=error))
    loaded = AsyncMock()
    monkeypatch.setattr(login, "registered_identity", loaded)
    with pytest.raises(ApplicationError) as rejected:
        asyncio.run(
            login.complete_login(
                SETTINGS, [], browser_binding="browser-binding", now=NOW, adapter=object()
            )
        )
    assert rejected.value is error
    loaded.assert_not_awaited()


@pytest.mark.parametrize("status", [401, 503])
def test_registration_failure_never_returns_an_identity(monkeypatch, status):
    """토큰 검증 성공 후에도 등록 거부·DB 장애를 사용자 성공으로 바꾸지 않는다.

    Args:
        monkeypatch: 검증된 식별자와 등록 조회 실패를 준비하는 pytest 도구.
        status: 등록 조건 거부 또는 DB 장애 상태.

    Returns:
        None: 검증된 Microsoft 계정만으로 서비스 사용자를 허용하지 않음을 확인한다.
    """
    monkeypatch.setattr(
        login,
        "complete_stored_callback",
        AsyncMock(return_value=MicrosoftSubject(ROW["tid"], ROW["oid"])),
    )
    error = HTTPException(status)
    monkeypatch.setattr(login, "registered_identity", AsyncMock(side_effect=error))
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(
            login.complete_login(
                SETTINGS, [], browser_binding="browser-binding", now=NOW, adapter=object()
            )
        )
    assert rejected.value is error


@pytest.mark.parametrize(
    "row",
    [None, ROW | {"roles": []}, ROW | {"roles": ["unknown"]}, ROW | {"roles": ["dept_staff"]}],
)
def test_shared_identity_rejects_missing_or_invalid_registration(row):
    """개발 세션과 로그인에 공통인 무역할·잘못된 역할·부서 누락 거부를 확인한다.

    Args:
        row: 조회 결과 없음 또는 유효한 권한을 충족하지 못하는 DB 행.

    Returns:
        None: 기존 Identity 계약에 따라 401로 거부함을 검증한다.
    """
    with pytest.raises(HTTPException) as rejected:
        identity(row)
    assert rejected.value.status_code == 401
