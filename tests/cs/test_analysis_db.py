"""KAN-107 analysis results and human corrections on a real PostgreSQL.

Analysis and draft rows are synthetic and stored through record_analysis / record_draft, the
same functions the job runner will call once KAN-69 is connected.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.api.cs.analysis.service import record_analysis, record_draft
from backend.api.cs.main import create_app
from backend.engine.cs.analysis import Deadline, effective_fields, merge_deadline
from backend.shared.access import Principal, principal_header
from backend.shared.database import connection
from backend.shared.settings import Settings
from tests.cs.test_workflow_db import AGENT, TRF_STAFF, act, code, ok

RECEIVED = datetime(2026, 10, 1, 9, tzinfo=UTC)
MANAGER = Principal(user_id=uuid4(), roles={"manager"}, managed_departments={"GCS_L1"})
AI = {
    "category_code": "DEFECT",
    "secondary_category": None,
    "urgency_flag": "SAFETY",
    "provided_info": ["serial_number"],
    "return_subtype": None,
}


@pytest.fixture(scope="module")
def settings(cs_database):
    return Settings(
        _env_file=None,
        environment="test",
        database_url=SecretStr(make_conninfo(cs_database, user="cs_app", password="test-only")),
    )


@pytest.fixture(scope="module")
def client(settings):
    with TestClient(create_app(settings), headers=principal_header(AGENT)) as test_client:
        yield test_client


@pytest.fixture
def ticket(admin):
    """A provided ticket with a GOLD customer, received 2026-10-01 09:00 UTC."""
    ticket_id = uuid4()
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, body) VALUES (%s, 'test', 'kan107', %s, 'synthetic', 'PROVIDED', %s, %s, "
        "'EMAIL', 'en', 'CUST-0374', 'TRF-OIL-1000', 'Oil leakage at gasket.')",
        (ticket_id, f"T-{ticket_id}", RECEIVED, RECEIVED),
    )
    admin.execute(
        "INSERT INTO cs.ticket_source_records (ticket_id, data_origin, customer_tier) "
        "VALUES (%s, 'synthetic', 'GOLD')",
        (ticket_id,),
    )
    return ticket_id


def store(settings, ticket_id, classification=AI, outcome="VALID", **extra):
    async def run():
        async with connection(settings) as conn:
            return await record_analysis(
                conn,
                ticket_id,
                {
                    "outcome": outcome,
                    "classification": classification if outcome == "VALID" else None,
                    "input_version": "cs-input-v1",
                    "input_sha256": "a" * 64,
                    "model_version": "synthetic",
                    "prompt_version": "v2",
                    **extra,
                },
            )

    return asyncio.run(run())


def analysis(client, ticket_id, user=AGENT):
    return ok(
        client.get(f"/api/v1/cs/tickets/{ticket_id}/analysis", headers=principal_header(user))
    )


def correct(client, ticket_id, user=AGENT, **body):
    body.setdefault("reason", "Customer confirmed by phone")
    return client.post(
        f"/api/v1/cs/tickets/{ticket_id}/analysis/corrections",
        json=body,
        headers=principal_header(user),
    )


def same_time(value, hours):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) == RECEIVED + timedelta(hours=hours)


# --- pure rules (no database) ---------------------------------------------------------------


def test_effective_values_take_the_latest_correction_per_field():
    ai = {**AI, "urgency_flag": "NONE"}
    values = effective_fields(ai, [{"urgency_flag": "SAFETY"}, {"urgency_flag": "SITE_DOWN"}])
    assert (values["urgency_flag"].effective_value, values["urgency_flag"].source) == (
        "SITE_DOWN",
        "human_input",
    )
    assert (values["category_code"].effective_value, values["category_code"].source) == (
        "DEFECT",
        "ai_draft",
    )
    assert effective_fields(None, [])["category_code"].source is None


def test_the_effective_deadline_is_the_earlier_one_and_ties_keep_the_ai_deadline():
    early, late = RECEIVED, RECEIVED + timedelta(hours=1)
    assert merge_deadline(late, early) == Deadline(late, early, early, "human_input")
    assert merge_deadline(early, late) == Deadline(early, late, early, "ai_draft")
    assert merge_deadline(early, early).source == "ai_draft"
    assert merge_deadline(early, None) == Deadline(early, None, early, "ai_draft")
    assert merge_deadline(None, early).source == "human_input"


# --- API ------------------------------------------------------------------------------------


def test_analysis_shows_ai_values_and_ai_rules_before_any_correction(client, settings, ticket):
    empty = analysis(client, ticket)
    assert (empty["latest_run"], empty["version"], empty["rules"]["ai_error"]) == (
        None,
        0,
        "CS_ANALYSIS_MISSING",
    )
    run = store(settings, ticket)
    data = analysis(client, ticket)
    assert data["latest_valid_run_id"] == str(run["id"]) and data["runs"][0]["job_id"] is None
    assert data["fields"]["urgency_flag"] == {
        "ai_value": "SAFETY",
        "corrected_value": None,
        "effective_value": "SAFETY",
        "source": "ai_draft",
        "corrected": False,
    }
    rules = data["rules"]
    assert (rules["ai"]["priority"], rules["ai"]["sla_id"]) == ("P1", "SLA-DEFECT-GOLD-P1")
    assert rules["effective"] == rules["ai"]
    assert same_time(rules["first_response_due"]["effective"], 2)
    assert rules["first_response_due"]["corrected"] is None
    assert rules["first_response_due"]["source"] == "ai_draft"
    # Department users who see the ticket see the analysis as well; others get 404.
    assert (
        client.get(
            f"/api/v1/cs/tickets/{ticket}/analysis", headers=principal_header(TRF_STAFF)
        ).status_code
        == 404
    )


def test_raising_urgency_pulls_deadlines_in_and_lowering_keeps_the_ai_deadline(
    client, settings, admin, ticket
):
    run = store(settings, ticket, {**AI, "urgency_flag": "NONE"})
    assert analysis(client, ticket)["rules"]["ai"]["priority"] == "P3"
    assert code(
        correct(client, ticket, version=0, analysis_id=str(run["id"]), urgency_flag="SAFETY")
    ) == (
        409,
        "CS_INVALID_TRANSITION",
    )
    ok(act(client, ticket, "start-review", version=0))

    raised = ok(
        correct(client, ticket, version=0, analysis_id=str(run["id"]), urgency_flag="SAFETY")
    )
    rules = raised["rules"]
    assert (rules["ai"]["priority"], rules["effective"]["priority"]) == ("P3", "P1")
    # P3 gives 16 h for the first response and P1 2 h, both from the receipt time.
    first = rules["first_response_due"]
    assert same_time(first["ai"], 16) and same_time(first["corrected"], 2)
    assert same_time(first["effective"], 2) and first["source"] == "human_input"
    assert same_time(rules["resolution_due"]["effective"], 48)
    assert raised["fields"]["urgency_flag"]["source"] == "human_input"
    [stored] = raised["corrections"]
    assert (stored["seq"], stored["fields"], stored["based_on_analysis_id"]) == (
        1,
        {"urgency_flag": "SAFETY"},
        str(run["id"]),
    )

    # Back to NONE: priority follows (P3) but the deadline never goes past the AI one.
    lowered = ok(
        correct(client, ticket, version=1, analysis_id=str(run["id"]), urgency_flag="NONE")
    )
    assert lowered["rules"]["effective"]["priority"] == "P3"
    assert same_time(lowered["rules"]["first_response_due"]["effective"], 16)
    assert lowered["rules"]["first_response_due"]["source"] == "ai_draft"

    events = admin.execute(
        "SELECT version, actor_user_id, changes FROM cs.audit_events "
        "WHERE entity_type='ticket_analysis' AND entity_id=%s ORDER BY version",
        (ticket,),
    ).fetchall()
    assert [(v, a) for v, a, _ in events] == [(1, AGENT.user_id), (2, AGENT.user_id)]
    first_change = events[0][2]
    assert first_change["urgency_flag"] == {"from": "NONE", "to": "SAFETY"}
    assert first_change["priority"] == {"from": "P3", "to": "P1"}
    assert first_change["provenance_kind"] == {"to": "human_input"}
    assert first_change["first_response_due"]["to"].startswith("2026-10-01T11:00:00")


def test_a_new_ai_run_does_not_replace_a_correction(client, settings, ticket):
    run = store(settings, ticket)
    ok(act(client, ticket, "start-review", version=0))
    ok(
        correct(
            client,
            ticket,
            version=0,
            analysis_id=str(run["id"]),
            category_code="INSTALL",
            secondary_category="DEFECT",
        )
    )
    newer = store(settings, ticket, {**AI, "category_code": "WARRANTY"})
    failed = store(settings, ticket, outcome="TRANSPORT_FAILURE", error_code="CS_TIMEOUT")
    data = analysis(client, ticket)
    assert data["latest_run"]["id"] == str(failed["id"])
    # The latest VALID run is the newer one; a failed run does not hide it.
    assert data["latest_valid_run_id"] == str(newer["id"])
    category = data["fields"]["category_code"]
    assert (category["ai_value"], category["effective_value"], category["source"]) == (
        "WARRANTY",
        "INSTALL",
        "human_input",
    )
    # A correction based on the older run is stale now.
    stale = correct(client, ticket, version=1, analysis_id=str(run["id"]), urgency_flag="NONE")
    assert code(stale) == (409, "CS_ANALYSIS_STALE")
    assert code(
        correct(client, ticket, version=0, analysis_id=str(newer["id"]), urgency_flag="NONE")
    ) == (409, "CS_ANALYSIS_VERSION_CONFLICT")


def test_correction_permissions_inputs_and_closing(client, settings, admin, ticket):
    run = store(settings, ticket)
    ok(act(client, ticket, "start-review", version=0))
    rid = str(run["id"])
    assert (
        correct(
            client, ticket, MANAGER, version=0, analysis_id=rid, urgency_flag="NONE"
        ).status_code
        == 403
    )
    assert (
        correct(
            client, ticket, TRF_STAFF, version=0, analysis_id=rid, urgency_flag="NONE"
        ).status_code
        == 404
    )
    assert code(correct(client, ticket, version=0, analysis_id=rid, urgency_flag="SAFETY")) == (
        422,
        "CS_CORRECTION_EMPTY",
    )
    assert code(correct(client, ticket, version=0, analysis_id=rid, category_code=None)) == (
        422,
        "CS_CORRECTION_INVALID",
    )
    assert code(
        correct(client, ticket, version=0, analysis_id=rid, secondary_category="DEFECT")
    ) == (422, "CS_CORRECTION_INVALID")
    # The return subtype has its own confirmation path (KAN-193).
    assert (
        correct(client, ticket, version=0, analysis_id=rid, return_subtype="DEFECT").status_code
        == 422
    )
    assert correct(client, ticket, version=0, analysis_id=rid, reason="").status_code == 422
    # Same set in another order is not a change.
    assert code(
        correct(client, ticket, version=0, analysis_id=rid, provided_info=["serial_number"])
    ) == (422, "CS_CORRECTION_EMPTY")
    data = ok(
        correct(
            client,
            ticket,
            version=0,
            analysis_id=rid,
            provided_info=["site_photo", "serial_number", "site_photo"],
        )
    )
    assert data["fields"]["provided_info"]["effective_value"] == ["serial_number", "site_photo"]
    # Closed tickets refuse corrections.
    admin.execute(
        "UPDATE cs.ticket_workflows SET state='CLOSED', closed_at=now(), closed_by=%s "
        "WHERE ticket_id=%s",
        (AGENT.user_id, ticket),
    )
    assert code(correct(client, ticket, version=1, analysis_id=rid, urgency_flag="NONE")) == (
        409,
        "CS_INVALID_TRANSITION",
    )


def draft_options():
    """Three valid options, one per label (DraftOutput)."""
    return {
        "options": [
            {
                "label": label,
                "sentences": [
                    {
                        "text": "We replaced the gasket.",
                        "kind": "EVIDENCE",
                        "evidence_chunk_ids": ["chunk-1"],
                    }
                ],
                "info_requests": [],
            }
            for label in ("CONCISE", "DETAILED", "INFO_FIRST")
        ]
    }


def test_drafts_are_stored_as_ai_runs_and_shown_with_the_analysis(client, settings, admin, ticket):
    run = store(settings, ticket)
    draft = draft_options()

    async def save(**values):
        async with connection(settings) as conn:
            return await record_draft(
                conn,
                ticket,
                {"input_version": "cs-draft-input-v1", "analysis_id": run["id"], **values},
            )

    stored = asyncio.run(save(outcome="VALID", draft=draft))
    # A draft the engine schema refuses is not stored (two options only).
    with pytest.raises(ValueError):
        asyncio.run(save(outcome="VALID", draft={"options": draft["options"][:2]}))
    with pytest.raises(ValueError):
        store(settings, ticket, {**AI, "urgency_flag": "URGENT"})
    # A draft cannot name another ticket's analysis run as its source.
    other = uuid4()
    admin.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, body) VALUES (%s, 'test', 'kan107', %s, 'synthetic', 'PROVIDED', %s, %s, "
        "'EMAIL', 'en', 'CUST-0375', 'TRF-OIL-1000', 'Other ticket.')",
        (other, f"T-{other}", RECEIVED, RECEIVED),
    )
    other_run = store(settings, other)
    with pytest.raises(ValueError, match="CS_DRAFT_ANALYSIS_MISMATCH"):
        asyncio.run(save(outcome="VALID", draft=draft, analysis_id=other_run["id"]))
    asyncio.run(save(outcome="SCHEMA_VIOLATION", error_code="CS_DRAFT_INVALID"))
    data = analysis(client, ticket)
    assert [d["seq"] for d in data["drafts"]] == [1, 2]
    assert data["latest_valid_draft_id"] == str(stored["id"])
    assert data["drafts"][0]["draft"] == draft and data["drafts"][1]["draft"] is None


def test_a_manual_ticket_without_a_tier_has_no_rules_but_still_shows_values(
    client, settings, admin, ticket
):
    admin.execute("DELETE FROM cs.ticket_source_records WHERE ticket_id=%s", (ticket,))
    store(settings, ticket)
    data = analysis(client, ticket)
    assert (data["rules"]["ai"], data["rules"]["ai_error"]) == (None, "CS_CUSTOMER_TIER_UNKNOWN")
    assert data["fields"]["category_code"]["effective_value"] == "DEFECT"


def test_rows_are_append_only_for_the_service(settings, cs_database, ticket):
    run = store(settings, ticket)
    with psycopg.connect(make_conninfo(cs_database, user="cs_app", password="test-only")) as conn:
        for sql in (
            "UPDATE cs.ticket_analyses SET outcome='HELD' WHERE id=%s",
            "DELETE FROM cs.ticket_analyses WHERE id=%s",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql, (run["id"],))
            conn.rollback()
        for table in ("ticket_drafts", "ticket_classification_corrections"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(f"DELETE FROM cs.{table} WHERE ticket_id=%s", (ticket,))
            conn.rollback()
