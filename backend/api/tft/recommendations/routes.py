"""요청 부서만 추천/검증한다. 추천은 선발 또는 최종 승인으로 취급하지 않는다."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel

from backend.api.tft.projects import repository as projects
from backend.api.tft.projects.schemas import CandidateRecommendation, StaffingRequest
from backend.api.tft.recommendations import repository
from backend.api.tft.recommendations.schemas import (
    AvailabilityCreate,
    CandidateList,
    CompetencyCreate,
    CreatedRecord,
    ExperienceCreate,
    RecommendationCreate,
)
from backend.api.tft.staffing_requests import repository as requests
from backend.api.tft.staffing_requests.routes import check_version
from backend.shared.access import Principal, current_principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import ErrorResponse, ResponseMeta, SuccessResponse

router = APIRouter(
    prefix="/api/v1/tft",
    tags=["department-recommendations"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 412, 422, 428, 503)},
)
PARTIAL = {"x-implementation-status": "partial"}
KEY = Header(alias="Idempotency-Key", min_length=1, max_length=128)
VERSION = Header(default=None, alias="If-Match")
Row = dict[str, Any]


class DepartmentRequests(BaseModel):
    items: list[StaffingRequest]
    next_cursor: UUID | None


class Recommendations(BaseModel):
    items: list[CandidateRecommendation]


def department(principal: Principal) -> str:
    if not principal.has("dept_staff") or not principal.dept:
        raise HTTPException(403)
    return principal.dept


async def scope(conn: Any, principal: Principal, request_id: UUID) -> Row:
    dept = department(principal)
    row = await repository.request_scope(conn, request_id)
    if (
        row is None
        or row["dept_code"] != dept
        or row["status"] == "DRAFT"
        or row["organization_company"] != row["company_code"]
    ):
        raise ApplicationError(404, "TFT_STAFFING_REQUEST_NOT_FOUND", "요청서를 찾을 수 없습니다.")
    return row


def respond(request: Request, data: Any) -> SuccessResponse[Any]:
    return SuccessResponse(data=data, meta=ResponseMeta(request_id=request.state.request_id))


@router.get(
    "/department-requests",
    response_model=SuccessResponse[DepartmentRequests],
    operation_id="tft_department_requests",
    openapi_extra=PARTIAL,
)
async def inbox(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    cursor: UUID | None = None,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[DepartmentRequests]:
    dept = department(principal)
    async with connection(request.app.state.settings) as conn:
        result = await conn.execute(
            f"SELECT {requests.REQUEST_COLUMNS} {requests.REQUEST_FROM} "
            "JOIN tft.projects p ON p.id = r.project_id WHERE o.dept_code = %s "
            "AND o.company_code = p.company_code AND r.status <> 'DRAFT' "
            "AND (%s::uuid IS NULL OR r.id > %s) ORDER BY r.id LIMIT %s",
            (dept, cursor, cursor, limit + 1),
        )
        rows = await result.fetchall()
    return respond(
        request,
        DepartmentRequests(
            items=rows[:limit], next_cursor=rows[limit - 1]["id"] if len(rows) > limit else None
        ),
    )


@router.get(
    "/staffing-requests/{request_id}/candidates",
    response_model=SuccessResponse[CandidateList],
    operation_id="tft_request_candidates",
    openapi_extra=PARTIAL,
)
async def candidates(
    request_id: UUID,
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    cursor: UUID | None = None,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[CandidateList]:
    department(principal)
    async with connection(request.app.state.settings) as conn:
        row = await scope(conn, principal, request_id)
        rows = await repository.candidates(conn, row["organization_id"], cursor, limit)
    return respond(
        request,
        CandidateList(
            items=rows[:limit], next_cursor=rows[limit - 1]["id"] if len(rows) > limit else None
        ),
    )


@router.get(
    "/staffing-requests/{request_id}/recommendations",
    response_model=SuccessResponse[Recommendations],
    operation_id="tft_request_recommendations",
    openapi_extra=PARTIAL,
)
async def recommendations(
    request_id: UUID, request: Request, principal: Principal = Depends(current_principal)
) -> SuccessResponse[Recommendations]:
    department(principal)
    async with connection(request.app.state.settings) as conn:
        row = await scope(conn, principal, request_id)
        project_id = row["project_id"]
        items = [
            item
            for item in await projects.list_recommendations(conn, project_id)
            if item["staffing_request_id"] == request_id
            and item["organization_id"] == row["organization_id"]
        ]
        evidence = await projects.list_experience_evidence(conn, project_id)
        checks = await projects.list_competency_checks(conn, project_id)
        availability = await projects.list_availability_checks(conn, project_id)
        for item in items:
            for field, records in (
                ("experience_evidence", evidence),
                ("competency_checks", checks),
                ("availability_checks", availability),
            ):
                item[field] = [
                    entry for entry in records if entry["recommendation_id"] == item["id"]
                ]
    return respond(request, Recommendations(items=items))


async def write_record(
    request_id: UUID,
    request: Request,
    response: Response,
    principal: Principal,
    key: str,
    if_match: str | None,
    payload: BaseModel,
    table: str,
    recommendation_id: UUID | None = None,
) -> SuccessResponse[CreatedRecord]:
    body = payload.model_dump(mode="json")
    operation = f"{table}/{recommendation_id or ''}"
    department(principal)
    async with connection(request.app.state.settings) as conn:
        # Serialise all commands for a request, including first idempotency-key use.
        row = await scope(conn, principal, request_id)
        parent = None
        if recommendation_id is not None:
            parent = await repository.recommendation(conn, recommendation_id)
            if (
                parent is None
                or parent["staffing_request_id"] != request_id
                or parent["organization_id"] != row["organization_id"]
            ):
                raise ApplicationError(
                    404, "TFT_RECOMMENDATION_NOT_FOUND", "추천을 찾을 수 없습니다."
                )
        replay = await repository.replay(conn, principal.user_id, request_id, operation, key)
        if replay:
            if replay["payload"] != body:
                raise ApplicationError(409, "IDEMPOTENCY_CONFLICT", "같은 키에 다른 입력입니다.")
            result = SuccessResponse[CreatedRecord].model_validate(replay["result"])
            response.headers["ETag"] = f'"{result.data.request_version}"'
            return result
        if (
            row["status"] not in {"ISSUED", "ANSWERED"}
            or row["project_status"] == "CONFIRMED"
            or (parent and parent["status"] != "RECOMMENDED")
        ):
            raise ApplicationError(
                409, "INVALID_TRANSITION", "현재 상태에서 추천/검증을 추가할 수 없습니다."
            )
        check_version(if_match, row["version"])
        values = payload.model_dump()
        origins = [row["data_origin"], row["project_origin"]]
        if isinstance(payload, RecommendationCreate):
            person = await repository.candidate(conn, payload.candidate_id)
            if person is None or person["organization_id"] != row["organization_id"]:
                raise ApplicationError(
                    404, "TFT_CANDIDATE_NOT_FOUND", "소속 부서 후보를 찾을 수 없습니다."
                )
            if await repository.exists_candidate(conn, request_id, payload.candidate_id):
                raise ApplicationError(409, "DUPLICATE_RECOMMENDATION", "이미 추천된 후보입니다.")
            origins.append(person["data_origin"])
            values.update(
                staffing_request_id=request_id,
                recommended_by_user_id=principal.user_id,
                recommended_at=datetime.now(UTC),
                status="RECOMMENDED",
            )
        else:
            assert parent is not None
            origins.append(parent["data_origin"])
            values["recommendation_id"] = recommendation_id
            if isinstance(payload, CompetencyCreate):
                if (
                    await repository.competency_role(conn, payload.competency_id)
                    != row["required_role_id"]
                ):
                    raise ApplicationError(422, "VALIDATION_ERROR", "이 역할의 역량이 아닙니다.")
                if await repository.competency_exists(conn, parent["id"], payload.competency_id):
                    raise ApplicationError(409, "DUPLICATE_CHECK", "이미 확인 기록이 있습니다.")
            prefix = "verified" if isinstance(payload, ExperienceCreate) else "checked"
            values[f"{prefix}_by_user_id"] = principal.user_id
            values[f"{prefix}_at"] = datetime.now(UTC)
        # Never promote synthetic or demo evidence to real, including mixed source records.
        origin = "synthetic" if "synthetic" in origins else "demo" if "demo" in origins else "real"
        record_id = uuid4()
        values.update(id=record_id, data_origin=origin, derivation_kind="PROVIDED")
        await repository.insert(conn, table, values)
        await requests.update_request(conn, request_id, row["version"], {"status": "ANSWERED"})
        await requests.insert_revision(
            conn,
            uuid4(),
            request_id,
            row["version"] + 1,
            "RECOMMEND" if isinstance(payload, RecommendationCreate) else "VERIFY",
            principal.user_id,
            {"record_id": str(record_id), "table": table},
        )
        if isinstance(payload, RecommendationCreate):
            await conn.execute(
                "UPDATE tft.required_roles SET status = 'RECOMMENDATIONS_RECEIVED', updated_at = now() WHERE id = %s AND status = 'ISSUED'",
                (row["required_role_id"],),
            )
        result = respond(request, CreatedRecord(id=record_id, request_version=row["version"] + 1))
        await repository.remember(
            conn,
            principal.user_id,
            request_id,
            operation,
            key,
            body,
            result.model_dump(mode="json"),
        )
    response.headers["ETag"] = f'"{result.data.request_version}"'
    return result


@router.post(
    "/staffing-requests/{request_id}/recommendations",
    response_model=SuccessResponse[CreatedRecord],
    operation_id="tft_recommend_candidate",
    openapi_extra=PARTIAL,
)
async def recommend(
    request_id: UUID,
    payload: RecommendationCreate,
    request: Request,
    response: Response,
    idempotency_key: str = KEY,
    if_match: str | None = VERSION,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[CreatedRecord]:
    return await write_record(
        request_id,
        request,
        response,
        principal,
        idempotency_key,
        if_match,
        payload,
        "candidate_recommendations",
    )


@router.post(
    "/staffing-requests/{request_id}/recommendations/{recommendation_id}/experience",
    response_model=SuccessResponse[CreatedRecord],
    operation_id="tft_record_experience",
    openapi_extra=PARTIAL,
)
async def experience(
    request_id: UUID,
    recommendation_id: UUID,
    payload: ExperienceCreate,
    request: Request,
    response: Response,
    idempotency_key: str = KEY,
    if_match: str | None = VERSION,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[CreatedRecord]:
    return await write_record(
        request_id,
        request,
        response,
        principal,
        idempotency_key,
        if_match,
        payload,
        "experience_evidence",
        recommendation_id,
    )


@router.post(
    "/staffing-requests/{request_id}/recommendations/{recommendation_id}/availability",
    response_model=SuccessResponse[CreatedRecord],
    operation_id="tft_record_availability",
    openapi_extra=PARTIAL,
)
async def availability(
    request_id: UUID,
    recommendation_id: UUID,
    payload: AvailabilityCreate,
    request: Request,
    response: Response,
    idempotency_key: str = KEY,
    if_match: str | None = VERSION,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[CreatedRecord]:
    return await write_record(
        request_id,
        request,
        response,
        principal,
        idempotency_key,
        if_match,
        payload,
        "availability_checks",
        recommendation_id,
    )


@router.post(
    "/staffing-requests/{request_id}/recommendations/{recommendation_id}/competency",
    response_model=SuccessResponse[CreatedRecord],
    operation_id="tft_record_competency",
    openapi_extra=PARTIAL,
)
async def competency(
    request_id: UUID,
    recommendation_id: UUID,
    payload: CompetencyCreate,
    request: Request,
    response: Response,
    idempotency_key: str = KEY,
    if_match: str | None = VERSION,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[CreatedRecord]:
    return await write_record(
        request_id,
        request,
        response,
        principal,
        idempotency_key,
        if_match,
        payload,
        "competency_checks",
        recommendation_id,
    )
