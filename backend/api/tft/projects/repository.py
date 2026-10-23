"""Read queries for TFT projects. Tables are created by infra/migrations/tft only."""

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

Row = dict[str, Any]

SUMMARY_COLUMNS = (
    "p.id, p.external_id, p.name, p.company_code, p.status, p.data_origin, p.derivation_kind, "
    "p.responsible_user_id, p.created_at, "
    "(SELECT max(v.version) FROM tft.composition_versions v WHERE v.project_id = p.id) "
    "AS current_composition_version"
)
DETAIL_COLUMNS = (
    "id, external_id, version, data_origin, derivation_kind, known_at, created_at, "
    "company_code, name, client_name, status, responsible_user_id"
)
REQUIREMENT_COLUMNS = (
    "id, requirement_code, field_tag, title, description, document_name, document_id, "
    "document_version_id, clause, page, excerpt, data_origin, derivation_kind"
)
ROLE_COLUMNS = (
    "id, role_code, title, field_tag, tasks, experience_condition, is_core, headcount, "
    "period_start, period_end, allocation_rate, status, reviewed_by_user_id, reviewed_at, "
    "next_action, next_action_organization_id, reply_due_at, data_origin, derivation_kind"
)
REQUEST_COLUMNS = (
    "r.id, r.required_role_id, r.organization_id, o.name AS organization_name, r.participation, "
    "r.status, r.issued_by_user_id, r.issued_at, r.due_at, r.message, r.data_origin, "
    "r.derivation_kind"
)
RECOMMENDATION_COLUMNS = (
    "c.id, c.staffing_request_id, r.required_role_id, c.candidate_id, "
    "k.display_name AS candidate_name, k.organization_id, c.recommended_by_user_id, "
    "c.recommended_at, c.rationale, c.status, c.data_origin, c.derivation_kind"
)
EVIDENCE_COLUMNS = (
    "e.id, e.recommendation_id, e.summary, e.project_name, e.role_title, e.period_start, "
    "e.period_end, e.evidence_source, e.status, e.verified_by_user_id, e.verified_at, e.basis, "
    "e.data_origin, e.derivation_kind"
)
COMPETENCY_CHECK_COLUMNS = (
    "e.id, e.recommendation_id, e.competency_id, e.status, e.checked_by_user_id, e.checked_at, "
    "e.basis, e.data_origin, e.derivation_kind"
)
AVAILABILITY_COLUMNS = (
    "e.id, e.recommendation_id, e.period_start, e.period_end, e.allocation_rate, "
    "e.constraints_note, e.checked_by_user_id, e.checked_at, e.basis, e.status, "
    "e.data_origin, e.derivation_kind"
)
VERSION_COLUMNS = "id, version, created_by_user_id, created_at, note, data_origin, derivation_kind"
MEMBER_COLUMNS = (
    "id, required_role_id, recommendation_id, status, rationale, remaining_conditions, "
    "gap_note, next_action_organization_id, follow_up_due_at"
)
# Rows of a recommendation-level table joined up to the project.
RECOMMENDATION_SCOPE = (
    "JOIN tft.candidate_recommendations c ON c.id = e.recommendation_id "
    "JOIN tft.staffing_requests r ON r.id = c.staffing_request_id "
    "WHERE r.project_id = %s"
)


async def list_projects(
    conn: AsyncConnection[Row], limit: int, after: tuple[datetime, UUID] | None
) -> list[Row]:
    """Newest first. Fetches one extra row so the caller knows whether a next page exists."""
    if after is None:
        cursor = await conn.execute(
            f"SELECT {SUMMARY_COLUMNS} FROM tft.projects p "
            "ORDER BY p.created_at DESC, p.id DESC LIMIT %s",
            (limit + 1,),
        )
    else:
        cursor = await conn.execute(
            f"SELECT {SUMMARY_COLUMNS} FROM tft.projects p "
            "WHERE (p.created_at, p.id) < (%s, %s) "
            "ORDER BY p.created_at DESC, p.id DESC LIMIT %s",
            (*after, limit + 1),
        )
    return await cursor.fetchall()


async def get_project(conn: AsyncConnection[Row], project_id: UUID) -> Row | None:
    cursor = await conn.execute(
        f"SELECT {DETAIL_COLUMNS} FROM tft.projects WHERE id = %s", (project_id,)
    )
    return await cursor.fetchone()


async def list_rfp_requirements(conn: AsyncConnection[Row], project_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {REQUIREMENT_COLUMNS} FROM tft.rfp_requirements "
        "WHERE project_id = %s ORDER BY requirement_code",
        (project_id,),
    )
    return await cursor.fetchall()


async def list_required_roles(conn: AsyncConnection[Row], project_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {ROLE_COLUMNS} FROM tft.required_roles WHERE project_id = %s ORDER BY role_code",
        (project_id,),
    )
    return await cursor.fetchall()


async def list_role_requirement_links(conn: AsyncConnection[Row], project_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        "SELECT l.required_role_id, l.requirement_id, l.rationale "
        "FROM tft.role_requirement_links l "
        "JOIN tft.required_roles r ON r.id = l.required_role_id "
        "JOIN tft.rfp_requirements q ON q.id = l.requirement_id "
        "WHERE r.project_id = %s ORDER BY q.requirement_code",
        (project_id,),
    )
    return await cursor.fetchall()


async def list_role_competencies(conn: AsyncConnection[Row], project_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        "SELECT c.id, c.required_role_id, c.kind, c.description FROM tft.role_competencies c "
        "JOIN tft.required_roles r ON r.id = c.required_role_id "
        "WHERE r.project_id = %s ORDER BY c.kind, c.created_at, c.id",
        (project_id,),
    )
    return await cursor.fetchall()


async def list_staffing_requests(conn: AsyncConnection[Row], project_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {REQUEST_COLUMNS} FROM tft.staffing_requests r "
        "JOIN tft.organizations o ON o.id = r.organization_id "
        "WHERE r.project_id = %s ORDER BY r.created_at, r.id",
        (project_id,),
    )
    return await cursor.fetchall()


async def list_recommendations(conn: AsyncConnection[Row], project_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {RECOMMENDATION_COLUMNS} FROM tft.candidate_recommendations c "
        "JOIN tft.staffing_requests r ON r.id = c.staffing_request_id "
        "JOIN tft.candidates k ON k.id = c.candidate_id "
        "WHERE r.project_id = %s ORDER BY c.recommended_at, c.id",
        (project_id,),
    )
    return await cursor.fetchall()


async def list_experience_evidence(conn: AsyncConnection[Row], project_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {EVIDENCE_COLUMNS} FROM tft.experience_evidence e {RECOMMENDATION_SCOPE} "
        "ORDER BY e.created_at, e.id",
        (project_id,),
    )
    return await cursor.fetchall()


async def list_competency_checks(conn: AsyncConnection[Row], project_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {COMPETENCY_CHECK_COLUMNS} FROM tft.competency_checks e {RECOMMENDATION_SCOPE} "
        "ORDER BY e.created_at, e.id",
        (project_id,),
    )
    return await cursor.fetchall()


async def list_availability_checks(conn: AsyncConnection[Row], project_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {AVAILABILITY_COLUMNS} FROM tft.availability_checks e {RECOMMENDATION_SCOPE} "
        "ORDER BY e.created_at, e.id",
        (project_id,),
    )
    return await cursor.fetchall()


async def get_current_composition(conn: AsyncConnection[Row], project_id: UUID) -> Row | None:
    cursor = await conn.execute(
        f"SELECT {VERSION_COLUMNS} FROM tft.composition_versions "
        "WHERE project_id = %s ORDER BY version DESC LIMIT 1",
        (project_id,),
    )
    return await cursor.fetchone()


async def list_composition_members(conn: AsyncConnection[Row], version_id: UUID) -> list[Row]:
    cursor = await conn.execute(
        f"SELECT {MEMBER_COLUMNS} FROM tft.composition_members "
        "WHERE composition_version_id = %s ORDER BY required_role_id, id",
        (version_id,),
    )
    return await cursor.fetchall()


async def get_latest_responsible_change(conn: AsyncConnection[Row], project_id: UUID) -> Row | None:
    cursor = await conn.execute(
        "SELECT new_user_id, changed_by_user_id, reason, occurred_at "
        "FROM tft.responsible_changes WHERE project_id = %s "
        "ORDER BY occurred_at DESC, id DESC LIMIT 1",
        (project_id,),
    )
    return await cursor.fetchone()


async def get_final_confirmation(conn: AsyncConnection[Row], project_id: UUID) -> Row | None:
    cursor = await conn.execute(
        "SELECT f.id, f.composition_version_id, v.version AS composition_version, "
        "f.confirmed_by_user_id, f.confirmed_at, f.note "
        "FROM tft.final_confirmations f "
        "JOIN tft.composition_versions v ON v.id = f.composition_version_id "
        "WHERE f.project_id = %s ORDER BY f.confirmed_at DESC, f.id DESC LIMIT 1",
        (project_id,),
    )
    return await cursor.fetchone()
