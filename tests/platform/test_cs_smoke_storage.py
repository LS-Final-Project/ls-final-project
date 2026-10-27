"""고정 합성 C/S 호출의 실제 DB 저장·복구·잠금 회귀. 실제 자료는 사용하지 않는다."""

import asyncio
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from backend.api.platform.evaluations.admission import (
    CONFIG_FIELDS,
    accept_run,
    canonical_hash,
    get_run,
    validate_smoke_experiment,
)
from backend.api.platform.evaluations.controls import control_run
from backend.api.platform.evaluations.cs_smoke import (
    DATASET_HASH,
    FIXTURES,
    INPUT_VERSION,
    PROFILE,
    PROMPT_VERSION,
    TARGET_SET_HASH,
)
from backend.api.platform.evaluations.job_worker import execute_run
from backend.api.platform.evaluations.smoke_worker import advance_run, claim_run, dispatch_one
from backend.api.platform.jobs.events import EventPayload
from backend.api.platform.jobs.status import get_evaluation_job
from backend.shared.errors import ApplicationError
from tests.platform import test_job_worker as worker_tests
from tests.platform.test_smoke_worker import expire

worker = worker_tests.worker


@pytest.fixture
def cs_experiment(experiment, evaluation_database):
    """접수 전 검사 전용 관리자 연결로 C/S 합성 실험을 준비한다.

    Args:
        experiment: 정리 절차를 제공하는 미접수 합성 실험.
        evaluation_database: 격리 DB 관리자 연결.

    Returns:
        dict: 고정 프로필과 재계산한 승인 hash.
    """
    experiment.update(
        configuration=PROFILE,
        dataset_hash=DATASET_HASH,
        input_builder_version=INPUT_VERSION,
        prompt_version=PROMPT_VERSION,
        target_set_hash=TARGET_SET_HASH,
        dataset_manifest={},
    )
    experiment["config_hash"] = canonical_hash({k: experiment[k] for k in CONFIG_FIELDS})
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            "UPDATE evaluation.experiments SET configuration=%s,dataset_hash=%s,"
            "input_builder_version=%s,prompt_version=%s,target_set_hash=%s,config_hash=%s WHERE id=%s",
            (
                Jsonb(PROFILE),
                DATASET_HASH,
                INPUT_VERSION,
                PROMPT_VERSION,
                TARGET_SET_HASH,
                experiment["config_hash"],
                experiment["id"],
            ),
        )
        conn.execute(
            "DELETE FROM evaluation.experiment_targets WHERE experiment_id=%s", (experiment["id"],)
        )
        for ref in FIXTURES:
            conn.execute(
                "INSERT INTO evaluation.experiment_targets "
                "(experiment_id,target_ref,target_kind,data_origin) VALUES (%s,%s,'smoke','synthetic')",
                (experiment["id"], ref),
            )
    return experiment


@pytest.fixture
def cs_access(access, cs_experiment):
    """새 합성 프로필만 승인한 서버 설정을 만든다.

    Args:
        access: 기존 local/test 설정.
        cs_experiment: 고정 합성 실험.

    Returns:
        EvaluationAccessSettings: 프로필별 승인 hash.
    """
    return access.model_copy(update={"approved_config_hashes": {cs_experiment["config_hash"]}})


@pytest.fixture
def cs_run(settings, cs_access, cs_experiment):
    """실제 접수 경로로 C/S 합성 실행을 만든다.

    Args:
        settings: 실행 계정 설정.
        cs_access: 합성 허용 설정.
        cs_experiment: 합성 실험.

    Returns:
        UUID: 새 실행 식별자.
    """
    return asyncio.run(
        accept_run(
            settings, cs_access, cs_experiment["actor"], cs_experiment["id"], str(uuid4()), uuid4()
        )
    ).data.run_id


def saved(dsn, run_id):
    """내부 감사 저장값을 검사 전용 관리자 연결로 읽는다.

    Args:
        dsn: 격리 DB 연결.
        run_id: 합성 실행.

    Returns:
        list: 원시 응답·사용량을 포함한 저장 시도.
    """
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        return conn.execute(
            "SELECT a.*,c.target_ref,c.status AS case_status,c.final_output "
            "FROM evaluation.case_attempts a JOIN evaluation.cases c ON c.id=a.case_id "
            "WHERE c.run_id=%s ORDER BY c.target_ref",
            (run_id,),
        ).fetchall()


def test_cs_attempts_persist_and_recover_without_duplicate(
    settings, cs_access, cs_experiment, cs_run, evaluation_database
):
    """완료 사례를 보존하며 만료 인수 후 실패까지 저장하고 부분 완료를 공개한다.

    Args:
        settings: 실행 계정 설정.
        cs_access: 승인 설정.
        cs_experiment: 합성 실험.
        cs_run: 접수 실행.
        evaluation_database: 격리 DB.

    Returns:
        None: 최초 시도·사용량 null/0·원문 차단·종료 재전달 불변을 확인한다.
    """
    lease = asyncio.run(claim_run(settings, cs_access, cs_run))
    assert not asyncio.run(advance_run(settings, cs_access, lease))
    first = saved(evaluation_database, cs_run)
    assert len(first) == 1
    expire(evaluation_database, cs_run)
    assert asyncio.run(dispatch_one(settings, cs_access)) == cs_run
    rows = saved(evaluation_database, cs_run)
    assert first[0] in rows
    assert len(rows) == 3
    assert {row["outcome"] for row in rows} == {"VALID", "SCHEMA_VIOLATION", "TIMEOUT"}
    for row in rows:
        assert row["step"] == "classify" and row["attempt"] == 1 and row["is_final"]
        assert len(row["input_sha256"]) == 64 and row["latency_ms"] >= 0
        assert row["input_tokens"] == 7 and row["cost_usd"] is None
        assert row["thought_tokens"] is None
        if row["outcome"] == "TIMEOUT":
            assert row["raw_output"] is None and row["parsed_output"] is None
            assert row["total_tokens"] is None and row["cached_input_tokens"] is None
            assert row["error_code"] == "CS_PROVIDER_TIMEOUT" and row["case_status"] == "FAILED"
        else:
            assert row["raw_output"] == "[REDACTED]"
            assert row["total_tokens"] == 10 and row["cached_input_tokens"] == 0
            assert row["model_version"] == "cs-smoke-v1" and row["finish_reasons"] == ["STOP"]
            if row["outcome"] == "SCHEMA_VIOLATION":
                assert row["parsed_output"] is None and row["final_output"] is None
                assert row["error_code"] == "CS_INVALID_OUTPUT"
                assert row["case_status"] == "INVALID_OUTPUT"
            else:
                assert row["parsed_output"] == row["final_output"]
                assert set(row["final_output"]) == {
                    "category_code",
                    "secondary_category",
                    "urgency_flag",
                    "provided_info",
                    "return_subtype",
                }
    status = asyncio.run(get_run(settings, cs_access, cs_experiment["actor"], cs_run))
    assert status.status == "PARTIAL" and status.requested_count == 3
    job = asyncio.run(
        get_evaluation_job(settings, cs_access, cs_experiment["actor"], status.job_id)
    )
    assert job.status == "PARTIAL"
    public = status.model_dump_json() + job.model_dump_json()
    assert all(secret not in public for secret in ("raw_output", "provided_info", "@example.test"))
    with psycopg.connect(evaluation_database) as conn:
        events = conn.execute(
            "SELECT type,payload FROM runtime.job_events WHERE job_id=%s ORDER BY sequence",
            (status.job_id,),
        ).fetchall()
        assert events[-1] == ("job.completed", {"state": "PARTIAL", "synthetic": True})
        assert "@example.test" not in str(events)
        for _, payload in events:
            EventPayload.model_validate(payload)
        assert conn.execute(
            "SELECT valid_output_count,failed_count,held_count,retry_count FROM evaluation.runs "
            "WHERE id=%s",
            (cs_run,),
        ).fetchone() == (1, 2, 0, 0)
    assert asyncio.run(claim_run(settings, cs_access, cs_run)) is None
    with pytest.raises(ApplicationError, match="STALE_WORKER"):
        asyncio.run(advance_run(settings, cs_access, lease))
    assert saved(evaluation_database, cs_run) == rows


@pytest.mark.parametrize("boundary", ["cancel", "approval", "evaluator", "expired"])
def test_cs_storage_rechecks_execution_boundary(
    settings, cs_access, cs_experiment, cs_run, evaluation_database, boundary
):
    """현재 실행 권한을 잃은 worker는 추가 사례를 저장하지 못한다.

    Args:
        settings: 실행 계정 설정.
        cs_access: 승인 설정.
        cs_experiment: 실험 소유자.
        cs_run: 접수 실행.
        evaluation_database: 격리 DB.
        boundary: 취소·구성 승인·평가자·lease 회수 종류.

    Returns:
        None: 이미 저장한 시도만 남는다.
    """
    lease = asyncio.run(claim_run(settings, cs_access, cs_run))
    asyncio.run(advance_run(settings, cs_access, lease))
    before = saved(evaluation_database, cs_run)
    if boundary == "cancel":
        state = asyncio.run(get_run(settings, cs_access, cs_experiment["actor"], cs_run))
        asyncio.run(
            control_run(
                settings,
                cs_access,
                cs_experiment["actor"],
                cs_run,
                "cancel",
                str(uuid4()),
                f'"{state.version}"',
                uuid4(),
            )
        )
    elif boundary == "approval":
        cs_access = cs_access.model_copy(update={"approved_config_hashes": set()})
    elif boundary == "evaluator":
        cs_access = cs_access.model_copy(update={"evaluator_user_ids": set()})
    else:
        expire(evaluation_database, cs_run)
    with pytest.raises(ApplicationError):
        asyncio.run(advance_run(settings, cs_access, lease))
    assert saved(evaluation_database, cs_run) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("configuration", {**PROFILE, "provider": "gemini"}),
        ("input_builder_version", "old"),
        ("prompt_version", "old"),
        ("dataset_hash", "b" * 64),
        ("target_set_hash", "b" * 64),
        ("data_split", "test"),
        ("model_id", "gemini"),
    ],
)
def test_approved_hash_cannot_override_fixed_cs_profile(cs_experiment, cs_access, field, value):
    """승인 목록에 있어도 실제 모델·시험·임의 사례와 판본은 거부한다.

    Args:
        cs_experiment: 합성 실험.
        cs_access: 승인 설정.
        field: 변조할 필드.
        value: 미지원 값.

    Returns:
        None: 저장 접수 전에 안전한 오류를 반환한다.
    """
    changed = {**cs_experiment, field: value}
    changed["config_hash"] = canonical_hash({k: changed[k] for k in CONFIG_FIELDS})
    access = cs_access.model_copy(update={"approved_config_hashes": {changed["config_hash"]}})
    with pytest.raises(ApplicationError) as error:
        validate_smoke_experiment(changed, access)
    assert error.value.code in {"LOCAL_SMOKE_ONLY", "FINAL_TEST_LOCKED"}


def test_cs_job_uses_dedicated_worker_role(
    worker, settings, cs_access, cs_experiment, cs_run, evaluation_database
):
    """전용 Jobs DB 역할로도 C/S 저장·부분 완료·중복 실행 차단을 확인한다.

    Args:
        worker: 최소 권한 worker 연결.
        settings: 플랫폼 조회 연결.
        cs_access: 합성 승인 설정.
        cs_experiment: 실험 소유자.
        cs_run: 지정 실행.
        evaluation_database: 격리 DB.

    Returns:
        None: 추가 DB 권한 없이 최초 시도 세 건이 보존된다.
    """
    asyncio.run(execute_run(worker, cs_access, cs_run))
    before = saved(evaluation_database, cs_run)
    assert len(before) == 3
    asyncio.run(execute_run(worker, cs_access, cs_run))
    assert saved(evaluation_database, cs_run) == before
    assert (
        asyncio.run(get_run(settings, cs_access, cs_experiment["actor"], cs_run)).status
        == "PARTIAL"
    )
