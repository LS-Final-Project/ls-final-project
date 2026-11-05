"""RFP worker가 부를 처리 함수의 등록 지점(KAN-199 2/2).

처리 함수는 tft 쪽(KAN-186 판정·KAN-184 후보 풀)이 고정 모듈 `backend.api.tft.worker_processor`의
`build(settings) -> Processor`로 준다. 모듈 경로를 환경 변수로 받지 않는다(임의 모듈 import 차단). 모듈이 없으면
worker는 작업을 `TFT_WORKER_PROCESSOR_MISSING`으로 실패 처리한다.

처리 함수 규약:
- `processor(job, emit)`을 부른다. `job`은 아래 `TftJob`(원문 없음)이다.
- 단계가 끝날 때마다 `emit({"step": ..., "counts": ..., "needs_review": ..., "document_version_id": ...})`를
  부른다. step은 계약 표 값만이다. emit은 소유권을 재검사·연장하고, 소유권을 잃었거나 취소 요청이 있으면 예외를
  올린다. 처리 함수는 그 예외를 삼키지 않는다.
- 끝나면 종료 이벤트에 넣을 요약(`counts`·`needs_review`·`document_version_id`)을 돌려준다. 없으면 None이다.
- 재실행 멱등(이미 만든 판본 행 재사용)은 처리 함수가 맡는다.
"""

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any
from uuid import UUID

from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings

PROCESSOR_MODULE = "backend.api.tft.worker_processor"


@dataclass(frozen=True)
class TftJob:
    """처리 함수에 넘기는 작업. 프로젝트 접근 검사는 tft 서비스가 접수 때 마쳤다."""

    job_id: UUID
    kind: str
    project_id: UUID
    input_version: str
    actor_id: UUID


Emit = Callable[[dict[str, Any]], None]
Processor = Callable[[TftJob, Emit], dict[str, Any] | None]


def load_processor(
    settings: Settings, import_module: Callable[[str], ModuleType] = importlib.import_module
) -> Processor:
    """tft 모듈의 처리 함수를 받는다.

    Args:
        settings: worker 설정(모듈 build에 그대로 넘긴다).
        import_module: 검사에서 바꿔 끼우는 import 함수.

    Returns:
        Processor: 처리 함수. 모듈이 없으면 ApplicationError TFT_WORKER_PROCESSOR_MISSING이다(모듈 안의 다른
            import 오류는 그대로 올린다).
    """
    try:
        module = import_module(PROCESSOR_MODULE)
    except ModuleNotFoundError as error:
        if error.name != PROCESSOR_MODULE:
            raise
        raise ApplicationError(
            503, "TFT_WORKER_PROCESSOR_MISSING", "The RFP processor is not connected."
        ) from None
    processor: Processor = module.build(settings)
    return processor
