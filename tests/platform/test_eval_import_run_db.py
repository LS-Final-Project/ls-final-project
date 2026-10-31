"""KAN-144 실측 결과 반입 스크립트의 DB 왕복. 실제 등록 함수·평가 스키마·잠금 집계 저장을 합성 자료로 검사한다."""

import contextlib
import hashlib
import io
import json
from pathlib import Path

# 백엔드 검사 이미지는 scripts/check_backend.py만 복사하므로 거기서는 건너뛰고, 전체 checkout인 auth-database
# 작업에서 실행한다.
from uuid import UUID

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from backend.api.platform.evaluations import summary as summary_module
from backend.api.platform.evaluations.access import EvaluationAccessSettings
from backend.api.platform.evaluations.summary import get_run_summary
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings

eval_import_run = pytest.importorskip("scripts.eval_import_run")
GoldLock = pytest.importorskip("eval.cs_gold_locks").GoldLock

PREDICTION = {
    "category_code": "WARRANTY",
    "secondary_category": None,
    "urgency_flag": "NONE",
    "provided_info": [],
    "return_subtype": None,
}
IDS = ("KAN144-IMPORT-1", "KAN144-IMPORT-2", "KAN144-IMPORT-3")


def write_inputs(root: Path) -> tuple[list[str], dict]:
    """합성 manifest·결과·라벨·정답 파일을 쓴다.

    Args:
        root: 쓸 디렉터리.

    Returns:
        tuple: (main 인자, 잠금표).
    """
    rows = "".join(f"{i},valid,EMAIL,en,Warranty,synthetic body {i}\n" for i in IDS)
    tickets = (
        "ticket_id,data_split,channel,body_language,inquiry_subject,inquiry_body\n" + rows
    ).encode()
    (root / "t.csv").write_bytes(tickets)
    (root / "m.json").write_text(
        json.dumps(
            {
                "tickets": {
                    "path": "t.csv",
                    "sha256": hashlib.sha256(tickets).hexdigest(),
                    "splits": ["valid"],
                    "rows": 3,
                },
                "provenance": {"data_origin": "synthetic"},
            }
        )
    )
    labels = json.dumps({IDS[0]: PREDICTION, IDS[1]: PREDICTION, IDS[2]: None}).encode()
    (root / "labels.json").write_bytes(labels)
    (root / "run.json").write_text(
        json.dumps(
            {
                "meta": {
                    "candidates": [{"name": "a", "model": "gemini-x", "prompt_version": "v2"}],
                    "targets": 3,
                    "target_ids_sha256": hashlib.sha256("\n".join(IDS).encode()).hexdigest(),
                    "started_at": "2026-10-29T01:00:00+00:00",
                    "finished_at": "2026-10-29T01:05:00+00:00",
                    "git_sha": "abc1234",
                    "label_files": {"a": hashlib.sha256(labels).hexdigest()},
                },
                "result": {
                    "candidates": [
                        {"name": "a", "outcomes": {"VALID": 2, "SCHEMA_VIOLATION": 1, "HELD": 0}}
                    ]
                },
            }
        )
    )
    golds = {
        "category": (
            "category_code",
            f"ticket_id,category_code\n{IDS[0]},WARRANTY\n{IDS[1]},RETURN\n{IDS[2]},WARRANTY\n",
        ),
        "urgency": ("urgency_flag", "".join(f"{i},NONE\n" for i in IDS)),
    }
    golds["urgency"] = ("urgency_flag", "ticket_id,urgency_flag\n" + golds["urgency"][1])
    locks = {}
    for metric, (column, text) in golds.items():
        (root / f"{metric}.csv").write_text(text)
        locks[metric] = GoldLock(
            f"test-{metric}",
            hashlib.sha256(text.encode()).hexdigest(),
            ("ticket_id", column),
            (("ALL", 0, 3),),
        )
    argv = [
        "--run", str(root / "run.json"),
        "--labels", str(root / "labels.json"),
        "--candidate", "a",
        "--manifest", str(root / "m.json"),
        "--data-split", "valid",
        "--gold-category", str(root / "category.csv"),
        "--gold-urgency", str(root / "urgency.csv"),
        "--scoring-version", "kan144-import-test",
    ]  # fmt: skip
    return argv, locks


def run(argv, locks):
    """main을 부르고 종료 코드와 요약을 돌려준다.

    Args:
        argv: 인자.
        locks: 잠금표.

    Returns:
        tuple[int, dict | str]: 종료 코드와 요약(JSON이 아니면 문자열).
    """
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        code = eval_import_run.main(argv, locks=locks)
    text = printed.getvalue()
    return code, json.loads(text) if code == 0 else text


def test_import_round_trip_registers_scores_and_stops_on_repeat(
    evaluation_database, registered_user, tmp_path, monkeypatch
):
    """등록·실행·사례·시도·잠금 집계를 만들고, 다시 실행하면 EXISTS로 멈춘다.

    Args:
        evaluation_database: 평가 스키마를 적용한 관리자 DSN.
        registered_user: 지정 평가자로 쓸 합성 사용자.
        tmp_path: 입력 파일 디렉터리.
        monkeypatch: 환경변수 주입.

    Returns:
        None: 행 내용·정답 비저장·반복 실행을 확인한다.
    """
    _, actor = registered_user
    dsn = make_conninfo(evaluation_database, user="platform_app", password="test-only")
    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("EVALUATION_EVALUATOR_USER_IDS", json.dumps([str(actor)]))
    monkeypatch.setenv("EVALUATION_LOCAL_ADMISSION_ENABLED", "true")
    argv, locks = write_inputs(tmp_path)
    dry_code, dry = run(argv, locks)
    assert dry_code == 0
    try:
        code, summary = run([*argv, "--execute", "--actor", str(actor)], locks)
        assert code == 0, summary
        assert (summary["status"], summary["admissible"]) == ("IMPORTED", False)
        assert summary["blocked_reason"] == "EXPERIMENT_NOT_APPROVED"
        assert summary["config_hash"] == dry["config_hash"]
        with psycopg.connect(evaluation_database) as conn:
            experiment = conn.execute(
                "SELECT config_hash,purpose,stage,model_id,data_split,data_origin "
                "FROM evaluation.experiments WHERE id=%s",
                (summary["experiment_id"],),
            ).fetchone()
            assert experiment == (
                dry["config_hash"], "pipeline", "classification", "gemini-x", "valid", "synthetic"
            )  # fmt: skip
            assert conn.execute(
                "SELECT status,provider,runner_ref,requested_count,valid_output_count,failed_count "
                "FROM evaluation.runs WHERE id=%s",
                (summary["run_id"],),
            ).fetchone() == ("PARTIAL", "gemini", "eval_import_run", 3, 2, 1)
            attempts = conn.execute(
                "SELECT c.target_ref,c.status,c.final_output,c.gold_labels,a.outcome,a.parsed_output,"
                "a.raw_output,a.input_tokens,a.model_id FROM evaluation.cases c "
                "JOIN evaluation.case_attempts a ON a.case_id=c.id "
                "WHERE c.run_id=%s ORDER BY c.target_ref",
                (summary["run_id"],),
            ).fetchall()
            assert attempts == [
                (IDS[0], "VALID", PREDICTION, None, "VALID", PREDICTION, None, None, "gemini-x"),
                (IDS[1], "VALID", PREDICTION, None, "VALID", PREDICTION, None, None, "gemini-x"),
                (IDS[2], "INVALID_OUTPUT", None, None, "SCHEMA_VIOLATION", None, None, None)
                + ("gemini-x",),
            ]
            metrics = dict(
                conn.execute(
                    "SELECT metric_key,value FROM evaluation.metrics WHERE run_id=%s "
                    "AND scoring_version='kan144-import-test' AND class_label IS NULL",
                    (summary["run_id"],),
                ).fetchall()
            )
            assert float(metrics["cs_locked_category_first_accuracy"]) == pytest.approx(1 / 3)
            assert float(metrics["cs_locked_urgency_first_accuracy"]) == pytest.approx(2 / 3)
            # 형식 위반율(KAN-148 2차): 응답 받은 3건 중 SCHEMA_VIOLATION 1건.
            assert summary["locked_metric_rows"]["outcomes"] == 7
            rate = conn.execute(
                "SELECT value,numerator,denominator FROM evaluation.metrics WHERE run_id=%s "
                "AND metric_key='cs_locked_schema_violation_rate' AND segment='all'",
                (summary["run_id"],),
            ).fetchone()
            assert (float(rate[0]), rate[1], rate[2]) == (pytest.approx(1 / 3), 1, 3)
            receipt = "SELECT origin,import_ref,response_request_id FROM evaluation.admission_receipts WHERE run_id=%s"
            [first] = conn.execute(receipt, (summary["run_id"],)).fetchall()
            run_ref = hashlib.sha256((tmp_path / "run.json").read_bytes()).hexdigest()[:12]
            assert first[:2] == ("IMPORT", run_ref) and first[2] is not None
        again_code, again = run([*argv, "--execute", "--actor", str(actor)], locks)
        assert (again_code, again["status"], again["run_id"]) == (0, "EXISTS", summary["run_id"])
        assert again["locked_metric_rows"]["outcomes"] == "EXISTS"
        with psycopg.connect(evaluation_database) as conn:
            assert conn.execute(receipt, (summary["run_id"],)).fetchall() == [first]
        # 영수증이 생겨 summary가 반입 실행을 연다. 승인 목록 검사는 그대로라 승인 전에는 403이다.
        settings = Settings()
        access = EvaluationAccessSettings()
        run_id = UUID(summary["run_id"])
        with pytest.raises(ApplicationError) as denied:
            get_run_summary(settings, access, actor, run_id)
        assert (denied.value.status_code, denied.value.code) == (403, "EXPERIMENT_NOT_APPROVED")
        # 반입 집계는 합성 잠금표로 저장했으므로 요약도 같은 잠금표로 검증한다.
        monkeypatch.setattr(summary_module, "GOLD_LOCKS", summary_module.GOLD_LOCKS | locks)
        approved = access.model_copy(update={"approved_config_hashes": {summary["config_hash"]}})
        report = get_run_summary(settings, approved, actor, run_id)
        assert (report.origin, report.status, report.target_count) == ("IMPORT", "PARTIAL", 3)
        assert report.all.stored_raw_response_count == 0
        experiment = report.experiment
        assert experiment is not None and str(experiment.experiment_id) == summary["experiment_id"]
        assert (experiment.stage, experiment.model_id, experiment.config_hash) == (
            "classification", "gemini-x", summary["config_hash"]
        )  # fmt: skip
        outcomes = report.locked_quality.outcomes
        assert outcomes is not None and outcomes.scoring_version == "kan144-import-test"
        assert (outcomes.schema_violations, outcomes.received) == (1, 3)
        assert outcomes.schema_violation_rate == pytest.approx(1 / 3)
        assert outcomes.outcomes["VALID"] == 2 and outcomes.outcomes["NOT_STARTED"] == 0
    finally:
        cleanup(evaluation_database, actor)


def cleanup(evaluation_database, actor):
    """검사 평가자가 만든 실험과 그 실행·사례·시도·지표 행을 지운다.

    Args:
        evaluation_database: 관리자 DSN.
        actor: 검사 평가자.

    Returns:
        None: 정리만 한다.
    """
    with psycopg.connect(evaluation_database) as conn:
        mine = "SELECT id FROM evaluation.experiments WHERE created_by_user_id=%s"
        runs = f"SELECT id FROM evaluation.runs WHERE experiment_id IN ({mine})"
        cases = f"SELECT id FROM evaluation.cases WHERE run_id IN ({runs})"
        conn.execute(f"DELETE FROM evaluation.metrics WHERE run_id IN ({runs})", (actor,))
        conn.execute(f"DELETE FROM evaluation.case_attempts WHERE case_id IN ({cases})", (actor,))
        conn.execute(f"DELETE FROM evaluation.cases WHERE run_id IN ({runs})", (actor,))
        conn.execute(
            f"DELETE FROM evaluation.admission_receipts WHERE run_id IN ({runs})", (actor,)
        )
        conn.execute(f"DELETE FROM evaluation.runs WHERE id IN ({runs})", (actor,))
        conn.execute(
            f"DELETE FROM evaluation.experiment_targets WHERE experiment_id IN ({mine})", (actor,)
        )
        conn.execute(f"DELETE FROM evaluation.experiments WHERE id IN ({mine})", (actor,))


def test_import_with_audit_fills_attempt_columns(
    evaluation_database, registered_user, tmp_path, monkeypatch
):
    """감사 파일이 있으면 시도 행의 사용량·지연·입력 hash·응답 판본·비용 열을 감사 값으로 채운다.

    Args:
        evaluation_database: 평가 스키마를 적용한 관리자 DSN.
        registered_user: 지정 평가자로 쓸 합성 사용자.
        tmp_path: 입력 파일 디렉터리.
        monkeypatch: 환경변수 주입.

    Returns:
        None: 사례별 시도 열 값을 확인한다.
    """
    _, actor = registered_user
    monkeypatch.setenv(
        "DATABASE_URL",
        make_conninfo(evaluation_database, user="platform_app", password="test-only"),
    )
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("EVALUATION_EVALUATOR_USER_IDS", json.dumps([str(actor)]))
    monkeypatch.setenv("EVALUATION_LOCAL_ADMISSION_ENABLED", "true")
    argv, locks = write_inputs(tmp_path)
    entries = audit_entries(("VALID", "VALID", "SCHEMA_VIOLATION"))
    raw = json.dumps(entries, sort_keys=True).encode()
    (tmp_path / "audit.json").write_bytes(raw)
    run_file = json.loads((tmp_path / "run.json").read_text())
    run_file["meta"]["audit_files"] = {"a": hashlib.sha256(raw).hexdigest()}
    (tmp_path / "run.json").write_text(json.dumps(run_file))
    try:
        code, summary = run(
            [*argv, "--audit", str(tmp_path / "audit.json"), "--execute", "--actor", str(actor)],
            locks,
        )
        assert code == 0, summary
        assert summary["audit"] is True
        with psycopg.connect(evaluation_database) as conn:
            rows = conn.execute(
                "SELECT c.target_ref,a.outcome,a.error_code,a.model_version,a.finish_reasons,"
                "a.input_sha256,a.latency_ms,a.input_tokens,a.output_tokens,a.thought_tokens,"
                "a.cached_input_tokens,a.total_tokens,a.call_attempts,a.cost_usd::text,"
                "a.pricing_version,a.requested_model,a.raw_output FROM evaluation.cases c "
                "JOIN evaluation.case_attempts a ON a.case_id=c.id WHERE c.run_id=%s "
                "ORDER BY c.target_ref",
                (summary["run_id"],),
            ).fetchall()
            # 감사 포함 반입도 같은 트랜잭션에서 IMPORT 영수증 한 행을 남긴다.
            receipts = conn.execute(
                "SELECT origin,import_ref FROM evaluation.admission_receipts WHERE run_id=%s",
                (summary["run_id"],),
            ).fetchall()
        run_ref = hashlib.sha256((tmp_path / "run.json").read_bytes()).hexdigest()[:12]
        assert receipts == [("IMPORT", run_ref)]
        tail = (813, 900, 40, 12, None, 952, 2, "0.000123", "gemini-x@2026-10-01", "gemini-x", None)
        assert rows == [
            (IDS[0], "VALID", None, "x-0429", ["STOP"], "1" * 64, *tail),
            (IDS[1], "VALID", None, "x-0429", ["STOP"], "2" * 64, *tail),
            (IDS[2], "SCHEMA_VIOLATION", "CS_INVALID_OUTPUT", "x-0429", ["STOP"], "3" * 64, *tail),
        ]
    finally:
        cleanup(evaluation_database, actor)


def audit_entries(outcomes, *, ids=IDS, start=1):
    """사례 감사 항목(cs_candidate_run.audit_entry 모양)을 만든다.

    Args:
        outcomes: IDS 순서의 outcome.
        ids: 사례 ID.
        start: input_sha256을 만드는 숫자 시작값.

    Returns:
        dict: {사례 ID: 감사 항목}.
    """
    usage = {
        "input_tokens": 900,
        "output_tokens": 40,
        "thought_tokens": 12,
        "cached_input_tokens": None,
        "total_tokens": 952,
    }
    return {
        ticket_id: {
            "outcome": outcome,
            "error_code": None if outcome == "VALID" else "CS_INVALID_OUTPUT",
            "http_status": None,
            "model": "gemini-x",
            "model_version": "x-0429",
            "input_version": eval_import_run.INPUT_VERSION_STRIPPED,
            "input_sha256": f"{index}" * 64,
            "usage": usage,
            "latency_ms": 812.6,
            "finish_reasons": ["STOP"],
            "attempts": 2,
            "cost_usd": "0.000123",
            "pricing_version": "gemini-x@2026-10-01",
        }
        for index, (ticket_id, outcome) in enumerate(zip(ids, outcomes, strict=True), start=start)
    }


def test_import_with_retry_adds_final_attempt_two(
    evaluation_database, registered_user, tmp_path, monkeypatch
):
    """재시도 감사·최종 라벨이 있으면 시도 1을 최종 아님, 시도 2를 최종으로 넣고 사례·실행은 최종 기준이다.
    잠금 채점은 시도 1을 읽고, 저장된 최초/최종 비교(compare_stored_attempts)는 시도 2를 최종으로 읽는다.

    Args:
        evaluation_database: 평가 스키마를 적용한 관리자 DSN.
        registered_user: 지정 평가자로 쓸 합성 사용자.
        tmp_path: 입력 파일 디렉터리.
        monkeypatch: 환경변수 주입.

    Returns:
        None: 시도·사례·실행 행과 최초·최종 지표를 확인한다.
    """
    from eval.cs_gold_locks import GOLD_BUNDLE_ID
    from eval.cs_stored_metrics import compare_stored_attempts

    _, actor = registered_user
    monkeypatch.setenv(
        "DATABASE_URL",
        make_conninfo(evaluation_database, user="platform_app", password="test-only"),
    )
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("EVALUATION_EVALUATOR_USER_IDS", json.dumps([str(actor)]))
    monkeypatch.setenv("EVALUATION_LOCAL_ADMISSION_ENABLED", "true")
    argv, locks = write_inputs(tmp_path)
    audit = json.dumps(audit_entries(("VALID", "VALID", "SCHEMA_VIOLATION")), sort_keys=True)
    retry = audit_entries(("VALID",), ids=IDS[2:], start=3)
    retry[IDS[2]]["error_code"] = None
    retry_raw = json.dumps(retry, sort_keys=True).encode()
    labels = json.loads((tmp_path / "labels.json").read_text())
    final_raw = json.dumps(labels | {IDS[2]: PREDICTION}, sort_keys=True).encode()
    (tmp_path / "audit.json").write_text(audit)
    (tmp_path / "retry.json").write_bytes(retry_raw)
    (tmp_path / "final.json").write_bytes(final_raw)
    run_file = json.loads((tmp_path / "run.json").read_text())
    run_file["meta"] |= {
        "invalid_output_retries": 1,
        "audit_files": {"a": hashlib.sha256(audit.encode()).hexdigest()},
        "retry_audit_files": {"a": hashlib.sha256(retry_raw).hexdigest()},
        "final_label_files": {"a": hashlib.sha256(final_raw).hexdigest()},
    }
    run_file["result"]["candidates"][0]["final"] = {"retried": 1, "outcomes": {"VALID": 3}}
    (tmp_path / "run.json").write_text(json.dumps(run_file))
    try:
        code, summary = run(
            [
                *argv,
                *("--audit", str(tmp_path / "audit.json")),
                *("--retry-audit", str(tmp_path / "retry.json")),
                *("--final-labels", str(tmp_path / "final.json")),
                *("--execute", "--actor", str(actor)),
            ],
            locks,
        )
        assert code == 0, summary
        assert (summary["attempts"], summary["retried"]) == (4, 1)
        with psycopg.connect(evaluation_database) as conn:
            assert conn.execute(
                "SELECT status,valid_output_count,failed_count FROM evaluation.runs WHERE id=%s",
                (summary["run_id"],),
            ).fetchone() == ("SUCCEEDED", 3, 0)
            rows = conn.execute(
                "SELECT c.status,c.final_output,a.attempt,a.is_final,a.outcome,a.parsed_output,"
                "a.input_sha256 FROM evaluation.cases c JOIN evaluation.case_attempts a "
                "ON a.case_id=c.id WHERE c.run_id=%s AND c.target_ref=%s ORDER BY a.attempt",
                (summary["run_id"], IDS[2]),
            ).fetchall()
            assert rows == [
                ("VALID", PREDICTION, 1, False, "SCHEMA_VIOLATION", None, "3" * 64),
                ("VALID", PREDICTION, 2, True, "VALID", PREDICTION, "3" * 64),
            ]
            first = dict(
                conn.execute(
                    "SELECT metric_key,value FROM evaluation.metrics WHERE run_id=%s "
                    "AND class_label IS NULL",
                    (summary["run_id"],),
                ).fetchall()
            )
            assert float(first["cs_locked_category_first_accuracy"]) == pytest.approx(1 / 3)
            gold = {IDS[0]: "WARRANTY", IDS[1]: "RETURN", IDS[2]: "WARRANTY"}
            compared = compare_stored_attempts(
                conn, summary["run_id"], gold, gold_version=GOLD_BUNDLE_ID, metric="category"
            )
        assert compared["final"]["accuracy"] == pytest.approx(2 / 3)
        assert compared["transitions"]["RECOVERED"] == 1
    finally:
        cleanup(evaluation_database, actor)
