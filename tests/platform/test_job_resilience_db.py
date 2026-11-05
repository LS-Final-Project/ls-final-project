"""KAN-150 재시도·동시 인수·중단·복구 뒤 작업 DB 불변식. 전용 PostgreSQL·worker 로그인·가짜 공급자로 확인한다.

기존 worker 검사(test_cs_worker_db·test_tft_worker_db)가 시나리오마다 결과를 본다면, 여기서는 (1) 같은 작업을
여러 worker가 동시에 인수하려는 경합, (2) 끝난 작업에 늦게 다시 온 전달, (3) TFT worker의 인수 거부를 더하고,
시나리오가 끝난 뒤 공통 불변식(tests/platform/runtime_invariants.py)이 지켜지는지 본다.
"""

import threading

import pytest

from backend.agent import job_runtime
from tests.platform.runtime_invariants import violations
from tests.platform.test_cs_worker_db import Harness, admin, orphan, snapshot
from tests.platform.test_cs_worker_db import worker_settings as cs_worker_settings  # noqa: F401
from tests.platform.test_tft_worker_db import run as run_tft
from tests.platform.test_tft_worker_db import worker_settings as tft_worker_settings  # noqa: F401

WORKERS = 8


def claim_together(settings, owner, job_id):
    """WORKERS개 스레드가 같은 순간에 같은 작업을 인수하려 한다(장벽으로 동시에 출발)."""
    barrier = threading.Barrier(WORKERS)
    results: list[object] = []
    lock = threading.Lock()

    def attempt():
        barrier.wait()
        result = job_runtime.claim(settings, owner, 60, job_id)
        with lock:
            results.append(result)

    threads = [threading.Thread(target=attempt) for _ in range(WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results


@pytest.mark.parametrize("owner", ["cs", "tft"])
def test_concurrent_claims_hand_out_exactly_one_lease(
    request, jobs_database, cs_job, tft_job, owner
):
    """같은 작업을 여러 worker가 동시에 인수해도 소유권은 하나, 시작 이벤트도 하나다."""
    settings = request.getfixturevalue(f"{owner}_worker_settings")
    job_id = cs_job if owner == "cs" else tft_job
    results = claim_together(settings, owner, job_id)
    leases = [r for r in results if isinstance(r, job_runtime.Lease)]
    assert len(results) == WORKERS and len(leases) == 1
    assert results.count(None) == WORKERS - 1
    status, released, generation, _, events = snapshot(jobs_database, job_id)
    assert (status, released, generation) == ("RUNNING", False, 1)
    assert [kind for kind, _ in events].count("job.started") == 1
    assert violations(jobs_database, [job_id]) == []


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE runtime.jobs SET status='SUCCEEDED' WHERE id=%s",
        "UPDATE runtime.job_control SET holder_id=gen_random_uuid(),"
        "lease_expires_at=now()+interval '1 minute' WHERE job_id=%s",
    ],
    ids=["finished", "live-owner"],
)
def test_tft_jobs_that_must_not_start_are_not_claimed(
    tft_worker_settings,  # noqa: F811
    jobs_database,
    tft_job,
    change,
):
    """끝난 작업이나 살아 있는 다른 소유자가 있는 작업은 인수하지 않고 처리 함수도 부르지 않는다."""
    admin(jobs_database, change, tft_job)
    calls = []
    assert run_tft(tft_worker_settings, tft_job, lambda job, emit: calls.append(job)) == (
        "NOT_CLAIMED"
    )
    assert calls == []


def test_a_late_redelivery_after_success_changes_nothing(
    cs_worker_settings,  # noqa: F811
    tft_worker_settings,  # noqa: F811
    jobs_database,
    cs_job,
    tft_job,
):
    """작업 전달이 늦게 한 번 더 와도(Cloud Run Jobs·Tasks 재전달) 끝난 작업은 그대로다."""
    harness = Harness()
    assert harness.run(cs_worker_settings, cs_job) == "CS_JOB_SUCCEEDED"
    processed = []

    def processor(job, emit):
        processed.append(job.job_id)
        emit({"step": "parse"})
        return {"counts": {"requirements": 0, "proposals": 0}, "needs_review": False}

    assert run_tft(tft_worker_settings, tft_job, processor).endswith("SUCCEEDED")
    before = {job: snapshot(jobs_database, job) for job in (cs_job, tft_job)}
    calls = list(harness.calls)
    assert harness.run(cs_worker_settings, cs_job) == "NOT_CLAIMED"
    assert run_tft(tft_worker_settings, tft_job, processor) == "NOT_CLAIMED"
    assert harness.calls == calls and processed == [tft_job]
    assert {job: snapshot(jobs_database, job) for job in (cs_job, tft_job)} == before
    assert violations(jobs_database, [cs_job, tft_job]) == []


@pytest.mark.parametrize("scenario", ["succeeded", "recovered", "cancelled", "dead-owner-cancel"])
def test_runtime_invariants_hold_after_each_scenario(
    cs_worker_settings,  # noqa: F811
    jobs_database,
    cs_job,
    scenario,
):
    """정상 종료·소유자가 죽은 뒤 복구·실행 중 취소·소유자가 죽은 뒤 취소, 어느 경로든 불변식을 지킨다."""
    harness = Harness()
    if scenario == "recovered":
        # 앞 worker가 lease를 잃은 상태에서 새 worker가 이어 끝낸다.
        orphan(jobs_database, cs_job)
    elif scenario == "cancelled":
        harness.on_classify = lambda: admin(
            jobs_database,
            "UPDATE runtime.job_control SET cancel_requested_at=now() WHERE job_id=%s",
            cs_job,
        )
    elif scenario == "dead-owner-cancel":
        orphan(jobs_database, cs_job)
        admin(
            jobs_database,
            "UPDATE runtime.job_control SET cancel_requested_at=now() WHERE job_id=%s",
            cs_job,
        )
    expected = {
        "succeeded": "CS_JOB_SUCCEEDED",
        "recovered": "CS_JOB_SUCCEEDED",
        "cancelled": "CS_JOB_CANCELLED",
        "dead-owner-cancel": "CS_JOB_CANCELLED",
    }[scenario]
    assert harness.run(cs_worker_settings, cs_job) == expected
    assert violations(jobs_database, [cs_job]) == []


def test_the_invariant_check_reports_broken_states(jobs_database, cs_job):
    """불변식 검사 자체가 깨진 상태를 잡는다(검사가 항상 통과하는 빈 검사가 아님을 확인)."""
    assert violations(jobs_database, [cs_job]) == []
    # 끝났다고 표시만 하고 종료 이벤트·소유자 정리를 하지 않은 상태.
    admin(jobs_database, "UPDATE runtime.jobs SET status='SUCCEEDED' WHERE id=%s", cs_job)
    admin(
        jobs_database,
        "UPDATE runtime.job_control SET holder_id=gen_random_uuid(),"
        "lease_expires_at=now()+interval '1 minute',failure_code='X_BROKEN' WHERE job_id=%s",
        cs_job,
    )
    found = violations(jobs_database, [cs_job])
    assert len(found) == 3
    assert any("소유자가 남음" in f for f in found)
    assert any("종료 이벤트 []" in f for f in found)
    assert any("failure_code=X_BROKEN" in f for f in found)
