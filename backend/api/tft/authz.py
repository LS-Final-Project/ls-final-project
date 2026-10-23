"""Actor identification and action checks for TFT writes (KAN-122, temporary).

The platform will relay the verified session user to the business services (KAN-78,
contracts/platform/permissions.md 1.3). That relay does not exist yet, so the actor is
read from a debug header in `local` and `test` only; production never reads it. Routes
call only the functions below, so the KAN-78 common code replaces this file alone.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import Request

from backend.shared.errors import ApplicationError

# Temporary. Replaced by the platform relay of the verified session (KAN-78).
ACTOR_HEADER = "X-Debug-User-Id"


@dataclass(frozen=True)
class Actor:
    user_id: UUID


def resolve_actor(request: Request) -> Actor:
    """Returns the acting login user or raises 401 AUTH_REQUIRED.

    Only `local` and `test` read the debug header. In production no actor can be
    resolved until KAN-78, so every write is refused as unauthenticated.
    """
    if request.app.state.settings.environment in {"local", "test"}:
        raw = request.headers.get(ACTOR_HEADER)
        if raw:
            try:
                return Actor(user_id=UUID(raw))
            except ValueError:
                pass
    raise ApplicationError(401, "AUTH_REQUIRED", "Authentication is required.")


def ensure_can_issue(actor: Actor, project: dict[str, Any]) -> None:
    """Only the project's responsible sales user issues department staffing requests
    (KAN-57 decision 2026-10-21: reviewed by sales, no intermediate approval)."""
    if project.get("sales_user_id") is None or project["sales_user_id"] != actor.user_id:
        raise ApplicationError(
            403, "FORBIDDEN", "Only the responsible sales user of the project may do this."
        )


def ensure_can_edit(actor: Actor, project: dict[str, Any]) -> None:
    """Editing a draft request follows the same scope as issuing it (permissions 4.2:
    요청서 작성 is `sales` within the responsible project)."""
    ensure_can_issue(actor, project)
