"""Retry guards, with only persistence and the platform boundary replaced."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.api.cs.workflow import mail_routes
from backend.api.cs.workflow.mail import unsettled
from backend.api.cs.workflow.schemas import Action
from backend.shared.access import Principal
from backend.shared.errors import ApplicationError


@pytest.fixture
def retry_context(monkeypatch):
    ticket_id, send_id = uuid4(), uuid4()
    row = {
        "id": send_id,
        "ticket_id": ticket_id,
        "workflow_version": 1,
        "request_status": "REQUEST_FAILED",
        "request_error": "PLATFORM_UNREACHABLE",
        "send_status": None,
    }
    rows = [row]
    transaction = {"open": False}

    @asynccontextmanager
    async def connection(settings):
        transaction["open"] = True
        yield object()
        transaction["open"] = False

    async def update(conn, row_id, values):
        assert transaction["open"] and row_id == send_id
        row.update(values)

    monkeypatch.setattr(mail_routes, "connection", connection)
    monkeypatch.setattr(mail_routes, "platform_mail", lambda request: object())
    monkeypatch.setattr(
        mail_routes, "begin", AsyncMock(return_value=({"version": 3, "state": "IN_REVIEW"}, [], []))
    )
    monkeypatch.setattr(mail_routes.repository, "lock_send", AsyncMock(return_value=row))
    monkeypatch.setattr(mail_routes.repository, "list_sends", AsyncMock(return_value=rows))
    monkeypatch.setattr(mail_routes.repository, "update_send", update)
    monkeypatch.setattr(mail_routes, "current", AsyncMock(return_value="response"))
    submit = AsyncMock()
    monkeypatch.setattr(mail_routes.sends, "submit", submit)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=object())))
    principal = Principal(user_id=uuid4(), roles={"cs_agent"})

    def retry():
        return asyncio.run(
            mail_routes.retry_send(ticket_id, send_id, Action(version=3), request, principal)
        )

    return SimpleNamespace(retry=retry, row=row, rows=rows, submit=submit, transaction=transaction)


@pytest.mark.parametrize("status", ["QUEUED", "FAILED", "ACCEPTED"])
def test_replaced_approval_cannot_be_retried(retry_context, status):
    context = retry_context
    context.rows.append(
        {
            "id": uuid4(),
            "workflow_version": 2,
            "request_status": "REGISTERED",
            "send_status": status,
        }
    )
    with pytest.raises(ApplicationError) as error:
        context.retry()
    assert (error.value.status_code, error.value.code) == (409, "CS_CUSTOMER_SEND_NOT_RETRYABLE")
    context.submit.assert_not_awaited()


def test_retry_blocks_new_approval_before_calling_platform(retry_context):
    context = retry_context

    async def submit(request, client, send):
        assert not context.transaction["open"]
        assert unsettled(context.row), "A concurrent new approval must see a pending send"
        assert context.row["request_error"] is None

    context.submit.side_effect = submit
    assert context.retry() == "response"
