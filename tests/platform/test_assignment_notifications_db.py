"""KAN-214 `cs.assigned` on a real PostgreSQL: the new owner gets exactly one notification.

Intake auto-assign (system), manager batch apply and manager reassign each enqueue one intent
in the owner-change transaction. The actor manager never receives it, an idempotent apply adds
nothing, and starting review yourself is not an assignment. Runs in the auth-database CI job.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo
from pydantic import SecretStr

from backend.api.cs.main import create_app
from backend.engine.cs.assignment import ASSIGNMENT_RULES_VERSION
from backend.shared.access import Principal, principal_header
from backend.shared.settings import Settings

MIGRATION = Path("infra/migrations/platform/20261111_KAN-214_assignment_notification.sql")
# Swahili: no other test file gives an agent this language, so OWNER is the only match.
LANGUAGE = "sw"
PRODUCT = "TRF-OIL-1000"  # TRANSFORMER
BASE = datetime(1980, 1, 1, tzinfo=UTC)


@pytest.fixture
def people(jobs_database):
    """Active identity users: two agents with skills and the C/S manager."""
    owner, other, manager = uuid4(), uuid4(), uuid4()
    roles = {owner: "cs_agent", other: "cs_agent", manager: "manager"}
    tickets: list[UUID] = []
    with psycopg.connect(jobs_database, autocommit=True) as conn:
        for user_id, role in roles.items():
            conn.execute(
                "INSERT INTO identity.users(id,tid,oid,email,display_name) VALUES (%s,%s,%s,%s,%s)",
                (user_id, uuid4(), uuid4(), f"{user_id}@example.invalid", "합성 배정 사용자"),
            )
            conn.execute(
                "INSERT INTO identity.user_roles(user_id,role) VALUES (%s,%s)", (user_id, role)
            )
        conn.execute(
            "INSERT INTO cs.agent_skills (user_id, languages, families, data_origin) "
            "VALUES (%s, %s, %s, 'synthetic')",
            (owner, [LANGUAGE], ["TRANSFORMER"]),
        )
    try:
        yield owner, other, manager, tickets
    finally:
        paths = [f"/cs/tickets/{t}" for t in tickets]
        with psycopg.connect(jobs_database, autocommit=True) as conn:
            conn.execute(
                "DELETE FROM notify.user_notifications WHERE intent_id IN "
                "(SELECT id FROM notify.notification_intents WHERE link_path = ANY(%s))",
                (paths,),
            )
            conn.execute(
                "DELETE FROM notify.notification_intents WHERE link_path = ANY(%s)", (paths,)
            )
            conn.execute("DELETE FROM cs.ticket_assignments WHERE ticket_id = ANY(%s)", (tickets,))
            conn.execute("DELETE FROM cs.audit_events WHERE entity_id = ANY(%s)", (tickets,))
            conn.execute("DELETE FROM cs.ticket_workflows WHERE ticket_id = ANY(%s)", (tickets,))
            conn.execute("DELETE FROM cs.tickets WHERE id = ANY(%s)", (tickets,))
            conn.execute("DELETE FROM cs.agent_skills WHERE user_id = %s", (owner,))
            conn.execute("DELETE FROM identity.user_roles WHERE user_id = ANY(%s)", (list(roles),))
            conn.execute("DELETE FROM identity.users WHERE id = ANY(%s)", (list(roles),))


@pytest.fixture
def client(jobs_database):
    dsn = make_conninfo(jobs_database, user="cs_app", password="test-only")
    settings = Settings(_env_file=None, environment="test", database_url=SecretStr(dsn))
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def stored_ticket(conn, tickets, minutes=0):
    ticket_id = uuid4()
    received = BASE + timedelta(minutes=minutes)
    conn.execute(
        "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
        "derivation_kind, received_at, known_at, channel, body_language, customer_external_id, "
        "product_code, body) VALUES (%s, 'test', 'kan214n', %s, 'synthetic', 'PROVIDED', %s, %s, "
        "'EMAIL', %s, 'CUST-0374', %s, 'Transformer oil leak.')",
        (ticket_id, f"N-{ticket_id}", received, received, LANGUAGE, PRODUCT),
    )
    tickets.append(ticket_id)
    return ticket_id


def recipients(conn, ticket_id):
    """(kind, user_id) of every recipient row for the ticket's assignment notifications."""
    return conn.execute(
        "SELECT i.kind, n.user_id FROM notify.notification_intents i "
        "JOIN notify.user_notifications n ON n.intent_id = i.id "
        "WHERE i.link_path = %s AND i.kind = 'cs.assigned' ORDER BY i.created_at",
        (f"/cs/tickets/{ticket_id}",),
    ).fetchall()


def intents(conn, ticket_id):
    return conn.execute(
        "SELECT title, actor_id, dedupe_key FROM notify.notification_intents "
        "WHERE link_path = %s AND kind = 'cs.assigned' ORDER BY created_at",
        (f"/cs/tickets/{ticket_id}",),
    ).fetchall()


def test_intake_auto_assign_notifies_only_the_new_owner(jobs_database, people, client):
    owner, _, _, tickets = people
    response = client.post(
        "/api/v1/cs/tickets",
        json={
            "channel": "EMAIL",
            "body_language": LANGUAGE,
            "customer_external_id": "CUST-0374",
            "product_code": PRODUCT,
            "body": "Transformer oil leak.",
        },
        headers=principal_header(Principal(user_id=uuid4(), roles={"cs_agent"})),
    )
    assert response.status_code == 201, response.text
    ticket_id = UUID(response.json()["data"]["id"])
    tickets.append(ticket_id)
    with psycopg.connect(jobs_database) as conn:
        assert conn.execute(
            "SELECT owner_user_id FROM cs.ticket_workflows WHERE ticket_id = %s", (ticket_id,)
        ).fetchone() == (owner,)
        assert recipients(conn, ticket_id) == [("cs.assigned", owner)]
        assert intents(conn, ticket_id) == [
            ("새 문의 배정", None, f"cs.assigned:{ticket_id}:{owner}:1")
        ]


def test_batch_apply_notifies_owner_not_manager_and_is_idempotent(jobs_database, people, client):
    owner, _, manager, tickets = people
    with psycopg.connect(jobs_database, autocommit=True) as conn:
        ticket_id = stored_ticket(conn, tickets)
    lead = Principal(user_id=manager, roles={"manager"}, managed_departments={"GCS_L1"})
    preview = client.post(
        "/api/v1/cs/assignments/preview", json={"limit": 200}, headers=principal_header(lead)
    )
    assert preview.status_code == 200, preview.text
    [item] = [i for i in preview.json()["data"]["items"] if i["ticket_id"] == str(ticket_id)]
    assert item["user_id"] == str(owner)
    body = {
        "rule_version": ASSIGNMENT_RULES_VERSION,
        "items": [{"ticket_id": item["ticket_id"], "user_id": item["user_id"]}],
    }
    for outcome in ("ASSIGNED", "ALREADY_ASSIGNED"):
        applied = client.post(
            "/api/v1/cs/assignments/apply", json=body, headers=principal_header(lead)
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["data"]["items"][0]["outcome"] == outcome
    with psycopg.connect(jobs_database) as conn:
        assert recipients(conn, ticket_id) == [("cs.assigned", owner)]
        assert [(t, a) for t, a, _ in intents(conn, ticket_id)] == [("새 문의 배정", manager)]


def test_reassign_notifies_new_owner_and_self_assignment_does_not(jobs_database, people, client):
    owner, other, manager, tickets = people
    lead = Principal(user_id=manager, roles={"manager"}, managed_departments={"GCS_L1"})
    with psycopg.connect(jobs_database, autocommit=True) as conn:
        ticket_id = stored_ticket(conn, tickets, minutes=1)
    # Starting review makes the agent the owner: not an assignment, nothing enqueued.
    started = client.post(
        f"/api/v1/cs/tickets/{ticket_id}/workflow/start-review",
        json={"version": 0},
        headers=principal_header(Principal(user_id=owner, roles={"cs_agent"})),
    )
    assert started.status_code == 200, started.text
    with psycopg.connect(jobs_database) as conn:
        assert intents(conn, ticket_id) == []

    def reassign(version, to):
        response = client.put(
            f"/api/v1/cs/tickets/{ticket_id}/workflow/owner",
            json={"version": version, "owner_user_id": str(to)},
            headers=principal_header(lead),
        )
        assert response.status_code == 200, response.text
        return response.json()["data"]["version"]

    version = reassign(started.json()["data"]["version"], other)
    # The manager taking it themselves enqueues nothing either.
    reassign(version, manager)
    with psycopg.connect(jobs_database) as conn:
        assert recipients(conn, ticket_id) == [("cs.assigned", other)]
        [(_, actor, key)] = intents(conn, ticket_id)
        assert (actor, key) == (manager, f"cs.assigned:{ticket_id}:{other}:{version}")


def test_database_without_the_new_kind_still_reassigns(jobs_database, people, client):
    """P35 not applied yet: the notification savepoint fails, the owner change commits."""
    owner, other, manager, tickets = people
    lead = Principal(user_id=manager, roles={"manager"}, managed_departments={"GCS_L1"})
    with psycopg.connect(jobs_database, autocommit=True) as conn:
        ticket_id = stored_ticket(conn, tickets, minutes=2)
        conn.execute(
            "ALTER TABLE notify.notification_intents DROP CONSTRAINT notification_intents_kind_check"
        )
        conn.execute(
            "ALTER TABLE notify.notification_intents ADD CONSTRAINT notification_intents_kind_check "
            "CHECK (kind IN ('cs.review_wait','cs.safety','cs.reply','mail.unknown','cs.transfer',"
            "'rfp.done','rfp.failed')) NOT VALID"
        )
    try:
        response = client.put(
            f"/api/v1/cs/tickets/{ticket_id}/workflow/owner",
            json={"version": 0, "owner_user_id": str(other)},
            headers=principal_header(lead),
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["owner_user_id"] == str(other)
        with psycopg.connect(jobs_database) as conn:
            assert intents(conn, ticket_id) == []
    finally:
        with psycopg.connect(jobs_database, autocommit=True) as conn:
            sql = MIGRATION.read_text()
            conn.execute("\n".join(x for x in sql.splitlines() if not x.startswith("\\")))
