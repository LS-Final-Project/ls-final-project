"""KAN-201 E3: UNKNOWN 실제 UPDATE·attempt dedupe·부가 알림 오류 격리."""

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from backend.api.platform.mail import send

INTENT, TICKET, APPROVER = uuid4(), uuid4(), uuid4()


class Conn:
    def __init__(self, returned=None, rows=None, reference=None, failure=None):
        self.returned = returned
        self.rows = rows or []
        self.reference = reference or f"cs_ticket:{TICKET}"
        self.failure = failure
        self.inserts = []

    @asynccontextmanager
    async def transaction(self):
        snapshot = list(self.inserts)
        try:
            yield
        except Exception:
            self.inserts = snapshot
            raise

    async def execute(self, sql, params):
        if sql.startswith("UPDATE"):
            if self.failure == "primary":
                raise RuntimeError("primary update failure")
            return NS(
                fetchone=AsyncMock(return_value=self.returned),
                fetchall=AsyncMock(return_value=self.rows),
            )
        if sql.startswith("SELECT"):
            if self.failure == "lookup":
                raise RuntimeError("private lookup failure")
            return NS(
                fetchone=AsyncMock(
                    return_value={
                        "business_ref": self.reference,
                        "approved_by": APPROVER,
                        "attempts": 2,
                    }
                )
            )
        self.inserts.append(params)
        if self.failure == "insert":
            raise RuntimeError("private insert failure")
        return NS(rowcount=1)


def setup(monkeypatch, conn):
    @asynccontextmanager
    async def connection(_):
        yield conn

    monkeypatch.setattr(send, "connection", connection)
    event = AsyncMock()
    monkeypatch.setattr(send, "add_event", event)
    return event


@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.parametrize("status", ["UNKNOWN", "ACCEPTED", "FAILED", "QUEUED"])
def test_finish_only_notifies_actual_unknown_updates(monkeypatch, changed, status):
    conn = Conn(returned={"id": INTENT} if changed else None)
    event = setup(monkeypatch, conn)
    asyncio.run(send.finish(None, INTENT, status, status, None))
    assert event.await_count == int(changed)
    assert len(conn.inserts) == int(changed and status == "UNKNOWN")
    if conn.inserts:
        params = conn.inserts[0]
        assert params[-1] == f"mail.unknown:{INTENT}:2"
        assert params[-2] is None
        assert params[5].obj == [{"user": str(APPROVER)}, {"manager_dept": "GCS_L1"}]


@pytest.mark.parametrize(
    "reference",
    [
        "other:case-1",
        "cs_ticket:TCK-1",
        "cs_ticket://evil.invalid",
        "cs_ticket:" + str(TICKET).replace("-", ""),
    ],
)
def test_non_ticket_or_noncanonical_reference_never_creates_an_unsafe_link(monkeypatch, reference):
    conn = Conn(returned={"id": INTENT}, reference=reference)
    event = setup(monkeypatch, conn)
    asyncio.run(send.finish(None, INTENT, "UNKNOWN", "UNKNOWN", None))
    event.assert_awaited_once()
    assert conn.inserts == []


@pytest.mark.parametrize("path", ["finish", "expire"])
@pytest.mark.parametrize("failure", ["lookup", "insert"])
def test_notification_lookup_or_insert_failure_keeps_actual_mail_transition(
    monkeypatch, caplog, path, failure
):
    conn = Conn(returned={"id": INTENT}, rows=[{"id": INTENT}], failure=failure)
    event = setup(monkeypatch, conn)
    if path == "finish":
        asyncio.run(send.finish(None, INTENT, "UNKNOWN", "UNKNOWN", "GRAPH_NO_RESPONSE"))
    else:
        assert asyncio.run(send.expire_submitting(None, timedelta(minutes=10))) == 1
    event.assert_awaited_once()
    assert conn.inserts == []
    assert [r.getMessage() for r in caplog.records] == ["NOTIFICATION_INTENT_FAILED"]
    assert "private" not in caplog.text


@pytest.mark.parametrize("path", ["finish", "expire"])
def test_primary_mail_update_failure_still_propagates(monkeypatch, caplog, path):
    conn = Conn(failure="primary")
    event = setup(monkeypatch, conn)
    with pytest.raises(RuntimeError, match="primary update"):
        if path == "finish":
            asyncio.run(send.finish(None, INTENT, "UNKNOWN", "UNKNOWN", None))
        else:
            asyncio.run(send.expire_submitting(None, timedelta(minutes=10)))
    event.assert_not_awaited()
    assert not caplog.records


@pytest.mark.parametrize("ids", [[], [INTENT], [INTENT, uuid4()]])
def test_expiration_uses_exactly_the_returned_ids(monkeypatch, ids):
    conn = Conn(rows=[{"id": i} for i in ids])
    event = setup(monkeypatch, conn)
    assert asyncio.run(send.expire_submitting(None, timedelta(minutes=10))) == len(ids)
    assert [call.args[1] for call in event.call_args_list] == ids
    assert [p[-1] for p in conn.inserts] == [f"mail.unknown:{i}:2" for i in ids]
