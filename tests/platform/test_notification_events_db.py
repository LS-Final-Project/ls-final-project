"""KAN-201 E2/E3 실제 CS/platform 역할·업무 TX·알림 SAVEPOINT. 전용 DB는 CI에서 실행한다."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from pydantic import SecretStr

from backend.api.cs.analysis import routes as analysis_routes
from backend.api.cs.analysis.service import record_analysis
from backend.api.cs.main import create_app
from backend.api.cs.workflow import service
from backend.api.platform.mail import send
from backend.shared.settings import Settings
from tests.cs.test_workflow_db import AGENT, TRF_STAFF, act, ok
from tests.platform.test_mail_send_db import request as mail_request
from tests.platform.test_mail_send_db import send_database as send_database
from tests.platform.test_mail_send_db import sender as sender
from tests.platform.test_mail_sync_db import mail_database as mail_database
from tests.platform.test_mail_sync_db import mailbox as mailbox


@pytest.fixture(autouse=True)
def notification_schema(jobs_database, request):
    request.getfixturevalue("send_database")


@pytest.fixture
def ticket_client(jobs_database):
    ticket = uuid4()
    with psycopg.connect(jobs_database) as conn:
        conn.execute(
            "INSERT INTO cs.tickets(id,source,source_instance,external_id,data_origin,derivation_kind,received_at,known_at,channel,body_language,customer_external_id,product_code,body) VALUES(%s,'test','kan201',%s,'synthetic','PROVIDED',now(),now(),'EMAIL','en','CUST-TEST','TRF-OIL-1000','Synthetic notification ticket')",
            (ticket, str(ticket)),
        )
        conn.execute(
            "INSERT INTO cs.ticket_workflows(ticket_id,state,owner_user_id) VALUES(%s,'IN_REVIEW',%s)",
            (ticket, AGENT.user_id),
        )
    dsn = make_conninfo(jobs_database, user="cs_app", password="test-only")
    settings = Settings(_env_file=None, environment="test", database_url=SecretStr(dsn))
    with TestClient(create_app(settings)) as client:
        yield ticket, client, dsn


@pytest.mark.parametrize("failure", [False, True])
def test_real_reply_savepoint_and_idempotency_as_cs_app(
    jobs_database, ticket_client, monkeypatch, failure
):
    ticket, _, dsn = ticket_client
    message = uuid4()
    with psycopg.connect(jobs_database) as conn:
        conn.execute(
            "UPDATE cs.ticket_workflows SET state='WAITING_CUSTOMER' WHERE ticket_id=%s", (ticket,)
        )
    if failure:

        async def broken(conn, **values):
            await conn.execute("INSERT INTO notify.notification_intents(id) VALUES(%s)", (uuid4(),))

        monkeypatch.setattr(service, "enqueue_notification", broken)

    async def run():
        async with await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
            result = await service.record_customer_reply(
                conn, ticket, message, "HEADER", datetime.now(UTC)
            )
            assert result[1:] == (True, True) and result[0]["state"] == "IN_REVIEW"
        async with await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
            assert (
                await service.record_customer_reply(
                    conn, ticket, message, "HEADER", datetime.now(UTC)
                )
            )[1:] == (False, False)

    asyncio.run(run())
    with psycopg.connect(jobs_database) as conn:
        assert conn.execute(
            "SELECT state FROM cs.ticket_workflows WHERE ticket_id=%s", (ticket,)
        ).fetchone() == ("IN_REVIEW",)
        assert conn.execute(
            "SELECT count(*) FROM notify.notification_intents WHERE dedupe_key=%s",
            (f"cs.reply:{message}",),
        ).fetchone() == (int(not failure),)


def test_real_transfer_and_rework_match_stored_transfer_ids(jobs_database, ticket_client):
    ticket, client, _ = ticket_client
    transferred = ok(act(client, ticket, "transfer", version=1, dept_code="TS_TRF", reason="test"))
    result = ok(act(client, ticket, "dept-result", TRF_STAFF, version=2, result="test result"))[
        "dept_results"
    ][0]
    reworked = ok(
        act(
            client,
            ticket,
            f"dept-results/{result['id']}/review",
            version=3,
            result_version=result["version"],
            decision="REWORK",
            note="recheck",
        )
    )
    ids = [t["id"] for t in reworked["transfers"]]
    assert len(ids) == 2 and transferred["transfers"][0]["id"] == ids[0]
    assert (
        act(client, ticket, "transfer", version=1, dept_code="TS_TRF", reason="stale").status_code
        == 409
    )
    with psycopg.connect(jobs_database) as conn:
        keys = conn.execute(
            "SELECT dedupe_key FROM notify.notification_intents WHERE link_path=%s AND kind='cs.transfer'",
            (f"/cs/tickets/{ticket}",),
        ).fetchall()
        assert {k[0] for k in keys} == {f"cs.transfer:{value}" for value in ids}


@pytest.mark.parametrize("failure", [False, True])
def test_real_safety_correction_only_actual_transition(
    jobs_database, ticket_client, monkeypatch, failure
):
    from unittest.mock import AsyncMock

    from tests.cs.test_analysis_db import correct

    ticket, client, dsn = ticket_client

    async def add_run():
        async with await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
            return await record_analysis(
                conn,
                ticket,
                {
                    "outcome": "VALID",
                    "classification": {
                        "category_code": "DEFECT",
                        "secondary_category": None,
                        "urgency_flag": "NONE",
                        "provided_info": [],
                        "return_subtype": None,
                    },
                    "input_version": "synthetic-v1",
                },
            )

    run = asyncio.run(add_run())
    if failure:

        async def broken(conn, **values):
            await conn.execute("INSERT INTO notify.notification_intents(id) VALUES(%s)", (uuid4(),))

        monkeypatch.setattr(service, "enqueue_notification", broken)
    spy = AsyncMock(wraps=analysis_routes.notify_cs)
    monkeypatch.setattr(analysis_routes, "notify_cs", spy)
    first = correct(client, ticket, version=0, analysis_id=str(run["id"]), urgency_flag="SAFETY")
    assert (
        first.status_code == 200
        and first.json()["data"]["fields"]["urgency_flag"]["effective_value"] == "SAFETY"
    )
    assert (
        correct(
            client,
            ticket,
            version=1,
            analysis_id=str(run["id"]),
            category_code="TECH_SPEC",
            urgency_flag="SAFETY",
        ).status_code
        == 200
    )
    spy.assert_awaited_once()
    with psycopg.connect(jobs_database) as conn:
        assert conn.execute(
            "SELECT count(*) FROM cs.ticket_classification_corrections WHERE ticket_id=%s",
            (ticket,),
        ).fetchone() == (2,)
        assert conn.execute(
            "SELECT count(*) FROM notify.notification_intents WHERE dedupe_key=%s",
            (f"cs.safety:{ticket}",),
        ).fetchone() == (int(not failure),)


def test_real_mail_unknown_same_attempt_is_idempotent_and_expiry_uses_new_attempt(
    settings, request, jobs_database
):
    sender = request.getfixturevalue("sender")
    intent = asyncio.run(
        send.create_intent(settings, mail_request(sender, business_ref=f"cs_ticket:{uuid4()}"))
    )
    asyncio.run(send.claim(settings, intent))
    asyncio.run(send.finish(settings, intent, "UNKNOWN", "UNKNOWN", "GRAPH_NO_RESPONSE"))
    asyncio.run(send.finish(settings, intent, "UNKNOWN", "UNKNOWN", "GRAPH_NO_RESPONSE"))
    with psycopg.connect(jobs_database) as conn:
        assert conn.execute(
            "SELECT count(*) FROM notify.notification_intents WHERE dedupe_key=%s",
            (f"mail.unknown:{intent}:1",),
        ).fetchone() == (1,)
        assert conn.execute(
            "SELECT count(*) FROM integration.mail_send_events WHERE intent_id=%s AND event='UNKNOWN'",
            (intent,),
        ).fetchone() == (1,)
        conn.execute(
            "UPDATE integration.mail_send_intents SET status='SUBMITTING',attempts=2,submitted_at=now()-interval '20 minutes' WHERE id=%s",
            (intent,),
        )
    asyncio.run(send.expire_submitting(settings, timedelta(minutes=10)))
    asyncio.run(send.expire_submitting(settings, timedelta(minutes=10)))
    with psycopg.connect(jobs_database) as conn:
        assert conn.execute(
            "SELECT count(*) FROM notify.notification_intents WHERE dedupe_key=%s",
            (f"mail.unknown:{intent}:2",),
        ).fetchone() == (1,)


@pytest.mark.parametrize("path", ["finish", "expire"])
def test_real_mail_insert_failure_preserves_unknown_update_and_event(
    settings, request, jobs_database, monkeypatch, path
):
    sender = request.getfixturevalue("sender")
    intent = asyncio.run(
        send.create_intent(settings, mail_request(sender, business_ref=f"cs_ticket:{uuid4()}"))
    )
    asyncio.run(send.claim(settings, intent))

    async def broken(conn, **values):
        await conn.execute("INSERT INTO notify.notification_intents(id) VALUES(%s)", (uuid4(),))

    monkeypatch.setattr(send, "enqueue_notification", broken)
    if path == "finish":
        asyncio.run(send.finish(settings, intent, "UNKNOWN", "UNKNOWN", "GRAPH_NO_RESPONSE"))
    else:
        with psycopg.connect(jobs_database) as conn:
            conn.execute(
                "UPDATE integration.mail_send_intents SET submitted_at=now()-interval '20 minutes' WHERE id=%s",
                (intent,),
            )
        asyncio.run(send.expire_submitting(settings, timedelta(minutes=10)))
    with psycopg.connect(jobs_database) as conn:
        assert conn.execute(
            "SELECT status FROM integration.mail_send_intents WHERE id=%s", (intent,)
        ).fetchone() == ("UNKNOWN",)
        assert conn.execute(
            "SELECT count(*) FROM integration.mail_send_events WHERE intent_id=%s AND event='UNKNOWN'",
            (intent,),
        ).fetchone() == (1,)
        assert conn.execute(
            "SELECT count(*) FROM notify.notification_intents WHERE dedupe_key=%s",
            (f"mail.unknown:{intent}:1",),
        ).fetchone() == (0,)
