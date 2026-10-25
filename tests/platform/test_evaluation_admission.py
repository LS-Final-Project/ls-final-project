"""합성 평가 접수의 실제 DB 원자성·접근·응답 재생 검사."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from backend.api.platform.auth.http import CSRF_HEADER, SESSION_COOKIE, csrf_token
from backend.api.platform.evaluations.admission import CONFIG_FIELDS, accept_run, canonical_hash
from backend.shared.access import Principal
from backend.shared.errors import ApplicationError
from tests.platform.test_evaluation_access import client_for


def record_counts(dsn):
    """격리 DB의 접수 관련 레코드 수를 반환한다.

    Args:
        dsn: 검사 DB 연결.

    Returns:
        tuple: 작업·이벤트·outbox·실행·사례·제어·응답 건수.
    """
    with psycopg.connect(dsn) as conn:
        return tuple(
            conn.execute(
                psycopg.sql.SQL("SELECT count(*) FROM {}.{}").format(
                    psycopg.sql.Identifier(schema), psycopg.sql.Identifier(table)
                )
            ).fetchone()[0]
            for schema, table in [
                ("runtime", "jobs"),
                ("runtime", "job_events"),
                ("runtime", "dispatch_outbox"),
                ("evaluation", "runs"),
                ("evaluation", "cases"),
                ("evaluation", "run_control"),
                ("evaluation", "admission_receipts"),
            ]
        )


def test_concurrent_acceptance_replays_first_response(
    settings, access, experiment, evaluation_database
):
    """동시 동일 요청이 하나의 전체 접수와 최초 응답만 만든다.

    Args:
        settings: 실행 계정 연결.
        access: 허용 설정.
        experiment: 합성 실험.
        evaluation_database: 전용 DB.

    Returns:
        None: 식별자·최초 응답 일치와 고정 분모를 확인한다.
    """
    key = str(uuid4())

    async def race():
        """두 연결에서 같은 접수를 동시에 호출한다.

        Args:
            없음.

        Returns:
            list: 두 접수 응답.
        """
        return await asyncio.gather(
            *(
                accept_run(settings, access, experiment["actor"], experiment["id"], key, uuid4())
                for _ in range(2)
            )
        )

    results = asyncio.run(race())
    assert results[0] == results[1]
    assert record_counts(evaluation_database) == (1, 1, 1, 1, 2, 1, 1)
    with pytest.raises(ApplicationError) as error:
        asyncio.run(
            accept_run(
                settings, access, experiment["actor"], experiment["id"], key, uuid4(), '"changed"'
            )
        )
    assert error.value.status_code == 409


@pytest.mark.parametrize("table", ["cases", "run_control", "admission_receipts"])
def test_partial_failure_rolls_back_everything(
    settings, access, experiment, evaluation_database, table
):
    """사례·제어·최초 응답 저장 실패마다 작업까지 모두 롤백한다.

    Args:
        settings: 실행 계정 설정.
        access: 허용 설정.
        experiment: 합성 실험.
        evaluation_database: 전용 DB.
        table: INSERT 실패를 주입할 테이블.

    Returns:
        None: 실패한 접수의 일부 기록이 남지 않음을 확인한다.
    """
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            psycopg.sql.SQL("REVOKE INSERT ON evaluation.{} FROM platform_app").format(
                psycopg.sql.Identifier(table)
            )
        )
    try:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as error:
            asyncio.run(
                accept_run(
                    settings, access, experiment["actor"], experiment["id"], str(uuid4()), uuid4()
                )
            )
        assert error.value.status_code == 503
        assert record_counts(evaluation_database) == (0, 0, 0, 0, 0, 0, 0)
    finally:
        with psycopg.connect(evaluation_database) as conn:
            conn.execute(
                psycopg.sql.SQL("GRANT INSERT ON evaluation.{} TO platform_app").format(
                    psycopg.sql.Identifier(table)
                )
            )


@pytest.mark.parametrize(
    "change,code",
    [
        ({"data_split": "test"}, "FINAL_TEST_LOCKED"),
        ({"data_origin": "real"}, "LOCAL_SMOKE_ONLY"),
        ({"configuration": {"model": "real-model"}}, "LOCAL_SMOKE_ONLY"),
        ({"model_id": "gemini"}, "LOCAL_SMOKE_ONLY"),
        ({"target_set_hash": "f" * 64}, "TARGET_SET_MISMATCH"),
    ],
)
def test_forbidden_experiments_never_create_jobs(
    settings, access, experiment, evaluation_database, change, code
):
    """허용 목록에 넣더라도 시험·실제 데이터·변조 대상을 접수하지 않는다.

    Args:
        settings: 실행 계정 연결.
        access: 허용 설정.
        experiment: 합성 실험.
        evaluation_database: 전용 DB.
        change: 합성 메타데이터 변형.
        code: 기대하는 오류 코드.

    Returns:
        None: 안전 거절과 기록 부재를 검증한다.
    """
    experiment.update(change)
    digest = canonical_hash({k: experiment[k] for k in CONFIG_FIELDS})
    with psycopg.connect(evaluation_database) as conn:
        for field, value in {**change, "config_hash": digest}.items():
            conn.execute(
                psycopg.sql.SQL("UPDATE evaluation.experiments SET {}=%s WHERE id=%s").format(
                    psycopg.sql.Identifier(field)
                ),
                (Jsonb(value) if isinstance(value, dict) else value, experiment["id"]),
            )
    access.approved_config_hashes = frozenset({digest})
    with pytest.raises(ApplicationError) as error:
        asyncio.run(
            accept_run(
                settings, access, experiment["actor"], experiment["id"], str(uuid4()), uuid4()
            )
        )
    assert error.value.code == code
    assert record_counts(evaluation_database) == (0, 0, 0, 0, 0, 0, 0)


def test_http_acceptance_status_and_revocation(settings, access, experiment, monkeypatch):
    """HTTP 접수·조회·재전송 및 허용 목록 철회 시 현재 권한을 확인한다.

    Args:
        settings: 실행 계정 연결.
        access: 허용 설정.
        experiment: 합성 실험.
        monkeypatch: 세션 조회 경계만 합성 사용자로 대체한다.

    Returns:
        None: 실제 DB commit 후202·Location·응답 재생과 철회 후 거부를 확인한다.
    """
    actor = experiment["actor"]
    client = client_for((actor,))
    client.app.state.settings = settings
    client.app.state.evaluation_access = access
    loader = AsyncMock(return_value=Principal(user_id=actor, roles={"sales"}))
    monkeypatch.setattr("backend.api.platform.evaluations.access.load_session_principal", loader)
    client.cookies.set(SESSION_COOKIE, "synthetic-session")
    client.headers.update(
        {
            "Origin": "https://app.example.invalid",
            CSRF_HEADER: csrf_token("synthetic-session"),
            "Idempotency-Key": str(uuid4()),
        }
    )
    body = {"experiment_id": str(experiment["id"])}
    first = client.post("/api/v1/platform/evaluations/runs", json=body)
    assert first.status_code == 202, first.text
    again = client.post("/api/v1/platform/evaluations/runs", json=body)
    assert again.json() == first.json()
    assert again.headers["location"] == first.headers["location"]
    status = client.get(first.headers["location"])
    assert status.status_code == 200
    assert status.json()["data"]["requested_count"] == 2
    assert "gold" not in status.text and "raw_output" not in status.text
    from backend.api.platform.evaluations.smoke_worker import dispatch_one

    assert asyncio.run(dispatch_one(settings, access)) is not None
    completed = client.get(first.headers["location"])
    assert completed.json()["data"]["status"] == "SUCCEEDED"
    replay = client.post("/api/v1/platform/evaluations/runs", json=body)
    assert replay.json() == first.json()
    access.approved_config_hashes = frozenset()
    assert client.get(first.headers["location"]).status_code == 404
    assert client.post("/api/v1/platform/evaluations/runs", json=body).status_code == 403
    access.evaluator_user_ids = frozenset()
    assert client.get(first.headers["location"]).status_code == 403


def test_production_and_other_creator_are_denied(settings, access, experiment, evaluation_database):
    """운영 활성화 우회와 다른 생성자의 실험 접근을 거절한다.

    Args:
        settings: 실행 계정 설정.
        access: 로컬 접수를 켠 허용 설정.
        experiment: 합성 실험.
        evaluation_database: 전용 DB.

    Returns:
        None: 환경 잠금503과 소유 범위404, 작업 미생성을 확인한다.
    """
    with pytest.raises(ApplicationError) as locked:
        asyncio.run(
            accept_run(
                settings.model_copy(update={"environment": "production"}),
                access,
                experiment["actor"],
                experiment["id"],
                str(uuid4()),
                uuid4(),
            )
        )
    assert locked.value.status_code == 503
    stranger = uuid4()
    access.evaluator_user_ids = frozenset({stranger})
    with pytest.raises(ApplicationError) as hidden:
        asyncio.run(accept_run(settings, access, stranger, experiment["id"], str(uuid4()), uuid4()))
    assert hidden.value.status_code == 404
    assert record_counts(evaluation_database) == (0, 0, 0, 0, 0, 0, 0)


def test_changed_configuration_and_targets_fail_closed(
    settings, access, experiment, evaluation_database
):
    """허용 hash를 바꾸지 않은 메타데이터 변조와 대상 추가를 검출한다.

    Args:
        settings: 실행 계정 연결.
        access: 허용 설정.
        experiment: 합성 실험.
        evaluation_database: 전용 DB.

    Returns:
        None: 구성·대상 drift를 거절하고 작업을 만들지 않음을 확인한다.
    """
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            "UPDATE evaluation.experiments SET policy_version='changed' WHERE id=%s",
            (experiment["id"],),
        )
    with pytest.raises(ApplicationError) as changed:
        asyncio.run(
            accept_run(
                settings, access, experiment["actor"], experiment["id"], str(uuid4()), uuid4()
            )
        )
    assert changed.value.code == "EXPERIMENT_HASH_MISMATCH"
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            "UPDATE evaluation.experiments SET policy_version=NULL WHERE id=%s", (experiment["id"],)
        )
        conn.execute(
            "INSERT INTO evaluation.experiment_targets "
            "(experiment_id,target_ref,target_kind,data_origin) "
            "VALUES (%s,'added','smoke','synthetic')",
            (experiment["id"],),
        )
    with pytest.raises(ApplicationError) as targets:
        asyncio.run(
            accept_run(
                settings, access, experiment["actor"], experiment["id"], str(uuid4()), uuid4()
            )
        )
    assert targets.value.code == "TARGET_SET_MISMATCH"
    assert record_counts(evaluation_database) == (0, 0, 0, 0, 0, 0, 0)
