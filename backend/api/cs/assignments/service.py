"""Applies the assignment rules (backend/engine/cs/assignment.py) to stored tickets.

The owner is written through the workflow `save` (owner_user_id plus an audit event); the
decision itself goes to cs.ticket_assignments. Every write locks the ticket row first.
"""

import logging
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection

from backend.api.cs.analysis import service as analysis
from backend.api.cs.assignments import repository
from backend.api.cs.workflow.service import lock, save
from backend.engine.cs.assignment import Agent, Decision, TicketInput, decide
from backend.engine.cs.rules import RuleInputError, product_family
from backend.shared.database import connection
from backend.shared.settings import Settings

Row = dict[str, Any]
logger = logging.getLogger(__name__)


def family_of(product_code: str) -> str | None:
    """product_master family, or None for a product the table does not know."""
    try:
        return product_family(product_code)
    except RuleInputError:
        return None


def agents_from(rows: list[Row]) -> list[Agent]:
    return [
        Agent(
            user_id=r["user_id"],
            languages=frozenset(r["languages"]),
            families=frozenset(r["families"]),
            active=r["active"],
            open_count=r["open_count"],
        )
        for r in rows
    ]


def ticket_input(row: Row, priority: str | None = None) -> TicketInput:
    return TicketInput(
        ticket_id=row["id"],
        language=row["body_language"],
        family=family_of(row["product_code"]),
        received_at=row["received_at"],
        priority=priority,
    )


async def priority_of(conn: AsyncConnection[Row], ticket_id: UUID) -> str | None:
    """Rule priority of the effective (corrected) analysis; None before any valid analysis."""
    view = await analysis.build(conn, ticket_id)
    return view.rules.effective.priority if view.rules.effective else None


async def record(
    conn: AsyncConnection[Row],
    workflow: Row,
    decision: Decision,
    trigger: str,
    actor: UUID | None,
) -> Row:
    """Sets the owner the decision names and stores why. The ticket is locked.

    NO_MATCH writes nothing: the ticket stays unassigned and the preview shows the reason.
    """
    if decision.user_id is None:
        return workflow
    assignment_id = uuid4()
    await repository.insert_assignment(
        conn,
        {
            "id": assignment_id,
            "ticket_id": decision.ticket_id,
            "user_id": decision.user_id,
            "rule_version": decision.rule_version,
            "reason_code": decision.reason,
            "matched_language": decision.matched_language,
            "matched_family": decision.matched_family,
            "candidate_count": decision.candidate_count,
            "chosen_open_count": decision.chosen_open_count,
            "trigger": trigger,
            "decided_by": actor,
        },
    )
    return await save(
        conn,
        workflow,
        actor,
        "auto_assign",
        {"owner_user_id": decision.user_id},
        assignment_id=assignment_id,
        reason_code=decision.reason,
        rule_version=decision.rule_version,
        trigger=trigger,
    )


async def assign_on_intake(settings: Settings, ticket_id: UUID) -> None:
    """Assigns a just-created ticket on its own connection, after the ticket is committed.

    Never raises: a failure is logged with a fixed code and the ticket stays unassigned for
    the manager batch (contracts/cs/assignment.md).
    """
    try:
        async with connection(settings) as conn:
            workflow = await lock(conn, ticket_id)
            if workflow["state"] != "RECEIVED" or workflow["owner_user_id"] is not None:
                return
            rows = await repository.tickets_by_ids(conn, [ticket_id])
            agent_rows = await repository.list_agents(conn)
            agents = agents_from(agent_rows)
            loads = {a.user_id: a.open_count for a in agents}
            decision = decide(ticket_input(rows[0]), agents, loads)
            await record(conn, workflow, decision, "INTAKE", None)
    except Exception:  # noqa: BLE001 - 배정은 접수의 보조 단계라 접수를 실패로 바꾸지 않는다.
        logger.warning(
            "cs auto assignment failed",
            extra={"code": "CS_AUTO_ASSIGN_FAILED", "ticket_id": str(ticket_id)},
        )
