"""Which tickets a user may see and change (contracts/platform/permissions.md 1.2, 4.1)."""

from fastapi import HTTPException

from backend.shared.access import Principal
from backend.shared.departments import CS_DEPARTMENT


def sees_all_tickets(principal: Principal) -> bool:
    """cs_agent, and a manager of the C/S department, see every C/S ticket."""
    return principal.has("cs_agent") or (
        principal.has("manager") and CS_DEPARTMENT in principal.managed_departments
    )


def transfer_departments(principal: Principal) -> frozenset[str]:
    """Departments whose transferred tickets the user may read (KAN-108).

    dept_staff reads tickets ever transferred to their own department, and a manager those
    transferred to a managed department. The ticket stays readable after the transfer ends.
    """
    departments: set[str] = set()
    if principal.has("dept_staff") and principal.dept:
        departments.add(principal.dept)
    if principal.has("manager"):
        departments |= principal.managed_departments
    departments.discard(CS_DEPARTMENT)
    return frozenset(departments)


def require_ticket_list(principal: Principal) -> None:
    # sales has no C/S scope at all. The others may list, possibly an empty page.
    if not principal.has("cs_agent", "manager", "dept_staff"):
        raise HTTPException(403)


def require_ticket_write(principal: Principal) -> None:
    # Only cs_agent records and corrects tickets (4.1). Scope is checked first by the caller.
    if not principal.has("cs_agent"):
        raise HTTPException(403)
