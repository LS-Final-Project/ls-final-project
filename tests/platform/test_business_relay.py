"""실제 PostgreSQL·세 서비스 앱·RSA 검증으로 운영 업무 중계와 우회 차단을 확인한다."""

import asyncio
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import psycopg
import pytest
import requests
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from psycopg.sql import SQL, Identifier
from pydantic import SecretStr

from backend.api.cs.main import create_app as create_cs
from backend.api.platform.auth.http import CSRF_HEADER, SESSION_COOKIE, csrf_token
from backend.api.platform.auth.sessions import issue_session, revoke_session
from backend.api.platform.auth.settings import CALLBACK_PATH, MicrosoftAuthSettings
from backend.api.platform.main import create_app
from backend.api.tft.main import create_app as create_tft
from backend.shared.access import PRINCIPAL_HEADER, Principal, principal_header
from backend.shared.service_auth import CALLER_HEADER
from backend.shared.settings import Settings

ORIGIN = "https://app.example.invalid"
CS = "https://cs.example.invalid"
TFT = "https://tft.example.invalid"
OPTIONS = {
    "environment": "production",
    "business_relay_enabled": True,
    "business_cs_url": CS,
    "business_tft_url": TFT,
    "business_platform_email": "platform-runtime@synthetic-project.iam.gserviceaccount.com",
}


@pytest.fixture
def business_database(auth_database):
    """전용 검사 DB에 업무 서비스의 실제 마이그레이션과 독립 런타임 권한을 적용한다.

    Args:
        auth_database: 이름이 검증된 전용 검사 DB 관리자 연결.

    Returns:
        str: identity와 업무 스키마를 함께 검사할 전용 DB 연결.
    """
    with psycopg.connect(auth_database, autocommit=True) as conn:
        assert conn.info.dbname == "kan77_auth_test"
        for service in ("cs", "tft"):
            migrator = service + "_migrator"
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (migrator,)).fetchone():
                conn.execute(SQL("CREATE ROLE {} NOLOGIN").format(Identifier(migrator)))
            conn.execute(
                SQL("ALTER ROLE {} LOGIN PASSWORD 'test-only'").format(Identifier(service + "_app"))
            )
            conn.execute(
                SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}").format(
                    Identifier(service), Identifier(migrator)
                )
            )
            conn.execute(
                SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    Identifier(service), Identifier(service + "_app")
                )
            )
            conn.execute(
                SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO {}"
                ).format(Identifier(migrator), Identifier(service), Identifier(service + "_app"))
            )
        for filename in (
            "cs/20261022_KAN-102_tickets.sql",
            "cs/20261023_KAN-102_ticket_audit.sql",
            "cs/20261027_KAN-102_installed_units.sql",
            "cs/20261027_KAN-108_ticket_workflow.sql",
            "cs/20261028_KAN-108_resolution_cases.sql",
            "tft/20261023_KAN-119_projects.sql",
            "tft/20261023_KAN-122_staffing_requests.sql",
            "tft/20261025_KAN-123_recommendation_commands.sql",
        ):
            migration = Path("infra/migrations", filename).read_text()
            conn.execute(
                "\n".join(line for line in migration.splitlines() if not line.startswith("\\"))
            )
    return auth_database


@pytest.fixture
def system(settings, business_database, registered_user, google_tokens, monkeypatch):
    """네트워크만 대체한 운영 모드 플랫폼·C/S·TFT 앱과 실제 세션을 연결한다.

    Args:
        settings: platform_app DB 연결.
        business_database: 업무 마이그레이션을 적용한 전용 DB.
        registered_user: 종료 시 정리할 합성 사용자.
        google_tokens: 실제 RSA 검증용 합성 Google 토큰 발급기.
        monkeypatch: 메타데이터·서비스 간 HTTP 통신 대체 도구.

    Returns:
        dict: 앱 클라이언트·호출 기록·세션·사용자·관리 범위와 쓰기 요청 헤더.
    """
    subject, user_id = registered_user
    projects = (uuid4(), uuid4())
    with psycopg.connect(business_database) as conn:
        conn.execute("INSERT INTO identity.user_roles VALUES (%s,'manager')", (user_id,))
        conn.execute("INSERT INTO identity.manager_departments VALUES (%s,'GCS_L1')", (user_id,))
        conn.execute("INSERT INTO identity.manager_projects VALUES (%s,%s)", (user_id, projects[0]))
        for project_id in projects:
            conn.execute(
                "INSERT INTO tft.projects(id,source,source_instance,data_origin,derivation_kind,known_at,company_code,name) VALUES (%s,'synthetic','KAN-178','synthetic','SYSTEM',now(),'TEST','합성 프로젝트')",
                (project_id,),
            )
    configured = Settings(_env_file=None, **(settings.model_dump() | OPTIONS))
    auth = MicrosoftAuthSettings(
        _env_file=None,
        enabled=True,
        client_id=UUID(int=1),
        client_secret=SecretStr("synthetic-secret"),
        app_origin=ORIGIN,
        redirect_uri=ORIGIN + CALLBACK_PATH,
    )
    platform = create_app(configured, auth)
    services = {}
    for origin, service, factory in ((CS, "cs", create_cs), (TFT, "tft", create_tft)):
        service_settings = Settings(
            _env_file=None,
            **(
                OPTIONS
                | {
                    "cs_manual_data_origin": "demo",
                    "database_url": SecretStr(
                        make_conninfo(
                            business_database, user=service + "_app", password="test-only"
                        )
                    ),
                }
            ),
        )
        services[origin] = TestClient(factory(service_settings), base_url=origin)
    calls = []

    def bridge(method, url, *, headers, data, **options):
        """실제 HTTP 소켓 대신 대상 서비스 앱을 호출하고 requests 응답 경계를 흉내 낸다.

        Args:
            method: 플랫폼이 허용한 HTTP 메서드.
            url: 고정 업무 서비스 주소와 경로·query.
            headers: 플랫폼에서 새로 만든 인증·사용자 헤더.
            data: 크기 검사를 통과한 본문.
            **options: 시간 제한·스트리밍·리디렉션 설정.

        Returns:
            Mock: 실제 서비스 앱 응답을 읽는 requests 스트림 경계.
        """
        parsed = urlsplit(url)
        origin = parsed.scheme + "://" + parsed.netloc
        calls.append((method, url, headers, data, options))
        response = services[origin].request(
            method,
            parsed.path + ("?" + parsed.query if parsed.query else ""),
            headers=headers,
            content=data,
        )
        upstream = Mock(status_code=response.status_code, headers=response.headers)
        upstream.__enter__ = Mock(return_value=upstream)
        upstream.__exit__ = Mock(return_value=False)
        upstream.iter_content = Mock(return_value=iter([response.content]))
        return upstream

    monkeypatch.setattr(requests, "request", bridge)
    platform.state.business_token_provider = google_tokens
    grant = asyncio.run(issue_session(configured, subject))
    token = grant.token.get_secret_value()
    client = TestClient(platform, base_url=ORIGIN)
    client.cookies.set(SESSION_COOKIE, token)
    try:
        yield {
            "client": client,
            "services": services,
            "calls": calls,
            "token": token,
            "settings": configured,
            "user_id": user_id,
            "projects": projects,
            "write": {"Origin": ORIGIN, CSRF_HEADER: csrf_token(token)},
        }
    finally:
        client.close()
        for service_client in services.values():
            service_client.close()
        with psycopg.connect(business_database) as conn:
            ticket_ids = [
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT entity_id FROM cs.audit_events WHERE actor_user_id=%s",
                    (user_id,),
                )
            ]
            conn.execute("DELETE FROM cs.audit_events WHERE actor_user_id=%s", (user_id,))
            conn.execute("DELETE FROM cs.tickets WHERE id=ANY(%s)", (ticket_ids,))
            conn.execute("DELETE FROM tft.projects WHERE id=ANY(%s)", (list(projects),))


@pytest.mark.parametrize("manual_origin", ["demo", "real"])
def test_real_cs_create_update_audit_and_user_header_replacement(system, manual_origin):
    """C/S 생성·수정·감사 기록에서 세션 사용자만 사용하고 비밀 헤더를 전달하지 않는다.

    Args:
        system: 실제 DB·세 서비스 앱과 합성 네트워크 경계.
        manual_origin: production 인증 정책과 독립적으로 지정한 서버의 수동 문의 출처.

    Returns:
        None: 현재 C/S API 상태·본문·판본·감사 작성자와 위조 제거를 확인한다.
    """
    client = system["client"]
    cs_app = system["services"][CS].app
    cs_app.state.settings = Settings(
        _env_file=None,
        **(cs_app.state.settings.model_dump() | {"cs_manual_data_origin": manual_origin}),
    )
    forged = Principal(user_id=uuid4(), roles={"sales"})
    headers = (
        system["write"]
        | principal_header(forged)
        | {
            CALLER_HEADER: "forged",
            "Authorization": "Bearer browser-secret",
            "X-Request-ID": str(uuid4()),
        }
    )
    result = client.post(
        "/api/v1/cs/tickets",
        headers=headers,
        json={
            "channel": "WEB_FORM",
            "body_language": "ko",
            "customer_external_id": "synthetic",
            "product_code": "TEST",
            "body": "합성 문의",
            "subject": "검사",
        },
    )
    assert result.status_code == 201, result.text
    ticket = result.json()["data"]
    assert ticket["data_origin"] == manual_origin
    ticket_path = "/api/v1/cs/tickets/" + ticket["id"]
    updated = client.patch(
        ticket_path, headers=system["write"], json={"version": 1, "subject": "수정된 합성 제목"}
    )
    assert updated.status_code == 200 and updated.json()["data"]["version"] == 2
    conflict = client.patch(
        ticket_path, headers=system["write"], json={"version": 1, "subject": "충돌"}
    )
    assert conflict.status_code == 409
    stored = client.get(ticket_path).json()["data"]
    assert stored["subject"] == "수정된 합성 제목" and stored["data_origin"] == manual_origin
    audit = client.get(ticket_path + "/audit").json()["data"]["items"]
    assert len(audit) == 2
    assert {event["actor_user_id"] for event in audit} == {str(system["user_id"])}
    outgoing = system["calls"][0][2]
    current = Principal.model_validate_json(outgoing[PRINCIPAL_HEADER])
    assert current.user_id == system["user_id"] and current.roles == {"cs_agent", "manager"}
    assert outgoing[CALLER_HEADER] != "forged"
    assert outgoing["Authorization"] == "Bearer " + outgoing[CALLER_HEADER]
    assert "Cookie" not in outgoing and system["token"] not in str(outgoing)
    assert outgoing["X-Request-ID"] == str(result.json()["meta"]["request_id"])
    assert result.headers["cache-control"] == "no-store"
    assert "set-cookie" not in result.headers
    listing = client.get("/api/v1/cs/tickets?limit=1")
    assert listing.status_code == 200 and len(listing.json()["data"]["items"]) == 1


def test_tft_scope_is_enforced_and_revoked_on_next_request(system, business_database):
    """프로젝트 관리 범위를 업무 API에서 다시 검사하고 같은 세션의 범위 회수를 반영한다.

    Args:
        system: 실제 세션과 프로젝트 두 개가 있는 업무 앱.
        business_database: 합성 관리 범위를 회수할 전용 DB.

    Returns:
        None: 허용 프로젝트만 보이고 범위 회수 직후 404가 됨을 확인한다.
    """
    client = system["client"]
    visible, hidden = system["projects"]
    listing = client.get("/api/v1/tft/projects?limit=10")
    assert listing.status_code == 200
    assert [p["id"] for p in listing.json()["data"]["items"]] == [str(visible)]
    assert client.get(f"/api/v1/tft/projects/{visible}").status_code == 200
    assert client.get(f"/api/v1/tft/projects/{hidden}").status_code == 404
    with psycopg.connect(business_database) as conn:
        conn.execute("DELETE FROM identity.manager_projects WHERE user_id=%s", (system["user_id"],))
    assert client.get(f"/api/v1/tft/projects/{visible}").status_code == 404
    assert client.get("/api/v1/tft/projects").json()["data"]["items"] == []


def test_role_revoke_and_session_logout_stop_next_business_request(system, business_database):
    """현재 역할과 세션 폐기를 매 요청 확인하고 클라이언트 역할을 복원에 쓰지 않는다.

    Args:
        system: 기존 cs_agent·manager 세션이 있는 업무 앱.
        business_database: 합성 역할을 회수할 전용 DB.

    Returns:
        None: 업무 역할 거부 403과 세션 철회 401이 즉시 적용됨을 확인한다.
    """
    with psycopg.connect(business_database) as conn:
        conn.execute(
            "DELETE FROM identity.manager_departments WHERE user_id=%s", (system["user_id"],)
        )
        conn.execute("DELETE FROM identity.manager_projects WHERE user_id=%s", (system["user_id"],))
        conn.execute("DELETE FROM identity.user_roles WHERE user_id=%s", (system["user_id"],))
        conn.execute("INSERT INTO identity.user_roles VALUES (%s,'sales')", (system["user_id"],))
    assert system["client"].get("/api/v1/cs/tickets").status_code == 403
    before = len(system["calls"])
    asyncio.run(revoke_session(system["settings"], system["token"]))
    assert system["client"].get("/api/v1/cs/tickets").status_code == 401
    assert len(system["calls"]) == before


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "csrf",
        "origin",
        "no_origin",
        "content",
        "size",
        "query",
        "duplicate",
        "unknown",
        "method",
    ],
)
def test_rejects_bad_browser_requests_before_metadata_or_forwarding(system, failure):
    """세션·Origin·CSRF·입력 제한 위반을 업무 호출 전에 차단한다.

    Args:
        system: 실제 세션과 중계 호출 기록.
        failure: 재현할 입력 또는 인증 위반.

    Returns:
        None: 거부 상태와 실제 업무 서비스 미호출을 확인한다.
    """
    client = system["client"]
    headers = system["write"] | {"Content-Type": "application/json"}
    path, method, body, expected = "/api/v1/cs/tickets", "POST", b"{}", 403
    if failure == "missing":
        client.cookies.clear()
        expected = 401
    elif failure == "csrf":
        headers[CSRF_HEADER] = "wrong"
    elif failure == "origin":
        headers["Origin"] = "https://evil.invalid"
    elif failure == "no_origin":
        del headers["Origin"]
    elif failure == "content":
        headers["Content-Type"] = "text/plain"
        expected = 415
    elif failure == "size":
        body, expected = b"x" * (1024 * 1024 + 1), 413
    elif failure in {"query", "duplicate"}:
        path += "?target=https://evil.invalid" if failure == "query" else "?limit=1&limit=2"
        method, body, expected = "GET", None, 400
    elif failure == "unknown":
        path, method, body, expected = "/api/v1/tft/projects/unknown", "GET", None, 404
    else:
        method, expected = "PATCH", 405
    result = client.request(method, path, headers=headers, content=body)
    assert result.status_code == expected, result.text
    assert not system["calls"]
    assert result.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "failure", ["missing", "forged", "audience", "email", "expired", "duplicate"]
)
def test_service_rejects_direct_or_wrong_platform_caller(system, google_tokens, failure):
    """업무 서비스 직접 호출은 사용자 헤더가 유효해도 올바른 플랫폼 증명 없이는 거부한다.

    Args:
        system: production과 중계 활성 설정의 실제 C/S 앱.
        google_tokens: 실제 RSA 서명 토큰 발급기.
        failure: 호출자 증명 누락·변조·다른 대상·계정·만료·중복.

    Returns:
        None: 업무 DB에 접근하기 전에 모두 401로 거부함을 확인한다.
    """
    principal = Principal(user_id=system["user_id"], roles={"cs_agent"})
    headers = list(principal_header(principal).items())
    token = google_tokens(CS)
    if failure == "forged":
        token = "not-a-google-token"
    elif failure == "audience":
        token = google_tokens(TFT)
    elif failure == "email":
        token = google_tokens(CS, email="web-runtime@synthetic-project.iam.gserviceaccount.com")
    elif failure == "expired":
        token = google_tokens(CS, exp=1)
    if failure != "missing":
        headers.append((CALLER_HEADER, token))
    if failure == "duplicate":
        headers.append((CALLER_HEADER, token))
    assert system["services"][CS].get("/api/v1/cs/tickets", headers=headers).status_code == 401


@pytest.mark.parametrize("failure", ["expired", "inactive", "no_roles", "db_failure"])
def test_platform_session_failure_never_reaches_business_service(
    system, business_database, failure
):
    """세션·사용자·DB 실패가 업무 호출 전에 차단되고 빈 권한 성공으로 바뀌지 않는다.

    Args:
        system: 정상 운영 세션과 업무 호출 기록.
        business_database: 검사 사용자 상태 또는 DB 권한을 변경할 전용 연결.
        failure: 만료·비활성·무역할·DB 조회 실패 조건.

    Returns:
        None: 인증 실패는 401, DB 장애는 503이며 업무 서비스 미호출을 확인한다.
    """
    try:
        with psycopg.connect(business_database) as conn:
            if failure == "expired":
                conn.execute(
                    "UPDATE identity.sessions SET created_at=now()-interval '9 hours', expires_at=now()-interval '1 second' WHERE user_id=%s",
                    (system["user_id"],),
                )
            elif failure == "inactive":
                conn.execute(
                    "UPDATE identity.users SET is_active=false WHERE id=%s", (system["user_id"],)
                )
            elif failure == "no_roles":
                conn.execute(
                    "DELETE FROM identity.user_roles WHERE user_id=%s", (system["user_id"],)
                )
            else:
                conn.execute("REVOKE SELECT ON identity.manager_projects FROM platform_app")
        result = system["client"].get("/api/v1/cs/tickets")
        assert result.status_code == (503 if failure == "db_failure" else 401)
        assert not system["calls"]
        assert system["token"] not in result.text
    finally:
        if failure == "db_failure":
            with psycopg.connect(business_database) as conn:
                conn.execute("GRANT SELECT ON identity.manager_projects TO platform_app")


@pytest.mark.parametrize("context", ["missing", "unknown_role", "extra", "duplicate", "oversize"])
def test_verified_caller_still_requires_valid_user_context(system, google_tokens, context):
    """플랫폼 실행 계정 인증만으로 사용자를 생략하거나 임의 역할·필드를 전달할 수 없다.

    Args:
        system: 호출자 검증이 활성화된 실제 업무 서비스.
        google_tokens: 합성 Google 서명 토큰 발급기.
        context: 사용자 맥락의 누락·허용 역할·추가 필드·중복·크기 위반.

    Returns:
        None: 호출자 검증 뒤 사용자 맥락 검증도 401로 거부함을 확인한다.
    """
    raw = Principal(user_id=system["user_id"], roles={"cs_agent"}).model_dump_json()
    if context == "unknown_role":
        raw = raw.replace("cs_agent", "admin")
    elif context == "extra":
        raw = raw[:-1] + ',"approved":true}'
    elif context == "oversize":
        raw = " " * 8193
    headers = [(CALLER_HEADER, google_tokens(CS))]
    if context != "missing":
        headers.append((PRINCIPAL_HEADER, raw))
    if context == "duplicate":
        headers.append((PRINCIPAL_HEADER, raw))
    assert system["services"][CS].get("/api/v1/cs/tickets", headers=headers).status_code == 401


def test_staffing_migration_preserves_legacy_missing_due_and_enforces_new_writes(business_database):
    """Upgrade is repeatable without inventing a legacy sales decision."""
    project, role, org, request, actor = [uuid4() for _ in range(5)]
    source = Path("infra/migrations/tft/20261023_KAN-122_staffing_requests.sql").read_text()
    migration = "\n".join(
        line
        for line in source.splitlines()
        if not line.startswith("\\") and line not in {"BEGIN;", "COMMIT;"}
    )
    with psycopg.connect(business_database, autocommit=True) as conn:
        with conn.transaction(force_rollback=True):
            conn.execute(
                "ALTER TABLE tft.staffing_requests DROP CONSTRAINT staffing_requests_issued_due"
            )
            conn.execute(
                "INSERT INTO tft.projects(id,source,source_instance,data_origin,derivation_kind,known_at,company_code,name) VALUES (%s,'test','test','synthetic','ASSUMED',now(),'TEST','Migration fixture')",
                (project,),
            )
            conn.execute(
                "INSERT INTO tft.organizations(id,source,source_instance,data_origin,derivation_kind,company_code,name) VALUES (%s,'test','test','synthetic','ASSUMED','TEST','Migration fixture')",
                (org,),
            )
            conn.execute(
                "INSERT INTO tft.required_roles(id,project_id,data_origin,derivation_kind,role_code,title) VALUES (%s,%s,'synthetic','ASSUMED','TEST','Migration fixture')",
                (role, project),
            )
            conn.execute(
                "INSERT INTO tft.staffing_requests(id,project_id,required_role_id,organization_id,data_origin,derivation_kind,status,issued_by_user_id,issued_at) VALUES (%s,%s,%s,%s,'synthetic','ASSUMED','ISSUED',%s,now())",
                (request, project, role, org, actor),
            )
            for _ in range(2):
                conn.execute(migration)
            assert conn.execute(
                "SELECT due_at,due_undecided FROM tft.staffing_requests WHERE id=%s", (request,)
            ).fetchone() == (None, False)
            assert conn.execute(
                "SELECT convalidated FROM pg_constraint WHERE conrelid='tft.staffing_requests'::regclass AND conname='staffing_requests_issued_due'"
            ).fetchone() == (False,)
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute(
                        "UPDATE tft.staffing_requests SET message='still missing due' WHERE id=%s",
                        (request,),
                    )
            conn.execute(
                "UPDATE tft.staffing_requests SET due_at='2026-11-01T00:00:00Z' WHERE id=%s",
                (request,),
            )
            conn.execute(migration)
            assert conn.execute(
                "SELECT convalidated FROM pg_constraint WHERE conrelid='tft.staffing_requests'::regclass AND conname='staffing_requests_issued_due'"
            ).fetchone() == (True,)
