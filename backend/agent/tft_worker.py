"""RFP 처리 Cloud Run Job 진입점(KAN-199 2/2). 지정 작업(TFT_JOB_ID) 하나만 처리한다.

플랫폼 전송(KAN-199 1/2)이 tft-worker Job을 `TFT_JOB_ID`=작업 ID로 실행한다. 이 모듈은 tft 이미지
(SERVICE_NAME=tft)에서 돌므로 `backend.api.platform`·`backend.api.cs`를 import하지 않는다.

- 게이트: `TFT_WORKER_ENABLED`(기본 꺼짐)가 켜졌을 때만. 로그인 역할은 `tft_worker` 멤버여야 한다.
- 소유권·이벤트는 `job_runtime`(C/S worker와 공통)이다. 처리 함수가 단계마다 emit을 부르면 소유권을 재검사·연장하고
  job.progress를 남긴다. 소유권을 잃으면 아무것도 쓰지 않고 멈추고(LEASE_LOST), 취소 요청이면 CANCELLED로 끝낸다.
- 처리 함수가 정상 반환하면 SUCCEEDED다(needs_review여도 같다. PARTIAL은 쓰지 않는다). 예외는 FAILED와 고정
  failure_code(`TFT_WORKER_PROCESSOR_MISSING`·`TFT_WORKER_PROCESSING_FAILED`)다.
- 처리 함수가 넘긴 값은 계약 허용 키만 형식을 확인해 저장한다(오류 코드·원문 키는 버린다).
"""

import os
import re
from collections.abc import Callable
from typing import Any
from uuid import UUID

import psycopg
from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.agent.job_runtime import (
    LeaseLost,
    check_role,
    claim,
    connect,
    failure,
    finish,
    parse_job_id,
    progress,
)
from backend.agent.processor_registry import Processor, TftJob, load_processor
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings

__all__ = ["TftWorkerSettings", "execute_job", "main", "parse_job_id"]

# contracts/tft/rfp-jobs.md 단계 표.
STEPS = (
    "parse",
    "extract",
    "search",
    "compare",
    "supplement",
    "candidates",
    "draft",
    "export",
    "annotate",
)
# 처리 함수 load 구간에서 worker가 그대로 인정하는 고정 실패 코드. 그 밖은 TFT_WORKER_PROCESSING_FAILED로 저장한다.
LOAD_CODES = frozenset({"TFT_WORKER_PROCESSOR_MISSING"})
UUID_TEXT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class TftWorkerSettings(BaseSettings):
    """worker 실행 허용과 lease 길이. 기본 비활성이다."""

    model_config = SettingsConfigDict(env_prefix="TFT_WORKER_", extra="ignore")
    enabled: bool = False
    # 단계 하나(문서 해석·판정 등)가 이 안에 끝나야 한다. 단계마다 연장한다.
    lease_seconds: int = Field(default=300, ge=10, le=900)


class Cancelled(Exception):
    """취소 요청이 보여 처리 함수를 멈춘다."""


def event_payload(values: dict[str, Any], *, final: bool = False) -> dict[str, Any]:
    """처리 함수가 넘긴 값에서 계약 허용 키만 형식을 확인해 남긴다.

    Args:
        values: 처리 함수가 넘긴 단계 값 또는 종료 요약.
        final: 종료 요약이면 True(step을 받지 않는다).

    Returns:
        dict: step·document_version_id·counts·needs_review 중 있는 것. 형식이 틀리면 ValueError다.
    """
    payload: dict[str, Any] = {}
    if not final:
        if values.get("step") not in STEPS:
            raise ValueError("TFT_WORKER_BAD_EVENT")
        payload["step"] = values["step"]
    if "document_version_id" in values:
        value = values["document_version_id"]
        if not isinstance(value, str) or not UUID_TEXT.fullmatch(value):
            raise ValueError("TFT_WORKER_BAD_EVENT")
        payload["document_version_id"] = value
    if "counts" in values:
        counts = values["counts"]
        if (
            not isinstance(counts, dict)
            or set(counts) != {"requirements", "proposals"}
            or not all(type(n) is int and n >= 0 for n in counts.values())
        ):
            raise ValueError("TFT_WORKER_BAD_EVENT")
        payload["counts"] = dict(counts)
    if "needs_review" in values:
        if type(values["needs_review"]) is not bool:
            raise ValueError("TFT_WORKER_BAD_EVENT")
        payload["needs_review"] = values["needs_review"]
    return payload


def execute_job(
    settings: Settings,
    worker: TftWorkerSettings,
    job_id: UUID,
    *,
    processor_loader: Callable[[Settings], Processor] = load_processor,
) -> str:
    """지정 작업 하나를 인수해 처리 함수를 부르고 상태를 확정한다.

    Args:
        settings: worker DB·실행 환경.
        worker: 실행 허용과 lease 길이.
        job_id: TFT_JOB_ID.
        processor_loader: 처리 함수 받기(검사에서 바꿔 끼운다).

    Returns:
        str: TFT_JOB_SUCCEEDED·TFT_JOB_CANCELLED·NOT_CLAIMED·LEASE_LOST. 실패는 작업을 FAILED로 남기고
            ApplicationError(고정 코드)를 올린다.
    """
    if not worker.enabled:
        raise ApplicationError(503, "TFT_WORKER_DISABLED", "RFP worker is disabled.")
    with connect(settings) as conn:
        check_role(conn, "tft_worker")
    lease = claim(settings, "tft", worker.lease_seconds, job_id)
    if lease is None:
        return "NOT_CLAIMED"
    if lease == "CANCELLED":
        return "TFT_JOB_CANCELLED"
    job = TftJob(lease.job_id, lease.kind, lease.subject_id, lease.input_version, lease.actor_id)

    def emit(values: dict[str, Any]) -> None:
        """단계 하나를 남긴다(처리 함수가 부른다).

        Args:
            values: 단계 값.

        Returns:
            None. 취소 요청이면 Cancelled, 소유권을 잃었으면 LeaseLost다.
        """
        if not progress(settings, worker.lease_seconds, lease, event_payload(values)):
            raise Cancelled

    # 처리 함수 load 구간에서만 TFT_WORKER_PROCESSOR_MISSING을 인정한다. 처리 구간의 같은 코드는 PROCESSING_FAILED다.
    try:
        processor = processor_loader(settings)
    except Exception as error:
        raise failure(settings, lease, error, LOAD_CODES, "TFT_WORKER_PROCESSING_FAILED") from None
    try:
        try:
            summary = processor(job, emit) or {}
        except Cancelled:
            finish(settings, lease, "CANCELLED", {})
            return "TFT_JOB_CANCELLED"
        finish(settings, lease, "SUCCEEDED", event_payload(summary, final=True))
        return "TFT_JOB_SUCCEEDED"
    except LeaseLost:
        return "LEASE_LOST"
    except Exception as error:
        # 처리 함수(다른 팀 코드)가 올린 코드는 그대로 저장하지 않는다(신뢰 경계).
        raise failure(settings, lease, error, frozenset(), "TFT_WORKER_PROCESSING_FAILED") from None


def main() -> None:
    """Jobs override로 받은 작업만 처리하고 실패를 고정 코드로 종료한다.

    Args:
        없음. 작업 ID와 설정은 서버 환경에서 읽는다.

    Returns:
        None: 성공·미인수·취소·소유권 상실은 정상 종료, 입력·권한·처리 실패는 비정상 종료다.
    """
    try:
        job_id = parse_job_id(os.getenv("TFT_JOB_ID"))
        result = execute_job(Settings(), TftWorkerSettings(), job_id)
    except ApplicationError as error:
        raise SystemExit(error.code) from None
    except ValidationError:
        raise SystemExit("INVALID_WORKER_CONFIGURATION") from None
    except psycopg.Error:
        raise SystemExit("DEPENDENCY_UNAVAILABLE") from None
    print(result)


if __name__ == "__main__":
    main()
