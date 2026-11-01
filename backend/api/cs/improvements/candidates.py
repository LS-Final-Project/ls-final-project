"""Improvement candidates (KAN-202): judge resolution records and save the eligible ones.

The rules are backend.engine.cs.improvement_candidates. A saved candidate is a DRAFT for a person
to review (permissions.md 2.2): nothing here sends it or decides a cause. Reading judges again
at as_of, so groups that are not eligible stay visible with their reason.
"""

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, get_args
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import AwareDatetime

from backend.api.cs.improvements import repository
from backend.api.cs.improvements.routes import commit_version
from backend.api.cs.improvements.schemas import (
    CandidateCollect,
    CandidateCollected,
    CandidateEvidence,
    CandidateItem,
    CandidateList,
    CandidateSkipped,
    DisplayState,
)
from backend.api.cs.tickets.access import sees_all_tickets
from backend.api.cs.tickets.routes import respond
from backend.engine.cs.improvement_candidates import (
    CANDIDATE_RULES_VERSION,
    CaseRecord,
    EvidenceRef,
    FaultCandidate,
    Outcome,
    RootCauseType,
    fault_candidates,
)
from backend.engine.cs.rules import firmware_releases
from backend.shared.access import Principal, current_principal
from backend.shared.database import connection
from backend.shared.departments import TRANSFER_DEPARTMENTS
from backend.shared.errors import ApplicationError
from backend.shared.models import ErrorResponse, SuccessResponse

router = APIRouter(
    prefix="/api/v1/cs/improvement-candidates",
    tags=["improvement-candidates"],
    responses={
        401: {"model": ErrorResponse, "description": "No valid user context"},
        403: {"model": ErrorResponse, "description": "Only C/S may read or collect candidates"},
        422: {"model": ErrorResponse, "description": "as_of is in the future"},
        503: {"model": ErrorResponse},
    },
)

Row = dict[str, Any]
Judged = tuple[str, FaultCandidate]
ROOT_CAUSES = frozenset(get_args(RootCauseType))
DISPLAY_VALUES = ("ticket_external_id", "case_external_id", "ticket_subject", "ticket_received_at")
NOT_CLAIMED = "후보는 사람이 검토할 거리다. 원인과 책임을 단정하지 않는다."


def require_candidates(principal: Principal) -> None:
    """Evidence names customers, so only users who see every ticket may read or collect."""
    if not sees_all_tickets(principal):
        raise HTTPException(403)


def judgement_time(as_of: datetime | None) -> datetime:
    """The time to judge at. A future time would read as data that is not known yet."""
    now = datetime.now(UTC)
    if as_of is None:
        return now
    if as_of > now:
        raise ApplicationError(422, "CS_CANDIDATE_AS_OF_FUTURE", "as_of is in the future.")
    return as_of


def record(row: Row) -> CaseRecord:
    cause = row["root_cause_type"]
    return CaseRecord(
        case_id=str(row["case_id"]),
        ticket_id=str(row["ticket_id"]),
        product_code=row["product_code"],
        fault_id=row["fault_id"],
        customer_id=row["customer_id"],
        known_at=row["known_at"],
        root_cause_type=cause if cause in ROOT_CAUSES else None,
        fw_version_at_issue=row["fw_version_at_issue"],
    )


def evidence(ref: EvidenceRef, rows: dict[str, Row]) -> CandidateEvidence:
    """The record with its ticket and record numbers, subject and receipt time."""
    row = rows.get(ref.case_id or "", {})
    return CandidateEvidence(
        **ref.model_dump(),
        **{name: row.get(name) for name in DISPLAY_VALUES},
    )


def display_state(candidate: FaultCandidate) -> DisplayState:
    if candidate.outcome != "ELIGIBLE":
        return candidate.outcome
    if candidate.condition_status == "UNKNOWN":
        return "CONDITION_UNCONFIRMED"
    return "CONFIRMED_CANDIDATE"


async def judge(conn: Any, as_of: datetime, rows: list[Row]) -> list[Judged]:
    """Judges each data origin on its own, so synthetic records never count toward real ones."""
    confirmed = await repository.confirmed_groups(conn, as_of)
    groups: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        groups[row["data_origin"]].append(row)
    judged: list[Judged] = []
    for origin, members in sorted(groups.items()):
        candidates = fault_candidates(
            [record(row) for row in members],
            list(firmware_releases()),
            as_of=as_of,
            condition_confirmed={
                (product, fault): True for kind, product, fault in confirmed if kind == origin
            },
        )
        judged.extend((origin, candidate) for candidate in candidates)
    return judged


def draft(origin: str, candidate: FaultCandidate, departments: list[str], as_of: datetime) -> Row:
    """The request row of one candidate. Its text repeats the rule's facts and nothing else."""
    assert candidate.reached_at is not None
    return {
        "id": uuid4(),
        "request_type": "FAULT",
        "origin": "SYSTEM_CANDIDATE",
        "data_origin": origin,
        "product_code": candidate.product_code,
        "fault_id": candidate.fault_id,
        "condition_status": candidate.condition_status,
        "fw_fixed_version": candidate.fw_fixed_version,
        "fw_released_on": candidate.fw_released_on,
        "problem_statement": f"{candidate.product_code} {candidate.fault_id}: {candidate.reason}",
        "confirmed_facts": (
            f"해결 기록 {candidate.cases_total}건, 고객 {candidate.distinct_customers}곳, "
            f"고객 식별자 없는 기록 {candidate.cases_without_customer}건."
        ),
        "missing_info": "사용 조건 미확인." if candidate.condition_status == "UNKNOWN" else None,
        "not_claimed": NOT_CLAIMED,
        "proposed_depts": departments,
        "candidate_reason_code": candidate.reason_code,
        "candidate_reached_at": candidate.reached_at,
        "candidate_rules_version": CANDIDATE_RULES_VERSION,
        "candidate_as_of": as_of,
    }


@router.get(
    "",
    response_model=SuccessResponse[CandidateList],
    operation_id="cs_list_improvement_candidates",
)
async def list_candidates(
    request: Request,
    as_of: AwareDatetime | None = Query(None, description="Judge as of this time. Default now."),
    outcome: Outcome | None = Query(None),
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[CandidateList]:
    require_candidates(principal)
    at = judgement_time(as_of)
    # ponytail: judged on every read over all records. Keep a table when the records outgrow it.
    async with connection(request.app.state.settings) as conn:
        rows = await repository.candidate_cases(conn, at)
        judged = await judge(conn, at, rows)
        requests = await repository.fault_requests(conn)
    shown = {str(row["case_id"]): row for row in rows}
    items = []
    for origin, candidate in judged:
        if outcome is not None and candidate.outcome != outcome:
            continue
        linked = requests.get((origin, candidate.product_code, candidate.fault_id))
        items.append(
            CandidateItem(
                **{
                    **candidate.model_dump(),
                    "evidence": [evidence(e, shown) for e in candidate.evidence],
                },
                data_origin=origin,
                display_state=display_state(candidate),
                request_id=linked["id"] if linked else None,
                request_status=linked["status"] if linked else None,
            )
        )
    return respond(
        request, CandidateList(as_of=at, rules_version=CANDIDATE_RULES_VERSION, items=items)
    )


@router.post(
    "/collect",
    response_model=SuccessResponse[CandidateCollected],
    operation_id="cs_collect_improvement_candidates",
)
async def collect_candidates(
    payload: CandidateCollect,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[CandidateCollected]:
    """Saves eligible groups that have no request yet. Running it again adds nothing."""
    require_candidates(principal)
    at = judgement_time(payload.as_of)
    run_id = uuid4()
    created: list[UUID] = []
    skipped: list[CandidateSkipped] = []
    already_requested = 0
    async with connection(request.app.state.settings) as conn:
        rows = await repository.candidate_cases(conn, at)
        departments = {str(row["case_id"]): row["resolved_by_dept"] for row in rows}
        judged = [pair for pair in await judge(conn, at, rows) if pair[1].outcome == "ELIGIBLE"]
        requests = await repository.fault_requests(conn)
        for origin, candidate in judged:
            if (origin, candidate.product_code, candidate.fault_id) in requests:
                already_requested += 1
                continue
            resolved = {departments[str(e.case_id)] for e in candidate.evidence}
            proposed = sorted(resolved & TRANSFER_DEPARTMENTS)
            # A fault request needs a department (CS_DEPARTMENT_REQUIRED). No candidate skips that.
            if not proposed:
                skipped.append(
                    CandidateSkipped(
                        data_origin=origin,
                        product_code=candidate.product_code,
                        fault_id=candidate.fault_id,
                        reason_code="NO_PROPOSED_DEPARTMENT",
                    )
                )
                continue
            values = draft(origin, candidate, proposed, at)
            if not await repository.insert_candidate(conn, values):
                already_requested += 1
                continue
            stored = await repository.get_request(conn, values["id"])
            assert stored is not None
            for item in candidate.evidence:
                await repository.insert_evidence(
                    conn,
                    {
                        "id": uuid4(),
                        "request_id": values["id"],
                        "ticket_id": UUID(item.ticket_id),
                        "case_id": UUID(item.case_id) if item.case_id else None,
                        "added_by": principal.user_id,
                    },
                )
            await commit_version(
                conn,
                stored,
                principal.user_id,
                "create",
                {
                    "origin": {"to": "SYSTEM_CANDIDATE"},
                    "reason_code": {"to": candidate.reason_code},
                    "run_id": {"to": str(run_id)},
                },
            )
            created.append(values["id"])
        await repository.insert_run_audit(
            conn,
            run_id,
            principal.user_id,
            {
                "as_of": at.isoformat(),
                "rules_version": CANDIDATE_RULES_VERSION,
                "eligible": len(judged),
                "created": len(created),
                "already_requested": already_requested,
                "skipped": len(skipped),
            },
        )
    return respond(
        request,
        CandidateCollected(
            run_id=run_id,
            as_of=at,
            rules_version=CANDIDATE_RULES_VERSION,
            eligible=len(judged),
            created=len(created),
            already_requested=already_requested,
            skipped=skipped,
            request_ids=created,
        ),
    )
