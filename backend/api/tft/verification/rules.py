"""Read-only rules: unknown evidence stays unknown; partial confirmation is not completion."""

from typing import Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel

from backend.api.tft.projects.schemas import (
    AvailabilityCheck,
    CandidateRecommendation,
    CheckStatus,
    CompetencyCheck,
    ExperienceEvidence,
    ProjectDetail,
    RequiredRole,
)


class CandidateAssessment(BaseModel):
    recommendation_id: UUID
    candidate_id: UUID
    experience: CheckStatus
    required_competencies: CheckStatus
    preferred_competencies: CheckStatus
    availability: CheckStatus
    overlapping_role_ids: list[UUID]
    reasons: list[str]
    ready_for_review: bool


class RoleAssessment(BaseModel):
    role_id: UUID
    candidates: list[CandidateAssessment]
    reasons: list[str]
    next_organization_ids: list[UUID]
    next_user_id: UUID | None
    next_action: Literal["DEPARTMENT_CHECK", "RESPONSIBLE_REVIEW", "ASSIGN_RESPONSIBLE"]
    confirmed_count: int
    required_count: int | None
    complete: bool


class TeamAssessment(BaseModel):
    project_id: UUID
    composition_version: int | None
    roles: list[RoleAssessment]
    confirmed_role_count: int
    total_role_count: int
    state: Literal["NEEDS_REVIEW", "PARTIALLY_CONFIRMED", "READY_FOR_FINAL_REVIEW", "CONFIRMED"]
    complete: bool


Evidence = ExperienceEvidence | CompetencyCheck | AvailabilityCheck
Record = TypeVar("Record", bound=Evidence)


def latest(records: list[Record], time_field: str) -> Record | None:
    # Missing actor/time cannot establish verification. UUID tie-break is deterministic.
    eligible = [r for r in records if getattr(r, time_field) is not None]
    return max(eligible, key=lambda r: (getattr(r, time_field), str(r.id)), default=None)


def checked(record: Evidence | None, actor_field: str) -> CheckStatus:
    if record is None:
        return "UNSPECIFIED"
    if record.status in {"CONFIRMED", "UNMET"} and (
        not getattr(record, actor_field) or not record.basis or not record.basis.strip()
    ):
        return "NEEDS_CHECK"
    return record.status


def combine(states: list[CheckStatus]) -> CheckStatus:
    if "UNMET" in states:
        return "UNMET"
    if "NEEDS_CHECK" in states:
        return "NEEDS_CHECK"
    if "UNSPECIFIED" in states:
        return "UNSPECIFIED"
    return "CONFIRMED"


def available(record: AvailabilityCheck | None, role: RequiredRole) -> CheckStatus:
    status = checked(record, "checked_by_user_id")
    if status != "CONFIRMED" or record is None:
        return status
    if (
        record.period_start is None
        or record.period_end is None
        or record.allocation_rate is None
        or role.period_start is None
        or role.period_end is None
        or role.allocation_rate is None
    ):
        return "NEEDS_CHECK"
    if (
        record.period_start > role.period_start
        or record.period_end < role.period_end
        or record.allocation_rate < role.allocation_rate
    ):
        return "UNMET"
    return "CONFIRMED"


def overlap(a: RequiredRole, b: RequiredRole) -> bool:
    return bool(
        a.period_start
        and a.period_end
        and b.period_start
        and b.period_end
        and max(a.period_start, b.period_start) <= min(a.period_end, b.period_end)
    )


def assess_candidate(
    candidate: CandidateRecommendation,
    role: RequiredRole,
    recommendations: list[CandidateRecommendation],
    roles: dict[UUID, RequiredRole],
) -> CandidateAssessment:
    evidence = latest(candidate.experience_evidence, "verified_at")
    experience = checked(evidence, "verified_by_user_id")
    if evidence is not None and not evidence.summary.strip():
        experience = "NEEDS_CHECK"
    competencies: dict[str, list[CheckStatus]] = {"REQUIRED": [], "PREFERRED": []}
    for competency in role.competencies:
        record = latest(
            [c for c in candidate.competency_checks if c.competency_id == competency.id],
            "checked_at",
        )
        competencies[competency.kind].append(checked(record, "checked_by_user_id"))
    required = combine(competencies["REQUIRED"])
    preferred = combine(competencies["PREFERRED"])
    availability = available(latest(candidate.availability_checks, "checked_at"), role)
    overlapping = sorted(
        {
            other.required_role_id
            for other in recommendations
            if other.candidate_id == candidate.candidate_id
            and other.required_role_id != role.id
            and other.required_role_id in roles
            and overlap(role, roles[other.required_role_id])
        },
        key=str,
    )
    reasons = []
    for name, status in (
        ("EXPERIENCE", experience),
        ("REQUIRED_COMPETENCY", required),
        ("AVAILABILITY", availability),
    ):
        if status != "CONFIRMED":
            reasons.append(f"{name}_{status}")
    if overlapping:
        reasons.append("OVERLAPPING_ROLE_PROPOSALS")
    return CandidateAssessment(
        recommendation_id=candidate.id,
        candidate_id=candidate.candidate_id,
        experience=experience,
        required_competencies=required,
        preferred_competencies=preferred,
        availability=availability,
        overlapping_role_ids=overlapping,
        reasons=reasons,
        ready_for_review=not reasons,
    )


def assess(project: ProjectDetail) -> TeamAssessment:
    roles = {role.id: role for role in project.required_roles}
    active_requests = {
        r.id for r in project.staffing_requests if r.status in {"ISSUED", "ANSWERED"}
    }
    recommendations = [
        r
        for r in project.recommendations
        if r.status == "RECOMMENDED" and r.staffing_request_id in active_requests
    ]
    composition = project.current_composition
    results = []
    for role in project.required_roles:
        candidates = [
            assess_candidate(r, role, recommendations, roles)
            for r in recommendations
            if r.required_role_id == role.id
        ]
        reasons = []
        if not candidates:
            reasons.append("NO_RECOMMENDATION")
        if role.headcount is None:
            reasons.append("HEADCOUNT_UNSPECIFIED")
        elif len({c.candidate_id for c in candidates}) < role.headcount:
            reasons.append("INSUFFICIENT_RECOMMENDATIONS")
        reasons.extend(sorted({reason for c in candidates for reason in c.reasons}))
        members = (
            [m for m in composition.members if m.required_role_id == role.id] if composition else []
        )
        ready_ids = {c.recommendation_id for c in candidates if c.ready_for_review}
        if composition:
            changed_ids = {
                r.id
                for r in recommendations
                if any(
                    stamp is not None and stamp > composition.created_at
                    for stamp in (
                        r.recommended_at,
                        *(e.verified_at for e in r.experience_evidence),
                        *(c.checked_at for c in r.competency_checks),
                        *(a.checked_at for a in r.availability_checks),
                    )
                )
            }
            ready_ids -= changed_ids
        candidate_by_recommendation = {c.recommendation_id: c.candidate_id for c in candidates}
        confirmed = {
            candidate_by_recommendation[m.recommendation_id]
            for m in members
            if m.status == "CONFIRMED" and m.recommendation_id in ready_ids
        }
        # A previously confirmed member with changed verification needs review again.
        if any(m.status == "CONFIRMED" and m.recommendation_id not in ready_ids for m in members):
            reasons.append("CONFIRMATION_NEEDS_REVIEW")
        complete = (
            role.headcount is not None and role.headcount > 0 and len(confirmed) >= role.headcount
        )
        organizations = {
            r.organization_id
            for r in project.staffing_requests
            if r.required_role_id == role.id and r.status != "CLOSED"
        }
        if role.next_action_organization_id:
            organizations.add(role.next_action_organization_id)
        organizations.update(
            m.next_action_organization_id for m in members if m.next_action_organization_id
        )
        action = (
            "DEPARTMENT_CHECK"
            if reasons
            else "RESPONSIBLE_REVIEW"
            if project.responsible_user_id
            else "ASSIGN_RESPONSIBLE"
        )
        results.append(
            RoleAssessment(
                role_id=role.id,
                candidates=candidates,
                reasons=list(dict.fromkeys(reasons)),
                next_organization_ids=sorted(organizations, key=str) if reasons else [],
                next_user_id=project.responsible_user_id if not reasons else None,
                next_action=action,
                confirmed_count=len(confirmed),
                required_count=role.headcount,
                complete=complete,
            )
        )
    confirmed_count = sum(r.complete for r in results)
    all_roles = bool(results) and confirmed_count == len(results)
    final = project.final_confirmation
    complete = bool(
        all_roles
        and composition
        and final
        and final.composition_version_id == composition.id
        and final.confirmed_by_user_id == project.responsible_user_id
        and project.status == "CONFIRMED"
    )
    state = (
        "CONFIRMED"
        if complete
        else "READY_FOR_FINAL_REVIEW"
        if all_roles
        else "PARTIALLY_CONFIRMED"
        if confirmed_count
        else "NEEDS_REVIEW"
    )
    return TeamAssessment(
        project_id=project.id,
        composition_version=composition.version if composition else None,
        roles=results,
        confirmed_role_count=confirmed_count,
        total_role_count=len(results),
        state=state,
        complete=complete,
    )
