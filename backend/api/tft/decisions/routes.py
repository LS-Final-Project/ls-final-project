"""Project/version locks, immutable history, and current-user checks for every decision."""

from typing import Any, Literal, NoReturn
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from psycopg.types.json import Jsonb

from backend.api.tft.decisions.schemas import (
    CompositionInput,
    DecisionHistory,
    DecisionInput,
    DecisionResult,
    ReopenInput,
)
from backend.api.tft.projects import repository
from backend.api.tft.projects.access import require_project_list, sees_project
from backend.api.tft.projects.routes import load_detail, respond
from backend.api.tft.projects.schemas import CompositionMember, CompositionVersion, ProjectDetail
from backend.api.tft.staffing_requests.routes import check_version
from backend.api.tft.verification.rules import assess
from backend.shared.access import Principal, current_principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.models import ErrorResponse, SuccessResponse

router = APIRouter(
    prefix="/api/v1/tft/projects",
    tags=["composition-decisions"],
    responses={code: {"model": ErrorResponse} for code in (401, 403, 404, 409, 412, 422, 428, 503)},
)
PARTIAL = {"x-implementation-status": "partial"}
KEY = Header(alias="Idempotency-Key", min_length=1, max_length=128)
VERSION = Header(default=None, alias="If-Match")


def fail(code: int, message: str) -> NoReturn:
    raise ApplicationError(
        code,
        {
            403: "FORBIDDEN",
            404: "TFT_PROJECT_NOT_FOUND",
            409: "INVALID_TRANSITION",
            422: "EVIDENCE_REQUIRED",
        }[code],
        message,
    )


async def visible(
    conn: Any, project_id: UUID, principal: Principal, lock: bool = False
) -> dict[str, Any]:
    require_project_list(principal)
    if lock:
        await conn.execute("SELECT id FROM tft.projects WHERE id=%s FOR UPDATE", (project_id,))
    row = await repository.get_project(conn, project_id)
    if row is None or not sees_project(
        principal, project_id, row["responsible_user_id"], row["sales_user_id"]
    ):
        fail(404, "프로젝트를 찾을 수 없습니다.")
    assert row is not None
    return row


async def save_composition(
    conn: Any, detail: ProjectDetail, payload: CompositionInput, actor: UUID
) -> CompositionVersion:
    roles = {r.id for r in detail.required_roles}
    if {m.required_role_id for m in payload.members} != roles:
        fail(422, "모든 역할을 후보 또는 공백으로 기록해야 합니다.")
    recommendations = {r.id: r for r in detail.recommendations if r.status == "RECOMMENDED"}
    seen = set()
    for member in payload.members:
        if member.recommendation_id:
            candidate = recommendations.get(member.recommendation_id)
            if (
                candidate is None
                or candidate.required_role_id != member.required_role_id
                or member.recommendation_id in seen
            ):
                fail(422, "추천과 역할의 연결 또는 중복을 확인하세요.")
            seen.add(member.recommendation_id)
        if member.next_action_organization_id:
            result = await conn.execute(
                "SELECT 1 FROM tft.organizations WHERE id=%s AND company_code=%s",
                (member.next_action_organization_id, detail.company_code),
            )
            if not await result.fetchone():
                fail(422, "같은 회사의 다음 확인 부서가 필요합니다.")
        if member.status == "CONFIRMED" and actor != detail.responsible_user_id:
            fail(403, "현재 책임자만 역할을 확정할 수 있습니다.")
    report = assess(detail)
    ready = {
        c.recommendation_id
        for r in report.roles
        for c in r.candidates
        if c.ready_for_review and c.preferred_competencies == "CONFIRMED"
    }
    if any(m.status == "CONFIRMED" and m.recommendation_id not in ready for m in payload.members):
        fail(422, "경험·역량·가용성 확인이 끝난 후보만 역할을 확정할 수 있습니다.")
    origins = {detail.data_origin} | {
        recommendations[m.recommendation_id].data_origin
        for m in payload.members
        if m.recommendation_id
    }
    origin = "synthetic" if "synthetic" in origins else "demo" if "demo" in origins else "real"
    version = detail.current_composition.version + 1 if detail.current_composition else 1
    version_id = uuid4()
    result = await conn.execute(
        "INSERT INTO tft.composition_versions (id,project_id,version,data_origin,derivation_kind,created_by_user_id,note) VALUES (%s,%s,%s,%s,'PROVIDED',%s,%s) RETURNING created_at",
        (version_id, detail.id, version, origin, actor, payload.note),
    )
    created = await result.fetchone()
    members = []
    for member in payload.members:
        row = CompositionMember(id=uuid4(), **member.model_dump())
        await conn.execute(
            "INSERT INTO tft.composition_members (id,composition_version_id,required_role_id,recommendation_id,status,rationale,remaining_conditions,gap_note,next_action_organization_id,follow_up_due_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                row.id,
                version_id,
                row.required_role_id,
                row.recommendation_id,
                row.status,
                row.rationale,
                row.remaining_conditions,
                row.gap_note,
                row.next_action_organization_id,
                row.follow_up_due_at,
            ),
        )
        members.append(row)
    return CompositionVersion(
        id=version_id,
        version=version,
        created_by_user_id=actor,
        created_at=created["created_at"],
        note=payload.note,
        members=members,
        data_origin=origin,
        derivation_kind="PROVIDED",
    )


async def command(
    project_id: UUID,
    request: Request,
    response: Response,
    principal: Principal,
    key: str,
    if_match: str | None,
    payload: CompositionInput | DecisionInput | ReopenInput,
    action: Literal["COMPOSE", "CONFIRM", "REJECT", "REOPEN"],
) -> SuccessResponse[DecisionResult]:
    body = payload.model_dump(mode="json")
    async with connection(request.app.state.settings) as conn:
        row = await visible(conn, project_id, principal, lock=True)
        if action in {"CONFIRM", "REJECT"}:
            if row["responsible_user_id"] is None:
                fail(409, "책임자가 지정되지 않았습니다.")
            if row["responsible_user_id"] != principal.user_id:
                fail(403, "현재 책임자만 결정할 수 있습니다.")
        elif principal.user_id != row["responsible_user_id"] and not (
            principal.has("sales") and principal.user_id == row["sales_user_id"]
        ):
            fail(403, "담당 sales 또는 현재 책임자만 구성안을 작성하거나 재검토할 수 있습니다.")
        replay = await conn.execute(
            "SELECT payload,result FROM tft.decision_commands WHERE actor_user_id=%s AND project_id=%s AND operation=%s AND idempotency_key=%s",
            (principal.user_id, project_id, action, key),
        )
        receipt = await replay.fetchone()
        if receipt:
            if receipt["payload"] != body:
                raise ApplicationError(409, "IDEMPOTENCY_CONFLICT", "같은 키에 다른 입력입니다.")
            envelope = SuccessResponse[DecisionResult].model_validate(receipt["result"])
            response.headers["ETag"] = f'"{envelope.data.project_version}"'
            return envelope
        check_version(if_match, row["version"])
        if row["status"] == "INITIAL_REVIEW":
            fail(409, "인력 요청 단계 이후에 구성안을 작성할 수 있습니다.")
        if row["status"] == "CONFIRMED" and action != "REOPEN":
            fail(409, "확정된 프로젝트는 먼저 재검토를 시작해야 합니다.")
        if action == "REOPEN" and row["status"] != "CONFIRMED":
            fail(409, "확정된 프로젝트만 재검토를 시작할 수 있습니다.")
        # Recommendation writes lock these same request rows. Freeze the checked inputs.
        await conn.execute(
            "SELECT id FROM tft.staffing_requests WHERE project_id=%s ORDER BY id FOR UPDATE",
            (project_id,),
        )
        detail = await load_detail(conn, row)
        version = detail.current_composition.version if detail.current_composition else None
        record_id = uuid4()
        status = "COMPOSING"
        if isinstance(payload, CompositionInput):
            composition = await save_composition(conn, detail, payload, principal.user_id)
            version, record_id = composition.version, composition.id
            reason = payload.note
        else:
            reason = payload.reason
            if isinstance(payload, DecisionInput):
                if detail.current_composition is None or version != payload.composition_version:
                    fail(409, "현재 구성안 판본을 대상으로 결정해야 합니다.")
                if action == "CONFIRM":
                    rejected = await conn.execute(
                        "SELECT 1 FROM tft.decision_history WHERE project_id=%s AND composition_version=%s AND action='REJECT'",
                        (project_id, version),
                    )
                    if await rejected.fetchone():
                        fail(409, "반려된 구성안은 새 판본을 작성해야 합니다.")
                    report = assess(detail)
                    if not report.roles or not all(r.complete for r in report.roles):
                        fail(422, "미확정 또는 재검토 역할이 남아 있어 전체 확정할 수 없습니다.")
                    if any(
                        c.preferred_competencies != "CONFIRMED"
                        for r in report.roles
                        for c in r.candidates
                        if any(
                            m.recommendation_id == c.recommendation_id and m.status == "CONFIRMED"
                            for m in detail.current_composition.members
                        )
                    ):
                        fail(422, "역량 확인이 끝나지 않았습니다.")
                    previous = await conn.execute(
                        "SELECT 1 FROM tft.final_confirmations WHERE composition_version_id=%s",
                        (detail.current_composition.id,),
                    )
                    if await previous.fetchone():
                        fail(409, "재확정은 새 구성안 판본이 필요합니다.")
                    await conn.execute(
                        "INSERT INTO tft.final_confirmations (id,project_id,composition_version_id,confirmed_by_user_id,note) VALUES (%s,%s,%s,%s,%s)",
                        (
                            record_id,
                            project_id,
                            detail.current_composition.id,
                            principal.user_id,
                            reason,
                        ),
                    )
                    status = "CONFIRMED"
        new_version = row["version"] + 1
        await conn.execute(
            "UPDATE tft.projects SET version=%s,status=%s,updated_at=now() WHERE id=%s",
            (new_version, status, project_id),
        )
        await conn.execute(
            "INSERT INTO tft.decision_history (id,project_id,action,actor_user_id,reason,project_version,composition_version) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (uuid4(), project_id, action, principal.user_id, reason, new_version, version),
        )
        envelope = respond(
            request,
            DecisionResult(
                id=record_id,
                project_version=new_version,
                composition_version=version,
                status=status,
            ),
        )
        await conn.execute(
            "INSERT INTO tft.decision_commands (actor_user_id,project_id,operation,idempotency_key,payload,result) VALUES (%s,%s,%s,%s,%s,%s)",
            (
                principal.user_id,
                project_id,
                action,
                key,
                Jsonb(body),
                Jsonb(envelope.model_dump(mode="json")),
            ),
        )
    response.headers["ETag"] = f'"{new_version}"'
    return envelope


@router.post(
    "/{project_id}/compositions",
    response_model=SuccessResponse[DecisionResult],
    operation_id="tft_create_composition",
    openapi_extra=PARTIAL,
)
async def compose(
    project_id: UUID,
    payload: CompositionInput,
    request: Request,
    response: Response,
    idempotency_key: str = KEY,
    if_match: str | None = VERSION,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[DecisionResult]:
    return await command(
        project_id, request, response, principal, idempotency_key, if_match, payload, "COMPOSE"
    )


@router.post(
    "/{project_id}/confirm",
    response_model=SuccessResponse[DecisionResult],
    operation_id="tft_confirm_composition",
    openapi_extra=PARTIAL,
)
async def confirm(
    project_id: UUID,
    payload: DecisionInput,
    request: Request,
    response: Response,
    idempotency_key: str = KEY,
    if_match: str | None = VERSION,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[DecisionResult]:
    return await command(
        project_id, request, response, principal, idempotency_key, if_match, payload, "CONFIRM"
    )


@router.post(
    "/{project_id}/reject",
    response_model=SuccessResponse[DecisionResult],
    operation_id="tft_reject_composition",
    openapi_extra=PARTIAL,
)
async def reject(
    project_id: UUID,
    payload: DecisionInput,
    request: Request,
    response: Response,
    idempotency_key: str = KEY,
    if_match: str | None = VERSION,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[DecisionResult]:
    return await command(
        project_id, request, response, principal, idempotency_key, if_match, payload, "REJECT"
    )


@router.post(
    "/{project_id}/reopen",
    response_model=SuccessResponse[DecisionResult],
    operation_id="tft_reopen_composition",
    openapi_extra=PARTIAL,
)
async def reopen(
    project_id: UUID,
    payload: ReopenInput,
    request: Request,
    response: Response,
    idempotency_key: str = KEY,
    if_match: str | None = VERSION,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[DecisionResult]:
    return await command(
        project_id, request, response, principal, idempotency_key, if_match, payload, "REOPEN"
    )


@router.get(
    "/{project_id}/compositions/{version}",
    response_model=SuccessResponse[CompositionVersion],
    operation_id="tft_get_composition_version",
    openapi_extra=PARTIAL,
)
async def get_version(
    project_id: UUID,
    version: int,
    request: Request,
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[CompositionVersion]:
    async with connection(request.app.state.settings) as conn:
        await visible(conn, project_id, principal)
        result = await conn.execute(
            f"SELECT {repository.VERSION_COLUMNS} FROM tft.composition_versions WHERE project_id=%s AND version=%s",
            (project_id, version),
        )
        row = await result.fetchone()
        if row is None:
            fail(404, "구성안 판본을 찾을 수 없습니다.")
        row["members"] = await repository.list_composition_members(conn, row["id"])
    return respond(request, CompositionVersion.model_validate(row))


@router.get(
    "/{project_id}/decisions",
    response_model=SuccessResponse[list[DecisionHistory]],
    operation_id="tft_decision_history",
    openapi_extra=PARTIAL,
)
async def history(
    project_id: UUID,
    request: Request,
    after_version: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    principal: Principal = Depends(current_principal),
) -> SuccessResponse[list[DecisionHistory]]:
    async with connection(request.app.state.settings) as conn:
        await visible(conn, project_id, principal)
        result = await conn.execute(
            "SELECT id,action,actor_user_id,reason,project_version,composition_version,occurred_at FROM tft.decision_history WHERE project_id=%s AND project_version>%s ORDER BY project_version LIMIT %s",
            (project_id, after_version, limit),
        )
        rows = await result.fetchall()
    return respond(request, [DecisionHistory.model_validate(row) for row in rows])
