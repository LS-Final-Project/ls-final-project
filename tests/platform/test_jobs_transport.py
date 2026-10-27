"""합성 Jobs HTTP와 실제 PostgreSQL을 연결해 중복 전송·인증·결과 경계를 검증한다."""

import asyncio
import json
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi import HTTPException
from pydantic import SecretStr, ValidationError

from backend.api.platform.evaluations.admission import accept_run
from backend.api.platform.evaluations.smoke_worker import claim_run
from backend.api.platform.jobs.cloud_run import CloudRunJobsSettings, DeliveryResult, dispatch_one
from backend.api.platform.jobs.dispatch import DispatchSettings, claim_next
from backend.shared.errors import ApplicationError

OPERATION = "projects/123456789012/locations/asia-northeast3/operations/synthetic-operation"


@pytest.fixture
def jobs():
    """실재 리소스를 가리키지 않는 전송 설정을 준비한다.

    Args:
        없음.

    Returns:
        CloudRunJobsSettings: 합성 프로젝트와 짧은 제한 시간.
    """
    return CloudRunJobsSettings(
        _env_file=None,
        enabled=True,
        project_number="123456789012",
        job="synthetic-worker",
        timeout_seconds=0.2,
    )


@pytest.fixture
def dispatch():
    """검사에서만 장부를 허용한다.

    Args:
        없음.

    Returns:
        DispatchSettings: 명시적 활성 설정.
    """
    return DispatchSettings(_env_file=None, enabled=True)


@pytest.fixture
def queued(settings, access, experiment):
    """합성 실행을 접수한다.

    Args:
        settings: 테스트 DB.
        access: 평가 허용 설정.
        experiment: 합성 실험.

    Returns:
        UUID: 실행 ID.
    """
    return asyncio.run(
        accept_run(settings, access, experiment["actor"], experiment["id"], str(uuid4()), uuid4())
    ).data.run_id


async def token_provider():
    """Google에 접근하지 않고 합성 access token을 제공한다.

    Args:
        없음.

    Returns:
        SecretStr: 검사에서만 사용하는 토큰.
    """
    return SecretStr("synthetic-access-token")


def evidence(dsn, run_id):
    """전송 증거와 실행 상태를 조회한다.

    Args:
        dsn: 전용 DB 관리자 연결.
        run_id: 실행 ID.

    Returns:
        tuple: outbox 상태·시도·operation·run 상태·판본·이벤트 수.
    """
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT o.state,o.attempt_no,a.operation_name,r.status,c.version,"
            "(SELECT count(*) FROM runtime.job_events e WHERE e.job_id=o.job_id) "
            "FROM evaluation.runs r JOIN runtime.dispatch_outbox o ON o.job_id=r.job_id "
            "JOIN evaluation.run_control c ON c.run_id=r.id "
            "LEFT JOIN runtime.dispatch_attempts a ON a.job_id=o.job_id AND a.attempt_no=o.attempt_no "
            "WHERE r.id=%s",
            (run_id,),
        ).fetchone()


def test_committed_single_post_and_no_execution(
    settings, access, dispatch, jobs, queued, evaluation_database
):
    """DB 잠금 없이 한 번 전송하고 operation만 보존한다.

    Args:
        settings: DB 설정.
        access: 평가 허용 설정.
        dispatch: 장부 설정.
        jobs: 합성 Jobs 설정.
        queued: 접수된 실행.
        evaluation_database: 관리자 연결.

    Returns:
        None: 정확한 요청·한 번 전송·실행 상태 불변·worker 독립 권한을 확인한다.
    """
    requests = []

    def handler(request):
        """전송 시점에 별도 연결로 commit과 잠금 해제를 확인한다.

        Args:
            request: 합성 HTTP 요청.

        Returns:
            httpx.Response: 접수 operation 응답.
        """
        requests.append(request)
        assert (
            str(request.url)
            == "https://run.googleapis.com/v2/projects/123456789012/locations/asia-northeast3/jobs/synthetic-worker:run"
        )
        assert request.method == "POST"
        assert request.headers["authorization"] == "Bearer synthetic-access-token"
        assert "x-team4-caller-token" not in request.headers
        assert json.loads(request.content) == {
            "overrides": {
                "taskCount": 1,
                "containerOverrides": [
                    {"name": "worker", "env": [{"name": "EVALUATION_RUN_ID", "value": str(queued)}]}
                ],
            }
        }
        with psycopg.connect(evaluation_database) as conn:
            assert conn.execute(
                "SELECT o.state FROM runtime.dispatch_outbox o JOIN evaluation.runs r ON r.job_id=o.job_id WHERE r.id=%s FOR UPDATE OF o NOWAIT",
                (queued,),
            ).fetchone() == ("SENDING",)
            conn.execute(
                "SELECT run_id FROM evaluation.run_control WHERE run_id=%s FOR UPDATE NOWAIT",
                (queued,),
            )
        return httpx.Response(200, json={"name": OPERATION, "metadata": {"secret": "not-saved"}})

    result = asyncio.run(
        dispatch_one(
            settings, access, dispatch, jobs, token_provider, transport=httpx.MockTransport(handler)
        )
    )
    assert result == DeliveryResult("ACCEPTED", OPERATION)
    assert evidence(evaluation_database, queued) == ("ACCEPTED", 1, OPERATION, "QUEUED", 1, 1)
    assert (
        asyncio.run(
            dispatch_one(
                settings,
                access,
                dispatch,
                jobs,
                token_provider,
                transport=httpx.MockTransport(handler),
            )
        )
        is None
    )
    assert len(requests) == 1
    revoked = access.model_copy(update={"approved_config_hashes": frozenset()})
    with pytest.raises(ApplicationError):
        asyncio.run(claim_run(settings, revoked, queued))


@pytest.mark.parametrize(
    "mode,expected,operation",
    [
        ("completed", "ACCEPTED", OPERATION),
        ("invalid-completed", "UNKNOWN", OPERATION),
        ("denied", "REJECTED", None),
        ("unauthenticated", "REJECTED", None),
        ("server", "UNKNOWN", None),
        ("rate", "UNKNOWN", None),
        ("redirect", "UNKNOWN", None),
        ("malformed", "UNKNOWN", None),
        ("wrong-project", "UNKNOWN", None),
        ("operation-error", "UNKNOWN", OPERATION),
        ("timeout", "UNKNOWN", None),
        ("disconnect", "UNKNOWN", None),
        ("oversize", "UNKNOWN", None),
        ("html", "UNKNOWN", None),
        ("wrong-denial", "UNKNOWN", None),
    ],
)
def test_transport_outcomes(
    settings, access, dispatch, jobs, queued, evaluation_database, mode, expected, operation
):
    """재시도 없는 전송 결과 분류와 원문 미보존을 검증한다.

    Args:
        settings: DB 설정.
        access: 평가 설정.
        dispatch: 장부 설정.
        jobs: 전송 설정.
        queued: 실행 ID.
        evaluation_database: 관리자 연결.
        mode: 합성 HTTP 응답/장애 유형.
        expected: 기대 장부 상태.
        operation: 보존 가능한 operation 이름.

    Returns:
        None: 한 번의 요청과 안전한 결과 저장을 확인한다.
    """
    calls = []

    async def handler(request):
        """민감 본문이 섞인 실패와 모호한 응답을 재현한다.

        Args:
            request: 전송 요청.

        Returns:
            httpx.Response: 지정한 실패 응답.
        """
        calls.append(request)
        if mode in {"completed", "invalid-completed"}:
            return httpx.Response(
                200,
                json={
                    "name": OPERATION,
                    "done": True,
                    "response": {
                        "@type": "type.googleapis.com/google.cloud.run.v2.Execution",
                        "name": "projects/123456789012/locations/asia-northeast3/jobs/"
                        + ("synthetic-worker" if mode == "completed" else "other-worker")
                        + "/executions/synthetic-worker-abc12",
                    },
                },
            )
        if mode == "timeout":
            await asyncio.sleep(10)
        if mode == "disconnect":
            raise httpx.ReadError("private provider body", request=request)
        if mode in {"denied", "unauthenticated", "wrong-denial"}:
            status = 401 if mode == "unauthenticated" else 403
            code = "UNAUTHENTICATED" if status == 401 else "PERMISSION_DENIED"
            return httpx.Response(
                status,
                json={
                    "error": {
                        "code": status,
                        "status": "INTERNAL" if mode == "wrong-denial" else code,
                        "message": "private provider body",
                    }
                },
            )
        if mode == "server":
            return httpx.Response(503, json={"name": OPERATION})
        if mode == "rate":
            return httpx.Response(
                429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}
            )
        if mode == "redirect":
            return httpx.Response(
                307,
                headers={"Location": "https://other.invalid/", "Content-Type": "application/json"},
                json={},
            )
        if mode == "wrong-project":
            return httpx.Response(
                200, json={"name": OPERATION.replace("123456789012", "999999999999")}
            )
        if mode == "operation-error":
            return httpx.Response(
                200,
                json={
                    "name": OPERATION,
                    "done": True,
                    "error": {"message": "private provider body"},
                },
            )
        if mode == "oversize":
            return httpx.Response(200, json={"name": OPERATION, "metadata": "x" * 65536})
        return httpx.Response(
            200,
            content="not json",
            headers={"Content-Type": "text/html" if mode == "html" else "application/json"},
        )

    result = asyncio.run(
        dispatch_one(
            settings, access, dispatch, jobs, token_provider, transport=httpx.MockTransport(handler)
        )
    )
    assert result == DeliveryResult(expected, operation)
    assert evidence(evaluation_database, queued) == (expected, 1, operation, "QUEUED", 1, 1)
    assert asyncio.run(claim_next(settings, access, dispatch)) is None
    assert len(calls) == 1
    assert "private" not in repr(result)


@pytest.mark.parametrize(
    "mode", ["disabled", "production", "deadline", "auth-error", "auth-invalid", "revoked"]
)
def test_no_post_before_authorization(
    settings, access, dispatch, jobs, queued, evaluation_database, mode
):
    """환경·인증 실패는 POST 전에 막고 승인 철회는 BLOCKED로 남긴다.

    Args:
        settings: DB 설정.
        access: 평가 설정.
        dispatch: 장부 설정.
        jobs: 전송 설정.
        queued: 실행 ID.
        evaluation_database: 관리자 연결.
        mode: 거부 사유.

    Returns:
        None: HTTP 호출 없음과 적절한 장부 상태를 확인한다.
    """
    calls = []

    async def provider():
        """공급자 오류 또는 잘못된 토큰을 주입한다.

        Args:
            없음.

        Returns:
            SecretStr: 검사 토큰.
        """
        if mode == "auth-error":
            raise RuntimeError("private credential response")
        return SecretStr("bad\r\nheader" if mode == "auth-invalid" else "synthetic-token")

    def handler(request):
        """금지된 HTTP 접근을 추적한다.

        Args:
            request: 요청.

        Returns:
            httpx.Response: 합성 응답.
        """
        calls.append(request)
        return httpx.Response(200, json={"name": OPERATION})

    if mode == "disabled":
        jobs = jobs.model_copy(update={"enabled": False})
    elif mode == "production":
        settings = settings.model_copy(update={"environment": "production"})
    elif mode == "deadline":
        jobs = jobs.model_copy(update={"timeout_seconds": 30})
    elif mode == "revoked":
        access = access.model_copy(update={"approved_config_hashes": frozenset()})
    action = dispatch_one(
        settings, access, dispatch, jobs, provider, transport=httpx.MockTransport(handler)
    )
    if mode == "revoked":
        assert asyncio.run(action) is None
        assert evidence(evaluation_database, queued)[:2] == ("BLOCKED", 1)
    else:
        with pytest.raises(ApplicationError) as error:
            asyncio.run(action)
        assert "private" not in str(error.value)
        assert evidence(evaluation_database, queued)[:2] == ("PENDING", 0)
    assert calls == []


def test_result_commit_failure_never_reposts(
    settings, access, dispatch, jobs, queued, evaluation_database
):
    """접수 응답 후 저장 실패를 재전송하지 않고 UNKNOWN으로 복구한다.

    Args:
        settings: DB 설정.
        access: 평가 설정.
        dispatch: 장부 설정.
        jobs: 전송 설정.
        queued: 실행 ID.
        evaluation_database: 관리자 연결.

    Returns:
        None: 결과 저장 실패 전파와 만료 복구를 검증한다.
    """
    calls = []

    def handler(request):
        """전송 접수 직후 결과 저장 권한 오류를 주입한다.

        Args:
            request: 요청.

        Returns:
            httpx.Response: 접수 확인 응답.
        """
        calls.append(request)
        with psycopg.connect(evaluation_database) as conn:
            conn.execute(
                "REVOKE UPDATE (operation_name) ON runtime.dispatch_attempts FROM platform_app"
            )
        return httpx.Response(200, json={"name": OPERATION})

    try:
        with pytest.raises(HTTPException) as error:
            asyncio.run(
                dispatch_one(
                    settings,
                    access,
                    dispatch,
                    jobs,
                    token_provider,
                    transport=httpx.MockTransport(handler),
                )
            )
        assert error.value.status_code == 503
        assert evidence(evaluation_database, queued) == ("SENDING", 1, None, "QUEUED", 1, 1)
    finally:
        with psycopg.connect(evaluation_database) as conn:
            conn.execute(
                "GRANT UPDATE (operation_name) ON runtime.dispatch_attempts TO platform_app"
            )
            conn.execute(
                "UPDATE runtime.dispatch_outbox SET lease_expires_at=now()-interval '1 second' WHERE job_id=(SELECT job_id FROM evaluation.runs WHERE id=%s)",
                (queued,),
            )
    assert (
        asyncio.run(
            dispatch_one(
                settings,
                access,
                dispatch,
                jobs,
                token_provider,
                transport=httpx.MockTransport(handler),
            )
        )
        is None
    )
    assert evidence(evaluation_database, queued) == ("UNKNOWN", 1, None, "QUEUED", 1, 1)
    assert len(calls) == 1


def test_job_destination_rejects_path_injection():
    """서버 목적지 설정의 경로 삽입과 다른 리전을 거부한다.

    Args:
        없음.

    Returns:
        None: 고정 호스트 밖으로 인증 토큰을 보낼 설정을 만들 수 없다.
    """
    for change in (
        {"project_number": "../other"},
        {"job": "worker?url=evil"},
        {"region": "us-central1"},
    ):
        with pytest.raises(ValidationError):
            CloudRunJobsSettings(
                _env_file=None,
                **({"project_number": "123456789012", "job": "synthetic-worker"} | change),
            )


def test_process_cancellation_preserves_sending(
    settings, access, dispatch, jobs, queued, evaluation_database
):
    """전송 중 취소가 미접수 재시도로 바뀌지 않음을 확인한다.

    Args:
        settings: DB 설정.
        access: 평가 설정.
        dispatch: 장부 설정.
        jobs: 전송 설정.
        queued: 실행 ID.
        evaluation_database: 관리자 연결.

    Returns:
        None: 취소 후 SENDING 보존과 만료 UNKNOWN 복구를 확인한다.
    """

    async def cancel_during_send():
        """POST 진입을 기다린 뒤 dispatcher 작업만 취소한다.

        Args:
            없음.

        Returns:
            None: CancelledError 전파를 확인한다.
        """
        entered = asyncio.Event()

        async def handler(request):
            """응답이 돌아오지 않는 외부 요청을 재현한다.

            Args:
                request: 합성 요청.

            Returns:
                httpx.Response: 취소 때문에 반환하지 않는다.
            """
            entered.set()
            await asyncio.Event().wait()
            return httpx.Response(200, json={"name": OPERATION})

        task = asyncio.create_task(
            dispatch_one(
                settings,
                access,
                dispatch,
                jobs,
                token_provider,
                transport=httpx.MockTransport(handler),
            )
        )
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_during_send())
    assert evidence(evaluation_database, queued)[:3] == ("SENDING", 1, None)
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            "UPDATE runtime.dispatch_outbox SET lease_expires_at=now()-interval '1 second' WHERE job_id=(SELECT job_id FROM evaluation.runs WHERE id=%s)",
            (queued,),
        )
    assert asyncio.run(claim_next(settings, access, dispatch)) is None
    assert evidence(evaluation_database, queued)[:3] == ("UNKNOWN", 1, None)


@pytest.mark.parametrize(
    ("status", "body", "confirmed"),
    [
        (200, {"name": OPERATION}, True),
        (200, {"name": OPERATION + "-other"}, False),
        (200, {"name": OPERATION, "error": {"message": "private"}}, False),
        (200, {"name": OPERATION, "done": True}, False),
        (200, {"name": OPERATION, "done": "true"}, False),
        (404, {"error": {"code": 404, "status": "NOT_FOUND"}}, False),
        (403, {"error": {"code": 403, "status": "PERMISSION_DENIED"}}, False),
        (503, {"name": OPERATION}, False),
        (302, {"name": OPERATION}, False),
        (200, {"name": OPERATION, "metadata": "x" * 65536}, False),
        (200, [], False),
        (
            200,
            {
                "name": OPERATION,
                "done": True,
                "response": {
                    "@type": "type.googleapis.com/google.cloud.run.v2.Execution",
                    "name": "projects/123456789012/locations/asia-northeast3/jobs/synthetic-worker/executions/test-1",
                },
            },
            True,
        ),
        (
            200,
            {
                "name": OPERATION,
                "done": True,
                "response": {
                    "@type": "type.googleapis.com/google.cloud.run.v2.Execution",
                    "name": "projects/123456789012/locations/asia-northeast3/jobs/other-worker/executions/test-1",
                },
            },
            False,
        ),
    ],
)
def test_inspect_unknown_is_read_only(
    settings, access, dispatch, jobs, queued, evaluation_database, status, body, confirmed
):
    """저장된 operation을 대조하고 HTTP 실패를 재전송 근거로 쓰지 않는다.

    Args:
        settings: 임시 DB 설정.
        access: 합성 허용 설정.
        dispatch: 장부 설정.
        jobs: 고정 Job 설정.
        queued: 접수한 run.
        evaluation_database: 관리자 DSN.
        status: 합성 HTTP 상태.
        body: 합성 응답 본문.
        confirmed: 기대 접수 확인 여부.

    Returns:
        None: GET 한 번과 장부·실행·이벤트 불변을 확인한다.
    """
    from backend.api.platform.jobs.dispatch import begin_send, record_result
    from backend.api.platform.jobs.operation import inspect_operation

    attempt = asyncio.run(claim_next(settings, access, dispatch))
    assert asyncio.run(begin_send(settings, access, dispatch, attempt))
    asyncio.run(record_result(settings, access, dispatch, attempt, "UNKNOWN", OPERATION))
    before = evidence(evaluation_database, queued)
    requests = []

    def handler(request):
        """고정 GET과 잠금 부재를 검사한다.

        Args:
            request: 합성 조회 요청.

        Returns:
            httpx.Response: 지정한 응답.
        """
        requests.append(request)
        assert request.method == "GET"
        assert str(request.url) == "https://run.googleapis.com/v2/" + OPERATION
        assert request.content == b""
        assert request.headers["authorization"] == "Bearer synthetic-access-token"
        with psycopg.connect(evaluation_database) as conn:
            conn.execute("SELECT job_id FROM runtime.dispatch_outbox FOR UPDATE NOWAIT")
        return httpx.Response(status, json=body, headers={"location": "https://example.org"})

    assert (
        asyncio.run(
            inspect_operation(
                settings,
                access,
                dispatch,
                jobs,
                attempt,
                asyncio.run(token_provider()),
                transport=httpx.MockTransport(handler),
            )
        )
        is confirmed
    )
    assert len(requests) == 1
    assert evidence(evaluation_database, queued) == before
    assert asyncio.run(claim_next(settings, access, dispatch)) is None


@pytest.mark.parametrize(
    "variant",
    [
        "missing",
        "foreign",
        "holder",
        "run",
        "attempt",
        "accepted",
        "production",
        "token",
        "timeout",
        "html",
    ],
)
def test_inspect_operation_boundaries(
    settings, access, dispatch, jobs, queued, evaluation_database, variant
):
    """대조 대상·환경·인증 경계와 불명확 응답을 검사한다.

    Args:
        settings: 임시 DB 설정.
        access: 합성 허용 설정.
        dispatch: 장부 설정.
        jobs: 고정 Job 설정.
        queued: 접수한 run.
        evaluation_database: 관리자 DSN.
        variant: 차단 또는 불명확 조건.

    Returns:
        None: 호출 범위와 장부 불변을 확인한다.
    """
    from dataclasses import replace

    from backend.api.platform.jobs.dispatch import begin_send, record_result
    from backend.api.platform.jobs.operation import inspect_operation

    attempt = asyncio.run(claim_next(settings, access, dispatch))
    assert asyncio.run(begin_send(settings, access, dispatch, attempt))
    name = None if variant == "missing" else OPERATION
    if variant == "foreign":
        name = OPERATION.replace("123456789012", "999999999999")
    state = "ACCEPTED" if variant == "accepted" else "UNKNOWN"
    asyncio.run(record_result(settings, access, dispatch, attempt, state, name))
    before = evidence(evaluation_database, queued)
    if variant == "holder":
        attempt = replace(attempt, holder_id=uuid4())
    if variant == "run":
        attempt = replace(attempt, run_id=uuid4())
    if variant == "attempt":
        attempt = replace(attempt, attempt_no=attempt.attempt_no + 1)
    if variant == "production":
        settings = settings.model_copy(update={"environment": "production"})
    token = SecretStr("bad\nheader") if variant == "token" else asyncio.run(token_provider())
    requests = []

    async def handler(request):
        """오류 응답 또는 전체 제한 시간 초과를 만든다.

        Args:
            request: 합성 조회 요청.

        Returns:
            httpx.Response: JSON 아닌 응답.
        """
        requests.append(request)
        if variant == "timeout":
            await asyncio.sleep(1)
        return httpx.Response(200, text="private", headers={"content-type": "text/html"})

    async def inspect():
        """현재 조건으로 조회를 실행한다.

        Args:
            없음.

        Returns:
            bool: 접수 증거 확인 여부.
        """
        return await inspect_operation(
            settings, access, dispatch, jobs, attempt, token, transport=httpx.MockTransport(handler)
        )

    if variant in {"production", "token"}:
        with pytest.raises(ApplicationError):
            asyncio.run(inspect())
    else:
        assert asyncio.run(inspect()) is False
    assert len(requests) == (1 if variant in {"timeout", "html"} else 0)
    assert evidence(evaluation_database, queued) == before
