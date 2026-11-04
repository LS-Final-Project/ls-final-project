"""RFP 자사 대응 경로의 골격(KAN-182, A01). OpenAPI 초안을 만들기 위한 선언이다.

명세 v1.1 §3의 30개 경로 중 아직 없는 28개다(1·3번 목록·상세는 projects/routes.py에 있고
응답 확장은 구현 작업이 한다). 변경 제안은 routes_proposals.py, 초안 25~29는 routes_draft.py가 처리한다.
구현 담당: 경로 1~19·25·28·29는 KAN-188(A07), 질문·제안·검토본은 KAN-189(A08), 30은 KAN-192(A12).
15·16·18·19·20은 KAN-189가 구현했다(routes_supplement.py·routes_questions.py). 7~10·12~14·17 읽기는 KAN-188(routes_read.py)이다. 11·30 접수는 jobs.py(KAN-199·KAN-188).
"""

from typing import Annotated, Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from backend.api.tft.projects.access import require_project_list
from backend.api.tft.rfp import (
    context_routes,
    history,
    jobs,
    routes_diagrams,
    routes_draft,
    routes_proposals,
    routes_questions,
    routes_read,
    routes_supplement,
    routes_write,
)
from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.context_schemas import ContextCorrection, ContextResponse
from backend.shared.access import Principal, current_principal
from backend.shared.errors import ApplicationError
from backend.shared.logs import bind_path_ids
from backend.shared.models import ErrorResponse, ResponseMeta, SuccessResponse


def rfp_principal(request: Request) -> Principal:
    """모든 RFP 경로의 공통 앞단: 사용자 맥락 401, TFT 역할 없음(cs_agent) 403.

    프로젝트 범위(404)는 경로마다 구현 티켓이 검사한다. 구현이 핸들러만 채워도 인증 없는
    경로가 열리지 않도록 라우터 단위로 둔다.

    Args:
        request: 플랫폼 중계가 사용자 맥락을 넣은 요청.

    Returns:
        Principal: 검증한 사용자.
    """
    principal = current_principal(request)
    require_project_list(principal)
    return principal


router = APIRouter(
    prefix="/api/v1/tft",
    tags=["rfp"],
    dependencies=[Depends(bind_path_ids), Depends(rfp_principal)],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse, "description": "Not found or outside the project scope"},
        409: {"model": ErrorResponse, "description": "TFT_VERSION_CONFLICT and similar"},
        422: {"model": ErrorResponse},
        501: {"model": ErrorResponse, "description": "Declared in KAN-182, not implemented yet"},
    },
)


def respond(request: Request, data: Any) -> SuccessResponse[Any]:
    return SuccessResponse(data=data, meta=ResponseMeta(request_id=request.state.request_id))


User = Annotated[Principal, Depends(rfp_principal)]


def planned(ticket: str) -> dict[str, Any]:
    return {"x-implementation-status": "planned", "x-implementation-ticket": ticket}


def not_implemented() -> NoReturn:
    raise ApplicationError(501, "TFT_NOT_IMPLEMENTED", "This RFP route is not implemented yet.")


A07, A08, A12 = planned("KAN-188"), planned("KAN-189"), planned("KAN-192")
# 상태를 만드는 POST는 모두 필수(8~128자). 같은 키의 재전송은 tft.rfp_commands의 첫 결과를 돌려준다.
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]
P = "/projects/{project_id}"


@router.post(
    "/projects",
    status_code=201,
    response_model=SuccessResponse[s.RfpProjectDetail],
    operation_id="tft_create_rfp_project",
)
async def create_project(
    request: Request, user: User, payload: s.ProjectCreate, idempotency_key: Key
) -> SuccessResponse[s.RfpProjectDetail]:
    """2. 접수. 호출자가 담당 sales가 된다."""
    return respond(
        request, await routes_write.create_project(request, user, payload, idempotency_key)
    )


@router.patch(
    P,
    response_model=SuccessResponse[s.RfpProjectDetail],
    operation_id="tft_update_rfp_project",
)
async def update_project(
    request: Request, user: User, project_id: UUID, payload: s.ProjectUpdate
) -> SuccessResponse[s.RfpProjectDetail]:
    """4. 이름·고객 목표·검토 부서 목록. 상태 두 개는 직접 쓰지 못한다."""
    return respond(request, await routes_write.update_project(request, user, project_id, payload))


@router.patch(
    P + "/assignment",
    response_model=SuccessResponse[s.RfpProjectDetail],
    operation_id="tft_assign_rfp_project",
)
async def assign_project(
    request: Request, user: User, project_id: UUID, payload: s.Assignment
) -> SuccessResponse[s.RfpProjectDetail]:
    """5. manager가 담당 sales·책임자를 지정한다. 이력을 남긴다."""
    return respond(request, await routes_write.assign_project(request, user, project_id, payload))


UPLOAD_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file", "kind", "title", "data_origin"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "kind": {
                            "type": "string",
                            "enum": [
                                "RFP_SPEC",
                                "DRAWING",
                                "ADDENDUM",
                                "DATASHEET",
                                "ANNOTATED_PDF",
                            ],
                        },
                        "title": {"type": "string", "maxLength": 300},
                        "note": {"type": "string", "maxLength": 2000},
                        "supersedes_document_id": {
                            "type": "string",
                            "format": "uuid",
                            "description": "주면 새 문서가 아니라 이 문서의 다음 판본(version+1)으로 저장한다.",
                        },
                        "base_document_id": {"type": "string", "format": "uuid"},
                        "data_origin": {"type": "string", "enum": ["synthetic", "demo", "real"]},
                    },
                }
            }
        },
    }
}


@router.post(
    P + "/documents",
    status_code=201,
    response_model=SuccessResponse[s.DocumentVersion],
    operation_id="tft_upload_rfp_document",
    responses={413: {"model": ErrorResponse, "description": "TFT_DOCUMENT_TOO_LARGE"}},
    openapi_extra=UPLOAD_BODY,
)
async def upload_document(
    request: Request, user: User, project_id: UUID, idempotency_key: Key
) -> SuccessResponse[s.DocumentVersion]:
    """6. DOCX·PDF 25 MiB 이하(multipart). 업로드만으로는 파싱하지 않는다.

    본문 형식은 OpenAPI에 직접 적었다. 형식은 내용으로 판별한다.
    """
    return respond(
        request, await routes_write.upload_document(request, user, project_id, idempotency_key)
    )


@router.get(
    P + "/documents",
    response_model=SuccessResponse[s.DocumentList],
    operation_id="tft_list_rfp_documents",
)
async def list_documents(
    project_id: UUID, request: Request, principal: User
) -> SuccessResponse[s.DocumentList]:
    """7. 문서·판본 목록."""
    return respond(request, await routes_read.list_documents(request, principal, project_id))


@router.get(
    "/documents/{document_id}",
    response_model=SuccessResponse[s.DocumentDetail],
    operation_id="tft_get_rfp_document",
)
async def get_document(
    document_id: UUID, request: Request, principal: User
) -> SuccessResponse[s.DocumentDetail]:
    """8. 최신 판본·편집 흔적 지원 여부·조항 수·삭제 값 수."""
    return respond(request, await routes_read.get_document(request, principal, document_id))


@router.get(
    "/documents/{document_id}/clauses",
    response_model=SuccessResponse[s.ClauseList],
    operation_id="tft_list_rfp_clauses",
)
async def list_clauses(
    document_id: UUID,
    request: Request,
    principal: User,
    parent: UUID | None = None,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
) -> SuccessResponse[s.ClauseList]:
    """9. 조항 트리(문서 순서). parent가 없으면 전체다. 취소선 값은 deleted_values로만 낸다."""
    data = await routes_read.list_clauses(
        request, principal, document_id, parent=parent, limit=limit, cursor=cursor
    )
    return respond(request, data)


@router.get(
    "/documents/{document_id}/file",
    operation_id="tft_download_rfp_document",
    response_model=None,
    response_class=Response,
    responses={
        200: {"content": {"application/octet-stream": {}}},
        503: {"model": ErrorResponse, "description": "TFT_DOCUMENT_UNAVAILABLE"},
    },
)
async def download_document(document_id: UUID, request: Request, principal: User) -> Response:
    """10. 원문 내려받기. 접근 검사는 프로젝트 조회와 같다."""
    return await routes_read.download_document(request, principal, document_id)


@router.post(
    P + "/process",
    status_code=202,
    response_model=SuccessResponse[s.JobAccepted],
    operation_id="tft_process_rfp",
)
async def process(
    project_id: UUID,
    payload: s.ProcessRequest,
    idempotency_key: Key,
    request: Request,
    principal: User,
) -> SuccessResponse[s.JobAccepted]:
    """11. 처리 작업 접수(플랫폼 runtime.jobs, owner=tft, kind=rfp.process).

    담당 sales·책임자만. 프로젝트에 요구가 이미 있으면 reason이 필요하다(422 TFT_REASON_REQUIRED).
    """
    data = await jobs.admit(request, principal, project_id, "rfp.process", payload, idempotency_key)
    return respond(request, data)


@router.get(
    P + "/context",
    response_model=SuccessResponse[ContextResponse],
    operation_id="tft_get_rfp_context",
)
async def get_context(
    request: Request,
    user: User,
    project_id: UUID,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> SuccessResponse[ContextResponse]:
    return respond(request, await context_routes.get_context(request, user, project_id, version))


@router.patch(
    P + "/context",
    response_model=SuccessResponse[ContextResponse],
    operation_id="tft_correct_rfp_context",
)
async def correct_context(
    request: Request,
    user: User,
    project_id: UUID,
    payload: ContextCorrection,
    idempotency_key: Key,
) -> SuccessResponse[ContextResponse]:
    return respond(
        request,
        await context_routes.correct_context(
            request,
            user,
            project_id,
            payload,
            idempotency_key,
        ),
    )


@router.post(
    P + "/context/process",
    status_code=202,
    response_model=SuccessResponse[s.JobAccepted],
    operation_id="tft_process_rfp_context",
)
async def process_context(
    request: Request,
    user: User,
    project_id: UUID,
    payload: s.ContextProcess,
    idempotency_key: Key,
) -> SuccessResponse[s.JobAccepted]:
    return respond(
        request,
        await jobs.admit(
            request,
            user,
            project_id,
            "rfp.context",
            payload,
            idempotency_key,
        ),
    )


@router.get(
    P + "/requirements",
    response_model=SuccessResponse[s.RequirementList],
    operation_id="tft_list_rfp_requirements",
)
async def list_requirements(
    project_id: UUID,
    request: Request,
    principal: User,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
    document_version_id: UUID | None = None,
    equipment: str | None = None,
    task: str | None = None,
    necessity: s.Necessity | None = None,
    status: s.RequirementStatus | None = None,
    needs_rereview: bool | None = None,
    needs_review: bool | None = None,
    has_open_question: bool | None = None,
    next_owner_field: s.NextOwner | None = None,
    unresolved_mandatory: bool | None = None,
    excluded: bool = False,
) -> SuccessResponse[s.RequirementList]:
    """12. 요구 목록·필터 11개. 뺀 요구는 excluded=true일 때만 나온다(KAN-225 별도 목록)."""
    data = await routes_read.list_requirements(
        request,
        principal,
        project_id,
        filters={
            "document_version_id": document_version_id,
            "equipment": equipment,
            "task": task,
            "necessity": necessity,
            "status": status,
            "needs_rereview": needs_rereview,
            "needs_review": needs_review,
            "has_open_question": has_open_question,
            "next_owner_field": next_owner_field,
            "unresolved_mandatory": unresolved_mandatory,
            "excluded": excluded,
        },
        limit=limit,
        cursor=cursor,
    )
    return respond(request, data)


@router.get(
    "/requirements/{requirement_id}",
    response_model=SuccessResponse[s.RequirementDetail],
    operation_id="tft_get_rfp_requirement",
)
async def get_requirement(
    requirement_id: UUID, request: Request, principal: User
) -> SuccessResponse[s.RequirementDetail]:
    """13. 원문·요구·판단·연결."""
    return respond(request, await routes_read.get_requirement(request, principal, requirement_id))


@router.get(
    "/requirements/{requirement_id}/comparison",
    response_model=SuccessResponse[s.Comparison],
    operation_id="tft_get_rfp_comparison",
)
async def get_comparison(
    requirement_id: UUID, request: Request, principal: User
) -> SuccessResponse[s.Comparison]:
    """14. 자사 비교. 같은 속성·단위·조건끼리만 비교한다."""
    return respond(request, await routes_read.get_comparison(request, principal, requirement_id))


@router.get(
    "/requirements/{requirement_id}/supplement-options",
    response_model=SuccessResponse[s.SupplementOptions],
    operation_id="tft_get_rfp_supplement_options",
)
async def get_supplement_options(
    requirement_id: UUID, request: Request, principal: User
) -> SuccessResponse[s.SupplementOptions]:
    """15. 보완 경로 선택지. 금지 경로는 allowed=false."""
    data = await routes_supplement.get_supplement_options(request, principal, requirement_id)
    return respond(request, data)


@router.post(
    "/requirements/{requirement_id}/supplement-selection",
    response_model=SuccessResponse[s.SupplementSelected],
    operation_id="tft_select_rfp_supplement",
)
async def select_supplement(
    requirement_id: UUID,
    payload: s.SupplementSelection,
    idempotency_key: Key,
    request: Request,
    principal: User,
) -> SuccessResponse[s.SupplementSelected]:
    """16. 경로·후보·역할 분담 선택(사람 결정, human_input 새 판본)."""
    data = await routes_supplement.select_supplement(
        request, principal, requirement_id, payload, idempotency_key
    )
    return respond(request, data)


@router.post(
    P + "/supplement-selections/batch",
    response_model=SuccessResponse[s.SupplementBatchResult],
    operation_id="tft_select_rfp_supplement_batch",
)
async def select_supplement_batch(
    project_id: UUID,
    payload: s.SupplementBatchSelection,
    request: Request,
    response: Response,
    principal: User,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key", min_length=8, max_length=128)
    ] = None,
) -> SuccessResponse[s.SupplementBatchResult]:
    """16-b. 같은 보완 경로를 요구 여러 건에 선택한다. dry_run이면 쓰지 않고 가능 여부만 돌려준다."""
    data = await routes_supplement.select_supplement_batch(
        request, principal, project_id, payload, idempotency_key
    )
    response.status_code = 201 if data.saved else 200
    return respond(request, data)


@router.get(
    P + "/partner-candidates",
    response_model=SuccessResponse[s.PartnerCandidateList],
    operation_id="tft_list_rfp_partner_candidates",
)
async def list_partner_candidates(
    project_id: UUID, request: Request, principal: User, requirement_id: UUID | None = None
) -> SuccessResponse[s.PartnerCandidateList]:
    """17. 가상 협력사 후보와 요구별 등급. 점수는 내지 않는다."""
    data = await routes_read.list_partner_candidates(request, principal, project_id, requirement_id)
    return respond(request, data)


@router.get(
    P + "/questions",
    response_model=SuccessResponse[s.QuestionList],
    operation_id="tft_list_rfp_questions",
)
async def list_questions(
    project_id: UUID,
    request: Request,
    principal: User,
    status: s.QuestionStatus | None = None,
    requirement_id: UUID | None = None,
    target: s.QuestionTarget | None = None,
    target_dept: str | None = Query(None, min_length=1, max_length=32),
) -> SuccessResponse[s.QuestionList]:
    """18. 고객·내부 자문 질문 목록."""
    data = await routes_questions.list_questions(
        request,
        principal,
        project_id,
        status=status,
        requirement_id=requirement_id,
        target=target,
        target_dept=target_dept,
    )
    return respond(request, data)


@router.post(
    P + "/questions",
    status_code=201,
    response_model=SuccessResponse[s.Question],
    operation_id="tft_create_rfp_question",
)
async def create_question(
    project_id: UUID,
    payload: s.QuestionCreate,
    idempotency_key: Key,
    request: Request,
    principal: User,
) -> SuccessResponse[s.Question]:
    """19. 질문 작성. 회신 전 요구값은 UNDETERMINED로 유지한다."""
    data = await routes_questions.create_question(
        request, principal, project_id, payload, idempotency_key
    )
    return respond(request, data)


@router.post(
    P + "/questions/batch",
    status_code=201,
    response_model=SuccessResponse[s.QuestionList],
    operation_id="tft_create_rfp_questions_batch",
)
async def create_questions_batch(
    project_id: UUID,
    payload: s.QuestionBatchCreate,
    idempotency_key: Key,
    request: Request,
    principal: User,
) -> SuccessResponse[s.QuestionList]:
    """19-b. 질문 여러 건을 한 번에 작성한다. 한 건이라도 어긋나면 아무것도 저장하지 않는다."""
    data = await routes_questions.create_questions_batch(
        request, principal, project_id, payload, idempotency_key
    )
    return respond(request, data)


@router.post(
    P + "/questions/{question_id}/confirm",
    response_model=SuccessResponse[s.Question],
    operation_id="tft_confirm_rfp_question",
)
async def confirm_question(
    project_id: UUID,
    question_id: UUID,
    idempotency_key: Key,
    request: Request,
    principal: User,
) -> SuccessResponse[s.Question]:
    """31. 질문 초안 확인. DRAFT만 OPEN으로 바꾼다(그 전에는 보낸 질문이 아니다)."""
    data = await routes_questions.confirm_question(
        request, principal, project_id, question_id, idempotency_key
    )
    return respond(request, data)


@router.post(
    "/questions/{question_id}/reply",
    response_model=SuccessResponse[s.QuestionReplyResult],
    operation_id="tft_reply_rfp_question",
)
async def reply_question(
    question_id: UUID,
    payload: s.QuestionReply,
    idempotency_key: Key,
    request: Request,
    principal: User,
) -> SuccessResponse[s.QuestionReplyResult]:
    """20. 회신 기록. CUSTOMER 회신이 미해결 항목을 줄이면 변경 제안(customer_reply)을 함께 만든다.

    승인 전에는 요구가 바뀌지 않는다.
    """
    data = await routes_questions.reply_question(
        request, principal, question_id, payload, idempotency_key
    )
    return respond(request, data)


@router.post(
    "/questions/{question_id}/reply/read",
    response_model=SuccessResponse[s.Question],
    operation_id="tft_read_rfp_question_reply",
)
async def read_question_reply(
    question_id: UUID,
    idempotency_key: Key,
    request: Request,
    principal: User,
) -> SuccessResponse[s.Question]:
    """KAN-224. 규칙이 못 읽은 고객 회신을 AI로 다시 읽는다(worker 작업, 모델 비용). 요구값은 바꾸지 않는다."""
    return respond(
        request,
        await routes_questions.read_again(request, principal, question_id, idempotency_key),
    )


@router.get(
    P + "/proposals",
    response_model=SuccessResponse[s.ProposalList],
    operation_id="tft_list_rfp_proposals",
)
async def list_proposals(
    request: Request,
    user: User,
    project_id: UUID,
    status: s.ProposalStatus | None = None,
    origin: s.ProposalOrigin | None = None,
    requirement_id: UUID | None = None,
) -> SuccessResponse[s.ProposalList]:
    """21. 승인 대기 목록(세 입구 공통)."""
    data = await routes_proposals.list_proposals(
        request, user, project_id, status=status, origin=origin, requirement_id=requirement_id
    )
    return respond(request, data)


@router.post(
    P + "/proposals",
    status_code=201,
    response_model=SuccessResponse[s.Proposal],
    operation_id="tft_create_rfp_proposal",
)
async def create_proposal(
    request: Request, user: User, project_id: UUID, payload: s.ProposalCreate, idempotency_key: Key
) -> SuccessResponse[s.Proposal]:
    """22. 웹 수정 제안(web)."""
    data = await routes_proposals.create_proposal(
        request, user, project_id, payload, idempotency_key
    )
    return respond(request, data)


@router.post(
    "/proposals/{proposal_id}/approve",
    response_model=SuccessResponse[s.RequirementDetail],
    operation_id="tft_approve_rfp_proposal",
)
async def approve_proposal(
    request: Request,
    user: User,
    proposal_id: UUID,
    payload: s.ProposalDecision,
    idempotency_key: Key,
) -> SuccessResponse[s.RequirementDetail]:
    """23. 승인 → 요구 새 판본(human_input), 연결 객체 재검토 표시, 감사에 제안자·승인자."""
    data = await routes_proposals.approve_proposal(
        request, user, proposal_id, payload, idempotency_key
    )
    return respond(request, data)


@router.post(
    "/proposals/{proposal_id}/reject",
    response_model=SuccessResponse[s.Proposal],
    operation_id="tft_reject_rfp_proposal",
)
async def reject_proposal(
    request: Request,
    user: User,
    proposal_id: UUID,
    payload: s.ProposalRejection,
    idempotency_key: Key,
) -> SuccessResponse[s.Proposal]:
    """24. 기각. 사유 필수."""
    data = await routes_proposals.reject_proposal(
        request, user, proposal_id, payload, idempotency_key
    )
    return respond(request, data)


@router.get(P + "/draft", response_model=SuccessResponse[s.Draft], operation_id="tft_get_rfp_draft")
async def get_draft(
    request: Request, user: User, project_id: UUID, version: int | None = Query(None, ge=1)
) -> SuccessResponse[s.Draft]:
    """25. 현재 또는 지정 초안 판본."""
    return respond(request, await routes_draft.get_draft(request, user, project_id, version))


@router.patch(
    P + "/draft", response_model=SuccessResponse[s.Draft], operation_id="tft_edit_rfp_draft"
)
async def edit_draft(
    request: Request, user: User, project_id: UUID, payload: s.DraftEdit
) -> SuccessResponse[s.Draft]:
    """26. 이유를 남기고 초안 새 판본을 만든다."""
    return respond(request, await routes_draft.edit_draft(request, user, project_id, payload))


@router.post(
    P + "/draft/rewrite",
    status_code=202,
    response_model=SuccessResponse[s.JobAccepted],
    operation_id="tft_rewrite_rfp_draft",
)
async def rewrite_draft(
    request: Request,
    user: User,
    project_id: UUID,
    payload: s.DraftRewrite,
    idempotency_key: Key,
) -> SuccessResponse[s.JobAccepted]:
    """32. 명시한 양식 초안 판본을 모델로 작성한다. 별도 비용 작업이며 실패하면 양식을 유지한다."""
    return respond(
        request,
        await jobs.admit(request, user, project_id, "rfp.draft_rewrite", payload, idempotency_key),
    )


@router.get(
    P + "/draft/rewrites/{job_id}",
    response_model=SuccessResponse[s.RewriteSummary],
    operation_id="tft_get_rfp_draft_rewrite",
)
async def get_draft_rewrite(
    request: Request, user: User, project_id: UUID, job_id: UUID
) -> SuccessResponse[s.RewriteSummary]:
    """33. 모델 작성 작업의 결과 요약."""
    return respond(request, await routes_draft.rewrite_summary(request, user, project_id, job_id))


@router.post(
    P + "/draft/regenerate",
    status_code=201,
    response_model=SuccessResponse[s.Draft],
    operation_id="tft_regenerate_rfp_draft",
)
async def regenerate_draft(
    request: Request,
    user: User,
    project_id: UUID,
    payload: s.DraftRegenerate,
    idempotency_key: Key,
) -> SuccessResponse[s.Draft]:
    """34. 저장된 결과로 초안 새 판본을 만든다. 모델·원문 해석 없음, 처리 중이면 409."""
    return respond(
        request,
        await routes_draft.regenerate(request, user, project_id, payload, idempotency_key),
    )


@router.get(
    P + "/draft/review-versions",
    response_model=SuccessResponse[s.ReviewVersionList],
    operation_id="tft_list_rfp_review_versions",
)
async def list_review_versions(
    request: Request, user: User, project_id: UUID
) -> SuccessResponse[s.ReviewVersionList]:
    """35. 검토본 이력, 최신순."""
    return respond(request, await routes_draft.review_versions(request, user, project_id))


@router.get(
    P + "/draft/exports",
    response_model=SuccessResponse[s.ExportList],
    operation_id="tft_list_rfp_exports",
)
async def list_exports(
    request: Request, user: User, project_id: UUID
) -> SuccessResponse[s.ExportList]:
    """36. 내보내기 이력, 최신순."""
    return respond(request, await routes_draft.exports(request, user, project_id))


@router.post(
    P + "/draft/review-versions",
    status_code=201,
    response_model=SuccessResponse[s.ReviewVersion],
    operation_id="tft_save_rfp_review_version",
)
async def save_review_version(
    request: Request,
    user: User,
    project_id: UUID,
    payload: s.ReviewVersionCreate,
    idempotency_key: Key,
) -> SuccessResponse[s.ReviewVersion]:
    """27. 근거·다음 행동·통합 책임을 확인하고 검토본 저장."""
    return respond(
        request, await routes_draft.save_review(request, user, project_id, payload, idempotency_key)
    )


@router.post(
    P + "/draft/exports",
    status_code=202,
    response_model=SuccessResponse[s.ExportAccepted],
    operation_id="tft_export_rfp_draft",
)
async def export_draft(
    request: Request, user: User, project_id: UUID, payload: s.ExportRequest, idempotency_key: Key
) -> SuccessResponse[s.ExportAccepted]:
    """28. DOCX 비동기 내보내기. PDF는 명시적 422 미지원."""
    return respond(
        request, await routes_draft.export(request, user, project_id, payload, idempotency_key)
    )


@router.get(
    P + "/draft/exports/{export_id}/file",
    operation_id="tft_download_rfp_export",
    responses={
        200: {
            "content": {
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {}
            }
        }
    },
    response_class=Response,
)
async def download_export(
    request: Request, user: User, project_id: UUID, export_id: UUID
) -> Response:
    """29. 완료된 검토본 내보내기를 첨부로 내려받는다."""
    return await routes_draft.download(request, user, project_id, export_id)


@router.post(
    P + "/annotations/process",
    status_code=202,
    response_model=SuccessResponse[s.JobAccepted],
    operation_id="tft_process_rfp_annotations",
)
async def process_annotations(
    project_id: UUID,
    payload: s.AnnotationProcess,
    idempotency_key: Key,
    request: Request,
    principal: User,
) -> SuccessResponse[s.JobAccepted]:
    """30. 범위 밖인 메모 PDF 해석은 접수 전에 명시적 422로 거절한다."""
    data = await jobs.admit(
        request, principal, project_id, "rfp.annotate", payload, idempotency_key
    )
    return respond(request, data)


@router.get(
    "/documents/{document_id}/diagrams",
    response_model=SuccessResponse[s.DiagramList],
    operation_id="tft_list_document_diagrams",
)
async def list_document_diagrams(
    document_id: UUID, document_version_id: UUID, request: Request, principal: User
) -> SuccessResponse[s.DiagramList]:
    return respond(
        request,
        await routes_diagrams.list_diagrams(request, principal, document_id, document_version_id),
    )


@router.get(
    "/documents/{document_id}/diagrams/{source_id}/image",
    operation_id="tft_download_diagram_image",
    response_class=Response,
)
async def download_diagram_image(
    document_id: UUID, source_id: str, document_version_id: UUID, request: Request, principal: User
) -> Response:
    return await routes_diagrams.image(
        request, principal, document_id, document_version_id, source_id
    )


@router.post(
    "/requirements/{requirement_id}/recompare",
    response_model=SuccessResponse[s.RecompareResult],
    operation_id="tft_recompare_rfp_requirement",
)
async def recompare_requirement(
    requirement_id: UUID,
    payload: s.RecompareRequest,
    idempotency_key: Key,
    request: Request,
    principal: User,
) -> SuccessResponse[s.RecompareResult]:
    from backend.api.tft.rfp.workflow import recompare

    return respond(
        request, await recompare(request, principal, requirement_id, payload, idempotency_key)
    )


@router.post(
    "/requirements/{requirement_id}/decision",
    response_model=SuccessResponse[s.DecisionResult],
    operation_id="tft_decide_rfp_requirement",
)
async def decide_requirement(
    requirement_id: UUID,
    payload: s.DecisionRequest,
    idempotency_key: Key,
    request: Request,
    principal: User,
) -> SuccessResponse[s.DecisionResult]:
    """결정 패널의 결정 하나를 바로 확정한다(KAN-219)."""
    from backend.api.tft.rfp.decisions import decide

    return respond(
        request, await decide(request, principal, requirement_id, payload, idempotency_key)
    )


@router.post(
    P + "/decisions/bulk",
    response_model=SuccessResponse[s.BulkDecisionResult],
    operation_id="tft_bulk_decide_rfp_requirements",
)
async def bulk_decide(
    project_id: UUID,
    payload: s.BulkDecisionRequest,
    idempotency_key: Key,
    request: Request,
    principal: User,
) -> SuccessResponse[s.BulkDecisionResult]:
    """같은 프로젝트의 결정 여러 건을 한 트랜잭션으로 확정한다(KAN-228). 한 건이라도 실패하면 모두 저장하지 않는다."""
    from backend.api.tft.rfp.bulk import bulk_decide as run

    return respond(request, await run(request, principal, project_id, payload, idempotency_key))


@router.post(
    P + "/decisions/bulk/{batch_id}/undo",
    response_model=SuccessResponse[s.BulkDecisionResult],
    operation_id="tft_bulk_undo_rfp_decisions",
)
async def bulk_undo(
    project_id: UUID,
    batch_id: UUID,
    idempotency_key: Key,
    request: Request,
    principal: User,
) -> SuccessResponse[s.BulkDecisionResult]:
    """일괄 결정 한 묶음을 되돌린다(KAN-228)."""
    from backend.api.tft.rfp.bulk import bulk_undo as run

    return respond(request, await run(request, principal, project_id, batch_id, idempotency_key))


@router.post(
    P + "/questions/sent",
    response_model=SuccessResponse[s.QuestionSendList],
    operation_id="tft_mark_rfp_questions_sent",
)
async def mark_questions_sent(
    project_id: UUID, payload: s.QuestionsSent, request: Request, principal: User
) -> SuccessResponse[s.QuestionSendList]:
    """질의서를 메일로 보낸 시각을 질문마다 남긴다(KAN-228). 다시 보내도 처음 기록을 그대로 둔다."""
    from backend.api.tft.rfp.bulk import mark_sent

    return respond(request, await mark_sent(request, principal, project_id, payload))


# KAN-226 변경 이력(읽기 전용). 이벤트 종류와 출처는 history.py와 contracts/tft/rfp-data.md.
# 두 중계가 같은 query 키 반복을 막아 kind는 쉼표로 잇는다(kind=decision,recompared).
HistoryKinds = Annotated[str | None, Query(pattern=history.KIND_PATTERN, max_length=200)]


@router.get(
    P + "/history",
    response_model=SuccessResponse[history.HistoryPage],
    operation_id="tft_list_rfp_project_history",
)
async def project_history(
    request: Request,
    user: User,
    project_id: UUID,
    kind: HistoryKinds = None,
    actor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> SuccessResponse[history.HistoryPage]:
    """프로젝트 변경 이력, 최신순. 사람(actor)·종류(kind) 필터."""
    return respond(
        request,
        await history.project_history(
            request,
            user,
            project_id,
            kinds=history.split_kinds(kind),
            actor=actor,
            limit=limit,
            cursor=cursor,
        ),
    )


@router.get(
    "/requirements/{requirement_id}/history",
    response_model=SuccessResponse[history.HistoryPage],
    operation_id="tft_list_rfp_requirement_history",
)
async def requirement_history(
    request: Request,
    user: User,
    requirement_id: UUID,
    kind: HistoryKinds = None,
    actor: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> SuccessResponse[history.HistoryPage]:
    """요구 한 건의 이력, 최신순."""
    return respond(
        request,
        await history.requirement_history(
            request,
            user,
            requirement_id,
            kinds=history.split_kinds(kind),
            actor=actor,
            limit=limit,
            cursor=cursor,
        ),
    )


@router.post(
    P + "/requirements",
    response_model=SuccessResponse[s.DecisionResult],
    operation_id="tft_add_rfp_requirement",
)
async def add_requirement(
    project_id: UUID,
    payload: s.ManualRequirementRequest,
    idempotency_key: Key,
    request: Request,
    principal: User,
) -> SuccessResponse[s.DecisionResult]:
    """원문에서 고른 글로 요구를 직접 추가하고 규칙 비교로 바로 판정한다(KAN-225)."""
    from backend.api.tft.rfp.manual_requirements import add

    return respond(request, await add(request, principal, project_id, payload, idempotency_key))


@router.get(
    P + "/equipment",
    response_model=SuccessResponse[s.EquipmentList],
    operation_id="tft_list_rfp_equipment",
)
async def list_equipment(
    project_id: UUID, request: Request, user: User
) -> SuccessResponse[s.EquipmentList]:
    """KAN-227 프로젝트 설비 목록(직접 추가 칸의 설비 선택). 최신 맥락의 설비 묶음, 없으면 요구 설비 칸."""
    from backend.api.tft.rfp.requirement_fields import list_equipment as equipment

    return respond(request, await equipment(request, user, project_id))
