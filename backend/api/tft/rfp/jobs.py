"""RFP 처리 작업 접수(경로 11 process·30 annotations/process, KAN-199·KAN-188).

tft 서비스가 프로젝트 범위·권한·입력을 검사한 뒤 플랫폼 `POST /internal/v1/platform/jobs/tft`를 tft 실행 계정의
ID 토큰으로 부른다(C/S 분석 접수와 같은 방식). 상태·SSE는 플랫폼 `GET /api/v1/platform/jobs/{id}`·`/events`다.

- 기본 비활성: `TFT_PROCESSING_ENABLED`가 꺼져 있으면 503 `TFT_PROCESSOR_NOT_CONNECTED`(플랫폼 미연결과 같은 코드).
- 플랫폼의 409 `TFT_JOB_IN_PROGRESS`·`IDEMPOTENCY_CONFLICT`와 503 `TFT_PROCESSOR_NOT_CONNECTED`는 그대로 옮기고,
  연결 실패·결과 모름·그 밖의 거절은 503 `DEPENDENCY_UNAVAILABLE`이다.
"""

import hashlib
import json
from collections.abc import Sequence
from typing import Any, Literal, Self
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.api.tft.projects.access import can_run_annotations, can_run_processing
from backend.api.tft.rfp import commands, repository
from backend.api.tft.rfp import schemas as s
from backend.api.tft.rfp.routes_questions import visible_project
from backend.shared.access import Principal
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.platform_jobs import (
    PlatformClient,
    PlatformRejected,
    PlatformUnknown,
    PlatformUnreachable,
    is_https_origin,
)
from backend.shared.service_auth import metadata_token

TFT_JOBS = "/internal/v1/platform/jobs/tft"
NOT_CONNECTED = "TFT_PROCESSOR_NOT_CONNECTED"
# 플랫폼 거절 중 그대로 옮기는 것. 나머지는 플랫폼을 쓸 수 없는 것으로 본다.
PASSED_THROUGH = {409: ("TFT_JOB_IN_PROGRESS", "IDEMPOTENCY_CONFLICT"), 503: (NOT_CONNECTED,)}
Kind = Literal["rfp.process", "rfp.annotate"]


class TftProcessingSettings(BaseSettings):
    """플랫폼 작업 접수 주소. 운영자가 플랫폼 접수·worker를 연결한 뒤 켠다."""

    model_config = SettingsConfigDict(
        env_prefix="TFT_PROCESSING_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    enabled: bool = False
    # 플랫폼 서비스 주소(ID 토큰 audience, 요청 origin).
    platform_url: str = ""

    def check(self) -> Self:
        if self.enabled and not is_https_origin(self.platform_url):
            raise ValueError("TFT_PROCESSING_PLATFORM_URL must be an HTTPS origin without a path")
        return self


class PlatformJobs(PlatformClient):
    """플랫폼 tft 작업 접수 클라이언트."""

    # 처리기 미연결 503은 저장하지 않고 거절한 것이다(플랫폼 jobs/tft.py).
    known_server_refusals = frozenset({NOT_CONNECTED})


def configure_processing_jobs(app: FastAPI, value: TftProcessingSettings, **overrides: Any) -> None:
    """설정과 플랫폼 클라이언트를 앱에 둔다. 꺼져 있으면 클라이언트가 없다.

    Args:
        app: tft 앱.
        value: 설정. 켰을 때만 주소를 검사한다.
        **overrides: 검사 주입 `token`(audience → ID 토큰), `transport`.
    """
    app.state.processing_jobs = None
    if not value.check().enabled:
        return
    app.state.processing_jobs = PlatformJobs(
        value.platform_url, overrides.get("token") or metadata_token, overrides.get("transport")
    )


def input_version(document_version_ids: Sequence[UUID]) -> str:
    """처리할 문서 판본 묶음의 SHA-256(플랫폼 멱등 비교용 동결 입력).

    Args:
        document_version_ids: 문서 판본 ID.

    Returns:
        str: 정렬한 ID JSON의 hex SHA-256.
    """
    encoded = json.dumps(sorted(str(i) for i in document_version_ids), separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def invalid(field: str, reason: str) -> ApplicationError:
    return ApplicationError(
        422,
        "VALIDATION_ERROR",
        "The request is invalid.",
        details=[{"field": field, "reason": reason}],
    )


async def admit(
    request: Request,
    principal: Principal,
    project_id: UUID,
    kind: Kind,
    payload: s.ProcessRequest | s.AnnotationProcess,
    key: str,
) -> s.JobAccepted:
    """범위·권한·입력을 검사하고 플랫폼에 작업을 접수한다.

    Args:
        request: 요청(설정·플랫폼 클라이언트).
        principal: 요청 사용자.
        project_id: 프로젝트 ID.
        kind: rfp.process 또는 rfp.annotate.
        payload: 처리 요청 본문.
        key: Idempotency-Key.

    Returns:
        s.JobAccepted: 작업 ID·상태·상태 경로. 같은 키 재전송이면 기존 작업이다.
    """
    async with connection(request.app.state.settings) as conn:
        project = await visible_project(
            conn, principal, project_id, "TFT_PROJECT_NOT_FOUND", "The project was not found."
        )
        allowed = can_run_processing if kind == "rfp.process" else can_run_annotations
        if not allowed(principal, project):
            raise HTTPException(403)
        if isinstance(payload, s.ProcessRequest):
            wanted = payload.document_version_ids
            versions = await repository.project_document_versions(conn, project_id, wanted)
            if not versions:
                raise invalid("document_version_ids", "no document in the project")
            if wanted and len(versions) != len(set(wanted)):
                raise invalid("document_version_ids", "not in the project")
            if payload.reason is None and await repository.has_requirements(conn, project_id):
                raise ApplicationError(
                    422, "TFT_REASON_REQUIRED", "Give a reason to process the project again."
                )
        else:
            wanted = [payload.document_version_id, payload.base_document_version_id]
            versions = await repository.project_document_versions(conn, project_id, wanted)
            if len(versions) != len(set(wanted)):
                raise invalid("document_version_id", "not in the project")
    client = getattr(request.app.state, "processing_jobs", None)
    if client is None:
        raise ApplicationError(503, NOT_CONNECTED, "RFP processing is not connected yet.")
    body = {
        "actor_id": str(principal.user_id),
        "kind": kind,
        "project_id": str(project_id),
        "input_version": input_version(versions),
        "idempotency_key": key,
        "request_hash": commands.request_hash(
            {"project_id": project_id}, payload.model_dump(mode="json")
        ),
    }
    try:
        data = await client.post(TFT_JOBS, body)
    except PlatformRejected as error:
        if error.code in PASSED_THROUGH.get(error.status, ()):
            raise ApplicationError(
                error.status, error.code, "The platform refused the job."
            ) from None
        raise ApplicationError(
            503, "DEPENDENCY_UNAVAILABLE", "The platform did not accept the job."
        ) from None
    except (PlatformUnreachable, PlatformUnknown):
        raise ApplicationError(
            503, "DEPENDENCY_UNAVAILABLE", "The platform did not answer the job request."
        ) from None
    return s.JobAccepted.model_validate(
        {k: data.get(k) for k in ("job_id", "status", "status_url")}
    )
