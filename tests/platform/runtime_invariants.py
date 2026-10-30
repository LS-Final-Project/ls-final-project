"""작업 실행 DB 불변식(KAN-150). 재시도·동시 인수·중단·복구 시나리오 검사가 끝난 뒤 부른다.

시나리오가 어떤 순서로 겹쳐도 runtime.jobs·job_control·job_events가 서로 맞아야 한다.
1. 끝난 작업(SUCCEEDED·FAILED·CANCELLED)에는 소유자(holder·lease)가 남지 않는다.
2. 끝난 작업에는 종료 이벤트(job.completed·job.failed)가 정확히 하나 있고, 그 state가 작업 상태와 같다.
3. 끝나지 않은 작업에는 종료 이벤트가 없다.
4. 시작 이벤트(job.started) 수는 인수 횟수(generation)와 같다(인수마다 generation이 1 늘고 시작을 한 번 남긴다).
5. failure_code는 FAILED일 때만 있다.
"""

from collections.abc import Iterable
from uuid import UUID

import psycopg

TERMINAL = ("SUCCEEDED", "FAILED", "CANCELLED")
FINISH_EVENTS = ("job.completed", "job.failed")


def violations(dsn: str, job_ids: Iterable[UUID]) -> list[str]:
    """주어진 작업들의 불변식 위반을 문장으로 돌려준다(없으면 빈 목록).

    Args:
        dsn: 관리자 DSN.
        job_ids: 검사할 작업 ID. 검사 DB를 다른 검사와 함께 쓰므로 그 시나리오의 작업만 본다.

    Returns:
        list[str]: '작업ID: 위반 내용' 목록.
    """
    found: list[str] = []
    with psycopg.connect(dsn) as conn:
        for job_id in job_ids:
            status, holder, expires, generation, code = conn.execute(
                "SELECT j.status,c.holder_id,c.lease_expires_at,c.generation,c.failure_code "
                "FROM runtime.jobs j JOIN runtime.job_control c ON c.job_id=j.id WHERE j.id=%s",
                (job_id,),
            ).fetchone()
            events = conn.execute(
                "SELECT type,payload->>'state' FROM runtime.job_events WHERE job_id=%s ORDER BY sequence",
                (job_id,),
            ).fetchall()
            finishes = [state for kind, state in events if kind in FINISH_EVENTS]
            started = sum(1 for kind, _ in events if kind == "job.started")
            if status in TERMINAL:
                if holder is not None or expires is not None:
                    found.append(f"{job_id}: 끝난 작업({status})에 소유자가 남음")
                if finishes != [status]:
                    found.append(f"{job_id}: 끝난 작업({status})의 종료 이벤트 {finishes}")
            elif finishes:
                found.append(f"{job_id}: 끝나지 않은 작업({status})에 종료 이벤트 {finishes}")
            if started != generation:
                found.append(f"{job_id}: 시작 이벤트 {started}개, 인수 횟수 {generation}")
            if (code is not None) != (status == "FAILED"):
                found.append(f"{job_id}: 상태 {status}인데 failure_code={code}")
    return found
