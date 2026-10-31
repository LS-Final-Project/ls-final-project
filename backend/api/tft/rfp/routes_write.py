"""경로 2·4·5·6: 프로젝트 접수·수정·담당 지정과 문서 업로드(KAN-188).

세 프로젝트 경로는 같은 RfpProjectDetail을 돌려준다. latest_job은 tft의 접수 기록에서 읽어 작업 ID만 있다.
원문은 저장소에 먼저 쓰고 DB를 커밋한다. DB가 실패하면 객체만 남는다(실행 계정에 삭제 권한이 없다).
"""

import asyncio
import hashlib
import io
import zipfile
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, Request
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import ValidationError
from starlette.datastructures import UploadFile
from starlette.types import Message

from backend.api.tft.projects.access import can_edit_project, can_run_processing, manages_project
from backend.api.tft.projects.repository import integration_state
from backend.api.tft.rfp import commands, repository, storage
from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.integration import integration_body
from backend.api.tft.rfp.routes_questions import visible_project
from backend.api.tft.rfp.routes_read import document_out
from backend.engine.documents.model import DOCX_MIME
from backend.shared.access import Principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError

Row = dict[str, Any]
PROJECT_NOT_FOUND = ("TFT_PROJECT_NOT_FOUND", "The project was not found.")
MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
# 파일 하나와 폼 칸의 틀. 받는 동안 넘으면 멈춘다(임시 파일이 한도를 넘지 않게).
MAX_FORM_BYTES = MAX_DOCUMENT_BYTES + 1024 * 1024


def invalid(field: str, reason: str) -> ApplicationError:
    """입력 검증 실패.

    Args:
        field: 문제가 된 칸.
        reason: 사유.

    Returns:
        ApplicationError: 422 VALIDATION_ERROR.
    """
    return ApplicationError(
        422,
        "VALIDATION_ERROR",
        "The request is invalid.",
        details=[{"field": field, "reason": reason}],
    )


def too_large() -> ApplicationError:
    """파일 크기 초과.

    Returns:
        ApplicationError: 413 TFT_DOCUMENT_TOO_LARGE.
    """
    return ApplicationError(413, "TFT_DOCUMENT_TOO_LARGE", "The document exceeds 25 MiB.")


async def project_fields(conn: AsyncConnection[Row], project_id: UUID) -> dict[str, Any]:
    """프로젝트 상세 칸(경로 3과 쓰기 응답이 같이 쓴다). 건수는 목록과 같은 SQL이다.

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.

    Returns:
        dict: RfpProjectDetail 입력. latest_job은 최신 처리 접수의 ID만 있다.
    """
    row, requirements, pending = await repository.project_detail_parts(conn, project_id)
    state = await integration_state(conn, project_id)
    return {
        **{k: row[k] for k in s.PROJECT_COLUMNS},
        "requirement_counts": {
            "by_status": Counter(r["status"] for r in requirements),
            "by_necessity": Counter(r["necessity"] for r in requirements),
            "needs_rereview": sum(r["needs_rereview"] for r in requirements),
            "open_questions": row["open_questions_count"],
        },
        "pending_proposals_by_origin": {
            origin: pending.get(origin, 0) for origin in s.PendingByOrigin.model_fields
        },
        # 접수 기록에서 읽는다. 상태는 플랫폼 소유라 비워 두고 화면이 작업 상태 경로로 본다.
        "latest_job": {"job_id": row["latest_job_id"], "status": None, "step": None}
        if row["latest_job_id"]
        else None,
        "integration_responsibility": integration_body(state),
    }


async def project_detail(conn: AsyncConnection[Row], project_id: UUID) -> s.RfpProjectDetail:
    """쓰기 응답의 프로젝트 상세.

    Args:
        conn: dict_row 연결.
        project_id: 프로젝트 ID.

    Returns:
        s.RfpProjectDetail: 프로젝트 칸과 집계.
    """
    return s.RfpProjectDetail.model_validate(await project_fields(conn, project_id))


async def check_departments(conn: AsyncConnection[Row], departments: list[str]) -> None:
    """검토 부서가 등록된 부서 코드인지 확인한다(없는 코드면 아무도 검토할 수 없다).

    Args:
        conn: dict_row 연결.
        departments: 부서 코드 목록.
    """
    unknown = set(departments) - set(await repository.department_kinds(conn))
    if unknown:
        raise invalid("review_departments", "unknown department")


def strategic_values(fields: Mapping[str, Any], actor: UUID, now: datetime) -> dict[str, Any]:
    """전략 고객 입력 → 칸. 값을 지우면 입력자·메모도 지운다.

    Args:
        fields: 본문에 온 칸.
        actor: 요청 사용자.
        now: 기록 시각.

    Returns:
        dict: 바꿀 칸.
    """
    values: dict[str, Any] = {}
    if "strategic_customer_note" in fields:
        values["strategic_customer_note"] = fields["strategic_customer_note"]
    if "strategic_customer" in fields:
        flag = fields["strategic_customer"]
        values["strategic_customer"] = flag
        values["strategic_customer_set_by"] = actor if flag is not None else None
        values["strategic_customer_set_at"] = now if flag is not None else None
        if flag is None:
            values["strategic_customer_note"] = None
    return values


async def create_project(
    request: Request, principal: Principal, payload: s.ProjectCreate, key: str
) -> s.RfpProjectDetail:
    """2. 접수. 호출자가 담당 sales가 되고 책임자는 자동 지정하지 않는다.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        payload: 접수 본문.
        key: Idempotency-Key.

    Returns:
        s.RfpProjectDetail: 만든(또는 같은 키로 처음 만든) 프로젝트.
    """
    if not principal.has("sales"):
        raise HTTPException(403)
    project_id = uuid4()
    async with connection(request.app.state.settings) as conn:

        async def work() -> dict[str, Any]:
            await check_departments(conn, payload.review_departments)
            fields = payload.model_dump()
            now = datetime.now(UTC)
            await repository.insert_project(
                conn,
                project_id=project_id,
                sales_user_id=principal.user_id,
                known_at=now,
                values={
                    "external_id": await repository.next_project_number(conn),
                    **{
                        k: fields[k]
                        for k in (
                            "name",
                            "client_name",
                            "company_code",
                            "purpose",
                            "customer_goal",
                            "supply_region",
                            "customer_manufacturer_list",
                            "off_list_permission",
                            "review_departments",
                            "data_origin",
                        )
                    },
                    **strategic_values(fields, principal.user_id, now),
                },
            )
            detail = await project_detail(conn, project_id)
            return detail.model_dump(mode="json")

        result = await commands.run_command(
            conn,
            actor=principal.user_id,
            operation="project.create",
            key=key,
            digest=commands.request_hash({}, payload.model_dump(mode="json")),
            project_id=project_id,
            status_code=201,
            work=work,
        )
    return s.RfpProjectDetail.model_validate(result)


async def update_project(
    request: Request, principal: Principal, project_id: UUID, payload: s.ProjectUpdate
) -> s.RfpProjectDetail:
    """4. 본문에 있는 칸만 바꾼다. 상태 두 개는 이 경로로 쓰지 못한다.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        project_id: 프로젝트 ID.
        payload: 수정 본문.

    Returns:
        s.RfpProjectDetail: 수정한 프로젝트.
    """
    async with connection(request.app.state.settings) as conn:
        project = await visible_project(conn, principal, project_id, *PROJECT_NOT_FOUND)
        if not can_edit_project(principal, project):
            raise HTTPException(403)
        fields = {k: getattr(payload, k) for k in payload.model_fields_set - {"version"}}
        for name in ("name", "review_departments", "integration_responsibility"):
            if name in fields and fields[name] is None:
                raise invalid(name, "must not be null")
        if "review_departments" in fields:
            await check_departments(conn, fields["review_departments"])
        now = datetime.now(UTC)
        values = {
            k: fields[k]
            for k in (
                "name",
                "client_name",
                "customer_goal",
                "review_departments",
                "supply_region",
                "customer_manufacturer_list",
                "off_list_permission",
            )
            if k in fields
        }
        values |= strategic_values(fields, principal.user_id, now)
        if (integration := fields.get("integration_responsibility")) is not None:
            values |= {
                "integration_holder": integration.holder,
                "integration_holder_candidates": integration.holder_candidate_codes,
                "integration_note": integration.note,
                "integration_set_by": principal.user_id,
                "integration_set_at": now,
            }
        before = await repository.project_values(conn, project_id, list(values))
        stored = dict(values)
        if "integration_holder_candidates" in stored:
            stored["integration_holder_candidates"] = Jsonb(stored["integration_holder_candidates"])
        await repository.update_project(conn, project_id, payload.version, stored)
        await commands.audit(
            conn,
            project_id=project_id,
            entity="project",
            entity_id=project_id,
            action="project.update",
            actor=principal.user_id,
            detail={"from": before, "to": values},
        )
        return await project_detail(conn, project_id)


async def assign_project(
    request: Request, principal: Principal, project_id: UUID, payload: s.Assignment
) -> s.RfpProjectDetail:
    """5. 관리 대상 manager의 담당 sales·책임자 지정. 호출마다 이력 한 행을 남긴다.

    다른 manager의 관리 범위는 tft에서 읽을 수 없어 책임자 후보는 담당 sales·기존 책임자·호출한 manager다.

    Args:
        request: 요청(설정).
        principal: 요청 사용자.
        project_id: 프로젝트 ID.
        payload: 지정 본문.

    Returns:
        s.RfpProjectDetail: 지정 뒤 프로젝트.
    """
    async with connection(request.app.state.settings) as conn:
        project = await visible_project(conn, principal, project_id, *PROJECT_NOT_FOUND)
        if not manages_project(principal, project_id):
            raise HTTPException(403)
        candidates = {
            None,
            payload.sales_user_id,
            project["responsible_user_id"],
            principal.user_id,
        }
        if payload.responsible_user_id not in candidates:
            raise invalid("responsible_user_id", "not a candidate of this project")
        version = await repository.update_project(
            conn,
            project_id,
            payload.version,
            {
                "sales_user_id": payload.sales_user_id,
                "responsible_user_id": payload.responsible_user_id,
            },
        )
        assignment_id = await repository.insert_assignment(
            conn,
            project=project,
            project_version=version,
            sales_user_id=payload.sales_user_id,
            responsible_user_id=payload.responsible_user_id,
            reason=payload.reason,
            changed_by=principal.user_id,
        )
        await commands.audit(
            conn,
            project_id=project_id,
            entity="assignment",
            entity_id=assignment_id,
            action="assignment.update",
            actor=principal.user_id,
            detail={
                "from": {k: project[k] for k in ("sales_user_id", "responsible_user_id")},
                "to": {
                    "sales_user_id": payload.sales_user_id,
                    "responsible_user_id": payload.responsible_user_id,
                },
            },
        )
        return await project_detail(conn, project_id)


def document_mime(data: bytes) -> str | None:
    """내용으로 형식을 판별한다. 클라이언트 Content-Type은 믿지 않는다.

    Args:
        data: 업로드 바이트.

    Returns:
        str | None: PDF·DOCX mime. 둘 다 아니면 None.
    """
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    try:
        # 목록만 읽고 압축은 풀지 않는다.
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return DOCX_MIME if "word/document.xml" in archive.namelist() else None
    except zipfile.BadZipFile:
        return None


async def read_upload(request: Request) -> tuple[s.UploadForm, bytes]:
    """multipart 본문을 읽고 폼 칸을 검증한다.

    Args:
        request: 요청.

    Returns:
        tuple: (폼 칸, 파일 바이트). 크기 초과는 413, 형식 오류는 422다.
    """
    received = 0

    async def limited() -> Message:
        # Content-Length가 없는 chunked 요청도 받는 동안 멈춘다.
        nonlocal received
        message = await request.receive()
        received += len(message.get("body", b""))
        if received > MAX_FORM_BYTES:
            raise too_large()
        return message

    async with Request(request.scope, limited).form(max_files=1, max_fields=10) as body:
        upload = body.get("file")
        if not isinstance(upload, UploadFile):
            raise invalid("file", "required")
        try:
            form = s.UploadForm.model_validate({k: v for k, v in body.items() if k != "file"})
        except ValidationError as error:
            first = error.errors()[0]
            raise invalid(".".join(map(str, first["loc"])), first["msg"]) from None
        data = await upload.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise too_large()
    return form, data


async def upload_document(
    request: Request, principal: Principal, project_id: UUID, key: str
) -> s.DocumentVersion:
    """6. 업로드. 파싱하지 않는다. supersedes_document_id를 주면 그 문서의 다음 판본이다.

    Args:
        request: 요청(설정·multipart 본문).
        principal: 요청 사용자.
        project_id: 프로젝트 ID.
        key: Idempotency-Key.

    Returns:
        s.DocumentVersion: 저장한(또는 같은 키로 처음 저장한) 판본.
    """
    settings = request.app.state.settings
    async with connection(settings) as conn:
        project = await visible_project(conn, principal, project_id, *PROJECT_NOT_FOUND)
        if not can_run_processing(principal, project):
            raise HTTPException(403)
    unavailable = ApplicationError(
        503, "TFT_DOCUMENT_UNAVAILABLE", "The document store is not available."
    )
    store = storage.document_store(storage.DocumentStorageSettings())
    if store is None:
        raise unavailable
    form, data = await read_upload(request)
    mime = document_mime(data)
    if mime is None:
        raise invalid("file", "only DOCX and PDF are accepted")
    sha256 = await asyncio.to_thread(lambda: hashlib.sha256(data).hexdigest())
    digest = commands.request_hash(
        {"project_id": project_id}, {**form.model_dump(mode="json"), "sha256": sha256}
    )

    async with connection(settings) as conn:

        async def work() -> dict[str, Any]:
            for linked in (form.supersedes_document_id, form.base_document_id):
                if linked is not None and (
                    await repository.document_project(conn, linked) != project_id
                ):
                    raise ApplicationError(
                        404, "TFT_DOCUMENT_NOT_FOUND", "The document was not found."
                    )
            if form.supersedes_document_id is not None:
                current = await repository.latest_document_version(
                    conn, form.supersedes_document_id
                )
                if current is None or current["kind"] != form.kind:
                    raise invalid("kind", "differs from the superseded document")
            version_id = uuid4()
            storage_key = f"rfp/{project_id}/{version_id}"
            try:
                await store.put(storage_key, data)
            except storage.DocumentUnavailable:
                raise unavailable from None
            document_id = await repository.insert_document_version(
                conn,
                project_id=project_id,
                version_id=version_id,
                form=form,
                mime=mime,
                storage_key=storage_key,
                sha256=sha256,
                size=len(data),
                edit_trace_support=mime == DOCX_MIME,
                actor=principal.user_id,
            )
            row = await repository.latest_document_version(conn, document_id)
            if row is None:
                raise ValueError("TFT_DOCUMENT_MISSING")
            return s.DocumentVersion.model_validate(document_out(row)).model_dump(mode="json")

        result = await commands.run_command(
            conn,
            actor=principal.user_id,
            operation="document.upload",
            key=key,
            digest=digest,
            project_id=project_id,
            status_code=201,
            work=work,
        )
    return s.DocumentVersion.model_validate(result)
