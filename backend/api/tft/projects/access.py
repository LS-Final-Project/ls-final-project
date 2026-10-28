"""Which TFT projects a user may see (contracts/platform/permissions.md 1.2, 4.2)."""

from fastapi import HTTPException

from backend.shared.access import Principal


def require_project_list(principal: Principal) -> None:
    # cs_agent has no TFT scope. The others may list, possibly an empty page.
    if not principal.has("sales", "dept_staff", "manager"):
        raise HTTPException(403)


def sees_project(
    principal: Principal,
    project_id: object,
    responsible_user_id: object,
    sales_user_id: object,
) -> bool:
    """A manager sees managed projects; anyone sees projects they are responsible for or
    assigned to as the sales user (KAN-122, `tft.projects.sales_user_id`).

    The dept_staff scope through staffing requests was removed with them in KAN-181.
    """
    return (
        project_id in principal.managed_projects
        or responsible_user_id == principal.user_id
        or sales_user_id == principal.user_id
    )
