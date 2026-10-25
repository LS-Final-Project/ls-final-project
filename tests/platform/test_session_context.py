"""전용 PostgreSQL에서 운영 세션·관리 범위 조회와 회수를 검증한다."""

import asyncio
from hashlib import sha256
from uuid import uuid4

import psycopg
import pytest
from fastapi import HTTPException

from backend.api.platform.auth.context import load_session_principal
from backend.api.platform.auth.sessions import issue_session, revoke_session
from backend.shared.access import PRINCIPAL_HEADER, Principal, principal_header


@pytest.fixture
def managed_session(settings, auth_database, registered_user):
    """실제 Microsoft 자격증명이 없는 manager의 운영 세션과 명시 관리 범위를 만든다.

    Args:
        settings: 전용 테스트 DB의 플랫폼 실행 설정.
        auth_database: 전용 테스트 DB 관리자 연결.
        registered_user: 종료 시 해당 사용자와 역할·세션·관리 범위를 정리하는 합성 사용자.

    Returns:
        tuple: 발급 세션·사용자 ID·정렬된 관리 프로젝트 ID 두 개.
    """
    subject, user_id = registered_user
    grant = asyncio.run(issue_session(settings, subject))
    projects = tuple(sorted((uuid4(), uuid4())))
    with psycopg.connect(auth_database) as conn:
        conn.execute("INSERT INTO identity.user_roles VALUES (%s,'manager')", (user_id,))
        conn.execute(
            "INSERT INTO identity.manager_departments VALUES (%s,'TS_TRF'),(%s,'GCS_L1')",
            (user_id, user_id),
        )
        for project in projects:
            conn.execute("INSERT INTO identity.manager_projects VALUES (%s,%s)", (user_id, project))
    return grant, user_id, projects


def test_context_reads_current_user_and_scopes_without_changing_session(
    settings, auth_database, managed_session
):
    """발급 뒤 부여된 역할·범위를 읽고 기존 세션과 공개 사용자 모델을 보존하는지 확인한다.

    Args:
        settings: 플랫폼 실행 권한의 DB 설정.
        auth_database: 세션 저장 상태를 확인할 전용 DB.
        managed_session: 합성 manager와 부서·프로젝트 범위.

    Returns:
        None: 소비자 공통 모델·직렬화 호환성, 만료 보존과 불필요한 사용자 정보 제외를 검증한다.
    """
    grant, user_id, projects = managed_session
    token = grant.token.get_secret_value()
    current = asyncio.run(load_session_principal(settings, token))
    assert current.user_id == user_id
    assert current.roles == {"cs_agent", "manager"}
    assert current.managed_departments == {"GCS_L1", "TS_TRF"}
    assert current.managed_projects == set(projects)
    raw = principal_header(current)[PRINCIPAL_HEADER]
    assert Principal.model_validate_json(raw) == current
    assert set(current.model_dump()) == {
        "user_id",
        "roles",
        "dept",
        "managed_departments",
        "managed_projects",
    }
    for private in (
        token,
        grant.user.email,
        grant.user.display_name,
        str(grant.user.tid),
        str(grant.user.oid),
    ):
        assert private not in raw
    with psycopg.connect(auth_database) as conn:
        assert conn.execute(
            "SELECT token_hash,expires_at FROM identity.sessions WHERE user_id=%s", (user_id,)
        ).fetchall() == [(sha256(token.encode()).hexdigest(), grant.expires_at)]


def test_context_reflects_department_role_and_scope_changes(
    settings, auth_database, managed_session
):
    """같은 세션으로 역할·소속 부서 변경과 일부 관리 범위 회수를 다음 조회에 반영한다.

    Args:
        settings: 플랫폼 실행 권한의 DB 설정.
        auth_database: 사용자·범위를 변경하는 전용 DB.
        managed_session: 합성 manager와 기존 관리 범위.

    Returns:
        None: 새 역할·부서와 남은 관리 범위만 반환함을 검증한다.
    """
    grant, user_id, projects = managed_session
    with psycopg.connect(auth_database) as conn:
        conn.execute("UPDATE identity.users SET dept='TS_TRF' WHERE id=%s", (user_id,))
        conn.execute("INSERT INTO identity.user_roles VALUES (%s,'dept_staff')", (user_id,))
        conn.execute(
            "DELETE FROM identity.manager_departments WHERE user_id=%s AND dept_code='TS_TRF'",
            (user_id,),
        )
        conn.execute(
            "DELETE FROM identity.manager_projects WHERE user_id=%s AND project_id=%s",
            (user_id, projects[0]),
        )
    current = asyncio.run(load_session_principal(settings, grant.token.get_secret_value()))
    assert current.roles == {"cs_agent", "dept_staff", "manager"}
    assert current.dept == "TS_TRF"
    assert current.managed_departments == {"GCS_L1"}
    assert current.managed_projects == {projects[1]}


@pytest.mark.parametrize("change", ["no_scopes", "manager_removed"])
def test_missing_grants_or_removed_manager_never_restore_scope(
    settings, auth_database, managed_session, change
):
    """관리 범위가 없거나 manager 역할만 회수돼도 전체 권한으로 확장하지 않는지 확인한다.

    Args:
        settings: 플랫폼 실행 권한의 DB 설정.
        auth_database: 역할 또는 관리 범위를 회수하는 전용 DB.
        managed_session: 관리 범위가 있는 합성 사용자.
        change: 모든 범위 회수 또는 범위 행을 남긴 manager 역할 회수.

    Returns:
        None: 범위 없는 manager의 빈 집합과 역할·범위 불일치의 401 거부를 검증한다.
    """
    grant, user_id, _ = managed_session
    with psycopg.connect(auth_database) as conn:
        if change == "no_scopes":
            conn.execute("DELETE FROM identity.manager_departments WHERE user_id=%s", (user_id,))
            conn.execute("DELETE FROM identity.manager_projects WHERE user_id=%s", (user_id,))
        else:
            conn.execute(
                "DELETE FROM identity.user_roles WHERE user_id=%s AND role='manager'", (user_id,)
            )
    if change == "manager_removed":
        with pytest.raises(HTTPException) as rejected:
            asyncio.run(load_session_principal(settings, grant.token.get_secret_value()))
        assert rejected.value.status_code == 401
    else:
        current = asyncio.run(load_session_principal(settings, grant.token.get_secret_value()))
        assert current.managed_departments == frozenset()
        assert current.managed_projects == frozenset()
        assert "manager" in current.roles


@pytest.mark.parametrize(
    "failure",
    ["missing", "unknown", "expired", "revoked", "inactive", "no_roles", "missing_dept", "dev"],
)
def test_context_rejects_invalid_session_or_registration(
    settings, auth_database, managed_session, failure
):
    """만료·철회·등록 불충족과 개발 세션을 운영 사용자 맥락으로 받지 않는지 확인한다.

    Args:
        settings: 플랫폼 실행 권한의 DB 설정.
        auth_database: 합성 세션·등록 상태를 바꾸는 전용 DB.
        managed_session: 기존 운영 세션과 관리 범위.
        failure: 재현할 세션·등록 거부 조건.

    Returns:
        None: 잘못된 조건에서 모두 401로 거부함을 검증한다.
    """
    grant, user_id, _ = managed_session
    token = grant.token.get_secret_value()
    if failure == "missing":
        token = None
    elif failure == "unknown":
        token = "unknown-session"
    elif failure == "revoked":
        asyncio.run(revoke_session(settings, token))
    else:
        with psycopg.connect(auth_database) as conn:
            if failure == "expired":
                conn.execute(
                    "UPDATE identity.sessions SET created_at=clock_timestamp()-interval '9 hours', "
                    "expires_at=clock_timestamp()-interval '1 second' WHERE user_id=%s",
                    (user_id,),
                )
            elif failure == "inactive":
                conn.execute("UPDATE identity.users SET is_active=false WHERE id=%s", (user_id,))
            elif failure == "no_roles":
                conn.execute("DELETE FROM identity.user_roles WHERE user_id=%s", (user_id,))
            elif failure == "missing_dept":
                conn.execute("INSERT INTO identity.user_roles VALUES (%s,'dept_staff')", (user_id,))
                conn.execute("UPDATE identity.users SET dept=NULL WHERE id=%s", (user_id,))
            else:
                conn.execute("DELETE FROM identity.sessions WHERE user_id=%s", (user_id,))
                conn.execute(
                    "INSERT INTO identity.dev_sessions(token_hash,user_id,expires_at) "
                    "VALUES (%s,%s,clock_timestamp()+interval '1 hour')",
                    (sha256(token.encode()).hexdigest(), user_id),
                )
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(load_session_principal(settings, token))
    assert rejected.value.status_code == 401


@pytest.mark.parametrize("failure", ["missing_table", "missing_privilege"])
def test_scope_database_failure_is_unavailable_instead_of_empty_success(
    settings, auth_database, managed_session, failure
):
    """관리 범위 조회 실패를 빈 범위나 인증 성공으로 숨기지 않는지 실제 DB에서 확인한다.

    Args:
        settings: 플랫폼 실행 권한의 DB 설정.
        auth_database: 전용 테스트 DB에만 장애를 재현하는 관리자 연결.
        managed_session: 정상 세션을 가진 합성 manager.
        failure: 관리 범위 테이블 또는 조회 권한 누락.

    Returns:
        None: 503 전파·원문 비노출과 테스트용 테이블·권한 복원을 검증한다.
    """
    grant, user_id, _ = managed_session
    token = grant.token.get_secret_value()
    try:
        with psycopg.connect(auth_database) as conn:
            assert conn.info.dbname == "kan77_auth_test"
            if failure == "missing_table":
                conn.execute(
                    "ALTER TABLE identity.manager_projects RENAME TO kan178_hidden_projects"
                )
            else:
                conn.execute("REVOKE SELECT ON identity.manager_departments FROM platform_app")
        with pytest.raises(HTTPException) as rejected:
            asyncio.run(load_session_principal(settings, token))
        assert rejected.value.status_code == 503
        assert token not in str(rejected.value) and str(user_id) not in str(rejected.value)
    finally:
        with psycopg.connect(auth_database) as conn:
            if failure == "missing_table":
                conn.execute(
                    "ALTER TABLE identity.kan178_hidden_projects RENAME TO manager_projects"
                )
            else:
                conn.execute("GRANT SELECT ON identity.manager_departments TO platform_app")
