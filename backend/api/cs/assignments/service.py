"""Applies the assignment rules (backend/engine/cs/assignment.py) to stored tickets.

The owner is written through the workflow `save` (owner_user_id plus an audit event); the
decision itself goes to cs.ticket_assignments. Every write locks the ticket row first.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg import AsyncConnection

from backend.api.cs.analysis import service as analysis
from backend.api.cs.assignments import repository
from backend.api.cs.workflow.service import lock, notify_assigned, save
from backend.engine.cs.assignment import (
    Agent,
    Decision,
    RequestDecision,
    RequestInput,
    TicketInput,
    decide,
    decide_request,
)
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
    """Sets the owner the decision names, stores why and notifies the new owner
    (`cs.assigned`, same transaction). The ticket is locked.

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
    saved = await save(
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
    await notify_assigned(conn, saved, actor)
    return saved


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


# --- Improvement requests (cs_owner) ------------------------------------------------------


@dataclass
class RequestAssigner:
    """Assigns improvement requests one by one inside the caller's transaction.

    Built once per run so a batch of candidates spreads the load as it goes. When the skill
    table cannot be read (C29 missing, any failure) it assigns nobody and records nothing, so
    saving candidates never fails because of assignment.
    """

    agents: list[Agent]
    loads: dict[UUID, int]
    enabled: bool

    @classmethod
    async def load(cls, conn: AsyncConnection[Row]) -> "RequestAssigner":
        try:
            # Savepoint: a failed read must not abort the caller's transaction.
            async with conn.transaction():
                agents = agents_from(await repository.list_agents(conn))
                loads = await repository.request_loads(conn)
        except Exception:  # noqa: BLE001 - 배정 실패로 후보 저장을 막지 않는다.
            logger.warning(
                "cs request assignment unavailable", extra={"code": "CS_REQUEST_ASSIGN_FAILED"}
            )
            return cls([], {}, False)
        return cls(agents, loads, True)

    def decide(
        self, request_id: UUID, product_code: str, created_at: datetime
    ) -> RequestDecision | None:
        if not self.enabled:
            return None
        return decide_request(
            RequestInput(request_id, family_of(product_code), created_at), self.agents, self.loads
        )

    def take(self, decision: RequestDecision) -> None:
        """Counts the decided owner's new request so the next decision sees it."""
        if decision.user_id is not None:
            self.loads[decision.user_id] = self.loads.get(decision.user_id, 0) + 1

    async def record(
        self,
        conn: AsyncConnection[Row],
        decision: RequestDecision,
        trigger: str,
        actor: UUID | None,
    ) -> None:
        """Stores the decision, NO_MATCH included."""
        await repository.insert_request_assignment(
            conn,
            {
                "id": uuid4(),
                "request_id": decision.request_id,
                "user_id": decision.user_id,
                "rule_version": decision.rule_version,
                "reason_code": decision.reason,
                "matched_family": decision.matched_family,
                "candidate_count": decision.candidate_count,
                "chosen_open_count": decision.chosen_open_count,
                "trigger": trigger,
                "decided_by": actor,
            },
        )
