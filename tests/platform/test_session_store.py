"""전용 PostgreSQL에서 운영 세션의 발급·만료·교체·철회와 권한 재조회를 검증한다."""

import asyncio
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException
from psycopg.sql import SQL, Identifier

from backend.api.platform.auth import sessions
from backend.api.platform.auth.callback import MicrosoftSubject


def test_session_stores_only_hash_and_lookup_does_not_extend_expiry(
    settings, auth_database, registered_user
):
    """발급 토큰 비노출·hash 저장·DB 만료 일치 및 조회 시 절대 만료 보존을 확인한다.

    Args:
        settings: 플랫폼 런타임 권한의 DB 설정.
        auth_database: 저장 내용을 확인할 전용 DB 관리자 연결.
        registered_user: 합성 Microsoft 식별자와 등록 사용자 ID.

    Returns:
        None: 원문 토큰을 저장하거나 repr에 노출하지 않고 현재 사용자를 반환함을 확인한다.
    """
    subject, user_id = registered_user
    grant = asyncio.run(sessions.issue_session(settings, subject))
    token = grant.token.get_secret_value()
    assert token not in repr(grant)
    assert grant.user.id == user_id
    assert grant.user.roles == ["cs_agent"]
    result = asyncio.run(sessions.load_session(settings, token))
    assert result == grant.user
    with psycopg.connect(auth_database) as conn:
        digest, owner, expires_at, lifetime = conn.execute(
            "SELECT token_hash,user_id,expires_at,expires_at-created_at "
            "FROM identity.sessions WHERE user_id=%s",
            (user_id,),
        ).fetchone()
    assert digest == sha256(token.encode()).hexdigest() and digest != token
    assert owner == user_id and expires_at == grant.expires_at
    assert abs(lifetime.total_seconds() - 28800) < 1


def test_session_replacement_revokes_old_token_but_preserves_other_browser(
    settings, registered_user
):
    """로그인 시 토큰을 새로 발급하고 지정한 기존 세션만 교체하는지 확인한다.

    Args:
        settings: 플랫폼 런타임 권한의 DB 설정.
        registered_user: 합성 Microsoft 식별자와 등록 사용자 ID.

    Returns:
        None: 기존 세션 재사용 거부와 다른 브라우저 세션의 유지를 확인한다.
    """
    subject, user_id = registered_user

    async def scenario():
        """두 브라우저 세션 중 하나를 교체한 후 세 토큰의 유효성을 확인한다.

        Args:
            없음.

        Returns:
            None: 토큰 교체 범위와 새 세션의 유효성을 검증한다.
        """
        old = await sessions.issue_session(settings, subject)
        other = await sessions.issue_session(settings, subject)
        new = await sessions.issue_session(
            settings,
            subject,
            previous_token_hash=sha256(old.token.get_secret_value().encode()).hexdigest(),
        )
        assert len({grant.token.get_secret_value() for grant in (old, other, new)}) == 3
        with pytest.raises(HTTPException) as rejected:
            await sessions.load_session(settings, old.token.get_secret_value())
        assert rejected.value.status_code == 401
        for grant in (other, new):
            assert (
                await sessions.load_session(settings, grant.token.get_secret_value())
            ).id == user_id

    asyncio.run(scenario())


def test_revocation_is_idempotent_and_blocks_replay(settings, registered_user):
    """중복 철회·없는 토큰을 허용하되 철회한 세션은 재사용할 수 없는지 확인한다.

    Args:
        settings: 플랫폼 런타임 권한의 DB 설정.
        registered_user: 합성 Microsoft 식별자와 등록 사용자 ID.

    Returns:
        None: 철회 후 반복 조회 거부와 관계없는 세션 보존을 검증한다.
    """
    subject, _ = registered_user

    async def scenario():
        """철회를 반복하고 세션을 다시 조회한다.

        Args:
            없음.

        Returns:
            None: 서버 철회와 idempotency를 검증한다.
        """
        grant = await sessions.issue_session(settings, subject)
        token = grant.token.get_secret_value()
        for missing in (None, "", "unknown"):
            await sessions.revoke_session(settings, missing)
        assert await sessions.load_session(settings, token) == grant.user
        await sessions.revoke_session(settings, token)
        await sessions.revoke_session(settings, token)
        with pytest.raises(HTTPException) as rejected:
            await sessions.load_session(settings, token)
        assert rejected.value.status_code == 401

    asyncio.run(scenario())


@pytest.mark.parametrize("token", [None, "", "unknown"])
def test_missing_or_unknown_session_is_unauthorized(settings, token):
    """누락·빈 값·알 수 없는 토큰을 운영 인증으로 허용하지 않는지 확인한다.

    Args:
        settings: 플랫폼 런타임 권한의 DB 설정.
        token: 존재하지 않는 세션 토큰.

    Returns:
        None: 항상 401로 거부함을 검증한다.
    """
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(sessions.load_session(settings, token))
    assert rejected.value.status_code == 401


@pytest.mark.parametrize("expired_by", ["0 seconds", "1 second"])
def test_expired_session_is_rejected_and_cleaned_on_next_issue(
    settings, auth_database, registered_user, expired_by
):
    """실제 DB 시각의 만료 경계 및 과거 만료를 거부하고 오래된 행을 정리하는지 확인한다.

    Args:
        settings: 플랫폼 런타임 권한의 DB 설정.
        auth_database: 만료 시각을 준비할 전용 DB 관리자 연결.
        registered_user: 합성 Microsoft 식별자와 등록 사용자 ID.
        expired_by: DB 현재 시각에서 뺄 만료 경과 시간.

    Returns:
        None: 만료한 토큰이 거부되고 새 세션만 남는지 확인한다.
    """
    subject, user_id = registered_user
    old = asyncio.run(sessions.issue_session(settings, subject))
    with psycopg.connect(auth_database) as conn:
        conn.execute(
            "UPDATE identity.sessions SET created_at=clock_timestamp()-interval '9 hours', "
            "expires_at=clock_timestamp()-%s::interval WHERE user_id=%s",
            (expired_by, user_id),
        )
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(sessions.load_session(settings, old.token.get_secret_value()))
    assert rejected.value.status_code == 401
    new = asyncio.run(sessions.issue_session(settings, subject))
    with psycopg.connect(auth_database) as conn:
        assert conn.execute(
            "SELECT token_hash FROM identity.sessions WHERE user_id=%s", (user_id,)
        ).fetchall() == [(sha256(new.token.get_secret_value().encode()).hexdigest(),)]


def test_existing_session_reads_current_roles_and_department(
    settings, auth_database, registered_user
):
    """발급 당시 역할을 캐시하지 않고 DB의 역할·부서 변경을 즉시 반영하는지 확인한다.

    Args:
        settings: 플랫폼 런타임 권한의 DB 설정.
        auth_database: 역할·부서를 변경할 전용 DB 관리자 연결.
        registered_user: 합성 Microsoft 식별자와 등록 사용자 ID.

    Returns:
        None: 같은 토큰으로 변경된 역할과 부서만 조회함을 검증한다.
    """
    subject, user_id = registered_user
    grant = asyncio.run(sessions.issue_session(settings, subject))
    with psycopg.connect(auth_database) as conn:
        conn.execute("DELETE FROM identity.user_roles WHERE user_id=%s", (user_id,))
        conn.execute(
            "INSERT INTO identity.user_roles(user_id,role) VALUES (%s,'dept_staff')", (user_id,)
        )
        conn.execute("UPDATE identity.users SET dept='service' WHERE id=%s", (user_id,))
    current = asyncio.run(sessions.load_session(settings, grant.token.get_secret_value()))
    assert current.roles == ["dept_staff"] and current.dept == "service"


@pytest.mark.parametrize("invalid", ["inactive", "no_roles", "missing_dept"])
def test_invalid_user_cannot_use_or_issue_session(
    settings, auth_database, registered_user, invalid
):
    """비활성·역할 삭제·부서 누락을 기존 세션 조회와 신규 발급에 모두 반영한다.

    Args:
        settings: 플랫폼 런타임 권한의 DB 설정.
        auth_database: 사용자 상태를 변경할 전용 DB 관리자 연결.
        registered_user: 합성 Microsoft 식별자와 등록 사용자 ID.
        invalid: 거부할 등록 상태.

    Returns:
        None: 기존 세션의 접근 거부와 추가 세션 생성 없음을 검증한다.
    """
    subject, user_id = registered_user
    grant = asyncio.run(sessions.issue_session(settings, subject))
    with psycopg.connect(auth_database) as conn:
        if invalid == "inactive":
            conn.execute("UPDATE identity.users SET is_active=false WHERE id=%s", (user_id,))
        else:
            conn.execute("DELETE FROM identity.user_roles WHERE user_id=%s", (user_id,))
            if invalid == "missing_dept":
                conn.execute(
                    "INSERT INTO identity.user_roles(user_id,role) VALUES (%s,'dept_staff')",
                    (user_id,),
                )
                conn.execute("UPDATE identity.users SET dept=NULL WHERE id=%s", (user_id,))
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(sessions.load_session(settings, grant.token.get_secret_value()))
    assert rejected.value.status_code == 401
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(sessions.issue_session(settings, subject))
    assert rejected.value.status_code == 401
    with psycopg.connect(auth_database) as conn:
        assert conn.execute(
            "SELECT count(*) FROM identity.sessions WHERE user_id=%s", (user_id,)
        ).fetchone() == (1,)


def test_unregistered_subject_never_creates_user_or_session(settings, auth_database):
    """미등록 Microsoft 식별자에 사용자·역할·세션을 자동 생성하지 않는지 확인한다.

    Args:
        settings: 플랫폼 런타임 권한의 DB 설정.
        auth_database: 사용자 생성 없음을 확인할 전용 DB 관리자 연결.

    Returns:
        None: 등록되지 않은 식별자의 세션 발급을 401로 거부함을 검증한다.
    """
    subject = MicrosoftSubject(uuid4(), uuid4())
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(sessions.issue_session(settings, subject))
    assert rejected.value.status_code == 401
    with psycopg.connect(auth_database) as conn:
        assert conn.execute(
            "SELECT count(*) FROM identity.users WHERE tid=%s AND oid=%s",
            (subject.tid, subject.oid),
        ).fetchone() == (0,)


def test_development_session_is_not_an_operational_session(
    settings, auth_database, registered_user
):
    """개발 세션 테이블의 토큰을 운영 세션의 대체 인증으로 사용하지 않는지 확인한다.

    Args:
        settings: 플랫폼 런타임 권한의 DB 설정.
        auth_database: 합성 개발 세션을 준비할 전용 DB 관리자 연결.
        registered_user: 합성 Microsoft 식별자와 등록 사용자 ID.

    Returns:
        None: 개발 토큰을 운영 조회에서 401로 거부함을 검증한다.
    """
    _, user_id = registered_user
    token = str(uuid4())
    with psycopg.connect(auth_database) as conn:
        conn.execute(
            "INSERT INTO identity.dev_sessions(token_hash,user_id,expires_at) "
            "VALUES (%s,%s,clock_timestamp()+interval '8 hours')",
            (sha256(token.encode()).hexdigest(), user_id),
        )
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(sessions.load_session(settings, token))
    assert rejected.value.status_code == 401


def test_failed_session_replacement_rolls_back_old_session(settings, registered_user, monkeypatch):
    """새 세션 INSERT 실패 시 이전 세션 삭제도 롤백하고 성공을 반환하지 않는지 확인한다.

    Args:
        settings: 플랫폼 런타임 권한의 DB 설정.
        registered_user: 합성 Microsoft 식별자와 등록 사용자 ID.
        monkeypatch: 토큰 충돌로 실제 DB INSERT 실패를 재현하는 pytest 도구.

    Returns:
        None: DB 오류 전파와 기존 세션 보존을 검증한다.
    """
    subject, _ = registered_user
    old = asyncio.run(sessions.issue_session(settings, subject))
    other = asyncio.run(sessions.issue_session(settings, subject))

    def colliding_token(nbytes):
        """이미 사용한 토큰으로 DB 중복 오류를 재현한다.

        Args:
            nbytes: 실제 구현이 요구한 난수 바이트 수.

        Returns:
            str: 이미 저장된 다른 세션의 원문 토큰.
        """
        return other.token.get_secret_value()

    monkeypatch.setattr(sessions, "token_urlsafe", colliding_token)
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(
            sessions.issue_session(
                settings,
                subject,
                previous_token_hash=sha256(old.token.get_secret_value().encode()).hexdigest(),
            )
        )
    assert rejected.value.status_code == 503
    for grant in (old, other):
        assert (
            asyncio.run(sessions.load_session(settings, grant.token.get_secret_value()))
            == grant.user
        )


@pytest.mark.parametrize("role", ["cs_app", "tft_app"])
def test_business_services_cannot_read_operational_sessions(auth_database, role):
    """업무 서비스가 운영 세션 hash와 사용자 연결을 직접 읽을 수 없는지 확인한다.

    Args:
        auth_database: 전용 DB 관리자 연결.
        role: 세션 조회를 거부할 업무 서비스 역할.

    Returns:
        None: PostgreSQL이 실제 SELECT 권한을 거부함을 검증한다.
    """
    with psycopg.connect(auth_database) as conn:
        conn.execute(SQL("SET ROLE {}").format(Identifier(role)))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT token_hash,user_id FROM identity.sessions")


def test_public_auth_sql_checks_real_runtime_role_and_rolls_back(
    settings, auth_database, registered_user
):
    """공용 DB 점검 SQL이 플랫폼 권한으로 실제 실행되고 읽기 전용 검사를 끝내는지 확인한다.

    Args:
        settings: 전용 테스트 DB의 플랫폼 실행 계정 설정.
        auth_database: 전용 테스트 DB 관리자 연결.
        registered_user: 실제 자격증명이 없는 합성 등록 사용자.

    Returns:
        None: 정상 등록 조건·권한 검증과 트랜잭션 종료를 확인한다.
    """
    _, user_id = registered_user
    with psycopg.connect(auth_database) as conn:
        conn.execute(
            "UPDATE identity.users SET tid='9188040d-6c67-4c5b-b112-36a304b66dad'::uuid WHERE id=%s",
            (user_id,),
        )
    script = Path("infra/sql/003_check_platform_auth.sql").read_text()
    with psycopg.connect(settings.database_url.get_secret_value(), autocommit=True) as conn:
        conn.execute(script)
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        assert conn.execute("SELECT current_user").fetchone() == ("platform_app",)


@pytest.mark.parametrize("failure", ["missing_table", "missing_privilege", "unregistered"])
def test_public_auth_sql_refuses_missing_production_prerequisites(
    settings, auth_database, registered_user, failure
):
    """실제 DB의 테이블·세션 권한·등록 조건 누락을 준비 완료로 보고하지 않는지 확인한다.

    Args:
        settings: 전용 테스트 DB의 플랫폼 실행 계정 설정.
        auth_database: 전용 테스트 DB만 허용하는 관리자 연결.
        registered_user: 해당 검사에서만 쓰는 합성 사용자.
        failure: 재현할 테이블·권한·실제 등록 조건 누락.

    Returns:
        None: SQL 점검 실패와 테스트용 권한·테이블 복원을 확인한다.
    """
    _, user_id = registered_user
    with psycopg.connect(auth_database) as conn:
        assert conn.info.dbname == "kan77_auth_test"
        conn.execute(
            "UPDATE identity.users SET tid='9188040d-6c67-4c5b-b112-36a304b66dad'::uuid WHERE id=%s",
            (user_id,),
        )
    try:
        with psycopg.connect(auth_database) as conn:
            if failure == "missing_table":
                conn.execute("ALTER TABLE identity.sessions RENAME TO kan77_hidden_sessions")
            elif failure == "missing_privilege":
                conn.execute("REVOKE DELETE ON identity.sessions FROM platform_app")
            else:
                conn.execute("UPDATE identity.users SET is_active=false WHERE id=%s", (user_id,))
        with psycopg.connect(settings.database_url.get_secret_value(), autocommit=True) as conn:
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute(Path("infra/sql/003_check_platform_auth.sql").read_text())
    finally:
        with psycopg.connect(auth_database) as conn:
            if failure == "missing_table":
                conn.execute("ALTER TABLE identity.kan77_hidden_sessions RENAME TO sessions")
            elif failure == "missing_privilege":
                conn.execute("GRANT DELETE ON identity.sessions TO platform_app")
