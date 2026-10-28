"""RFP 자사 대응 경로의 골격(KAN-182, A01). OpenAPI 초안을 만들기 위한 선언이다.

명세 v1.1 §3의 30개 경로 중 아직 없는 28개다(1·3번 목록·상세는 projects/routes.py에 있고
응답 확장은 구현 작업이 한다). 모든 경로는 구현 전까지 501 `TFT_NOT_IMPLEMENTED`를 돌려준다.
구현 담당: 경로 1~19·25·28·29는 KAN-188(A07), 질문·제안·검토본은 KAN-189(A08), 30은 KAN-192(A12).
"""

from typing import Annotated, Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, Header, Query

from backend.api.tft.rfp import schemas as s
from backend.shared.errors import ApplicationError
from backend.shared.models import ErrorResponse, SuccessResponse

router = APIRouter(
    prefix="/api/v1/tft",
    tags=["rfp"],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse, "description": "Not found or outside the project scope"},
        409: {"model": ErrorResponse, "description": "TFT_VERSION_CONFLICT and similar"},
        422: {"model": ErrorResponse},
        501: {"model": ErrorResponse, "description": "Declared in KAN-182, not implemented yet"},
    },
)


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
    openapi_extra=A07,
)
async def create_project(payload: s.ProjectCreate, idempotency_key: Key) -> NoReturn:
    """2. 접수. 호출자가 담당 sales가 된다."""
    not_implemented()


@router.patch(
    P,
    response_model=SuccessResponse[s.RfpProjectDetail],
    operation_id="tft_update_rfp_project",
    openapi_extra=A07,
)
async def update_project(project_id: UUID, payload: s.ProjectUpdate) -> NoReturn:
    """4. 이름·고객 목표·검토 부서 목록. 상태 두 개는 직접 쓰지 못한다."""
    not_implemented()


@router.patch(
    P + "/assignment",
    response_model=SuccessResponse[s.RfpProjectDetail],
    operation_id="tft_assign_rfp_project",
    openapi_extra=A07,
)
async def assign_project(project_id: UUID, payload: s.Assignment) -> NoReturn:
    """5. manager가 담당 sales·책임자를 지정한다. 이력을 남긴다."""
    not_implemented()


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
                        "supersedes_document_id": {"type": "string", "format": "uuid"},
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
    openapi_extra={**A07, **UPLOAD_BODY},
)
async def upload_document(project_id: UUID, idempotency_key: Key) -> NoReturn:
    """6. DOCX·PDF 25 MiB 이하(multipart). 업로드만으로는 파싱하지 않는다.

    본문 형식은 OpenAPI에 직접 적었다. 구현(KAN-188) 때 multipart 파서를 붙인다.
    """
    not_implemented()


@router.get(
    P + "/documents",
    response_model=SuccessResponse[s.DocumentList],
    operation_id="tft_list_rfp_documents",
    openapi_extra=A07,
)
async def list_documents(project_id: UUID) -> NoReturn:
    """7. 문서·판본 목록."""
    not_implemented()


@router.get(
    "/documents/{document_id}",
    response_model=SuccessResponse[s.DocumentDetail],
    operation_id="tft_get_rfp_document",
    openapi_extra=A07,
)
async def get_document(document_id: UUID) -> NoReturn:
    """8. 판본·편집 흔적 지원 여부·조항 수·삭제 값 수."""
    not_implemented()


@router.get(
    "/documents/{document_id}/clauses",
    response_model=SuccessResponse[s.ClauseList],
    operation_id="tft_list_rfp_clauses",
    openapi_extra=A07,
)
async def list_clauses(
    document_id: UUID,
    parent: UUID | None = None,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
) -> NoReturn:
    """9. 조항 트리. 취소선 값은 deleted_values로만 낸다."""
    not_implemented()


@router.get(
    "/documents/{document_id}/file",
    operation_id="tft_download_rfp_document",
    response_model=None,
    openapi_extra=A07,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def download_document(document_id: UUID) -> NoReturn:
    """10. 원문 내려받기. 접근 검사는 프로젝트 조회와 같다."""
    not_implemented()


@router.post(
    P + "/process",
    status_code=202,
    response_model=SuccessResponse[s.JobAccepted],
    operation_id="tft_process_rfp",
    openapi_extra=A07,
)
async def process(project_id: UUID, payload: s.ProcessRequest, idempotency_key: Key) -> NoReturn:
    """11. 처리 작업 접수(플랫폼 runtime.jobs, owner=tft, kind=rfp.process)."""
    not_implemented()


@router.get(
    P + "/requirements",
    response_model=SuccessResponse[s.RequirementList],
    operation_id="tft_list_rfp_requirements",
    openapi_extra=A07,
)
async def list_requirements(
    project_id: UUID,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = None,
    document_version_id: UUID | None = None,
    equipment: str | None = None,
    task: str | None = None,
    necessity: s.Necessity | None = None,
    status: s.RequirementStatus | None = None,
    needs_rereview: bool | None = None,
    has_open_question: bool | None = None,
) -> NoReturn:
    """12. 요구 목록·필터 7개."""
    not_implemented()


@router.get(
    "/requirements/{requirement_id}",
    response_model=SuccessResponse[s.RequirementDetail],
    operation_id="tft_get_rfp_requirement",
    openapi_extra=A07,
)
async def get_requirement(requirement_id: UUID) -> NoReturn:
    """13. 원문·요구·판단·연결."""
    not_implemented()


@router.get(
    "/requirements/{requirement_id}/comparison",
    response_model=SuccessResponse[s.Comparison],
    operation_id="tft_get_rfp_comparison",
    openapi_extra=A07,
)
async def get_comparison(requirement_id: UUID) -> NoReturn:
    """14. 자사 비교. 같은 속성·단위·조건끼리만 비교한다."""
    not_implemented()


@router.get(
    "/requirements/{requirement_id}/supplement-options",
    response_model=SuccessResponse[s.SupplementOptions],
    operation_id="tft_get_rfp_supplement_options",
    openapi_extra=A07,
)
async def get_supplement_options(requirement_id: UUID) -> NoReturn:
    """15. 보완 경로 선택지. 금지 경로는 allowed=false."""
    not_implemented()


@router.post(
    "/requirements/{requirement_id}/supplement-selection",
    response_model=SuccessResponse[s.SupplementSelected],
    operation_id="tft_select_rfp_supplement",
    openapi_extra=A07,
)
async def select_supplement(
    requirement_id: UUID, payload: s.SupplementSelection, idempotency_key: Key
) -> NoReturn:
    """16. 경로·후보·역할 분담 선택(사람 결정, human_input 새 판본)."""
    not_implemented()


@router.get(
    P + "/partner-candidates",
    response_model=SuccessResponse[s.PartnerCandidateList],
    operation_id="tft_list_rfp_partner_candidates",
    openapi_extra=A07,
)
async def list_partner_candidates(project_id: UUID, requirement_id: UUID | None = None) -> NoReturn:
    """17. 가상 협력사 후보와 요구별 등급. 점수는 내지 않는다."""
    not_implemented()


@router.get(
    P + "/questions",
    response_model=SuccessResponse[s.QuestionList],
    operation_id="tft_list_rfp_questions",
    openapi_extra=A07,
)
async def list_questions(
    project_id: UUID, status: s.QuestionStatus | None = None, requirement_id: UUID | None = None
) -> NoReturn:
    """18. 고객 질문 목록."""
    not_implemented()


@router.post(
    P + "/questions",
    status_code=201,
    response_model=SuccessResponse[s.Question],
    operation_id="tft_create_rfp_question",
    openapi_extra=A07,
)
async def create_question(
    project_id: UUID, payload: s.QuestionCreate, idempotency_key: Key
) -> NoReturn:
    """19. 질문 작성. 회신 전 요구값은 UNDETERMINED로 유지한다."""
    not_implemented()


@router.post(
    "/questions/{question_id}/reply",
    response_model=SuccessResponse[s.Proposal],
    operation_id="tft_reply_rfp_question",
    openapi_extra=A08,
)
async def reply_question(
    question_id: UUID, payload: s.QuestionReply, idempotency_key: Key
) -> NoReturn:
    """20. 회신 기록 → 변경 제안(customer_reply). 승인 전에는 요구가 바뀌지 않는다."""
    not_implemented()


@router.get(
    P + "/proposals",
    response_model=SuccessResponse[s.ProposalList],
    operation_id="tft_list_rfp_proposals",
    openapi_extra=A08,
)
async def list_proposals(
    project_id: UUID,
    status: s.ProposalStatus | None = None,
    origin: s.ProposalOrigin | None = None,
    requirement_id: UUID | None = None,
) -> NoReturn:
    """21. 승인 대기 목록(세 입구 공통)."""
    not_implemented()


@router.post(
    P + "/proposals",
    status_code=201,
    response_model=SuccessResponse[s.Proposal],
    operation_id="tft_create_rfp_proposal",
    openapi_extra=A08,
)
async def create_proposal(
    project_id: UUID, payload: s.ProposalCreate, idempotency_key: Key
) -> NoReturn:
    """22. 웹 수정 제안(web)."""
    not_implemented()


@router.post(
    "/proposals/{proposal_id}/approve",
    response_model=SuccessResponse[s.RequirementDetail],
    operation_id="tft_approve_rfp_proposal",
    openapi_extra=A08,
)
async def approve_proposal(
    proposal_id: UUID, payload: s.ProposalDecision, idempotency_key: Key
) -> NoReturn:
    """23. 승인 → 요구 새 판본(human_input), 연결 객체 재검토 표시, 감사에 제안자·승인자."""
    not_implemented()


@router.post(
    "/proposals/{proposal_id}/reject",
    response_model=SuccessResponse[s.Proposal],
    operation_id="tft_reject_rfp_proposal",
    openapi_extra=A08,
)
async def reject_proposal(
    proposal_id: UUID, payload: s.ProposalRejection, idempotency_key: Key
) -> NoReturn:
    """24. 기각. 사유 필수."""
    not_implemented()


@router.get(
    P + "/draft",
    response_model=SuccessResponse[s.Draft],
    operation_id="tft_get_rfp_draft",
    openapi_extra=A07,
)
async def get_draft(project_id: UUID, version: int | None = Query(None, ge=1)) -> NoReturn:
    """25. 현재(또는 지정) 초안."""
    not_implemented()


@router.patch(
    P + "/draft",
    response_model=SuccessResponse[s.Draft],
    operation_id="tft_edit_rfp_draft",
    openapi_extra=A08,
)
async def edit_draft(project_id: UUID, payload: s.DraftEdit) -> NoReturn:
    """26. 문장 수정 → 초안 새 판본. 근거 없는 문장은 evidence_missing."""
    not_implemented()


@router.post(
    P + "/draft/review-versions",
    status_code=201,
    response_model=SuccessResponse[s.ReviewVersion],
    operation_id="tft_save_rfp_review_version",
    openapi_extra=A08,
)
async def save_review_version(
    project_id: UUID, payload: s.ReviewVersionCreate, idempotency_key: Key
) -> NoReturn:
    """27. 검토본 저장. 조건 미충족이면 422 TFT_DRAFT_INCOMPLETE."""
    not_implemented()


@router.post(
    P + "/draft/exports",
    status_code=202,
    response_model=SuccessResponse[s.JobAccepted],
    operation_id="tft_export_rfp_draft",
    openapi_extra=A07,
)
async def export_draft(
    project_id: UUID, payload: s.ExportRequest, idempotency_key: Key
) -> NoReturn:
    """28. 검토본 내보내기 작업(rfp.export)."""
    not_implemented()


@router.get(
    P + "/draft/exports/{export_id}/file",
    operation_id="tft_download_rfp_export",
    response_model=None,
    openapi_extra=A07,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def download_export(project_id: UUID, export_id: UUID) -> NoReturn:
    """29. 내보낸 파일."""
    not_implemented()


@router.post(
    P + "/annotations/process",
    status_code=202,
    response_model=SuccessResponse[s.JobAccepted],
    operation_id="tft_process_rfp_annotations",
    openapi_extra=A12,
)
async def process_annotations(
    project_id: UUID, payload: s.AnnotationProcess, idempotency_key: Key
) -> NoReturn:
    """30. (A12 조건부) 메모 PDF 해석 → pdf_annot 제안."""
    not_implemented()
