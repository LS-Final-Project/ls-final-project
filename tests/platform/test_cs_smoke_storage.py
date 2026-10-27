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


@pytest.mark.parametrize("metric", ["category", "urgency", "secondary"])
def test_stored_metrics_match_first_calls_and_ignore_retry(
    settings, cs_access, cs_run, evaluation_database, metric
):
    """실제 저장 결과가 직접 집계와 같으며 최종 재시도 성공에도 변하지 않는다.

    Args:
        settings: 합성 실행 설정.
        cs_access: 합성 승인 설정.
        cs_run: 접수 실행.
        evaluation_database: 격리 DB.
        metric: 검증할 세 분류 지표.

    Returns:
        None: 읽기 전용 집계·실패 분모·고정 메타데이터·원문 비노출을 확인한다.
    """
    from backend.api.platform.evaluations.cs_smoke import run_case
    from eval.cs_stored_metrics import summarize_stored_first_attempts
    from eval.cs_strata import summarize_first_attempts_by_group

    gold = dict.fromkeys(
        FIXTURES, {"category": "RETURN", "urgency": "NONE", "secondary": None}[metric]
    )
    metadata = {"cs-smoke-valid": {"channel": "PHONE", "body_language": "ko"}}
    assert asyncio.run(dispatch_one(settings, cs_access)) == cs_run
    # 합성 worker는 빈 attributes만 허용하므로 종료 후 검사 관리자만 메타데이터를 주입한다.
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            "UPDATE evaluation.experiment_targets SET attributes=%s "
            "WHERE experiment_id=(SELECT experiment_id FROM evaluation.runs WHERE id=%s) "
            "AND target_ref='cs-smoke-valid'",
            (Jsonb({**metadata["cs-smoke-valid"], "unrelated": "private@example.invalid"}), cs_run),
        )
    expected = summarize_first_attempts_by_group(
        gold, {ref: run_case(ref) for ref in FIXTURES}, metadata, metric=metric
    )
    with psycopg.connect(evaluation_database) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        first = summarize_stored_first_attempts(
            conn, cs_run, gold, gold_version=None, metric=metric
        )
    assert first["report"] == expected
    assert first["run_status"] == "PARTIAL"
    assert first["report"]["overall"]["total"] == 3
    assert first["report"]["overall"]["correct"] == 1
    valid = run_case("cs-smoke-valid").prediction.model_dump()
    with psycopg.connect(evaluation_database) as conn:
        cases = conn.execute(
            "SELECT id FROM evaluation.cases WHERE run_id=%s", (cs_run,)
        ).fetchall()
        for (case_id,) in cases:
            conn.execute(
                "UPDATE evaluation.case_attempts SET is_final=false WHERE case_id=%s", (case_id,)
            )
            conn.execute(
                "INSERT INTO evaluation.case_attempts(id,case_id,step,attempt,is_final,outcome,parsed_output,started_at,finished_at) "
                "VALUES (%s,%s,'classify',2,true,'VALID',%s,now(),now())",
                (uuid4(), case_id, Jsonb(valid)),
            )
        conn.execute(
            "UPDATE evaluation.cases SET final_output=%s WHERE run_id=%s", (Jsonb(valid), cs_run)
        )
    with psycopg.connect(evaluation_database) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        assert (
            summarize_stored_first_attempts(conn, cs_run, gold, gold_version=None, metric=metric)
            == first
        )
    assert "private@example.invalid" not in str(first)
    assert all(field not in str(first) for field in ("raw_output", "provided_info", "final_output"))


def test_stored_metrics_preserve_unstarted_targets(
    settings, cs_access, cs_run, evaluation_database
):
    """미실행·부분 진행 및 사례 행 누락에도 실험 대상 전체를 분모로 유지한다.

    Args:
        settings: 합성 설정.
        cs_access: 승인 설정.
        cs_run: 접수 실행.
        evaluation_database: 격리 DB.

    Returns:
        None: 저장 행 수 대신 고정 대상 수로 계산함을 확인한다.
    """
    from eval.cs_stored_metrics import summarize_stored_first_attempts

    gold = dict.fromkeys(FIXTURES, "RETURN")
    with psycopg.connect(evaluation_database) as conn:
        before = summarize_stored_first_attempts(
            conn, cs_run, gold, gold_version=None, metric="category"
        )
    assert before["run_status"] == "QUEUED"
    assert before["report"]["overall"]["outcomes"]["NOT_STARTED"] == 3
    lease = asyncio.run(claim_run(settings, cs_access, cs_run))
    assert not asyncio.run(advance_run(settings, cs_access, lease))
    with psycopg.connect(evaluation_database) as conn:
        conn.execute("DELETE FROM evaluation.cases WHERE run_id=%s AND status='PENDING'", (cs_run,))
        partial = summarize_stored_first_attempts(
            conn, cs_run, gold, gold_version=None, metric="category"
        )
    assert partial["run_status"] == "RUNNING"
    assert partial["report"]["overall"]["total"] == 3
    assert partial["report"]["overall"]["outcomes"]["NOT_STARTED"] == 2
    assert partial["report"]["missing_metadata"] == {"channel": 3, "body_language": 3}


@pytest.mark.parametrize(
    "invalid", ["run", "missing_gold", "extra_gold", "version", "count", "schema", "outcome"]
)
def test_stored_metrics_reject_inconsistent_snapshot(
    settings, cs_access, cs_run, evaluation_database, invalid
):
    """다른 대상·정답 판본·저장 스키마 및 상태 모순을 빈 성공으로 숨기지 않는다.

    Args:
        settings: 합성 설정.
        cs_access: 승인 설정.
        cs_run: 접수 실행.
        evaluation_database: 격리 DB.
        invalid: 주입할 불일치 종류.

    Returns:
        None: 원문 없는 명시적 오류를 확인한다.
    """
    from eval.cs_stored_metrics import summarize_stored_first_attempts

    assert asyncio.run(dispatch_one(settings, cs_access)) == cs_run
    gold = dict.fromkeys(FIXTURES, "RETURN")
    if invalid == "missing_gold":
        gold.pop("cs-smoke-timeout")
    if invalid == "extra_gold":
        gold["not-in-run"] = "RETURN"
    with psycopg.connect(evaluation_database) as conn:
        if invalid == "count":
            conn.execute("UPDATE evaluation.runs SET requested_count=2 WHERE id=%s", (cs_run,))
        if invalid in {"schema", "outcome"}:
            value = {"private": "private@example.invalid"} if invalid == "schema" else None
            conn.execute(
                "UPDATE evaluation.case_attempts SET parsed_output=%s WHERE outcome='VALID' "
                "AND case_id IN (SELECT id FROM evaluation.cases WHERE run_id=%s)",
                (Jsonb(value) if value is not None else None, cs_run),
            )
        with pytest.raises(ValueError, match="^CS_(STORED_METRICS|METRICS)_INVALID_INPUT$"):
            summarize_stored_first_attempts(
                conn,
                uuid4() if invalid == "run" else cs_run,
                gold,
                gold_version="different" if invalid == "version" else None,
                metric="category",
            )


@pytest.mark.parametrize("metric", ["category", "urgency", "secondary"])
@pytest.mark.parametrize("final_failure", [False, True])
def test_stored_attempt_comparison_keeps_first_and_counts_transitions(
    settings, cs_access, cs_run, evaluation_database, metric, final_failure
):
    """최초 결과를 보존하며 복구·악화·미확정을 동일 분모에서 비교한다.

    Args:
        settings: 합성 실행 설정.
        cs_access: 합성 승인 설정.
        cs_run: 접수 실행.
        evaluation_database: 격리 DB.
        metric: 비교할 세 지표.
        final_failure: 정답에서 실행 실패로 악화되는 경우.

    Returns:
        None: 최초/최종 분리, null 정답, 최종 표시 부재와 분모 보존을 검증한다.
    """
    from backend.api.platform.evaluations.cs_smoke import run_case
    from eval.cs_stored_metrics import compare_stored_attempts, summarize_stored_first_attempts

    gold = dict.fromkeys(
        FIXTURES, {"category": "RETURN", "urgency": "NONE", "secondary": None}[metric]
    )
    with psycopg.connect(evaluation_database) as conn:
        pending = compare_stored_attempts(conn, cs_run, gold, gold_version=None, metric=metric)
    assert pending["transitions"]["FINAL_PENDING"] == 3
    assert pending["final"]["accuracy"] == 0
    assert asyncio.run(dispatch_one(settings, cs_access)) == cs_run
    with psycopg.connect(evaluation_database) as conn:
        first = summarize_stored_first_attempts(
            conn, cs_run, gold, gold_version=None, metric=metric
        )
        original = compare_stored_attempts(conn, cs_run, gold, gold_version=None, metric=metric)
    assert original["first"] == first
    assert original["transitions"] == {
        "STILL_CORRECT": 1,
        "STILL_INCORRECT": 2,
        "RECOVERED": 0,
        "REGRESSED": 0,
        "FINAL_PENDING": 0,
    }
    valid = run_case("cs-smoke-valid").prediction.model_dump()
    wrong = {
        **valid,
        "category_code": "INSTALL",
        "urgency_flag": "SAFETY",
        "secondary_category": "DEFECT",
    }
    with psycopg.connect(evaluation_database) as conn:
        rows = conn.execute(
            "SELECT id,target_ref FROM evaluation.cases WHERE run_id=%s", (cs_run,)
        ).fetchall()
        for case_id, ref in rows:
            conn.execute(
                "UPDATE evaluation.case_attempts SET is_final=false WHERE case_id=%s", (case_id,)
            )
            conn.execute(
                "INSERT INTO evaluation.case_attempts(id,case_id,step,attempt,is_final,outcome,parsed_output,started_at,finished_at) "
                "VALUES (%s,%s,'classify',2,%s,'VALID',%s,now(),now())",
                (
                    uuid4(),
                    case_id,
                    ref != "cs-smoke-timeout",
                    Jsonb(wrong if ref == "cs-smoke-valid" else valid),
                ),
            )
            if final_failure and ref == "cs-smoke-valid":
                conn.execute(
                    "UPDATE evaluation.case_attempts SET outcome='TIMEOUT',parsed_output=NULL "
                    "WHERE case_id=%s AND step='classify' AND attempt=2",
                    (case_id,),
                )
            # 다른 처리 단계의 큰 시도 번호/최종 표시는 분류 비교에 섞이지 않는다.
            conn.execute(
                "INSERT INTO evaluation.case_attempts(id,case_id,step,attempt,is_final,outcome,started_at,finished_at) "
                "VALUES (%s,%s,'draft',99,true,'TIMEOUT',now(),now())",
                (uuid4(), case_id),
            )
            # 사례의 최종 필드는 모두 정답이어도 선택한 시도의 결과만 비교한다.
            conn.execute(
                "UPDATE evaluation.cases SET final_output=%s WHERE id=%s", (Jsonb(valid), case_id)
            )
    with psycopg.connect(evaluation_database) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        compared = compare_stored_attempts(conn, cs_run, gold, gold_version=None, metric=metric)
    assert compared["first"] == first
    assert compared["final"]["total"] == 3
    assert compared["final"]["correct"] == 1
    assert compared["final"]["accuracy"] == pytest.approx(1 / 3)
    assert compared["final"]["outcomes"]["NOT_FINAL"] == 1
    assert compared["version"] == "cs-stored-attempt-comparison-v2"
    final_quality = compared["final"]
    assert final_quality["selection"] == "is_final"
    assert [case["attempt_number"] for case in final_quality["cases"]].count(2) == 2
    if metric == "category":
        assert final_quality["macro_f1"] == pytest.approx(0.5 / 10)
    elif metric == "urgency":
        assert final_quality["macro_f1"] == pytest.approx(0.5 / 6)
        assert final_quality["critical"]["support"] == 0
        assert final_quality["non_none"]["recall"] is None
    else:
        assert final_quality["composite"]["macro_f1"] is None
        assert final_quality["absent"]["invented"] == (0 if final_failure else 1)
    assert compared["transitions"] == {
        "RECOVERED": 1,
        "REGRESSED": 1,
        "FINAL_PENDING": 1,
        "STILL_CORRECT": 0,
        "STILL_INCORRECT": 0,
    }
    cases = {case["case_id"]: case for case in compared["cases"]}
    assert cases["cs-smoke-invalid"]["final_attempt_number"] == 2
    assert cases["cs-smoke-timeout"]["final_attempt_number"] is None
    assert cases["cs-smoke-timeout"]["final_prediction"] is None
    assert cases["cs-smoke-timeout"]["final_correct"] is False
    assert sum(compared["transitions"].values()) == 3
    assert "raw_output" not in str(compared) and "provided_info" not in str(compared)


@pytest.mark.parametrize(
    "invalid", ["missing_first", "stale_final", "unfinished", "schema", "outcome"]
)
def test_stored_attempt_comparison_rejects_inconsistent_final(
    settings, cs_access, cs_run, evaluation_database, invalid
):
    """최초 부재·오래된 최종 표시·미종료·스키마·상태 모순을 거부한다.

    Args:
        settings: 합성 실행 설정.
        cs_access: 승인 설정.
        cs_run: 합성 실행.
        evaluation_database: 격리 DB.
        invalid: 주입할 모순.

    Returns:
        None: 모순을 복구 성공이나 최초 품질 변경으로 숨기지 않음을 확인한다.
    """
    from eval.cs_stored_metrics import compare_stored_attempts, summarize_stored_first_attempts

    assert asyncio.run(dispatch_one(settings, cs_access)) == cs_run
    gold = dict.fromkeys(FIXTURES, "RETURN")
    with psycopg.connect(evaluation_database) as conn:
        case_id = conn.execute(
            "SELECT id FROM evaluation.cases WHERE run_id=%s AND target_ref='cs-smoke-timeout'",
            (cs_run,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO evaluation.case_attempts(id,case_id,step,attempt,is_final,outcome,started_at,finished_at) "
            "VALUES (%s,%s,'classify',2,false,'TIMEOUT',now(),now())",
            (uuid4(), case_id),
        )
        if invalid != "stale_final":
            conn.execute(
                "UPDATE evaluation.case_attempts SET is_final=false WHERE case_id=%s", (case_id,)
            )
            conn.execute(
                "UPDATE evaluation.case_attempts SET is_final=true WHERE case_id=%s AND attempt=2",
                (case_id,),
            )
        if invalid == "missing_first":
            conn.execute(
                "DELETE FROM evaluation.case_attempts WHERE case_id=%s AND attempt=1", (case_id,)
            )
        if invalid == "unfinished":
            conn.execute(
                "UPDATE evaluation.case_attempts SET finished_at=NULL WHERE case_id=%s AND attempt=2",
                (case_id,),
            )
        if invalid in {"schema", "outcome"}:
            conn.execute(
                "UPDATE evaluation.case_attempts SET outcome='VALID',parsed_output=%s WHERE case_id=%s AND attempt=2",
                (
                    Jsonb({"private": "private@example.invalid"}) if invalid == "schema" else None,
                    case_id,
                ),
            )
        # 최초 전용 조회는 최종 저장 모순에 영향받지 않는다.
        assert (
            summarize_stored_first_attempts(
                conn, cs_run, gold, gold_version=None, metric="category"
            )["report"]["overall"]["total"]
            == 3
        )
        with pytest.raises(ValueError, match="^CS_STORED_COMPARISON_INVALID_INPUT$"):
            compare_stored_attempts(conn, cs_run, gold, gold_version=None, metric="category")


def test_stored_usage_preserves_missing_zero_failures_and_retries(
    settings, cs_access, cs_run, evaluation_database
):
    """실제 DB의 부분 사용량과 실패·재시도 비용을 손계산 값과 대조한다.

    Args:
        settings: 합성 실행 설정.
        cs_access: 승인 설정.
        cs_run: 접수 실행.
        evaluation_database: 격리 검사 DB.

    Returns:
        None: 누락/0·최초/재시도 분리 및 지연 보간을 확인한다.
    """
    from eval.cs_usage import summarize_stored_usage

    with psycopg.connect(evaluation_database) as conn:
        empty = summarize_stored_usage(conn, cs_run)
    assert empty["target_count"] == empty["targets_without_first_attempt"] == 3
    assert empty["all"]["stored_attempt_count"] == 0
    assert empty["all"]["cost_usd"]["total"] is None
    assert empty["all"]["latency_ms"]["p95"] is None
    assert asyncio.run(dispatch_one(settings, cs_access)) == cs_run
    with psycopg.connect(evaluation_database) as conn:
        cases = dict(
            conn.execute(
                "SELECT target_ref,id FROM evaluation.cases WHERE run_id=%s", (cs_run,)
            ).fetchall()
        )
        for ref, latency, cost in (
            ("cs-smoke-valid", 0, "0.000000"),
            ("cs-smoke-invalid", 100, "0.100000"),
            ("cs-smoke-timeout", None, None),
        ):
            conn.execute(
                "UPDATE evaluation.case_attempts SET latency_ms=%s,cost_usd=%s,pricing_version=%s WHERE case_id=%s",
                (latency, cost, "price-v1" if cost is not None else None, cases[ref]),
            )
        for ref, latency, tokens, cost, outcome, finished in (
            ("cs-smoke-invalid", 200, 5, "0.200000", "TIMEOUT", True),
            ("cs-smoke-timeout", 400, 0, "0.300000", "HELD", False),
        ):
            conn.execute(
                "INSERT INTO evaluation.case_attempts(id,case_id,step,attempt,outcome,started_at,finished_at,latency_ms,input_tokens,cost_usd,pricing_version) "
                "VALUES (%s,%s,'classify',2,%s,now(),CASE WHEN %s THEN now() END,%s,%s,%s,'price-v2')",
                (uuid4(), cases[ref], outcome, finished, latency, tokens, cost),
            )
        conn.execute(
            "INSERT INTO evaluation.case_attempts(id,case_id,step,attempt,outcome,started_at,input_tokens) "
            "VALUES (%s,%s,'draft',1,'HELD',now(),99999)",
            (uuid4(), cases["cs-smoke-valid"]),
        )
    with psycopg.connect(evaluation_database) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        report = summarize_stored_usage(conn, cs_run)
    assert report["targets_without_first_attempt"] == 0
    first, retry, all_usage = (report[key] for key in ("first", "retry", "all"))
    assert [group["stored_attempt_count"] for group in (first, retry, all_usage)] == [3, 2, 5]
    assert all_usage["unfinished_attempt_count"] == 1
    assert all_usage["outcomes"]["TIMEOUT"] == 2 and all_usage["outcomes"]["HELD"] == 1
    assert first["tokens"]["input_tokens"]["total"] == 21
    assert retry["tokens"]["input_tokens"]["total"] == 5
    assert all_usage["tokens"]["input_tokens"]["total"] == 26
    assert first["tokens"]["total_tokens"] == {
        "reported_count": 2,
        "missing_count": 1,
        "known_sum": 20,
        "total": None,
    }
    assert first["tokens"]["cached_input_tokens"]["known_sum"] == 0
    assert all_usage["tokens"]["thought_tokens"]["known_sum"] is None
    assert first["latency_ms"] == {"reported_count": 2, "missing_count": 1, "p50": 50, "p95": 95}
    assert retry["latency_ms"]["p50"] == 300 and retry["latency_ms"]["p95"] == 390
    assert all_usage["latency_ms"]["p50"] == 150
    assert all_usage["latency_ms"]["p95"] == pytest.approx(370)
    assert first["cost_usd"] == {
        "reported_count": 2,
        "missing_count": 1,
        "known_sum": "0.100000",
        "total": None,
        "pricing_versions": ["price-v1"],
    }
    assert retry["cost_usd"]["total"] == "0.500000"
    assert all_usage["cost_usd"]["known_sum"] == "0.600000"
    assert all_usage["cost_usd"]["total"] is None
    assert all_usage["cost_usd"]["pricing_versions"] == ["price-v1", "price-v2"]
    assert "raw_output" not in str(report) and "parsed_output" not in str(report)


@pytest.mark.parametrize("invalid", ["run", "count", "nan_cost", "empty_price"])
def test_stored_usage_rejects_invalid_run_or_cost(
    settings, cs_access, cs_run, evaluation_database, invalid
):
    """실행·분모·가격표·비유한 금액 오류를 합계로 숨기지 않는다.

    Args:
        settings: 합성 실행 설정.
        cs_access: 승인 설정.
        cs_run: 접수 실행.
        evaluation_database: 격리 DB.
        invalid: 주입할 오류.

    Returns:
        None: 안전한 명시적 오류를 확인한다.
    """
    from eval.cs_usage import summarize_stored_usage

    assert asyncio.run(dispatch_one(settings, cs_access)) == cs_run
    with psycopg.connect(evaluation_database) as conn:
        if invalid == "count":
            conn.execute("UPDATE evaluation.runs SET requested_count=2 WHERE id=%s", (cs_run,))
        if invalid in {"nan_cost", "empty_price"}:
            conn.execute(
                "UPDATE evaluation.case_attempts SET cost_usd=%s,pricing_version=%s "
                "WHERE case_id IN (SELECT id FROM evaluation.cases WHERE run_id=%s)",
                (
                    "NaN" if invalid == "nan_cost" else "0",
                    "v1" if invalid == "nan_cost" else " ",
                    cs_run,
                ),
            )
        with pytest.raises(ValueError, match="^CS_USAGE_INVALID_INPUT$"):
            summarize_stored_usage(conn, uuid4() if invalid == "run" else cs_run)


def test_stored_usage_single_zero_is_reported_not_missing(
    settings, cs_access, cs_run, evaluation_database
):
    """단일 보고값 0과 아직 저장되지 않은 대상의 경계를 유지한다.

    Args:
        settings: 합성 실행 설정.
        cs_access: 승인 설정.
        cs_run: 접수 실행.
        evaluation_database: 격리 DB.

    Returns:
        None: 0 합계·단일 표본 분위수와 미실행 대상 수를 확인한다.
    """
    from eval.cs_usage import summarize_stored_usage

    lease = asyncio.run(claim_run(settings, cs_access, cs_run))
    assert not asyncio.run(advance_run(settings, cs_access, lease))
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            "UPDATE evaluation.case_attempts SET input_tokens=0,cost_usd=0,pricing_version='v1',latency_ms=0 "
            "WHERE case_id IN (SELECT id FROM evaluation.cases WHERE run_id=%s)",
            (cs_run,),
        )
        result = summarize_stored_usage(conn, cs_run)
    assert result["targets_without_first_attempt"] == 2
    assert result["all"]["tokens"]["input_tokens"] == {
        "reported_count": 1,
        "missing_count": 0,
        "known_sum": 0,
        "total": 0,
    }
    assert result["all"]["cost_usd"]["total"] == "0.000000"
    assert result["all"]["latency_ms"]["p50"] == result["all"]["latency_ms"]["p95"] == 0
    assert result["retry"]["cost_usd"]["total"] is None


@pytest.fixture
def baseline_pair(settings, cs_access, cs_run, evaluation_database):
    """합성 종료 실행을 복제해 다른 실험의 비교 후보를 트랜잭션 안에 준비한다.

    Args:
        settings: 합성 설정.
        cs_access: 승인 설정.
        cs_run: 기준 실행.
        evaluation_database: 격리 DB.

    Returns:
        tuple: 연결·기준/후보 실행·후보 실험. 종료 시 복제를 롤백한다.
    """
    assert asyncio.run(dispatch_one(settings, cs_access)) == cs_run
    with psycopg.connect(evaluation_database) as conn:
        try:
            source_experiment = conn.execute(
                "SELECT experiment_id FROM evaluation.runs WHERE id=%s", (cs_run,)
            ).fetchone()[0]
            experiment_id, candidate_id = uuid4(), uuid4()
            conn.execute(
                "INSERT INTO evaluation.experiments SELECT (jsonb_populate_record(NULL::evaluation.experiments,to_jsonb(e)||%s)).* "
                "FROM evaluation.experiments e WHERE id=%s",
                (
                    Jsonb(
                        {
                            "id": str(experiment_id),
                            "config_hash": uuid4().hex + uuid4().hex,
                            "model_id": "candidate-fake",
                        }
                    ),
                    source_experiment,
                ),
            )
            conn.execute(
                "INSERT INTO evaluation.experiment_targets SELECT %s,target_ref,target_kind,data_origin,attributes "
                "FROM evaluation.experiment_targets WHERE experiment_id=%s",
                (experiment_id, source_experiment),
            )
            conn.execute(
                "INSERT INTO evaluation.runs SELECT (jsonb_populate_record(NULL::evaluation.runs,to_jsonb(r)||%s)).* "
                "FROM evaluation.runs r WHERE id=%s",
                (
                    Jsonb(
                        {
                            "id": str(candidate_id),
                            "experiment_id": str(experiment_id),
                            "job_id": None,
                        }
                    ),
                    cs_run,
                ),
            )
            for (case_id,) in conn.execute(
                "SELECT id FROM evaluation.cases WHERE run_id=%s", (cs_run,)
            ).fetchall():
                new_case = uuid4()
                conn.execute(
                    "INSERT INTO evaluation.cases SELECT (jsonb_populate_record(NULL::evaluation.cases,to_jsonb(c)||%s)).* "
                    "FROM evaluation.cases c WHERE id=%s",
                    (
                        Jsonb(
                            {
                                "id": str(new_case),
                                "run_id": str(candidate_id),
                                "experiment_id": str(experiment_id),
                            }
                        ),
                        case_id,
                    ),
                )
                conn.execute(
                    "INSERT INTO evaluation.case_attempts SELECT (jsonb_populate_record(NULL::evaluation.case_attempts,to_jsonb(a)||%s)).* "
                    "FROM evaluation.case_attempts a WHERE case_id=%s",
                    (Jsonb({"id": str(uuid4()), "case_id": str(new_case)}), case_id),
                )
            yield conn, cs_run, candidate_id, experiment_id
        finally:
            conn.rollback()


@pytest.mark.parametrize("metric", ["category", "urgency", "secondary"])
def test_baseline_comparison_finds_regression_hidden_by_equal_score(baseline_pair, metric):
    """평균 점수가 같아도 사례 퇴행·새 위험 누락을 숨기지 않는다.

    Args:
        baseline_pair: 같은 자료의 합성 종료 실행 쌍.
        metric: 비교 지표.

    Returns:
        None: 동일 분모·최초 시도·사례 전이 및 위험 누락 대조를 확인한다.
    """
    from eval.cs_stored_metrics import compare_stored_runs

    conn, baseline, candidate, _ = baseline_pair
    good = {
        "category_code": "RETURN",
        "secondary_category": "DEFECT",
        "urgency_flag": "SAFETY",
        "provided_info": [],
        "return_subtype": None,
    }
    bad = {**good, "category_code": "INSTALL", "secondary_category": None, "urgency_flag": "NONE"}
    for run, ref, output in (
        (baseline, "cs-smoke-valid", good),
        (candidate, "cs-smoke-valid", bad),
        (candidate, "cs-smoke-invalid", good),
    ):
        conn.execute(
            "UPDATE evaluation.case_attempts SET outcome='VALID',parsed_output=%s WHERE case_id IN "
            "(SELECT id FROM evaluation.cases WHERE run_id=%s AND target_ref=%s)",
            (Jsonb(output), run, ref),
        )
    gold = dict.fromkeys(
        FIXTURES, {"category": "RETURN", "urgency": "SAFETY", "secondary": "DEFECT"}[metric]
    )
    result = compare_stored_runs(conn, baseline, candidate, gold, gold_version=None, metric=metric)
    assert result["delta"]["accuracy"] == 0
    base_scores = result["baseline"]["report"]["overall"]
    assert base_scores["accuracy"] == pytest.approx(1 / 3)
    if metric == "secondary":
        assert base_scores["composite"]["macro_f1"] == pytest.approx(0.5 / 10)
    else:
        assert base_scores["macro_f1"] == pytest.approx(0.5 / (6 if metric == "urgency" else 10))
    assert all(value == 0 for value in result["delta"].values())
    assert (
        result["baseline"]["report"]["overall"]["total"]
        == result["candidate"]["report"]["overall"]["total"]
        == 3
    )
    assert result["transitions"] == {
        "IMPROVED": 1,
        "REGRESSED": 1,
        "STILL_CORRECT": 0,
        "STILL_INCORRECT": 1,
    }
    cases = {case["case_id"]: case for case in result["cases"]}
    assert cases["cs-smoke-valid"]["transition"] == "REGRESSED"
    assert cases["cs-smoke-invalid"]["transition"] == "IMPROVED"
    if metric == "urgency":
        assert cases["cs-smoke-valid"]["new_critical_miss"] is True
        assert cases["cs-smoke-invalid"]["recovered_critical_miss"] is True
    assert result["settings"]["candidate"]["model_id"] == "candidate-fake"
    assert result["quality_gate_evaluated"] is False
    assert "raw_output" not in str(result)
    case_id = conn.execute(
        "SELECT id FROM evaluation.cases WHERE run_id=%s AND target_ref='cs-smoke-valid'",
        (candidate,),
    ).fetchone()[0]
    conn.execute("UPDATE evaluation.case_attempts SET is_final=false WHERE case_id=%s", (case_id,))
    conn.execute(
        "INSERT INTO evaluation.case_attempts SELECT (jsonb_populate_record(NULL::evaluation.case_attempts,to_jsonb(a)||%s)).* "
        "FROM evaluation.case_attempts a WHERE case_id=%s AND attempt=1",
        (
            Jsonb({"id": str(uuid4()), "attempt": 2, "is_final": True, "parsed_output": good}),
            case_id,
        ),
    )
    assert (
        compare_stored_runs(conn, baseline, candidate, gold, gold_version=None, metric=metric)
        == result
    )


@pytest.mark.parametrize(
    "mismatch",
    [
        "same_run",
        "missing_run",
        "active",
        "dataset",
        "split",
        "gold",
        "policy",
        "target_hash",
        "target_rows",
        "metadata",
    ],
)
def test_baseline_comparison_rejects_incompatible_runs(baseline_pair, mismatch):
    """평가 대상·판본·그룹 또는 실행 상태가 다르면 비교를 거부한다.

    Args:
        baseline_pair: 합성 실행 쌍.
        mismatch: 주입할 비교 불가 조건.

    Returns:
        None: 조건 불일치를 점수 변화로 해석하지 않음을 확인한다.
    """
    from eval.cs_stored_metrics import compare_stored_runs

    conn, baseline, candidate, experiment_id = baseline_pair
    fields = {
        "dataset": ("dataset_hash", "b" * 64),
        "split": ("data_split", "valid"),
        "gold": ("gold_version", "different"),
        "policy": ("policy_version", "different"),
        "target_hash": ("target_set_hash", "c" * 64),
    }
    if mismatch in fields:
        field, value = fields[mismatch]
        conn.execute(
            psycopg.sql.SQL("UPDATE evaluation.experiments SET {}=%s WHERE id=%s").format(
                psycopg.sql.Identifier(field)
            ),
            (value, experiment_id),
        )
    if mismatch == "active":
        conn.execute(
            "UPDATE evaluation.runs SET status='RUNNING',finished_at=NULL WHERE id=%s", (candidate,)
        )
    if mismatch == "target_rows":
        conn.execute(
            "DELETE FROM evaluation.case_attempts WHERE case_id IN (SELECT id FROM evaluation.cases WHERE run_id=%s AND target_ref='cs-smoke-timeout')",
            (candidate,),
        )
        conn.execute(
            "DELETE FROM evaluation.cases WHERE run_id=%s AND target_ref='cs-smoke-timeout'",
            (candidate,),
        )
        conn.execute(
            "DELETE FROM evaluation.experiment_targets WHERE experiment_id=%s AND target_ref='cs-smoke-timeout'",
            (experiment_id,),
        )
    if mismatch == "metadata":
        conn.execute(
            "UPDATE evaluation.experiment_targets SET attributes=%s WHERE experiment_id=%s",
            (Jsonb({"channel": "PHONE"}), experiment_id),
        )
    with pytest.raises(ValueError, match="^CS_BASELINE_INCOMPATIBLE$"):
        compare_stored_runs(
            conn,
            baseline,
            baseline
            if mismatch == "same_run"
            else uuid4()
            if mismatch == "missing_run"
            else candidate,
            dict.fromkeys(FIXTURES, "RETURN"),
            gold_version=None,
            metric="category",
        )


def test_baseline_comparison_keeps_unsupported_recall_null(baseline_pair):
    """비NONE 대상이 없으면 재현율 차이를 0이나 개선으로 만들지 않는다.

    Args:
        baseline_pair: 합성 실행 쌍.

    Returns:
        None: 빈 지원 집합의 null과 취소 후 미실행 분모를 확인한다.
    """
    from eval.cs_stored_metrics import compare_stored_runs

    conn, baseline, candidate, _ = baseline_pair
    conn.execute(
        "DELETE FROM evaluation.case_attempts WHERE case_id IN (SELECT id FROM evaluation.cases WHERE run_id=%s)",
        (candidate,),
    )
    conn.execute("UPDATE evaluation.runs SET status='CANCELLED' WHERE id=%s", (candidate,))
    result = compare_stored_runs(
        conn,
        baseline,
        candidate,
        dict.fromkeys(FIXTURES, "NONE"),
        gold_version=None,
        metric="urgency",
    )
    assert result["delta"]["non_none_recall"] is None
    assert result["delta"]["accuracy"] == pytest.approx(-1 / 3)
    assert result["candidate"]["report"]["overall"]["outcomes"]["NOT_STARTED"] == 3
