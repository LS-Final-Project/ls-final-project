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
            receipt = "SELECT origin,import_ref,response_request_id FROM evaluation.admission_receipts WHERE run_id=%s"
            [first] = conn.execute(receipt, (summary["run_id"],)).fetchall()
            run_ref = hashlib.sha256((tmp_path / "run.json").read_bytes()).hexdigest()[:12]
            assert first[:2] == ("IMPORT", run_ref) and first[2] is not None
        again_code, again = run([*argv, "--execute", "--actor", str(actor)], locks)
        assert (again_code, again["status"], again["run_id"]) == (0, "EXISTS", summary["run_id"])
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
    finally:
        with psycopg.connect(evaluation_database) as conn:
            mine = "SELECT id FROM evaluation.experiments WHERE created_by_user_id=%s"
            runs = f"SELECT id FROM evaluation.runs WHERE experiment_id IN ({mine})"
            cases = f"SELECT id FROM evaluation.cases WHERE run_id IN ({runs})"
            conn.execute(f"DELETE FROM evaluation.metrics WHERE run_id IN ({runs})", (actor,))
            conn.execute(
                f"DELETE FROM evaluation.case_attempts WHERE case_id IN ({cases})", (actor,)
            )
            conn.execute(f"DELETE FROM evaluation.cases WHERE run_id IN ({runs})", (actor,))
            conn.execute(
                f"DELETE FROM evaluation.admission_receipts WHERE run_id IN ({runs})", (actor,)
            )
            conn.execute(f"DELETE FROM evaluation.runs WHERE id IN ({runs})", (actor,))
            conn.execute(
                f"DELETE FROM evaluation.experiment_targets WHERE experiment_id IN ({mine})",
                (actor,),
            )
            conn.execute(f"DELETE FROM evaluation.experiments WHERE id IN ({mine})", (actor,))
