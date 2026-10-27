"""권한을 확인한 내부 평가 연결에서 최초 지표와 최종 정오를 분리하는 집계 경계."""

from collections.abc import Mapping
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row
from pydantic import ValidationError

from backend.engine.cs.classification import ClassificationOutput
from backend.engine.cs.classification_call import ClassificationAttempt
from eval.cs_metrics import OUTCOMES
from eval.cs_strata import Metric, summarize_first_attempts_by_group


def summarize_stored_first_attempts(
    conn: Connection,
    run_id: UUID,
    gold: Mapping[str, str | None],
    *,
    gold_version: str | None,
    metric: Metric,
) -> dict:
    """저장된 고정 대상과 정답 판본을 대조하고 최초 시도로만 집계한다.

    Args:
        conn: 호출자가 조회 권한을 확인한 내부 DB 연결. 인증을 대신하지 않는다.
        run_id: 조회 권한이 있는 실행 ID.
        gold: 해당 실험의 사전 잠금 정답. 대상 ID가 정확히 일치해야 한다.
        gold_version: 인수한 정답 판본. 실험의 gold_version과 일치해야 한다.
        metric: category/urgency/secondary 중 집계할 지표.

    Returns:
        dict: 한 SQL 스냅샷의 실행 상태와 최초 시도 집계. 원시 출력은 읽지 않는다.
            불명 실행·대상/판본 불일치·잘못된 저장 출력은 안전한 ValueError다.
            DB 오류는 성공 결과로 바꾸지 않는다. 연결의 commit/rollback은 호출자 몫이다.
    """
    rows = _read_snapshot(conn, run_id)
    return _summarize_snapshot(rows, run_id, gold, gold_version=gold_version, metric=metric)


def _read_snapshot(conn: Connection, run_id: UUID, *, compare_final: bool = False) -> list[dict]:
    """최초/최종 비교도 같은 SQL 스냅샷에서 읽는다.

    Args:
        conn: 권한 확인된 내부 연결.
        run_id: 대상 실행.
        compare_final: 최종 표시와 최신 시도 번호까지 읽을지 여부.

    Returns:
        list[dict]: 원시 출력 없이 고정 대상별 저장 행.
    """
    final_columns = (
        ",f.id AS final_id,f.attempt AS final_attempt,f.outcome AS final_outcome,"
        "f.parsed_output AS final_output,f.finished_at AS final_finished_at,"
        "(SELECT max(later.attempt) FROM evaluation.case_attempts later "
        "WHERE later.case_id=c.id AND later.step='classify') AS last_attempt "
        if compare_final
        else ""
    )
    final_join = (
        "LEFT JOIN evaluation.case_attempts f "
        "ON f.case_id=c.id AND f.step='classify' AND f.is_final "
        if compare_final
        else ""
    )
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            "SELECT r.experiment_id,r.status,r.requested_count,e.gold_version,"
            "t.target_ref,t.attributes->'channel' AS channel,"
            "t.attributes->'body_language' AS body_language,a.id AS attempt_id,"
            "a.outcome,a.parsed_output " + final_columns + "FROM evaluation.runs r "
            "JOIN evaluation.experiments e ON e.id=r.experiment_id "
            "LEFT JOIN evaluation.experiment_targets t ON t.experiment_id=r.experiment_id "
            "LEFT JOIN evaluation.cases c ON c.run_id=r.id AND c.target_ref=t.target_ref "
            "LEFT JOIN evaluation.case_attempts a "
            "ON a.case_id=c.id AND a.step='classify' AND a.attempt=1 "
            + final_join
            + "WHERE r.id=%s ORDER BY t.target_ref",
            (run_id,),
        ).fetchall()
    return rows


def _summarize_snapshot(
    rows: list[dict],
    run_id: UUID,
    gold: Mapping[str, str | None],
    *,
    gold_version: str | None,
    metric: Metric,
) -> dict:
    """조회한 스냅샷의 대상/판본을 검증하고 기존 최초 지표를 만든다.

    Args:
        rows: 동일 SQL로 조회한 행.
        run_id: 대상 실행.
        gold: 잠금 정답.
        gold_version: 정답 판본.
        metric: 분류 지표.

    Returns:
        dict: 기존 최초 집계 계약 그대로의 결과.
    """
    if not rows:
        raise ValueError("CS_STORED_METRICS_INVALID_INPUT")
    targets = {row["target_ref"] for row in rows if row["target_ref"] is not None}
    if (
        targets != set(gold)
        or rows[0]["requested_count"] != len(targets)
        or rows[0]["gold_version"] != gold_version
    ):
        raise ValueError("CS_STORED_METRICS_INVALID_INPUT")
    attempts = {}
    metadata = {}
    for row in rows:
        target = row["target_ref"]
        if target is None:
            continue
        metadata[target] = {key: row[key] for key in ("channel", "body_language")}
        if row["attempt_id"] is None:
            continue  # 접수/시도 누락도 고정 대상에서 지우지 않는다.
        try:
            prediction = (
                ClassificationOutput.model_validate(row["parsed_output"])
                if row["parsed_output"] is not None
                else None
            )
        except ValidationError:
            raise ValueError("CS_STORED_METRICS_INVALID_INPUT") from None
        # 기존 집계가 outcome/예측의 일관성과 허용 상태를 다시 검사한다.
        attempts[target] = ClassificationAttempt(
            attempt_number=1, outcome=row["outcome"], prediction=prediction
        )
    return {
        "version": "cs-stored-first-attempt-v1",
        "run_id": str(run_id),
        "experiment_id": str(rows[0]["experiment_id"]),
        "run_status": rows[0]["status"],
        "gold_version": gold_version,
        "report": summarize_first_attempts_by_group(gold, attempts, metadata, metric=metric),
    }


def compare_stored_attempts(
    conn: Connection,
    run_id: UUID,
    gold: Mapping[str, str | None],
    *,
    gold_version: str | None,
    metric: Metric,
) -> dict:
    """최초 품질과 최종 시도의 복구·악화를 같은 고정 대상에서 비교한다.

    Args:
        conn: 조회 권한·정답 잠금을 호출자가 확인한 내부 연결.
        run_id: 비교할 실행.
        gold: 고정 대상별 해당 지표 정답.
        gold_version: 실험과 일치해야 하는 정답 판본.
        metric: category/urgency/secondary 중 비교할 지표.

    Returns:
        dict: 최초 집계와 별도 최종 정확도·전이 건수·사례별 시도 번호.
            최종 미표시를 추정하지 않으며 모순된 저장은 안전한 오류로 거부한다.
    """
    rows = _read_snapshot(conn, run_id, compare_final=True)
    first = _summarize_snapshot(rows, run_id, gold, gold_version=gold_version, metric=metric)
    first_cases = {case["case_id"]: case for case in first["report"]["overall"]["cases"]}
    field = {
        "category": "category_code",
        "urgency": "urgency_flag",
        "secondary": "secondary_category",
    }[metric]
    transitions = dict.fromkeys(
        ("RECOVERED", "REGRESSED", "STILL_CORRECT", "STILL_INCORRECT", "FINAL_PENDING"), 0
    )
    outcomes = dict.fromkeys(sorted(OUTCOMES | {"NOT_FINAL"}), 0)
    cases = []
    correct = 0
    for row in rows:
        target = row["target_ref"]
        if target is None:
            continue
        initial = first_cases[target]
        prediction = None
        final_correct = False
        outcome = "NOT_FINAL"
        transition = "FINAL_PENDING"
        if row["final_id"] is not None:
            if (
                row["attempt_id"] is None
                or row["final_attempt"] != row["last_attempt"]
                or row["final_finished_at"] is None
                or row["final_outcome"] not in OUTCOMES
                or (row["final_outcome"] == "VALID") != (row["final_output"] is not None)
            ):
                raise ValueError("CS_STORED_COMPARISON_INVALID_INPUT")
            outcome = row["final_outcome"]
            if row["final_output"] is not None:
                try:
                    output = ClassificationOutput.model_validate(row["final_output"])
                except ValidationError:
                    raise ValueError("CS_STORED_COMPARISON_INVALID_INPUT") from None
                prediction = getattr(output, field)
                final_correct = prediction == gold[target]
            transition = (
                "STILL_CORRECT"
                if initial["correct"] and final_correct
                else "REGRESSED"
                if initial["correct"]
                else "RECOVERED"
                if final_correct
                else "STILL_INCORRECT"
            )
        transitions[transition] += 1
        outcomes[outcome] += 1
        correct += final_correct
        cases.append(
            {
                "case_id": target,
                "first_correct": initial["correct"],
                "final_attempt_number": row["final_attempt"],
                "final_outcome": outcome,
                "final_prediction": prediction,
                "final_correct": final_correct,
                "transition": transition,
            }
        )
    total = len(gold)
    return {
        "version": "cs-stored-attempt-comparison-v1",
        "quality_gate_evaluated": False,
        "first": first,
        "final": {
            "selection": "is_final",
            "total": total,
            "correct": correct,
            "accuracy": correct / total if total else None,
            "outcomes": outcomes,
        },
        "transitions": transitions,
        "cases": cases,
    }
