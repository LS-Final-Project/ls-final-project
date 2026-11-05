"""합성 실행 제어의 실제 DB 상태·판본·재전송·worker 경합 검사."""

import asyncio
from uuid import uuid4

import pytest

from backend.api.platform.evaluations.admission import accept_run, get_run
from backend.api.platform.evaluations.controls import control_run
from backend.api.platform.evaluations.smoke_worker import advance_run, claim_run, dispatch_one
from backend.shared.errors import ApplicationError


@pytest.fixture
def run_id(settings, access, experiment):
    """현재 사용자의 합성 실행을 접수한다.

    Args:
        settings: 실행 계정 설정.
        access: 합성 허용 설정.
        experiment: 합성 실험.

    Returns:
        UUID: 접수된 실행.
    """
    return asyncio.run(
        accept_run(settings, access, experiment["actor"], experiment["id"], str(uuid4()), uuid4())
    ).data.run_id


def test_cancel_replay_and_stale_worker(settings, access, experiment, run_id):
    """실행 중 취소가 기존 worker를 막고 같은 요청은 최초 응답을 재생한다.

    Args:
        settings: 실행 설정.
        access: 허용 설정.
        experiment: 합성 실험.
        run_id: 접수 UUID.

    Returns:
        None: 취소·재전송·옛 worker 차단을 확인한다.
    """
    lease = asyncio.run(claim_run(settings, access, run_id))
    state = asyncio.run(get_run(settings, access, experiment["actor"], run_id))
    key = str(uuid4())
    args = (settings, access, experiment["actor"], run_id, "cancel", key, f'"{state.version}"')
    first = asyncio.run(control_run(*args, uuid4()))
    assert first.data.status == "CANCELLED"
    assert asyncio.run(control_run(*args, uuid4())) == first
    with pytest.raises(ApplicationError) as stale:
        asyncio.run(advance_run(settings, access, lease))
    assert stale.value.code == "STALE_WORKER"
    with pytest.raises(ApplicationError) as terminal:
        asyncio.run(
            control_run(
                settings,
                access,
                experiment["actor"],
                run_id,
                "resume",
                str(uuid4()),
                f'"{first.data.version}"',
                uuid4(),
            )
        )
    assert terminal.value.code == "INVALID_TRANSITION"
    assert asyncio.run(dispatch_one(settings, access)) is None


def test_review_pause_restore_resume(settings, access, experiment, run_id):
    """허용 철회로 보류된 실행은 자동 반복되지 않고 조건 복구 뒤 명시 재개한다.

    Args:
        settings: 실행 설정.
        access: 허용 설정.
        experiment: 합성 실험.
        run_id: 접수 UUID.

    Returns:
        None: 보류·판본 충돌·명시 재개·완료를 확인한다.
    """
    approved = access.approved_config_hashes
    access.approved_config_hashes = frozenset()
    with pytest.raises(ApplicationError):
        asyncio.run(dispatch_one(settings, access))
    assert asyncio.run(dispatch_one(settings, access)) is None
    state = asyncio.run(get_run(settings, access, experiment["actor"], run_id))
    assert state.needs_review and state.failure_code == "EXPERIMENT_NOT_APPROVED"
    with pytest.raises(ApplicationError) as revoked:
        asyncio.run(
            control_run(
                settings,
                access,
                experiment["actor"],
                run_id,
                "resume",
                str(uuid4()),
                f'"{state.version}"',
                uuid4(),
            )
        )
    assert revoked.value.code == "EXPERIMENT_NOT_APPROVED"
    access.approved_config_hashes = approved
    with pytest.raises(ApplicationError) as conflict:
        asyncio.run(
            control_run(
                settings,
                access,
                experiment["actor"],
                run_id,
                "resume",
                str(uuid4()),
                '"1"',
                uuid4(),
            )
        )
    assert conflict.value.status_code == 412
    resumed = asyncio.run(
        control_run(
            settings,
            access,
            experiment["actor"],
            run_id,
            "resume",
            str(uuid4()),
            f'"{state.version}"',
            uuid4(),
        )
    )
    assert not resumed.data.needs_review
    assert asyncio.run(dispatch_one(settings, access)) == run_id


def test_cancel_missing_version_other_actor_and_revocation(settings, access, experiment, run_id):
    """판본 누락과 타 사용자 요청은 거부하고 구성 철회 후 안전한 취소는 허용한다.

    Args:
        settings: 실행 설정.
        access: 허용 설정.
        experiment: 합성 실험.
        run_id: 접수 UUID.

    Returns:
        None: 현재 권한과 안전한 종료 경계를 검증한다.
    """
    with pytest.raises(ApplicationError) as missing:
        asyncio.run(
            control_run(
                settings, access, experiment["actor"], run_id, "cancel", str(uuid4()), None, uuid4()
            )
        )
    assert missing.value.status_code == 428
    stranger = uuid4()
    access.evaluator_user_ids = frozenset({stranger, experiment["actor"]})
    with pytest.raises(ApplicationError) as hidden:
        asyncio.run(
            control_run(settings, access, stranger, run_id, "cancel", str(uuid4()), '"1"', uuid4())
        )
    assert hidden.value.status_code == 404
    access.approved_config_hashes = frozenset()
    result = asyncio.run(
        control_run(
            settings, access, experiment["actor"], run_id, "cancel", str(uuid4()), '"1"', uuid4()
        )
    )
    assert result.data.status == "CANCELLED"


def test_concurrent_cancel_is_one_event(settings, access, experiment, run_id, evaluation_database):
    """동시 동일 취소 요청이 최초 응답과 종료 이벤트 하나만 남긴다.

    Args:
        settings: 실행 설정.
        access: 허용 설정.
        experiment: 합성 실험.
        run_id: 접수 UUID.
        evaluation_database: 검사 DB.

    Returns:
        None: 응답 재생과 단일 상태 전이를 확인한다.
    """
    import psycopg

    key = str(uuid4())

    async def race():
        """별도 DB 연결에서 동일 취소를 동시에 요청한다.

        Args:
            없음.

        Returns:
            list: 두 제어 응답.
        """
        return await asyncio.gather(
            *(
                control_run(
                    settings, access, experiment["actor"], run_id, "cancel", key, '"1"', uuid4()
                )
                for _ in range(2)
            )
        )

    results = asyncio.run(race())
    assert results[0] == results[1]
    with psycopg.connect(evaluation_database) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM runtime.job_events WHERE job_id=%s AND type='job.completed'",
                (results[0].data.job_id,),
            ).fetchone()[0]
            == 1
        )
    with pytest.raises(ApplicationError) as conflict:
        asyncio.run(
            control_run(
                settings, access, experiment["actor"], run_id, "cancel", key, '"2"', uuid4()
            )
        )
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"


def test_receipt_failure_rolls_back_cancel(
    settings, access, experiment, run_id, evaluation_database
):
    """응답 저장 실패가 취소 상태와 이벤트까지 되돌린다.

    Args:
        settings: 실행 설정.
        access: 허용 설정.
        experiment: 합성 실험.
        run_id: 접수 UUID.
        evaluation_database: 검사 DB.

    Returns:
        None: 실패 후에도 최초 판본과 실행 가능 상태를 보존한다.
    """
    import psycopg

    with psycopg.connect(evaluation_database) as conn:
        conn.execute("REVOKE INSERT ON evaluation.run_action_receipts FROM platform_app")
    try:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as failure:
            asyncio.run(
                control_run(
                    settings,
                    access,
                    experiment["actor"],
                    run_id,
                    "cancel",
                    str(uuid4()),
                    '"1"',
                    uuid4(),
                )
            )
        assert failure.value.status_code == 503
    finally:
        with psycopg.connect(evaluation_database) as conn:
            conn.execute("GRANT INSERT ON evaluation.run_action_receipts TO platform_app")
    state = asyncio.run(get_run(settings, access, experiment["actor"], run_id))
    assert state.status == "QUEUED" and state.version == 1
    assert asyncio.run(dispatch_one(settings, access)) == run_id


def test_http_control_headers_and_csrf(settings, access, experiment, run_id, monkeypatch):
    """HTTP 제어가 세션·CSRF·판본과 재전송 계약을 지킨다.

    Args:
        settings: 실행 설정.
        access: 허용 설정.
        experiment: 합성 실험.
        run_id: 접수 UUID.
        monkeypatch: 세션 조회 대체.

    Returns:
        None: 누락 요청 거부와 실제 취소·재전송 ETag를 확인한다.
    """
    from unittest.mock import AsyncMock

    from backend.api.platform.auth.http import CSRF_HEADER, SESSION_COOKIE, csrf_token
    from backend.shared.access import Principal
    from tests.platform.test_evaluation_access import client_for

    actor = experiment["actor"]
    client = client_for((actor,))
    client.app.state.settings = settings
    client.app.state.evaluation_access = access
    monkeypatch.setattr(
        "backend.api.platform.evaluations.access.load_session_principal",
        AsyncMock(return_value=Principal(user_id=actor, roles={"sales"})),
    )
    client.cookies.set(SESSION_COOKIE, "synthetic-session")
    path = f"/api/v1/platform/evaluations/runs/{run_id}"
    state = client.get(path)
    assert state.status_code == 200 and state.headers["etag"] == '"1"'
    headers = {"Origin": "https://app.example.invalid", "Idempotency-Key": str(uuid4())}
    assert client.post(path + "/cancel", headers=headers).status_code == 403
    headers[CSRF_HEADER] = csrf_token("synthetic-session")
    assert client.post(path + "/cancel", headers=headers).status_code == 428
    headers["If-Match"] = '"0"'
    assert client.post(path + "/cancel", headers=headers).status_code == 412
    headers["If-Match"] = state.headers["etag"]
    cancelled = client.post(path + "/cancel", headers=headers)
    assert cancelled.status_code == 200 and cancelled.headers["etag"] == '"2"'
    assert cancelled.json()["data"]["status"] == "CANCELLED"
    assert client.post(path + "/cancel", headers=headers).json() == cancelled.json()
    assert "raw_output" not in cancelled.text and "gold" not in cancelled.text


def test_waiting_worker_does_not_reopen_cancelled_run(
    settings, access, experiment, run_id, evaluation_database, monkeypatch
):
    """취소 transaction을 기다린 worker가 취소 전 snapshot으로 실행을 재개하지 않는다.

    Args:
        settings: 실행 설정.
        access: 허용 설정.
        experiment: 합성 실험.
        run_id: 접수 UUID.
        evaluation_database: 잠금 대기를 관찰할 검사 DB.
        monkeypatch: 취소 commit 직전 대기 지점 주입.

    Returns:
        None: 잠금 경합 후에도 CANCELLED 상태를 보존한다.
    """
    import psycopg

    from backend.api.platform.evaluations import controls

    original = controls.append_event

    async def race():
        """취소 저장 중 worker를 대기시키고 취소 commit 후 결과를 확인한다.

        Args:
            없음.

        Returns:
            None: 실제 DB 잠금 대기와 종료 상태 보존을 확인한다.
        """
        entered, release = asyncio.Event(), asyncio.Event()

        async def hold_event(*args):
            """종료 이벤트 저장 후 취소 transaction을 잠시 유지한다.

            Args:
                args: 원래 이벤트 저장 인수.

            Returns:
                None: 검사에서 commit을 허용하면 반환한다.
            """
            await original(*args)
            entered.set()
            await release.wait()

        monkeypatch.setattr(controls, "append_event", hold_event)
        cancel = asyncio.create_task(
            control_run(
                settings,
                access,
                experiment["actor"],
                run_id,
                "cancel",
                str(uuid4()),
                '"1"',
                uuid4(),
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=3)
        worker = asyncio.create_task(claim_run(settings, access, run_id))
        try:
            async with await psycopg.AsyncConnection.connect(
                evaluation_database, autocommit=True
            ) as observer:
                waiting = False
                for _ in range(100):
                    cursor = await observer.execute(
                        "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' "
                        "AND query LIKE '%%evaluation.run_control%%')"
                    )
                    if (await cursor.fetchone())[0]:
                        waiting = True
                        break
                    await asyncio.sleep(0.01)
                assert waiting, "worker의 실제 제어 행 잠금 대기를 확인해야 한다."
        finally:
            release.set()
            results = await asyncio.gather(cancel, worker)
        assert results[0].data.status == "CANCELLED"
        assert results[1] is None
        assert (await get_run(settings, access, experiment["actor"], run_id)).status == "CANCELLED"

    asyncio.run(race())
