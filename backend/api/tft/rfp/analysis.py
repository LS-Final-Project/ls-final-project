"""KAN-232 지난 분석 기록과 다시 분석 방식(읽기 전용).

진행 이벤트는 이미 플랫폼 `runtime.job_events`에 저장되고 지우지 않는다. 끝난 작업도
`GET /api/v1/platform/jobs/{job_id}/events?after=0`이 처음부터 재생한다. 그래서 새 저장소는 만들지 않고,
화면이 어떤 작업을 재생할지 고를 수 있게 프로젝트의 분석 접수 목록과 다음 처리 실행의 방식만 낸다.
- jobs: tft.rfp_commands의 처리(project.process)·맥락(context.process) 접수, 최신순. 상태는 플랫폼 작업 조회가 낸다.
- process_mode: 최신 판본의 규격 문서(RFP_SPEC·ADDENDUM·DRAWING)가 모두 조항을 가지면 "regrade"(원문을 다시 읽지 않고
  저장 요구를 모델 없이 재채점), 하나라도 없으면 "extract"(새 판본 추출, 모델 비용), 규격 문서가 없으면 null.
  처리 함수(worker_processor)의 `document_processed` 규칙과 같다.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import Request

from backend.api.tft.rfp.routes_questions import visible_project
from backend.api.tft.rfp.schemas import Model
from backend.shared.access import Principal
from backend.shared.database import connection

JOB_LIMIT = 20
KINDS = {"project.process": "rfp.process", "context.process": "rfp.context"}


class AnalysisJob(Model):
    job_id: UUID
    kind: Literal["rfp.process", "rfp.context"]
    requested_at: datetime
    actor_user_id: UUID


class AnalysisOverview(Model):
    jobs: list[AnalysisJob]
    process_mode: Literal["regrade", "extract"] | None


async def overview(request: Request, principal: Principal, project_id: UUID) -> AnalysisOverview:
    async with connection(request.app.state.settings) as conn:
        await visible_project(
            conn, principal, project_id, "TFT_PROJECT_NOT_FOUND", "The project was not found."
        )
        cursor = await conn.execute(
            "SELECT result->>'job_id' AS job_id, operation, created_at, actor_user_id "
            "FROM tft.rfp_commands WHERE project_id = %s "
            "AND operation IN ('project.process', 'context.process') AND result ? 'job_id' "
            "ORDER BY created_at DESC LIMIT %s",
            (project_id, JOB_LIMIT),
        )
        jobs = [
            AnalysisJob(
                job_id=row["job_id"],
                kind=KINDS[row["operation"]],
                requested_at=row["created_at"],
                actor_user_id=row["actor_user_id"],
            )
            for row in await cursor.fetchall()
        ]
        cursor = await conn.execute(
            "SELECT EXISTS (SELECT 1 FROM tft.clauses c WHERE c.document_version_id = dv.id) "
            "AS processed FROM tft.documents d JOIN tft.document_versions dv "
            "ON dv.document_id = d.id AND dv.version = d.latest_version "
            "WHERE d.project_id = %s AND d.kind IN ('RFP_SPEC', 'ADDENDUM', 'DRAWING')",
            (project_id,),
        )
        processed = [row["processed"] for row in await cursor.fetchall()]
    mode: Literal["regrade", "extract"] | None = (
        None if not processed else "regrade" if all(processed) else "extract"
    )
    return AnalysisOverview(jobs=jobs, process_mode=mode)
