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
    jobs,
    routes_draft,
    routes_proposals,
    routes_questions,
    routes_read,
    routes_supplement,
    routes_write,
)
from backend.api.tft.rfp import schemas as s
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
    """2. 접수. 호출자가 담당 sales가 된다. latest_job은 쓰기 응답에서 항상 null이다."""
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
    has_open_question: bool | None = None,
) -> SuccessResponse[s.RequirementList]:
    """12. 요구 목록·필터 7개."""
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
            "has_open_question": has_open_question,
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
    """30. (A12 조건부) 메모 PDF 해석 → pdf_annot 제안. 접수만 하고 해석 단계는 처리 함수 몫이다."""
    data = await jobs.admit(
        request, principal, project_id, "rfp.annotate", payload, idempotency_key
    )
    return respond(request, data)
