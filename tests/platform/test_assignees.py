"""후보 조회의 역할 경계와 중계 전 대상 검증(KAN-195)."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from backend.api.platform.assignees import routes
from backend.api.platform.auth.routes import me
from backend.api.platform.auth.schemas import Identity
from backend.api.platform.main import create_app
from backend.shared.access import Principal
from backend.shared.errors import ApplicationError
from backend.shared.models import ResponseMeta, SuccessResponse
from backend.shared.settings import Settings

USER = UUID(int=195)


def test_candidate_route_role_query_and_minimal_response(monkeypatch):
    app = create_app(Settings(_env_file=None, environment="test"))
    user = Identity(
        id=USER,
        tid=USER,
        oid=USER,
        email="synthetic@example.invalid",
        display_name="관리자",
        dept=None,
        roles=["manager"],
        managed_departments=["GCS_L1"],
    )
    app.dependency_overrides[me] = lambda: SuccessResponse(
        data=user, meta=ResponseMeta(request_id=USER)
    )
    cursor = AsyncMock()
    cursor.fetchall.return_value = [{"user_id": USER, "display_name": "상담원"}]
    conn = AsyncMock()
    conn.execute.return_value = cursor

    @asynccontextmanager
    async def database(settings):
        yield conn

    monkeypatch.setattr(routes, "connection", database)
    with TestClient(app) as client:
        response = client.get("/api/v1/platform/assignees?role=cs_agent")
        assert response.status_code == 200
        assert response.json()["data"] == {
            "items": [{"user_id": str(USER), "display_name": "상담원"}]
        }
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/v1/platform/assignees?role=sales").status_code == 422
        assert (
            client.get("/api/v1/platform/assignees?role=cs_agent&role=cs_agent").status_code == 400
        )
        assert client.get("/api/v1/platform/assignees?unknown=1").status_code == 400
        for roles, scopes in [(["cs_agent"], []), (["manager"], ["TS_AUTO"])]:
            user.roles, user.managed_departments = roles, scopes
            conn.execute.reset_mock()
            assert client.get("/api/v1/platform/assignees").status_code == 403
            conn.execute.assert_not_called()


def test_owner_validation_rejects_missing_target_and_malformed_body(monkeypatch):
    import asyncio

    principal = Principal(user_id=USER, roles={"manager"}, managed_departments={"GCS_L1"})
    cursor = AsyncMock()
    cursor.fetchone.return_value = None
    conn = AsyncMock()
    conn.execute.return_value = cursor

    @asynccontextmanager
    async def database(settings):
        yield conn

    monkeypatch.setattr(routes, "connection", database)
    settings = Settings(_env_file=None, environment="test")
    for body, code in [
        (b"{}", "VALIDATION_ERROR"),
        (
            b'{"version":1,"owner_user_id":"00000000-0000-0000-0000-0000000000c3"}',
            "CS_INVALID_OWNER",
        ),
    ]:
        with pytest.raises(ApplicationError) as error:
            asyncio.run(routes.validate_owner(settings, principal, body))
        assert error.value.code == code


def test_user_names_are_read_in_one_query_and_expose_only_the_name(monkeypatch):
    """ID 묶음을 한 번에 읽는다. 로그인만 보고 이메일 같은 값은 내지 않는다."""
    app = create_app(Settings(_env_file=None, environment="test"))
    user = Identity(
        id=USER,
        tid=USER,
        oid=USER,
        email="synthetic@example.invalid",
        display_name="담당",
        dept=None,
        roles=["sales"],
    )
    app.dependency_overrides[me] = lambda: SuccessResponse(
        data=user, meta=ResponseMeta(request_id=USER)
    )
    cursor = AsyncMock()
    cursor.fetchall.return_value = [{"user_id": USER, "display_name": "담당"}]
    conn = AsyncMock()
    conn.execute.return_value = cursor

    @asynccontextmanager
    async def database(settings):
        yield conn

    monkeypatch.setattr(routes, "connection", database)
    other = UUID(int=196)
    with TestClient(app) as client:
        response = client.get(f"/api/v1/platform/users?ids={USER},{other}")
        assert response.status_code == 200
        assert response.json()["data"] == {
            "items": [{"user_id": str(USER), "display_name": "담당"}]
        }
        assert response.headers["cache-control"] == "no-store"
        assert conn.execute.await_count == 1
        assert conn.execute.await_args.args[1] == ([USER, other],)
        conn.execute.reset_mock()
        assert client.get("/api/v1/platform/users").status_code == 422
        assert client.get("/api/v1/platform/users?ids=nope").status_code == 422
        too_many = ",".join([str(USER)] * 101)
        assert client.get(f"/api/v1/platform/users?ids={too_many}").status_code == 422
        compact = ",".join([USER.hex] * 101)
        assert len(compact) <= 3699
        assert client.get(f"/api/v1/platform/users?ids={compact}").status_code == 422
        conn.execute.assert_not_awaited()
    app.dependency_overrides.clear()
    with TestClient(app) as client:
        assert client.get(f"/api/v1/platform/users?ids={USER}").status_code == 401
