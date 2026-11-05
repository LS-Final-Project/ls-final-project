"""KAN-201 E2: 실제 분기와 저장 ID, 부가 알림 실패의 업무 격리."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from backend.api.cs.analysis import routes as analysis
from backend.api.cs.analysis.schemas import CorrectionRequest
from backend.api.cs.workflow import routes, service
from backend.api.cs.workflow.schemas import ReviewRequest, TransferRequest
from backend.shared.access import Principal

TICKET, MESSAGE, ACTOR, RESULT = (uuid4() for _ in range(4))
PRINCIPAL = Principal(user_id=ACTOR, roles={"cs_agent"})
REQUEST = NS(app=NS(state=NS(settings=None)), state=NS(request_id=uuid4()))


class Conn:
    def __init__(self, fail=False):
        self.inserts = []
        self.fail = fail

    @asynccontextmanager
    async def transaction(self):
        previous = list(self.inserts)
        try:
            yield
        except Exception:
            self.inserts = previous
            raise

    async def execute(self, sql, params):
        self.inserts.append(params)
        if self.fail:
            raise RuntimeError("private notification insert")
        return NS(rowcount=1)


def workflow(owner=ACTOR, state="IN_REVIEW"):
    return {"ticket_id": TICKET, "owner_user_id": owner, "state": state, "version": 1}


def database(monkeypatch, module, conn):
    @asynccontextmanager
    async def connection(_):
        async with conn.transaction():
            yield conn

    monkeypatch.setattr(module, "connection", connection)


@pytest.mark.parametrize("recorded", [False, True])
@pytest.mark.parametrize("reopened", [False, True])
@pytest.mark.parametrize("owner", [None, ACTOR])
def test_reply_requires_both_recorded_and_reopened(monkeypatch, recorded, reopened, owner):
    conn = Conn()
    row = workflow(owner, "WAITING_CUSTOMER" if reopened else "IN_REVIEW")
    monkeypatch.setattr(service, "lock", AsyncMock(return_value=row))
    monkeypatch.setattr(service.repository, "insert_reply", AsyncMock(return_value=recorded))
    reopen = AsyncMock(return_value=workflow(owner))
    monkeypatch.setattr(service, "reopen_for_customer_reply", reopen)
    result = asyncio.run(
        service.record_customer_reply(conn, TICKET, MESSAGE, "HEADER", datetime.now(UTC))
    )
    assert result[1:] == ((reopened, True) if recorded else (False, False))
    assert len(conn.inserts) == int(recorded and reopened)
    if conn.inserts:
        params = conn.inserts[0]
        assert params[-1] == f"cs.reply:{MESSAGE}" and params[-2] is None
        assert params[5].obj == ([{"user": str(owner)}] if owner else [{"role": "cs_agent"}])


def test_duplicate_reply_and_notification_failure_do_not_reopen_twice(monkeypatch, caplog):
    conn = Conn(fail=True)
    monkeypatch.setattr(service, "lock", AsyncMock(return_value=workflow(state="WAITING_CUSTOMER")))
    monkeypatch.setattr(service.repository, "insert_reply", AsyncMock(side_effect=[True, False]))
    reopen = AsyncMock(return_value=workflow())
    monkeypatch.setattr(service, "reopen_for_customer_reply", reopen)
    for _ in range(2):
        asyncio.run(
            service.record_customer_reply(conn, TICKET, MESSAGE, "HEADER", datetime.now(UTC))
        )
    reopen.assert_awaited_once()
    assert conn.inserts == [] and [r.getMessage() for r in caplog.records] == [
        "NOTIFICATION_INTENT_FAILED"
    ]
    assert "private" not in caplog.text


def setup_transfer(monkeypatch, conn):
    database(monkeypatch, routes, conn)
    result = {"id": RESULT, "dept_code": "TS_TRF", "review_status": "SUBMITTED", "version": 1}
    monkeypatch.setattr(routes, "begin", AsyncMock(return_value=(workflow(), [], [result])))
    monkeypatch.setattr(routes.repository, "list_sends", AsyncMock(return_value=[]))
    insert = AsyncMock()
    monkeypatch.setattr(routes.repository, "insert_transfer", insert)
    monkeypatch.setattr(routes.repository, "review_result", AsyncMock())
    monkeypatch.setattr(routes.repository, "insert_audit", AsyncMock())
    monkeypatch.setattr(routes, "save", AsyncMock(return_value=workflow()))
    monkeypatch.setattr(routes, "view", AsyncMock(return_value=workflow()))
    return insert


@pytest.mark.parametrize("path", ["transfer", "REWORK", "ACCEPT"])
@pytest.mark.parametrize("failure", [False, True])
def test_transfer_ids_match_dedupe_and_notification_failure_preserves_primary(
    monkeypatch, caplog, path, failure
):
    conn = Conn(fail=failure)
    insert = setup_transfer(monkeypatch, conn)
    if path == "transfer":
        monkeypatch.setattr(routes, "begin", AsyncMock(return_value=(workflow(), [], [])))
        asyncio.run(
            routes.transfer(
                TICKET,
                TransferRequest(version=1, dept_code="TS_TRF", reason="test"),
                REQUEST,
                PRINCIPAL,
            )
        )
    else:
        asyncio.run(
            routes.review_dept_result(
                TICKET,
                RESULT,
                ReviewRequest(version=1, result_version=1, decision=path, note="test"),
                REQUEST,
                PRINCIPAL,
            )
        )
    if path == "ACCEPT":
        insert.assert_not_awaited()
        assert conn.inserts == []
    else:
        insert.assert_awaited_once()
        if not failure:
            stored = insert.call_args.args[1]
            params = conn.inserts[0]
            assert params[-1] == f"cs.transfer:{stored['id']}"
            assert params[5].obj == [{"dept": "TS_TRF"}, {"manager_dept": "TS_TRF"}]
            assert params[-2] == ACTOR
        else:
            assert conn.inserts == []
            assert [r.getMessage() for r in caplog.records] == ["NOTIFICATION_INTENT_FAILED"]
    routes.save.assert_awaited_once()


@pytest.mark.parametrize("path", ["transfer", "REWORK"])
@pytest.mark.parametrize("failure", ["insert", "authorization", "save"])
def test_transfer_primary_errors_are_outside_notification_boundary(monkeypatch, path, failure):
    conn = Conn()
    insert = setup_transfer(monkeypatch, conn)
    if path == "transfer":
        monkeypatch.setattr(routes, "begin", AsyncMock(return_value=(workflow(), [], [])))
    if failure == "save":
        routes.save.side_effect = RuntimeError("primary transfer failure")
    else:
        insert.side_effect = RuntimeError("primary transfer failure")
    principal = (
        PRINCIPAL if failure in {"insert", "save"} else Principal(user_id=ACTOR, roles={"sales"})
    )
    with pytest.raises(RuntimeError if failure in {"insert", "save"} else HTTPException):
        if path == "transfer":
            asyncio.run(
                routes.transfer(
                    TICKET,
                    TransferRequest(version=1, dept_code="TS_TRF", reason="test"),
                    REQUEST,
                    principal,
                )
            )
        else:
            asyncio.run(
                routes.review_dept_result(
                    TICKET,
                    RESULT,
                    ReviewRequest(version=1, result_version=1, decision="REWORK", note="test"),
                    REQUEST,
                    principal,
                )
            )
    assert conn.inserts == []


@pytest.mark.parametrize(
    "before,changes,notify",
    [
        ("NONE", {"urgency_flag": "SAFETY"}, True),
        ("SAFETY", {"category_code": "TECH_SPEC"}, False),
        ("SAFETY", {"urgency_flag": "SAFETY", "category_code": "TECH_SPEC"}, False),
        ("NONE", {"urgency_flag": "SITE_DOWN"}, False),
    ],
)
@pytest.mark.parametrize("failure", [False, True])
def test_correction_only_actual_safety_transition_and_keeps_business_on_alert_failure(
    monkeypatch, caplog, before, changes, notify, failure
):
    conn = Conn(fail=failure)
    database(monkeypatch, analysis, conn)
    monkeypatch.setattr(analysis, "check_visible", AsyncMock())
    monkeypatch.setattr(analysis.tickets, "lock_ticket", AsyncMock(return_value={"id": TICKET}))
    monkeypatch.setattr(analysis.workflow, "lock_workflow", AsyncMock(return_value=workflow()))
    fields = {
        key: NS(effective_value=value)
        for key, value in {
            "category_code": "DEFECT",
            "secondary_category": None,
            "urgency_flag": before,
            "provided_info": [],
        }.items()
    }
    current = NS(
        version=0,
        latest_valid_run_id=None,
        fields=fields,
        rules=NS(
            effective=None, first_response_due=NS(effective=None), resolution_due=NS(effective=None)
        ),
    )
    monkeypatch.setattr(analysis.service, "build", AsyncMock(return_value=current))
    correction = AsyncMock()
    monkeypatch.setattr(analysis.repository, "insert_correction", correction)
    audit = AsyncMock()
    monkeypatch.setattr(analysis.workflow, "insert_audit", audit)
    asyncio.run(
        analysis.correct(
            TICKET,
            CorrectionRequest(version=0, analysis_id=None, reason="test", **changes),
            REQUEST,
            PRINCIPAL,
        )
    )
    correction.assert_awaited_once()
    audit.assert_awaited_once()
    assert len(conn.inserts) == int(notify and not failure)
    if conn.inserts:
        assert conn.inserts[0][-1] == f"cs.safety:{TICKET}"
        assert conn.inserts[0][-2] == ACTOR
    assert bool(caplog.records) == (notify and failure)
