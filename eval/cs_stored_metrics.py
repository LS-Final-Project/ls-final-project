"""권한을 확인한 내부 평가 연결에서 최초 지표와 최종 정오를 분리하는 집계 경계."""

from collections.abc import Mapping
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row
from pydantic import ValidationError

from backend.engine.cs.classification import ClassificationOutput
from backend.engine.cs.classification_call import ClassificationAttempt
from eval.cs_metrics import OUTCOMES, summarize_final_attempts
from eval.cs_strata import GoldValue, Metric, summarize_first_attempts_by_group

TERMINAL_STATUSES = frozenset({"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED", "TIMED_OUT"})


def summarize_stored_first_attempts(
    conn: Connection,
    run_id: UUID,
    gold: Mapping[str, GoldValue],
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
        metric: 집계할 지표. return_subtype만 gold가 실험 대상의 비어 있지 않은 부분집합이다.

    Returns:
        dict: 한 SQL 스냅샷의 실행 상태와 최초 시도 집계. 원시 출력은 읽지 않는다.
            불명 실행·대상/판본 불일치·잘못된 저장 출력은 안전한 ValueError다.
            DB 오류는 성공 결과로 바꾸지 않는다. 연결의 commit/rollback은 호출자 몫이다.
    """
    rows = _read_snapshot(conn, run_id)
    return _summarize_snapshot(rows, run_id, gold, gold_version=gold_version, metric=metric)


def _read_snapshot(
    conn: Connection, run_id: UUID, *, compare_final: bool = False, baseline_id: UUID | None = None
) -> list[dict]:
    """최초/최종 비교도 같은 SQL 스냅샷에서 읽는다.

    Args:
        conn: 권한 확인된 내부 연결.
        run_id: 대상 실행.
        compare_final: 최종 표시와 최신 시도 번호까지 읽을지 여부.
        baseline_id: 함께 읽을 기준 실행. 두 실행도 한 SQL 스냅샷을 사용한다.

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
            "SELECT r.id AS run_id,r.experiment_id,r.status,r.requested_count,e.gold_version,"
            "e.purpose,e.data_origin,e.dataset_hash,e.data_split,e.target_set_hash,e.policy_version,"
            "e.config_hash,e.model_id,e.prompt_version,e.input_builder_version,"
            "t.target_ref,t.attributes->'channel' AS channel,"
            "t.attributes->'body_language' AS body_language,a.id AS attempt_id,"
            "a.outcome,a.parsed_output " + final_columns + "FROM evaluation.runs r "
            "JOIN evaluation.experiments e ON e.id=r.experiment_id "
            "LEFT JOIN evaluation.experiment_targets t ON t.experiment_id=r.experiment_id "
            "LEFT JOIN evaluation.cases c ON c.run_id=r.id AND c.target_ref=t.target_ref "
            "LEFT JOIN evaluation.case_attempts a "
            "ON a.case_id=c.id AND a.step='classify' AND a.attempt=1 "
            + final_join
            + "WHERE r.id=ANY(%s) ORDER BY r.id,t.target_ref",
            ([run_id] if baseline_id is None else [run_id, baseline_id],),
        ).fetchall()
    return rows


def _summarize_snapshot(
    rows: list[dict],
    run_id: UUID,
    gold: Mapping[str, GoldValue],
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
    # 반품 하위유형 정답은 반품 대상만 잠갔으므로 실험 대상의 부분집합을 채점한다.
    scoped = metric == "return_subtype"
    if (
        (not gold or not set(gold) <= targets if scoped else targets != set(gold))
        or rows[0]["requested_count"] != len(targets)
        or rows[0]["gold_version"] != gold_version
    ):
        raise ValueError("CS_STORED_METRICS_INVALID_INPUT")
    attempts = {}
    metadata = {}
    for row in rows:
        target = row["target_ref"]
        if target not in gold:
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
        "target_count": len(targets),
        "scored_target_count": len(gold),
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
        metric: 비교할 지표. return_subtype은 gold 대상만 비교한다.

    Returns:
        dict: 최초 집계와 별도 최종 품질 지표·전이 건수·사례별 시도 번호.
            최종 미표시를 추정하지 않으며 모순된 저장은 안전한 오류로 거부한다.
    """
    rows = _read_snapshot(conn, run_id, compare_final=True)
    first = _summarize_snapshot(rows, run_id, gold, gold_version=gold_version, metric=metric)
    first_cases = {case["case_id"]: case for case in first["report"]["overall"]["cases"]}
    final_attempts = {}
    for row in rows:
        if row["final_id"] is None or row["target_ref"] not in gold:
            continue
        if (
            row["attempt_id"] is None
            or row["final_attempt"] != row["last_attempt"]
            or row["final_finished_at"] is None
            or row["final_outcome"] not in OUTCOMES
            or (row["final_outcome"] == "VALID") != (row["final_output"] is not None)
        ):
            raise ValueError("CS_STORED_COMPARISON_INVALID_INPUT")
        try:
            output = (
                ClassificationOutput.model_validate(row["final_output"])
                if row["final_output"] is not None
                else None
            )
        except ValidationError:
            raise ValueError("CS_STORED_COMPARISON_INVALID_INPUT") from None
        final_attempts[row["target_ref"]] = ClassificationAttempt(
            attempt_number=row["final_attempt"], outcome=row["final_outcome"], prediction=output
        )
    final = summarize_final_attempts(gold, final_attempts, metric=metric)
    transitions = dict.fromkeys(
        ("RECOVERED", "REGRESSED", "STILL_CORRECT", "STILL_INCORRECT", "FINAL_PENDING"), 0
    )
    cases = []
    for case in final["cases"]:
        initial = first_cases[case["case_id"]]
        transition = (
            "FINAL_PENDING"
            if case["outcome"] == "NOT_FINAL"
            else "STILL_CORRECT"
            if initial["correct"] and case["correct"]
            else "REGRESSED"
            if initial["correct"]
            else "RECOVERED"
            if case["correct"]
            else "STILL_INCORRECT"
        )
        transitions[transition] += 1
        cases.append(
            {
                "case_id": case["case_id"],
                "first_correct": initial["correct"],
                "final_attempt_number": case["attempt_number"],
                "final_outcome": case["outcome"],
                "final_prediction": case["prediction"],
                "final_correct": case["correct"],
                "transition": transition,
            }
        )
    return {
        "version": "cs-stored-attempt-comparison-v2",
        "quality_gate_evaluated": False,
        "first": first,
        "final": final,
        "transitions": transitions,
        "cases": cases,
    }


def compare_stored_runs(
    conn: Connection,
    baseline_run_id: UUID,
    candidate_run_id: UUID,
    gold: Mapping[str, str | None],
    *,
    gold_version: str | None,
    metric: Metric,
) -> dict:
    """동일 평가 조건의 종료 실행을 최초 시도 기준으로 비교한다.

    Args:
        conn: 두 실행 조회 권한과 정답 잠금을 확인한 내부 연결.
        baseline_run_id: 사용자가 지정한 기준 실행.
        candidate_run_id: 비교할 후보 실행.
        gold: 두 실행에 공통으로 적용할 고정 대상별 정답.
        gold_version: 두 실험과 일치해야 하는 인수 정답 판본.
        metric: 비교할 지표. return_subtype은 gold 대상만 비교한다.

    Returns:
        dict: 기준·후보 최초 지표, 후보-기준 차이와 사례별 퇴행/개선.
            평가 조건이 다르거나 진행 중이면 안전한 오류로 거부한다. 합격 판정은 없다.
    """
    if baseline_run_id == candidate_run_id:
        raise ValueError("CS_BASELINE_INCOMPATIBLE")
    rows = _read_snapshot(conn, candidate_run_id, baseline_id=baseline_run_id)
    baseline = [row for row in rows if row["run_id"] == baseline_run_id]
    candidate = [row for row in rows if row["run_id"] == candidate_run_id]
    conditions = (
        "purpose",
        "data_origin",
        "dataset_hash",
        "data_split",
        "gold_version",
        "target_set_hash",
        "policy_version",
    )
    if (
        not baseline
        or not candidate
        or baseline[0]["status"] not in TERMINAL_STATUSES
        or candidate[0]["status"] not in TERMINAL_STATUSES
        or any(baseline[0][key] != candidate[0][key] for key in conditions)
        or {row["target_ref"]: (row["channel"], row["body_language"]) for row in baseline}
        != {row["target_ref"]: (row["channel"], row["body_language"]) for row in candidate}
    ):
        raise ValueError("CS_BASELINE_INCOMPATIBLE")
    base_report = _summarize_snapshot(
        baseline, baseline_run_id, gold, gold_version=gold_version, metric=metric
    )
    candidate_report = _summarize_snapshot(
        candidate, candidate_run_id, gold, gold_version=gold_version, metric=metric
    )
    old, new = base_report["report"]["overall"], candidate_report["report"]["overall"]
    values = {"accuracy": (old["accuracy"], new["accuracy"])}
    if metric == "secondary":
        values["composite_macro_f1"] = (old["composite"]["macro_f1"], new["composite"]["macro_f1"])
        values["invented_secondary"] = (old["absent"]["invented"], new["absent"]["invented"])
    else:
        values["macro_f1"] = (old["macro_f1"], new["macro_f1"])
    if metric == "urgency":
        values["non_none_recall"] = (old["non_none"]["recall"], new["non_none"]["recall"])
        values["critical_missed"] = (old["critical"]["missed"], new["critical"]["missed"])
    old_cases = {case["case_id"]: case for case in old["cases"]}
    transitions = dict.fromkeys(("IMPROVED", "REGRESSED", "STILL_CORRECT", "STILL_INCORRECT"), 0)
    cases = []
    for case in new["cases"]:
        previous = old_cases[case["case_id"]]
        transition = (
            "STILL_CORRECT"
            if previous["correct"] and case["correct"]
            else "REGRESSED"
            if previous["correct"]
            else "IMPROVED"
            if case["correct"]
            else "STILL_INCORRECT"
        )
        transitions[transition] += 1
        item = {
            "case_id": case["case_id"],
            "baseline_correct": previous["correct"],
            "candidate_correct": case["correct"],
            "transition": transition,
        }
        if metric == "urgency":
            item["new_critical_miss"] = not previous["critical_missed"] and case["critical_missed"]
            item["recovered_critical_miss"] = (
                previous["critical_missed"] and not case["critical_missed"]
            )
        cases.append(item)
    provenance = ("config_hash", "model_id", "prompt_version", "input_builder_version")
    return {
        "version": "cs-baseline-first-attempt-v1",
        "quality_gate_evaluated": False,
        "baseline": base_report,
        "candidate": candidate_report,
        "conditions": {key: baseline[0][key] for key in conditions},
        "settings": {
            "baseline": {key: baseline[0][key] for key in provenance},
            "candidate": {key: candidate[0][key] for key in provenance},
        },
        "delta": {
            key: after - before if before is not None and after is not None else None
            for key, (before, after) in values.items()
        },
        "transitions": transitions,
        "cases": cases,
    }
