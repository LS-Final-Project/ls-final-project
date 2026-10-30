"""Which TFT projects a user may see (contracts/platform/permissions.md 1.2, 4.2)."""

from collections.abc import Mapping, Sequence
from typing import Any

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


def in_review_department(principal: Principal, review_departments: Sequence[str]) -> bool:
    """프로젝트 검토 부서 소속 dept_staff인지(명세 v1.3 §2.2).

    Args:
        principal: 현재 세션 사용자.
        review_departments: 프로젝트 검토 부서 코드 목록.

    Returns:
        bool: 검토 부서 소속 dept_staff면 True.
    """
    return "dept_staff" in principal.roles and principal.dept in set(review_departments)


def can_view_rfp(principal: Principal, project: Mapping[str, Any]) -> bool:
    """RFP 기록(질문·보완 선택지) 조회 범위: 위 프로젝트 범위에 검토 부서 dept_staff를 더한다.

    Args:
        principal: 현재 세션 사용자.
        project: id·responsible_user_id·sales_user_id·review_departments가 있는 행.

    Returns:
        bool: 조회할 수 있으면 True.
    """
    return sees_project(
        principal, project["id"], project["responsible_user_id"], project["sales_user_id"]
    ) or in_review_department(principal, project["review_departments"])


def can_create_question(principal: Principal, project: Mapping[str, Any]) -> bool:
    """질문 작성(경로 19) 권한: 담당 sales·책임자·검토 부서 dept_staff.

    Args:
        principal: 현재 세션 사용자.
        project: responsible_user_id·sales_user_id·review_departments가 있는 행.

    Returns:
        bool: 작성할 수 있으면 True.
    """
    return principal.user_id in (
        project["sales_user_id"],
        project["responsible_user_id"],
    ) or in_review_department(principal, project["review_departments"])


def can_reply(
    principal: Principal, project: Mapping[str, Any], question: Mapping[str, Any]
) -> bool:
    """회신 기록(경로 20) 권한.

    CUSTOMER 질문은 v1.2 그대로 담당 sales다. INTERNAL 질문은 대상 부서(target_dept) dept_staff와, 회의 결과를
    대신 적는 담당 sales·책임자다.

    Args:
        principal: 현재 세션 사용자.
        project: responsible_user_id·sales_user_id가 있는 행.
        question: target·target_dept가 있는 질문 행.

    Returns:
        bool: 회신할 수 있으면 True.
    """
    if question["target"] != "INTERNAL":
        return principal.user_id == project["sales_user_id"]
    return principal.user_id in (project["sales_user_id"], project["responsible_user_id"]) or (
        "dept_staff" in principal.roles and principal.dept == question["target_dept"]
    )


def can_select_supplement(principal: Principal, project: Mapping[str, Any]) -> bool:
    """보완 경로·후보·역할 분담 선택: 담당 sales와 책임자만(permissions.md 역할별 행동표).

    Args:
        principal: 요청 사용자.
        project: id·sales_user_id·responsible_user_id가 있는 프로젝트 행.

    Returns:
        bool: 선택할 수 있으면 True.
    """
    return principal.user_id in (project["sales_user_id"], project["responsible_user_id"])


def can_run_processing(principal: Principal, project: Mapping[str, Any]) -> bool:
    """처리 작업(경로 11) 실행: 담당 sales와 책임자만(permissions.md 역할별 행동표).

    Args:
        principal: 요청 사용자.
        project: sales_user_id·responsible_user_id가 있는 프로젝트 행.

    Returns:
        bool: 실행할 수 있으면 True.
    """
    return can_select_supplement(principal, project)


def can_edit_project(principal: Principal, project: Mapping[str, Any]) -> bool:
    """프로젝트 속성·검토 부서 수정(경로 4): 담당 sales와 책임자만(permissions.md 4.2).

    Args:
        principal: 요청 사용자.
        project: sales_user_id·responsible_user_id가 있는 프로젝트 행.

    Returns:
        bool: 수정할 수 있으면 True.
    """
    return can_select_supplement(principal, project)


def manages_project(principal: Principal, project_id: object) -> bool:
    """담당·책임자 지정(경로 5): 그 프로젝트를 관리 대상으로 가진 manager만(permissions.md 3.3).

    Args:
        principal: 요청 사용자.
        project_id: 프로젝트 ID.

    Returns:
        bool: 지정할 수 있으면 True.
    """
    return principal.has("manager") and project_id in principal.managed_projects


def can_run_annotations(principal: Principal, project: Mapping[str, Any]) -> bool:
    """메모 PDF 해석(경로 30) 실행: 담당 sales·책임자·검토 부서 dept_staff.

    Args:
        principal: 요청 사용자.
        project: 프로젝트 행(review_departments 포함).

    Returns:
        bool: 실행할 수 있으면 True.
    """
    return can_run_processing(principal, project) or in_review_department(
        principal, project["review_departments"]
    )
