"""Which tickets a user may see and change (contracts/platform/permissions.md 1.2, 4.1)."""

from fastapi import HTTPException

from backend.shared.access import CS_DEPARTMENT, Principal


def sees_all_tickets(principal: Principal) -> bool:
    """cs_agent, and a manager of the C/S department, see every C/S ticket.

    dept_staff and managers of other departments see only tickets transferred to their
    departments. Tickets have no transfer department until KAN-108, so for now those users
    see none.
    """
    return principal.has("cs_agent") or (
        principal.has("manager") and CS_DEPARTMENT in principal.managed_departments
    )


def require_ticket_list(principal: Principal) -> None:
    # sales has no C/S scope at all. The others may list, possibly an empty page.
    if not principal.has("cs_agent", "manager", "dept_staff"):
        raise HTTPException(403)


def require_ticket_write(principal: Principal) -> None:
    # Only cs_agent records and corrects tickets (4.1). Scope is checked first by the caller.
    if not principal.has("cs_agent"):
        raise HTTPException(403)
