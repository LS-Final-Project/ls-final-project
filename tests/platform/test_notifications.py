"""KAN-201 N1/N2의 삽입 경계와 세션·Origin·CSRF·본인 수신함 경계."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from backend.api.platform.auth.http import SESSION_COOKIE, csrf_token
from backend.api.platform.auth.routes import me
from backend.api.platform.auth.schemas import Identity
from backend.api.platform.notifications import repository, routes
from backend.shared.models import ResponseMeta, SuccessResponse
from backend.shared.notifications import (
    EVENTS,
    INSERT_INTENT,
    enqueue_notification,
    enqueue_notification_sync,
    intent_values,
)
from tests.platform.test_evaluation_access import client_for

USER = uuid4()
SUBJECT = uuid4()


def values(**changes):
    return {
        "kind": "cs.review_wait",
        "subject_id": SUBJECT,
        "audience": [{"user": USER}],
        "actor_id": None,
        "dedupe_key": f"cs.review_wait:{SUBJECT}",
        **changes,
    }


def test_both_writers_only_insert_into_the_provided_connection():
    sync = Mock()
    sync.execute.return_value.rowcount = 1
    assert enqueue_notification_sync(sync, **values())
    sql, parameters = sync.execute.call_args.args
    assert sql == INSERT_INTENT and "RETURNING" not in sql and "SELECT" not in sql
    assert parameters[3:5] == ("C/S 답변 확인 대기", f"/cs/tickets/{SUBJECT}")
    sync.commit.assert_not_called()
    async_conn = AsyncMock()
    async_conn.execute.return_value.rowcount = 0
    assert asyncio.run(enqueue_notification(async_conn, **values())) is False
    async_conn.commit.assert_not_called()
    assert async_conn.execute.call_args.args[0] == sql


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "external"},
        {"subject_id": "//evil.invalid"},
        {"audience": []},
        {"audience": [{"role": "manager"}]},
        {"audience": [{"role": "cs_agent", "user": USER}]},
        {"audience": [{"user": None}]},
        {"audience": [{"dept": "../unsafe"}]},
        {"dedupe_key": "wrong-prefix"},
        {"needs_review": True},
    ],
)
def test_invalid_intents_are_rejected_before_any_sql(change):
    conn = Mock()
    with pytest.raises(ValueError):
        enqueue_notification_sync(conn, **values(**change))
    conn.execute.assert_not_called()


def test_fixed_titles_safe_paths_and_scope_deduplication():
    for kind, (owner, title, prefix) in EVENTS.items():
        params = intent_values(
            **values(
                kind=kind, dedupe_key=f"{kind}:{SUBJECT}", audience=[{"user": USER}, {"user": USER}]
            )
        )
        assert params[1:5] == (owner, kind, title, prefix + str(SUBJECT))
        assert params[5].obj == [{"user": str(USER)}]
    assert (
        intent_values(
            **values(kind="rfp.done", dedupe_key=f"rfp.done:{SUBJECT}", needs_review=True)
        )[3]
        == "RFP 처리 완료, 검토 필요"
    )


def setup(monkeypatch, *, enabled=False, dev=True):
    client = client_for(enabled=enabled, dev=dev)
    user = Identity(
        id=USER,
        tid=USER,
        oid=USER,
        email="test@example.invalid",
        display_name="Test",
        dept=None,
        roles=["sales"],
    )
    client.app.dependency_overrides[me] = lambda: SuccessResponse(
        data=user, meta=ResponseMeta(request_id=USER)
    )
    conn = AsyncMock()

    @asynccontextmanager
    async def connection(settings):
        yield conn

    monkeypatch.setattr(routes, "connection", connection)
    expand = AsyncMock(return_value=1)
    listing = AsyncMock(
        return_value=[
            {
                "id": SUBJECT,
                "kind": "rfp.done",
                "title": "RFP 처리 완료",
                "link_path": f"/tft/projects/{SUBJECT}",
                "created_at": datetime.now(UTC),
                "read_at": None,
            }
        ]
    )
    count = AsyncMock(return_value=1)
    read = AsyncMock(return_value=True)
    monkeypatch.setattr(repository, "expand_pending", expand)
    monkeypatch.setattr(repository, "list_mine", listing)
    monkeypatch.setattr(repository, "unread_count", count)
    monkeypatch.setattr(repository, "mark_read", read)
    return client, conn, expand, listing, count, read


def test_my_list_count_and_read_never_take_a_caller_supplied_user(monkeypatch):
    client, conn, expand, listing, count, read = setup(monkeypatch)
    with client:
        response = client.get("/api/v1/platform/notifications")
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert set(response.json()["data"]["items"][0]) == {
            "id",
            "kind",
            "title",
            "link_path",
            "created_at",
            "read_at",
        }
        listing.assert_awaited_once_with(conn, USER, 51, 0)
        assert client.get("/api/v1/platform/notifications/unread-count").json()["data"] == {
            "count": 1
        }
        count.assert_awaited_once_with(conn, USER)
        assert expand.await_count == 2
        for _ in range(2):
            assert client.post(f"/api/v1/platform/notifications/{SUBJECT}/read").status_code == 204
        read.assert_awaited_with(conn, USER, SUBJECT)
        read.return_value = False
        assert client.post(f"/api/v1/platform/notifications/{uuid4()}/read").status_code == 404
        assert client.get(f"/api/v1/platform/notifications?user_id={uuid4()}").status_code == 400
        assert client.get("/api/v1/platform/notifications?limit=2&limit=3").status_code == 400
        assert client.get("/api/v1/platform/notifications?limit=101").status_code == 422
        assert (
            client.post(
                f"/api/v1/platform/notifications/{SUBJECT}/read",
                headers={"Origin": "https://evil.invalid"},
            ).status_code
            == 403
        )


def test_operational_mutation_reuses_actual_csrf_and_origin_guard(monkeypatch):
    client, _, _, _, _, read = setup(monkeypatch, enabled=True, dev=False)
    path = f"/api/v1/platform/notifications/{SUBJECT}/read"
    client.cookies.set(SESSION_COOKIE, "test-session")
    with client:
        for headers in (
            {},
            {"Origin": "https://app.example.invalid"},
            {"Origin": "https://evil.invalid", "X-CSRF-Token": csrf_token("test-session")},
            {"Origin": "https://app.example.invalid", "X-CSRF-Token": "wrong"},
        ):
            assert client.post(path, headers=headers).status_code == 403
        read.assert_not_awaited()
        headers = {
            "Origin": "https://app.example.invalid",
            "X-CSRF-Token": csrf_token("test-session"),
        }
        assert client.post(path, headers=headers).status_code == 204


def test_missing_session_is_rejected_before_notification_queries(monkeypatch):
    client = client_for(enabled=False, dev=False)
    query = AsyncMock()
    monkeypatch.setattr(repository, "expand_pending", query)
    with client:
        assert client.get("/api/v1/platform/notifications").status_code == 401
        assert client.get("/api/v1/platform/notifications/unread-count").status_code == 401
        assert client.post(f"/api/v1/platform/notifications/{SUBJECT}/read").status_code == 401
    query.assert_not_awaited()
