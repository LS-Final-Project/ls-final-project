"""지정 UUID 하나만 처리하는 local/test 합성 Job 진입점."""

import asyncio
import os
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError

from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.evaluations.admission import admission_available
from backend.api.platform.evaluations.smoke_worker import (
    REVIEW_CODES,
    advance_run,
    claim_run,
    park_run,
)
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings


def parse_run_id(value: str | None) -> UUID:
    """누락·잘못된 입력을 큐 탐색으로 대체하지 않고 거부한다.

    Args:
        value: EVALUATION_RUN_ID 서버 환경 변수.

    Returns:
        UUID: 표준 하이픈 형식의 대상 실행 식별자.
    """
    try:
        run_id = UUID(value or "")
        if str(run_id) != value:
            raise ValueError
        return run_id
    except ValueError:
        raise ApplicationError(400, "INVALID_RUN_ID", "Run ID is required.") from None


async def execute_run(settings: Settings, access: EvaluationAccessSettings, run_id: UUID) -> bool:
    """전용 DB 역할로 지정 실행만 인수하고 기존 승인·lease 검사를 매 단계 적용한다.

    Args:
        settings: 전용 worker DB 로그인과 local/test 환경.
        access: 현재 지정 평가자·승인 hash·합성 실행 허용 설정.
        run_id: 명시적으로 전달받은 대상 UUID.

    Returns:
        bool: 이번 호출이 완료하면 True, 이미 종료/다른 소유자/보류 상태면 False.
    """
    if not admission_available(settings, access):
        raise ApplicationError(503, "SMOKE_WORKER_DISABLED", "Smoke worker is disabled.")
    async with connection(settings) as conn:
        cursor = await conn.execute(
            "SELECT current_user=session_user AND pg_has_role(current_user,'evaluation_worker','USAGE') AND NOT "
            "(rolsuper OR rolcreaterole OR rolcreatedb OR rolbypassrls) AND NOT EXISTS ("
            "SELECT 1 FROM pg_roles p WHERE p.rolname IN "
            "('platform_app','cs_app','tft_app','platform_migrator','cs_migrator','tft_migrator') "
            "AND pg_has_role(current_user,p.oid,'MEMBER')) AS allowed "
            "FROM pg_roles WHERE rolname=current_user"
        )
        row = await cursor.fetchone()
        if row is None or not row["allowed"]:
            raise ApplicationError(403, "WORKER_ROLE_REQUIRED", "Worker role is required.")
    lease = None
    try:
        lease = await claim_run(settings, access, run_id)
        if lease is None:
            return False
        while not await advance_run(settings, access, lease):
            pass
        return True
    except ApplicationError as error:
        if error.code in REVIEW_CODES:
            await park_run(settings, access, run_id, error.code, lease)
        raise


def main() -> None:
    """Jobs override로 전달된 실행만 처리하며 실패를 고정 코드로 출력한다.

    Args:
        없음. 실행 UUID와 허용 설정은 서버 환경에서 읽는다.

    Returns:
        None: 완료/미인수는 정상 종료, 잘못된 입력·권한·DB 실패는 비정상 종료다.
    """
    try:
        run_id = parse_run_id(os.getenv("EVALUATION_RUN_ID"))
        done = asyncio.run(execute_run(Settings(), EvaluationAccessSettings(), run_id))
    except ApplicationError as error:
        raise SystemExit(error.code) from None
    except HTTPException:
        raise SystemExit("DEPENDENCY_UNAVAILABLE") from None
    except ValidationError:
        raise SystemExit("INVALID_WORKER_CONFIGURATION") from None
    print("SYNTHETIC_COMPLETED" if done else "NOT_CLAIMED")


if __name__ == "__main__":
    main()
