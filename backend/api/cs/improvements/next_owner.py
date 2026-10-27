"""Who acts next on an improvement request (permissions.md 2.2, 2.2.1).

The request state alone does not say who must act: in one round a department may have
adopted while another rejected and a third asked for information. This reads the current
round's department rows and lists every pending task.
"""

from typing import Any
from uuid import UUID

from backend.api.cs.improvements.schemas import NextOwner

Row = dict[str, Any]

_DEPARTMENT_TASKS = {"SENT": "ACKNOWLEDGE", "ACKNOWLEDGED": "REPLY", "ADOPTED": "RECORD_ACTION"}
_CS_TASKS = {"INFO_REQUESTED": "SUPPLEMENT"}


def next_owners(request: Row, departments: list[Row]) -> list[NextOwner]:
    """Pending tasks, C/S tasks first. Empty once the request is completed."""
    owner: UUID | None = request["cs_owner"]
    status = request["status"]

    def cs(task: str, dept: str | None = None) -> NextOwner:
        return NextOwner(actor="CS_OWNER", user_id=owner, dept_code=dept, task=task)  # type: ignore[arg-type]

    if status == "COMPLETED":
        return []
    if status == "DRAFT":
        return [cs("REVIEW")]
    if status == "REVIEWED":
        return [cs("CONFIRM_RECEIVER" if not request["proposed_depts"] else "SEND")]
    if status == "FOLLOW_UP":
        return [cs("RECORD_FOLLOWUP")]

    tasks: list[NextOwner] = []
    department_tasks: list[NextOwner] = []
    for row in sorted(departments, key=lambda r: r["dept_code"]):
        dept, state = row["dept_code"], row["status"]
        if state in _CS_TASKS:
            tasks.append(cs(_CS_TASKS[state], dept))
        elif state == "REJECTED" and row["rejection_reviewed_at"] is None:
            tasks.append(cs("REVIEW_REJECTION", dept))
        elif state in _DEPARTMENT_TASKS:
            department_tasks.append(
                NextOwner(
                    actor="DEPARTMENT",
                    user_id=None,
                    dept_code=dept,
                    task=_DEPARTMENT_TASKS[state],  # type: ignore[arg-type]
                )
            )
    # Follow-up planning opens once every department acted or had its rejection reviewed,
    # with at least one action (2.2 ACTION_TAKEN → FOLLOW_UP).
    acted = [r for r in departments if r["status"] == "ACTION_TAKEN"]
    settled = all(
        r["status"] == "ACTION_TAKEN"
        or (r["status"] == "REJECTED" and r["rejection_reviewed_at"] is not None)
        for r in departments
    )
    if departments and acted and settled:
        tasks.append(cs("PLAN_FOLLOWUP"))
    return tasks + department_tasks
