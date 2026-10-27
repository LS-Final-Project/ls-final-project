"""권한을 확인한 내부 평가 연결에서 최초 분류 시도만 읽는 집계 경계."""

from collections.abc import Mapping
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row
from pydantic import ValidationError

from backend.engine.cs.classification import ClassificationOutput
from backend.engine.cs.classification_call import ClassificationAttempt
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
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            "SELECT r.experiment_id,r.status,r.requested_count,e.gold_version,"
            "t.target_ref,t.attributes->'channel' AS channel,"
            "t.attributes->'body_language' AS body_language,a.id AS attempt_id,"
            "a.outcome,a.parsed_output "
            "FROM evaluation.runs r "
            "JOIN evaluation.experiments e ON e.id=r.experiment_id "
            "LEFT JOIN evaluation.experiment_targets t ON t.experiment_id=r.experiment_id "
            "LEFT JOIN evaluation.cases c ON c.run_id=r.id AND c.target_ref=t.target_ref "
            "LEFT JOIN evaluation.case_attempts a "
            "ON a.case_id=c.id AND a.step='classify' AND a.attempt=1 "
            "WHERE r.id=%s ORDER BY t.target_ref",
            (run_id,),
        ).fetchall()
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
