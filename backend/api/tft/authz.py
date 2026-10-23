"""Action checks for staffing-request writes (KAN-122, permissions.md 1.3 step 3).

The caller is the `Principal` the platform relays (backend/shared/access.py, KAN-78);
project visibility (step 2) is `projects.access.sees_project`. Only the action rules live here.
"""

from typing import Any

from backend.shared.access import Principal
from backend.shared.errors import ApplicationError


def ensure_can_issue(principal: Principal, project: dict[str, Any]) -> None:
    """Only the project's assigned sales user issues department staffing requests
    (KAN-57 decision 2026-10-21: reviewed by sales, no intermediate approval)."""
    if (
        not principal.has("sales")
        or project.get("sales_user_id") is None
        or project["sales_user_id"] != principal.user_id
    ):
        raise ApplicationError(
            403, "FORBIDDEN", "Only the assigned sales user of the project may do this."
        )


def ensure_can_edit(principal: Principal, project: dict[str, Any]) -> None:
    """Editing a draft request follows the same rule as issuing it (permissions 4.2:
    요청서 작성 is `sales` within the assigned project)."""
    ensure_can_issue(principal, project)
