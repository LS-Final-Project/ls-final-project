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


RETURN_GOLD = {"cs-smoke-valid": "DEFECT", "cs-smoke-timeout": "NON_DEFECT"}


def test_stored_return_subtype_scores_locked_subset_in_report(
    settings, cs_access, cs_run, evaluation_database
):
    """반품 하위유형만 잠긴 반품 대상 부분집합을 채점하고 통합 보고서에 선택 연결한다.

    Args:
        settings: 합성 실행 설정.
        cs_access: 합성 승인 설정.
        cs_run: 접수 실행.
        evaluation_database: 격리 DB.

    Returns:
        None: 전체·채점 대상 수, 유효 null과 실패 분리, 기존 세 지표 불변을 확인한다.
    """
    from eval import cs_report
    from eval.cs_stored_metrics import compare_stored_attempts, summarize_stored_first_attempts

    assert asyncio.run(dispatch_one(settings, cs_access)) == cs_run
    with psycopg.connect(evaluation_database) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        first = summarize_stored_first_attempts(
            conn, cs_run, RETURN_GOLD, gold_version=None, metric="return_subtype"
        )
        compared = compare_stored_attempts(
            conn, cs_run, RETURN_GOLD, gold_version=None, metric="return_subtype"
        )
    assert first["target_count"] == 3 and first["scored_target_count"] == 2
    overall = first["report"]["overall"]
    assert overall["version"] == "cs-return-subtype-first-attempt-v1"
    assert overall["total"] == 2 and overall["correct"] == 0
    assert overall["null_predictions"] == 1
    assert overall["outcomes"]["VALID"] == overall["outcomes"]["TIMEOUT"] == 1
    assert sorted(case["case_id"] for case in overall["cases"]) == sorted(RETURN_GOLD)
    assert sum(compared["transitions"].values()) == 2
    assert compared["final"]["version"] == "cs-return-subtype-final-attempt-v1"
    with psycopg.connect(evaluation_database) as conn:
        base = cs_report.build_stored_report(conn, cs_run, report_gold(), gold_version=None)
        report = cs_report.build_stored_report(
            conn, cs_run, {**report_gold(), "return_subtype": RETURN_GOLD}, gold_version=None
        )
    assert "return_subtype" not in base["quality"]
    assert report["quality"]["return_subtype"]["first"]["report"] == first["report"]
    for metric in ("category", "urgency", "secondary"):
        assert (
            report["quality"][metric]["first"]["report"]
            == base["quality"][metric]["first"]["report"]
        )


@pytest.mark.parametrize(
    "metric,gold",
    [
        ("return_subtype", {}),
        ("return_subtype", {**RETURN_GOLD, "not-in-run": "DEFECT"}),
        ("return_subtype", {"cs-smoke-valid": None}),
        ("category", {"cs-smoke-valid": "RETURN"}),
    ],
)
def test_stored_subset_gold_is_return_subtype_only(
    settings, cs_access, cs_run, evaluation_database, metric, gold
):
    """빈·대상 밖·null 반품 정답과 다른 지표의 부분집합 정답을 거부한다.

    Args:
        settings: 합성 설정.
        cs_access: 승인 설정.
        cs_run: 접수 실행.
        evaluation_database: 격리 DB.
        metric: 검사할 지표.
        gold: 잘못된 합성 정답.

    Returns:
        None: 부분집합 허용이 반품 하위유형에 한정되고 다른 지표는 정확 일치를 유지한다.
    """
    from eval.cs_stored_metrics import summarize_stored_first_attempts

    assert asyncio.run(dispatch_one(settings, cs_access)) == cs_run
    with psycopg.connect(evaluation_database) as conn:
        with pytest.raises(ValueError, match="^CS_(STORED_METRICS|METRICS)_INVALID_INPUT$"):
            summarize_stored_first_attempts(conn, cs_run, gold, gold_version=None, metric=metric)


def return_gold_csv(gold=RETURN_GOLD):
    """합성 반품 하위유형 정답 파일 바이트와 SHA-256을 만든다.

    Args:
        gold: 대상 ID별 합성 하위유형.

    Returns:
        tuple[bytes, str]: 두 열 CSV 바이트와 소문자 hex hash.
    """
    import hashlib

    data = ("ticket_id,return_subtype\n" + "".join(f"{k},{v}\n" for k, v in gold.items())).encode()
    return data, hashlib.sha256(data).hexdigest()


def stored_quality_rows(dsn, run_id):
    """실행의 저장된 metrics 행을 모두 읽는다.

    Args:
        dsn: 격리 DB 연결 문자열.
        run_id: 대상 실행.

    Returns:
        list[dict]: 저장 행.
    """
    with psycopg.connect(dsn) as conn, conn.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(
            "SELECT * FROM evaluation.metrics WHERE run_id=%s ORDER BY metric_key,class_label",
            (run_id,),
        ).fetchall()


@pytest.fixture
def quality_rows_cleanup(cs_run, evaluation_database):
    """공용 실험 정리 전에 이 실행의 추가 전용 metrics 행을 검사 관리자로 지운다.

    Args:
        cs_run: 접수 실행.
        evaluation_database: 격리 DB.

    Returns:
        None: 검사 뒤 FK 정리를 보장한다.
    """
    yield
    with psycopg.connect(evaluation_database) as conn:
        conn.execute("DELETE FROM evaluation.metrics WHERE run_id=%s", (cs_run,))


def test_synthetic_quality_rows_store_aggregates_only(
    settings, cs_access, cs_run, evaluation_database, quality_rows_cleanup
):
    """종료된 합성 실행의 반품 하위유형 집계를 실행 단위 행으로만 저장한다.

    Args:
        settings: 합성 실행 설정.
        cs_access: 합성 승인 설정.
        cs_run: 접수 실행.
        evaluation_database: 격리 DB.
        quality_rows_cleanup: 검사 뒤 metrics 행 정리.

    Returns:
        None: 저장 수치가 같은 집계와 일치하고 사례·정답·예측·원시 출력이 없음을 확인한다.
    """
    from eval.cs_report import store_synthetic_return_subtype_metrics
    from eval.cs_stored_metrics import summarize_stored_first_attempts

    assert asyncio.run(dispatch_one(settings, cs_access)) == cs_run
    data, digest = return_gold_csv()
    with psycopg.connect(evaluation_database) as conn:
        stored = store_synthetic_return_subtype_metrics(
            conn, cs_run, data, gold_sha256=digest, gold_version=None, scoring_version="syn-v1"
        )
        expected = summarize_stored_first_attempts(
            conn, cs_run, RETURN_GOLD, gold_version=None, metric="return_subtype"
        )["report"]["overall"]
    assert stored == {"run_id": str(cs_run), "scoring_version": "syn-v1", "stored_rows": 5}
    rows = stored_quality_rows(evaluation_database, cs_run)
    assert [(r["metric_key"], r["class_label"]) for r in rows] == [
        ("return_subtype_first_accuracy", None),
        ("return_subtype_first_f1", "DEFECT"),
        ("return_subtype_first_f1", "NON_DEFECT"),
        ("return_subtype_first_macro_f1", None),
        ("return_subtype_first_null_predictions", None),
    ]
    by_key = {(r["metric_key"], r["class_label"]): r for r in rows}
    accuracy = by_key[("return_subtype_first_accuracy", None)]
    assert (accuracy["value"], accuracy["numerator"], accuracy["denominator"]) == (0, 0, 2)
    assert by_key[("return_subtype_first_null_predictions", None)]["value"] == 1
    assert by_key[("return_subtype_first_macro_f1", None)]["value"] == expected["macro_f1"]
    assert by_key[("return_subtype_first_f1", "DEFECT")]["details"] == {
        key: expected["per_class"]["DEFECT"][key]
        for key in ("tp", "fp", "fn", "precision", "recall")
    }
    assert accuracy["details"] == {
        "report_version": "cs-return-subtype-first-attempt-v1",
        "data_origin": "synthetic",
        "gold_version": None,
        "gold_sha256": digest,
        "run_status": "PARTIAL",
        "target_count": 3,
        "scored_target_count": 2,
        "outcomes": expected["outcomes"],
    }
    assert all(
        r["case_id"] is None and r["segment"] == "all" and r["threshold"] is None for r in rows
    )
    assert all(r["passed"] is None and r["comparison"] is None for r in rows)
    # 사례 ID·정답/예측 기록·원시 출력·본문이 어떤 행에도 남지 않는다.
    dumped = str(rows)
    assert all(ref not in dumped for ref in FIXTURES)
    assert all(
        word not in dumped
        for word in (
            "'cases'",
            "'prediction'",
            "'gold'",
            "raw_output",
            "parsed_output",
            "Synthetic",
        )
    )


@pytest.mark.parametrize(
    "invalid,code",
    [
        ("not_terminal", "CS_QUALITY_RUN_NOT_TERMINAL"),
        ("gold_version", "CS_QUALITY_GOLD_VERSION_MISMATCH"),
        ("hash", "CS_QUALITY_GOLD_HASH_MISMATCH"),
        ("test_split", "CS_QUALITY_FINAL_TEST_LOCKED"),
        ("synthetic", "CS_QUALITY_SYNTHETIC_ONLY"),
        ("subset", "CS_QUALITY_TARGET_MISMATCH"),
        ("duplicate", "CS_QUALITY_ALREADY_STORED"),
    ],
)
def test_synthetic_quality_rows_reject_without_partial_rows(
    settings, cs_access, cs_run, evaluation_database, quality_rows_cleanup, invalid, code
):
    """거부 조건마다 원문 없는 오류로 끝나고 새 행을 남기지 않는다.

    Args:
        settings: 합성 설정.
        cs_access: 승인 설정.
        cs_run: 접수 실행.
        evaluation_database: 격리 DB.
        quality_rows_cleanup: 검사 뒤 metrics 행 정리.
        invalid: 주입할 거부 조건.
        code: 기대 오류 코드.

    Returns:
        None: 실패한 저장이 기존 행을 바꾸거나 일부 행을 남기지 않음을 확인한다.
    """
    from eval.cs_report import store_synthetic_return_subtype_metrics

    if invalid != "not_terminal":
        assert asyncio.run(dispatch_one(settings, cs_access)) == cs_run
    data, digest = return_gold_csv(
        {**RETURN_GOLD, "not-in-run": "DEFECT"} if invalid == "subset" else RETURN_GOLD
    )
    field = {"test_split": ("data_split", "test"), "synthetic": ("prompt_version", "unapproved")}
    with psycopg.connect(evaluation_database) as conn:
        if invalid in field:
            conn.execute(
                psycopg.sql.SQL(
                    "UPDATE evaluation.experiments SET {}=%s WHERE id=(SELECT experiment_id FROM evaluation.runs WHERE id=%s)"
                ).format(psycopg.sql.Identifier(field[invalid][0])),
                (field[invalid][1], cs_run),
            )
            conn.commit()
        kwargs = {"gold_sha256": digest, "gold_version": None, "scoring_version": "syn-v1"}
        if invalid == "duplicate":
            store_synthetic_return_subtype_metrics(conn, cs_run, data, **kwargs)
        before = stored_quality_rows(evaluation_database, cs_run)
        if invalid == "gold_version":
            kwargs["gold_version"] = "other"
        if invalid == "hash":
            kwargs["gold_sha256"] = "0" * 64
        with pytest.raises(ValueError, match=f"^{code}$"):
            store_synthetic_return_subtype_metrics(conn, cs_run, data, **kwargs)
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    assert stored_quality_rows(evaluation_database, cs_run) == before
    assert len(before) == (5 if invalid == "duplicate" else 0)


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


def test_response_audit_separates_schema_retries_and_case_fallback(
    settings, cs_access, cs_run, evaluation_database
):
    """원문 없이 응답 분모·재시도·폴백 기록을 분리해 계산한다.

    Args:
        settings: 합성 실행 설정.
        cs_access: 합성 승인 설정.
        cs_run: 접수 실행.
        evaluation_database: 격리 DB.

    Returns:
        None: 최초 위반 보존·빈 응답 분모·사례 중복 제거와 내용 비노출을 확인한다.
    """
    from backend.api.platform.evaluations.cs_smoke import run_case
    from eval.cs_response_audit import summarize_stored_response_audit

    with psycopg.connect(evaluation_database) as conn:
        empty = summarize_stored_response_audit(conn, cs_run)
    assert empty["first"]["schema_violation_rate"] is None
    assert empty["targets_without_first_attempt"] == 3
    assert (
        empty["case_fallback"]["recorded_not_used"] == empty["case_fallback"]["pending_case"] == 3
    )
    assert asyncio.run(dispatch_one(settings, cs_access)) == cs_run
    with psycopg.connect(evaluation_database) as conn:
        original = summarize_stored_response_audit(conn, cs_run)
        cases = dict(
            conn.execute(
                "SELECT target_ref,id FROM evaluation.cases WHERE run_id=%s", (cs_run,)
            ).fetchall()
        )
        good = run_case("cs-smoke-valid").prediction.model_dump()
        conn.execute(
            "UPDATE evaluation.cases SET fallback_used=true,final_output=%s,status='VALID' WHERE id=%s",
            (Jsonb(good), cases["cs-smoke-invalid"]),
        )
        for ref, outcome, raw, parsed in (
            ("cs-smoke-invalid", "VALID", "private@example.invalid", good),
            ("cs-smoke-valid", "SCHEMA_VIOLATION", "", None),
        ):
            conn.execute(
                "INSERT INTO evaluation.case_attempts(id,case_id,step,attempt,outcome,started_at,finished_at,raw_output,parsed_output) "
                "VALUES (%s,%s,'classify',2,%s,now(),now(),%s,%s)",
                (uuid4(), cases[ref], outcome, raw, Jsonb(parsed) if parsed is not None else None),
            )
        conn.execute(
            "INSERT INTO evaluation.case_attempts(id,case_id,step,attempt,outcome,started_at,finished_at,raw_output) "
            "VALUES (%s,%s,'draft',1,'SCHEMA_VIOLATION',now(),now(),'private-draft')",
            (uuid4(), cases["cs-smoke-invalid"]),
        )
    with psycopg.connect(evaluation_database) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        report = summarize_stored_response_audit(conn, cs_run)
    assert original["first"] == report["first"]
    assert report["first"]["stored_attempt_count"] == 3
    assert report["first"]["stored_raw_response_count"] == 2
    assert report["first"]["schema_violation_count"] == 1
    assert report["first"]["schema_violation_rate"] == 0.5
    assert (
        report["retry"]["stored_attempt_count"] == report["retry"]["stored_raw_response_count"] == 2
    )
    assert report["retry"]["schema_violation_rate"] == 0.5
    assert report["all"]["stored_raw_response_count"] == 4
    assert (
        report["all"]["schema_violation_count"] == report["all"]["stored_parsed_output_count"] == 2
    )
    assert report["all"]["schema_violation_rate"] == 0.5
    assert report["all"]["incomplete_evidence_attempt_count"] == 0
    assert report["all"]["outcomes"]["TIMEOUT"] == 1
    assert report["case_fallback"] == {
        "scope": "case",
        "target_count": 3,
        "recorded_used": 1,
        "recorded_not_used": 2,
        "missing_case": 0,
        "pending_case": 0,
    }
    assert "private" not in str(report) and "provided_info" not in str(report)


@pytest.mark.parametrize(
    "issue", ["missing_raw", "unexpected_raw", "inconsistent_parsed", "unfinished"]
)
def test_response_audit_withholds_rate_when_evidence_is_incomplete(
    settings, cs_access, cs_run, evaluation_database, issue
):
    """불완전한 증거를 0 위반이나 확정된 비율로 표시하지 않는다.

    Args:
        settings: 합성 실행 설정.
        cs_access: 승인 설정.
        cs_run: 합성 실행.
        evaluation_database: 격리 DB.
        issue: 주입할 누락 또는 모순.

    Returns:
        None: 위반 건수는 보존하고 비율만 보류함을 확인한다.
    """
    from eval.cs_response_audit import summarize_stored_response_audit

    assert asyncio.run(dispatch_one(settings, cs_access)) == cs_run
    with psycopg.connect(evaluation_database) as conn:
        field, value, ref = {
            "missing_raw": ("raw_output", None, "cs-smoke-invalid"),
            "unexpected_raw": ("raw_output", "private-timeout", "cs-smoke-timeout"),
            "inconsistent_parsed": ("parsed_output", None, "cs-smoke-valid"),
            "unfinished": ("finished_at", None, "cs-smoke-valid"),
        }[issue]
        conn.execute(
            psycopg.sql.SQL(
                "UPDATE evaluation.case_attempts SET {}=%s WHERE case_id IN (SELECT id FROM evaluation.cases WHERE run_id=%s AND target_ref=%s)"
            ).format(psycopg.sql.Identifier(field)),
            (value, cs_run, ref),
        )
        report = summarize_stored_response_audit(conn, cs_run)
    assert report["first"]["schema_violation_rate"] is None
    assert report["first"]["schema_violation_count"] == 1
    assert report["first"]["incomplete_evidence_attempt_count"] == 1
    assert report["first"]["evidence_issues"][issue] == 1
    assert "private-timeout" not in str(report)


def test_response_audit_preserves_missing_case_and_empty_response_denominator(
    settings, cs_access, cs_run, evaluation_database
):
    """응답 없는 시도와 사례 행 누락도 고정 대상에서 제거하지 않는다.

    Args:
        settings: 합성 실행 설정.
        cs_access: 승인 설정.
        cs_run: 합성 실행.
        evaluation_database: 격리 DB.

    Returns:
        None: 응답0의 null과 폴백 기록 미확인을 확인한다.
    """
    from eval.cs_response_audit import summarize_stored_response_audit

    assert asyncio.run(dispatch_one(settings, cs_access)) == cs_run
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            "DELETE FROM evaluation.case_attempts WHERE case_id IN (SELECT id FROM evaluation.cases WHERE run_id=%s AND target_ref!='cs-smoke-timeout')",
            (cs_run,),
        )
        conn.execute(
            "DELETE FROM evaluation.cases WHERE run_id=%s AND target_ref='cs-smoke-valid'",
            (cs_run,),
        )
        report = summarize_stored_response_audit(conn, cs_run)
        with pytest.raises(ValueError, match="CS_RESPONSE_AUDIT_INVALID_INPUT"):
            summarize_stored_response_audit(conn, uuid4())
        conn.execute("UPDATE evaluation.runs SET requested_count=2 WHERE id=%s", (cs_run,))
        with pytest.raises(ValueError, match="CS_RESPONSE_AUDIT_INVALID_INPUT"):
            summarize_stored_response_audit(conn, cs_run)
    assert report["target_count"] == 3 and report["targets_without_first_attempt"] == 2
    assert report["case_fallback"]["missing_case"] == 1
    assert report["case_fallback"]["recorded_not_used"] == 2
    assert (
        report["first"]["stored_raw_response_count"]
        == report["first"]["schema_violation_count"]
        == 0
    )
    assert report["first"]["schema_violation_rate"] is None


def report_gold():
    """실제 정답을 사용하지 않는 통합 보고서용 고정 기대값을 만든다.

    Args:
        없음.

    Returns:
        dict: 지표별 합성 기대값.
    """
    return {
        "category": dict.fromkeys(FIXTURES, "RETURN"),
        "urgency": dict.fromkeys(FIXTURES, "NONE"),
        "secondary": dict.fromkeys(FIXTURES, None),
    }


def test_combined_report_keeps_one_snapshot_during_concurrent_commit(
    settings, cs_access, cs_run, evaluation_database, monkeypatch
):
    """다른 연결의 commit이 집계 사이에 발생해도 한 보고서의 시점은 섞이지 않는다.

    Args:
        settings: 합성 실행 설정.
        cs_access: 승인 설정.
        cs_run: 실행 ID.
        evaluation_database: 격리 DB.
        monkeypatch: 첫 집계 뒤 실제 별도 연결 변경을 삽입한다.

    Returns:
        None: 반복 가능한 읽기와 다음 조회의 최신 반영을 확인한다.
    """
    from eval import cs_report

    assert asyncio.run(dispatch_one(settings, cs_access)) == cs_run
    original = cs_report.compare_stored_attempts
    changed = False

    def concurrent_update(conn, *args, **kwargs):
        """첫 지표 조회 후 다른 연결에서 저장값을 변경한다.

        Args:
            conn: 보고서의 읽기 연결.
            args: 기존 집계 인자.
            kwargs: 기존 집계 옵션.

        Returns:
            dict: 실제 집계 결과.
        """
        nonlocal changed
        assert conn.execute("SHOW transaction_isolation").fetchone()[0] == "repeatable read"
        assert conn.execute("SHOW transaction_read_only").fetchone()[0] == "on"
        result = original(conn, *args, **kwargs)
        if not changed:
            with psycopg.connect(evaluation_database) as writer:
                writer.execute("UPDATE evaluation.runs SET status='FAILED' WHERE id=%s", (cs_run,))
                writer.execute(
                    "UPDATE evaluation.case_attempts SET input_tokens=99 WHERE case_id IN (SELECT id FROM evaluation.cases WHERE run_id=%s AND target_ref='cs-smoke-valid')",
                    (cs_run,),
                )
                writer.execute(
                    "UPDATE evaluation.case_attempts SET raw_output=NULL WHERE case_id IN (SELECT id FROM evaluation.cases WHERE run_id=%s AND target_ref='cs-smoke-invalid')",
                    (cs_run,),
                )
            changed = True
        return result

    monkeypatch.setattr(cs_report, "compare_stored_attempts", concurrent_update)
    with psycopg.connect(evaluation_database) as conn:
        report = cs_report.build_stored_report(conn, cs_run, report_gold(), gold_version=None)
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        assert (
            report["run_status"]
            == report["usage"]["run_status"]
            == report["response_audit"]["run_status"]
            == "PARTIAL"
        )
        assert all(
            value["first"]["run_status"] == "PARTIAL" for value in report["quality"].values()
        )
        assert report["usage"]["all"]["tokens"]["input_tokens"]["total"] == 21
        assert report["response_audit"]["all"]["schema_violation_rate"] == 0.5
        latest = cs_report.build_stored_report(conn, cs_run, report_gold(), gold_version=None)
    assert latest["run_status"] == "FAILED"
    assert latest["usage"]["all"]["tokens"]["input_tokens"]["total"] == 113
    assert latest["response_audit"]["all"]["schema_violation_rate"] is None
    assert set(report["quality"]) == {"category", "urgency", "secondary"}
    assert report["target_count"] == 3 and report["snapshot_isolation"] == "repeatable_read"
    assert "configuration" not in report["provenance"]


def test_combined_report_rejects_active_transaction_and_rolls_back_failure(
    cs_run, evaluation_database, monkeypatch
):
    """호출자의 변경은 건드리지 않고 보고서 실패의 트랜잭션만 정리한다.

    Args:
        cs_run: 접수 실행.
        evaluation_database: 격리 DB.
        monkeypatch: 사용량 단계에 금지된 쓰기를 주입한다.

    Returns:
        None: 입력 실패·읽기 전용 강제·연결 재사용 경계를 확인한다.
    """
    from eval import cs_report

    with psycopg.connect(evaluation_database) as conn:
        conn.execute("UPDATE evaluation.runs SET retry_count=9 WHERE id=%s", (cs_run,))
        with pytest.raises(ValueError, match="CS_REPORT_INVALID_INPUT"):
            cs_report.build_stored_report(conn, cs_run, report_gold(), gold_version=None)
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.INTRANS
        assert (
            conn.execute(
                "SELECT retry_count FROM evaluation.runs WHERE id=%s", (cs_run,)
            ).fetchone()[0]
            == 9
        )
        conn.rollback()
        bad_gold = report_gold()
        bad_gold["urgency"].pop("cs-smoke-valid")
        with pytest.raises(ValueError):
            cs_report.build_stored_report(conn, cs_run, bad_gold, gold_version=None)
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        with pytest.raises(ValueError, match="CS_REPORT_INVALID_INPUT"):
            cs_report.build_stored_report(conn, uuid4(), report_gold(), gold_version=None)

        def forbidden_write(connection, run_id):
            """보고서 안의 쓰기가 DB에서 실제로 거부되는지 검사한다.

            Args:
                connection: 읽기 전용 보고서 연결.
                run_id: 실행 ID.

            Returns:
                dict: 반환 전에 DB가 쓰기를 거부해야 한다.
            """
            connection.execute("UPDATE evaluation.runs SET retry_count=99 WHERE id=%s", (run_id,))
            return {}

        with monkeypatch.context() as patch:
            patch.setattr(cs_report, "summarize_stored_usage", forbidden_write)
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                cs_report.build_stored_report(conn, cs_run, report_gold(), gold_version=None)
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        report = cs_report.build_stored_report(conn, cs_run, report_gold(), gold_version=None)
        assert report["run_status"] == "QUEUED"
        assert (
            conn.execute(
                "SELECT retry_count FROM evaluation.runs WHERE id=%s", (cs_run,)
            ).fetchone()[0]
            == 0
        )


def test_synthetic_report_cli_exports_private_json_without_overwrite(
    settings, cs_access, cs_run, evaluation_database, monkeypatch, tmp_path, capsys
):
    """합성 CLI가 사용 가능한 JSON을 비공개 새 파일로만 내보낸다.

    Args:
        settings: 합성 실행 설정.
        cs_access: 승인 설정.
        cs_run: 실행 ID.
        evaluation_database: 격리 DB.
        monkeypatch: 합성 환경 변수 설정.
        tmp_path: 검사 출력 위치.
        capsys: 안전한 출력/오류 확인.

    Returns:
        None: JSON·권한·원문 비노출·덮어쓰기 방지를 확인한다.
    """
    import json
    import stat

    from eval.cs_report import main

    assert asyncio.run(dispatch_one(settings, cs_access)) == cs_run
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("EVALUATION_REPORT_DATABASE_URL", evaluation_database)
    output = tmp_path / "report.json"
    args = ["--run-id", str(cs_run), "--output", str(output)]
    assert main(args) == 0
    saved_text = output.read_text()
    report = json.loads(saved_text)
    assert report["synthetic_gold_fixture"] == "cs-report-demo-gold-v1"
    assert report["quality"]["category"]["first"]["report"]["overall"]["correct"] == 1
    assert report["response_audit"]["first"]["schema_violation_rate"] == 0.5
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert "@example.test" not in saved_text and "provided_info" not in saved_text
    assert main(args) == 2 and output.read_text() == saved_text
    captured = capsys.readouterr()
    assert captured.out == "CS_REPORT_EXPORTED\n"
    assert captured.err == "CS_REPORT_EXPORT_FAILED\n"


@pytest.mark.parametrize("blocked", ["production", "real", "test_split", "profile"])
def test_synthetic_report_cli_keeps_execution_boundary(
    cs_run, evaluation_database, monkeypatch, tmp_path, capsys, blocked
):
    """CLI가 운영·실자료·시험·다른 합성 프로필을 내보내지 않는다.

    Args:
        cs_run: 실행 ID.
        evaluation_database: 격리 DB.
        monkeypatch: CLI 환경 설정.
        tmp_path: 금지 출력 위치.
        capsys: 오류 원문 비노출 검사.
        blocked: 허용하지 않을 조건.

    Returns:
        None: 금지 조건에서 파일이 생성되지 않음을 확인한다.
    """
    from eval.cs_report import main

    monkeypatch.setenv("ENVIRONMENT", "production" if blocked == "production" else "test")
    monkeypatch.setenv("EVALUATION_REPORT_DATABASE_URL", evaluation_database)
    if blocked != "production":
        field, value = {
            "real": ("data_origin", "real"),
            "test_split": ("data_split", "test"),
            "profile": ("prompt_version", "unapproved"),
        }[blocked]
        with psycopg.connect(evaluation_database) as conn:
            conn.execute(
                psycopg.sql.SQL(
                    "UPDATE evaluation.experiments SET {}=%s WHERE id=(SELECT experiment_id FROM evaluation.runs WHERE id=%s)"
                ).format(psycopg.sql.Identifier(field)),
                (value, cs_run),
            )
    output = tmp_path / "denied.json"
    assert main(["--run-id", str(cs_run), "--output", str(output)]) == 2
    assert not output.exists()
    assert evaluation_database not in capsys.readouterr().err


@pytest.fixture
def summary_client(settings, cs_access, cs_experiment, cs_run, registered_user):
    """현재 DB 세션으로 실제 합성 요약 API에 연결한다.

    Args:
        settings: 플랫폼 실행 권한의 DB 설정.
        cs_access: 지정 평가자와 구성 승인.
        cs_experiment: 고정 합성 실험.
        cs_run: 접수 실행.
        registered_user: 세션을 발급할 합성 사용자.

    Returns:
        tuple: 실제 HTTP 클라이언트와 요약 URL.
    """
    from backend.api.platform.auth.http import SESSION_COOKIE
    from backend.api.platform.auth.sessions import issue_session
    from tests.platform.test_evaluation_access import client_for

    grant = asyncio.run(issue_session(settings, registered_user[0]))
    client = client_for((cs_experiment["actor"],))
    client.app.state.settings = settings
    client.app.state.evaluation_access = cs_access
    client.cookies.set(SESSION_COOKIE, grant.token.get_secret_value())
    with client:
        yield client, f"/api/v1/platform/evaluations/runs/{cs_run}/summary"


def test_summary_http_preserves_nulls_and_excludes_content(summary_client, settings, cs_access):
    """실행 전후의 저장 집계를 허용된 필드만으로 반환한다.

    Args:
        summary_client: 실제 세션 HTTP.
        settings: 합성 worker 설정.
        cs_access: 현재 승인.

    Returns:
        None: 빈 값·부분 보고·민감 필드 비노출·캐시 금지를 검증한다.
    """
    client, url = summary_client
    queued = client.get(url)
    assert queued.status_code == 200, queued.text
    assert queued.json()["data"]["targets_without_first_attempt"] == 3
    assert queued.json()["data"]["first"]["tokens"]["input_tokens"]["total"] is None
    assert queued.json()["data"]["first"]["schema_violation_rate"] is None
    asyncio.run(dispatch_one(settings, cs_access))
    response = client.get(url)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert set(data) == {
        "report_version",
        "run_id",
        "status",
        "generated_at",
        "data_origin",
        "quality_available",
        "quality_unavailable_reason",
        "target_count",
        "targets_without_first_attempt",
        "first",
        "retry",
        "all",
    }
    assert data["status"] == "PARTIAL" and data["data_origin"] == "synthetic"
    assert data["quality_available"] is False
    assert data["first"]["stored_attempt_count"] == 3
    assert data["first"]["tokens"]["input_tokens"]["total"] == 21
    assert data["first"]["tokens"]["output_tokens"] == {
        "reported_count": 2,
        "missing_count": 1,
        "known_sum": 6,
        "total": None,
    }
    assert data["first"]["stored_raw_response_count"] == 2
    assert data["first"]["schema_violation_rate"] == 0.5
    assert data["retry"]["latency_ms"]["p95"] is None
    assert response.headers["cache-control"] == "no-store"
    for secret in (
        "target_ref",
        "raw_output",
        "parsed_output",
        "gold",
        "configuration",
        "@",
        *FIXTURES,
    ):
        assert secret not in response.text


@pytest.mark.parametrize("revocation", ["session", "account", "role", "evaluator", "approval"])
def test_summary_http_checks_revocation(
    summary_client, cs_access, evaluation_database, registered_user, revocation
):
    """다음 조회에서 세션·업무 접근·평가자·구성 철회를 적용한다.

    Args:
        summary_client: 실제 세션 클라이언트.
        cs_access: 현재 평가 설정.
        evaluation_database: 전용 관리자 연결.
        registered_user: 영향을 한정할 사용자.
        revocation: 철회 종류.

    Returns:
        None: 상세 없는401/403 및 비캐시를 검증한다.
    """
    client, url = summary_client
    assert client.get(url).status_code == 200
    actor = registered_user[1]
    with psycopg.connect(evaluation_database) as conn:
        if revocation == "session":
            conn.execute("DELETE FROM identity.sessions WHERE user_id=%s", (actor,))
        elif revocation == "account":
            conn.execute("UPDATE identity.users SET is_active=false WHERE id=%s", (actor,))
        elif revocation == "role":
            conn.execute("DELETE FROM identity.user_roles WHERE user_id=%s", (actor,))
        elif revocation == "evaluator":
            cs_access.evaluator_user_ids = frozenset()
        else:
            cs_access.approved_config_hashes = frozenset()
    result = client.get(url)
    assert result.status_code == (403 if revocation in {"evaluator", "approval"} else 401)
    assert "data" not in result.json()
    assert result.headers["cache-control"] == "no-store"
    if revocation == "approval":
        assert client.get(url.removesuffix("/summary")).status_code == 200


@pytest.mark.parametrize(
    "boundary",
    [
        "requester",
        "creator",
        "test",
        "missing",
        "production",
        "disabled",
        "real",
        "profile",
        "target",
    ],
)
def test_summary_http_scope_boundary(
    summary_client, settings, cs_access, cs_experiment, cs_run, evaluation_database, boundary
):
    """소유권·시험·환경·프로필 제한을 집계 전에 적용한다.

    Args:
        summary_client: 실제 세션 HTTP.
        settings: 환경 설정.
        cs_access: 승인 설정.
        cs_experiment: 실험 메타데이터.
        cs_run: 실행 ID.
        evaluation_database: 전용 검사 DB.
        boundary: 거부할 경계.

    Returns:
        None: 제한된 오류만 반환하고 민감 내용을 숨긴다.
    """
    client, url = summary_client
    expected = 404
    with psycopg.connect(evaluation_database) as conn:
        if boundary == "requester":
            conn.execute(
                "UPDATE evaluation.runs SET requested_by_user_id=NULL WHERE id=%s", (cs_run,)
            )
        elif boundary == "creator":
            conn.execute(
                "UPDATE evaluation.experiments SET created_by_user_id=NULL WHERE id=%s",
                (cs_experiment["id"],),
            )
        elif boundary == "test":
            conn.execute(
                "UPDATE evaluation.experiments SET data_split='test' WHERE id=%s",
                (cs_experiment["id"],),
            )
        elif boundary == "missing":
            url = url.replace(str(cs_run), str(uuid4()))
        elif boundary == "production":
            settings.environment = "production"
            expected = 503
        elif boundary == "disabled":
            cs_access.local_admission_enabled = False
            expected = 503
        elif boundary in {"real", "profile"}:
            column, value = (
                ("data_origin", "real")
                if boundary == "real"
                else ("configuration", Jsonb({"task": "unapproved"}))
            )
            conn.execute(
                psycopg.sql.SQL("UPDATE evaluation.experiments SET {}=%s WHERE id=%s").format(
                    psycopg.sql.Identifier(column)
                ),
                (value, cs_experiment["id"]),
            )
            expected = 403
        elif boundary == "target":
            conn.execute(
                "UPDATE evaluation.experiment_targets SET data_origin='real' WHERE experiment_id=%s AND target_ref='cs-smoke-valid'",
                (cs_experiment["id"],),
            )
            expected = 409
    response = client.get(url)
    assert response.status_code == expected, response.text
    assert "data" not in response.json()
    assert response.headers["cache-control"] == "no-store"


def test_summary_snapshot_and_safe_failure(
    summary_client, cs_run, settings, cs_access, evaluation_database, monkeypatch
):
    """집계 사이 실제 commit에도 일관성을 유지하고 오류를 성공으로 숨기지 않는다.

    Args:
        summary_client: 실제 세션 HTTP.
        cs_run: 실행 ID.
        settings: 합성 worker 환경.
        cs_access: 승인 설정.
        evaluation_database: 별도 writer 연결.
        monkeypatch: 집계 경계에 동시 갱신과 실패 주입.

    Returns:
        None: 동일 시점 집계와 원문 없는503을 확인한다.
    """
    from backend.api.platform.evaluations import summary

    client, url = summary_client
    asyncio.run(dispatch_one(settings, cs_access))
    original = summary.summarize_stored_usage

    def update_after_usage(conn, run_id):
        """사용량 조회 후 원시 증거를 별도 연결에서 제거한다.

        Args:
            conn: 읽기 전용 스냅샷.
            run_id: 조회 실행.

        Returns:
            dict: 변경 전 사용량.
        """
        assert conn.execute("SHOW transaction_read_only").fetchone()[0] == "on"
        result = original(conn, run_id)
        with psycopg.connect(evaluation_database) as writer:
            writer.execute(
                "UPDATE evaluation.case_attempts SET raw_output=NULL WHERE case_id IN (SELECT id FROM evaluation.cases WHERE run_id=%s)",
                (run_id,),
            )
        return result

    with monkeypatch.context() as patch:
        patch.setattr(summary, "summarize_stored_usage", update_after_usage)
        assert client.get(url).json()["data"]["first"]["schema_violation_rate"] == 0.5
    assert client.get(url).json()["data"]["first"]["schema_violation_rate"] is None

    def unavailable(*args):
        """집계 저장 오류를 안전한 응답으로 변환하도록 유도한다.

        Args:
            args: 집계 인자.

        Returns:
            None: 성공 대신 원문을 가진 오류를 발생시킨다.
        """
        raise psycopg.OperationalError("sensitive database diagnostic")

    monkeypatch.setattr(summary, "summarize_stored_usage", unavailable)
    failure = client.get(url)
    assert failure.status_code == 503 and "sensitive" not in failure.text
    assert failure.headers["cache-control"] == "no-store"


def test_summary_cost_preserves_decimal_partial_retry_and_price_count(
    summary_client, cs_run, settings, cs_access, evaluation_database
):
    """저장 비용의 정밀도·미보고·실제0·가격표 혼합을 HTTP까지 보존한다.

    Args:
        summary_client: 실제 세션 HTTP.
        cs_run: 고정 합성 실행.
        settings: 합성 worker 설정.
        cs_access: 현재 승인.
        evaluation_database: 검사 전용 관리자 연결.

    Returns:
        None: 가격표 원문 없는 비용 집계와 최초/재시도 분리를 검증한다.
    """
    client, url = summary_client
    empty = client.get(url).json()["data"]["first"]["cost_usd"]
    assert empty == {
        "reported_count": 0,
        "missing_count": 0,
        "known_sum": None,
        "total": None,
        "pricing_version_count": 0,
    }
    asyncio.run(dispatch_one(settings, cs_access))
    assert client.get(url).json()["data"]["first"]["cost_usd"]["missing_count"] == 3
    with psycopg.connect(evaluation_database) as conn:
        cases = dict(
            conn.execute(
                "SELECT target_ref,id FROM evaluation.cases WHERE run_id=%s", (cs_run,)
            ).fetchall()
        )
        for ref, cost, price in (
            ("cs-smoke-valid", "0.000000", "private-price-a@example.test"),
            ("cs-smoke-invalid", "0.000001", "private-price-b@example.test"),
        ):
            conn.execute(
                "UPDATE evaluation.case_attempts SET cost_usd=%s,pricing_version=%s WHERE case_id=%s",
                (cost, price, cases[ref]),
            )
        conn.execute(
            "INSERT INTO evaluation.case_attempts(id,case_id,step,attempt,outcome,started_at,finished_at,cost_usd,pricing_version) "
            "VALUES (%s,%s,'classify',2,'TIMEOUT',now(),now(),0.000002,'private-price-b@example.test')",
            (uuid4(), cases["cs-smoke-invalid"]),
        )
    response = client.get(url)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["first"]["cost_usd"] == {
        "reported_count": 2,
        "missing_count": 1,
        "known_sum": "0.000001",
        "total": None,
        "pricing_version_count": 2,
    }
    assert data["retry"]["cost_usd"]["total"] == "0.000002"
    assert data["all"]["cost_usd"]["known_sum"] == "0.000003"
    assert data["all"]["cost_usd"]["total"] is None
    assert data["all"]["cost_usd"]["pricing_version_count"] == 2
    assert "private-price" not in response.text and "pricing_versions" not in response.text
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            "UPDATE evaluation.case_attempts SET cost_usd=0,pricing_version='zero-price' WHERE case_id IN (SELECT id FROM evaluation.cases WHERE run_id=%s)",
            (cs_run,),
        )
    zero = client.get(url).json()["data"]
    assert zero["first"]["cost_usd"]["total"] == "0.000000"
    assert zero["all"]["cost_usd"]["reported_count"] == 4
    assert zero["all"]["cost_usd"]["pricing_version_count"] == 1


@pytest.mark.parametrize("invalid", ["NaN", "missing_price"])
def test_summary_invalid_cost_stays_safe_failure(
    summary_client, cs_run, settings, cs_access, evaluation_database, invalid
):
    """유효하지 않은 금액·가격표를0이나 정상 집계로 숨기지 않는다.

    Args:
        summary_client: 실제 세션 HTTP.
        cs_run: 합성 실행.
        settings: worker 설정.
        cs_access: 승인 목록.
        evaluation_database: 오류 주입용 전용 관리자 연결.
        invalid: 잘못된 금액 또는 비어 있는 가격표.

    Returns:
        None:503·비캐시 및 내부 진단 비노출 확인.
    """
    client, url = summary_client
    asyncio.run(dispatch_one(settings, cs_access))
    with psycopg.connect(evaluation_database) as conn:
        conn.execute(
            "UPDATE evaluation.case_attempts SET cost_usd=%s,pricing_version=%s WHERE case_id IN (SELECT id FROM evaluation.cases WHERE run_id=%s)",
            (
                "NaN" if invalid == "NaN" else "0",
                "secret-price" if invalid == "NaN" else " ",
                cs_run,
            ),
        )
    response = client.get(url)
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "data" not in response.json() and "secret-price" not in response.text
