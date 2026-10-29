"""저장된 평가 수치를 원응답(RAW)→DB→화면(SCREEN)→CSV 단계로 대조하는 CLI(KAN-148 2차).

RAW는 저장된 최초 시도를 cs_report와 같은 경로(eval/cs_stored_metrics.summarize_stored_first_attempts)로 다시
계산한 값이다. DB는 evaluation.metrics의 실행 단위 행(case_id·class_label NULL, segment 'all')에서 지표마다 저장
시각이 가장 늦은 채점 판본 하나를 쓴다. SCREEN(--screen-json + --screen-paths)과 CSV(--csv + --csv-columns)는
선택이며 없으면 그 단계를 비교하지 않는다. 대조는 eval/report_reconcile.reconcile이 한다.

지표 이름은 게이트 어휘(gates-v1.json)다. cs_report가 저장하는 키(cs_locked_{metric}_first_{key})와 다르므로
METRIC_MAP으로 잇는다. 매핑에 없는 게이트 키는 RAW·DB 모두 None(미계산)이며 결과 meta.unmapped_keys에 적는다.
정답 파일은 eval/cs_gold_locks 잠금 SHA와 같아야 한다(scripts/cs_candidate_run.load_gold). 결과에는 원문·정답·사례
ID를 넣지 않는다. DB 연결은 읽기 전용이며 연결 문자열은 --database-url 또는 환경변수 DATABASE_URL에서만 읽는다.
"""

import argparse
import csv
import io
import json
import os
import sys
from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from eval.cs_gold_locks import GOLD_BUNDLE_ID
from eval.cs_stored_metrics import summarize_stored_first_attempts
from eval.release_gate import load_gates
from eval.report_reconcile import (
    Number,
    RunStatus,
    StageValues,
    csv_totals,
    reconcile,
    screen_payload_values,
)
from scripts.cs_candidate_run import git_sha, load_gold

# 게이트 키 → (집계 지표, overall 안의 경로, cs_report 저장 키).
METRIC_MAP: dict[str, tuple[str, tuple[str, ...], str]] = {
    "category_macro_f1": ("category", ("macro_f1",), "cs_locked_category_first_macro_f1"),
    "category_micro_accuracy": ("category", ("accuracy",), "cs_locked_category_first_accuracy"),
    "urgency_macro_f1": ("urgency", ("macro_f1",), "cs_locked_urgency_first_macro_f1"),
    "urgency_non_none_recall": (
        "urgency",
        ("non_none", "recall"),
        "cs_locked_urgency_first_non_none_recall",
    ),
    "critical_miss_count": (
        "urgency",
        ("critical", "missed"),
        "cs_locked_urgency_first_critical_missed",
    ),
}
# float 지표를 numeric으로 저장하면 유효숫자 15자리로 반올림된다(예 1/3 → 0.333333333333333).
# 그 차이(최대 약 1e-15)를 불일치로 보지 않도록 기본 허용 절대 차를 둔다. 실제 지표 차이보다 충분히 작다.
DEFAULT_TOLERANCE = 1e-9
# 사례 수의 DB 기준: 주분류 정확도 행의 분모(채점 대상 수).
CASE_COUNT_KEY = "cs_locked_category_first_accuracy"
RUN_STATUS: dict[str, RunStatus] = {"SUCCEEDED": "COMPLETED", "PARTIAL": "PARTIAL"}
TARGETS_SQL = (
    "SELECT t.target_ref FROM evaluation.runs r "
    "JOIN evaluation.experiment_targets t ON t.experiment_id=r.experiment_id WHERE r.id=%s"
)
METRICS_SQL = (
    "SELECT metric_key,value,denominator,scoring_version,created_at FROM evaluation.metrics "
    "WHERE run_id=%s AND case_id IS NULL AND class_label IS NULL AND segment='all' "
    "AND metric_key=ANY(%s)"
)
Reader = Callable[[str, UUID, Path, Path], tuple[dict[str, dict], list[dict]]]


def read_database(
    url: str, run_id: UUID, gold_category: Path, gold_urgency: Path
) -> tuple[dict[str, dict], list[dict]]:
    """읽기 전용 연결 하나로 RAW 재계산과 DB 저장 행을 읽는다.

    Args:
        url: 평가 DB 연결 문자열.
        run_id: 대조할 실행.
        gold_category: 주분류 잠금 정답 CSV.
        gold_urgency: 긴급도 잠금 정답 CSV.

    Returns:
        tuple: ({"category"·"urgency": summarize_stored_first_attempts 결과}, 매핑된 저장 키의 metrics 행).
    """
    with psycopg.connect(url) as conn:
        conn.read_only = True
        targets = {row[0] for row in conn.execute(TARGETS_SQL, (run_id,))}
        if not targets:
            raise ValueError("CS_RECONCILE_UNKNOWN_RUN")
        summaries = {
            metric: summarize_stored_first_attempts(
                conn,
                run_id,
                load_gold(path, metric, targets),
                gold_version=GOLD_BUNDLE_ID,
                metric=metric,  # type: ignore[arg-type]
            )
            for metric, path in (("category", gold_category), ("urgency", gold_urgency))
        }
        keys = sorted({db_key for _, _, db_key in METRIC_MAP.values()})
        with conn.cursor(row_factory=dict_row) as cursor:
            rows = cursor.execute(METRICS_SQL, (run_id, keys)).fetchall()
    return summaries, rows


def _dig(node: Any, path: tuple[str, ...]) -> Number | None:
    """중첩 dict에서 값을 꺼낸다.

    Args:
        node: 집계 dict.
        path: 키 경로.

    Returns:
        Number | None: 값. 경로가 없으면 None이다.
    """
    for part in path:
        node = node.get(part) if isinstance(node, Mapping) else None
    return node


def raw_stage(
    summaries: Mapping[str, dict], keys: list[str]
) -> tuple[StageValues, RunStatus, int, int]:
    """RAW 단계 값과 실행 상태·대상 수·평가 완료 수를 만든다.

    Args:
        summaries: {"category"·"urgency": summarize_stored_first_attempts 결과}.
        keys: 대조할 게이트 키.

    Returns:
        tuple: (RAW StageValues, reconcile 실행 상태, 대상 수, 최초 시도가 있는 대상 수). 매핑 없는 키는 None이다.
    """
    overall = {metric: summary["report"]["overall"] for metric, summary in summaries.items()}
    values = {
        key: _dig(overall[METRIC_MAP[key][0]], METRIC_MAP[key][1]) if key in METRIC_MAP else None
        for key in keys
    }
    category = overall["category"]
    status = RUN_STATUS.get(summaries["category"]["run_status"], "ABORTED")
    targets = summaries["category"]["target_count"]
    evaluated = category["total"] - category["outcomes"].get("NOT_STARTED", 0)
    return StageValues("RAW", values, category["total"]), status, targets, evaluated


def db_stage(rows: list[dict], keys: list[str]) -> tuple[StageValues, dict[str, str]]:
    """저장 행에서 지표마다 가장 늦게 저장된 채점 판본 값을 고른다.

    Args:
        rows: METRICS_SQL 결과(metric_key·value·denominator·scoring_version·created_at).
        keys: 대조할 게이트 키.

    Returns:
        tuple: (DB StageValues, 저장 키별 고른 scoring_version). 값은 float, 행이 없으면 None이다.
    """
    latest: dict[str, dict] = {}
    for row in rows:
        current = latest.get(row["metric_key"])
        if current is None or (row["created_at"], row["scoring_version"]) > (
            current["created_at"],
            current["scoring_version"],
        ):
            latest[row["metric_key"]] = row

    def number(value: Decimal | None) -> float | None:
        return None if value is None else float(value)

    values = {
        key: number(latest[METRIC_MAP[key][2]]["value"])
        if key in METRIC_MAP and METRIC_MAP[key][2] in latest
        else None
        for key in keys
    }
    count = latest.get(CASE_COUNT_KEY, {}).get("denominator")
    versions = {key: row["scoring_version"] for key, row in sorted(latest.items())}
    return StageValues("DB", values, count), versions


def csv_stage(text: str, columns: Mapping[str, str], keys: list[str]) -> StageValues:
    """내보내기 CSV의 열 합계를 지표 값으로 쓴다(누락이 있으면 None).

    Args:
        text: CSV 본문.
        columns: 게이트 키 → 열 이름.
        keys: 대조할 게이트 키.

    Returns:
        StageValues: CSV 단계 값. 행 수가 사례 수다.
    """
    rows: list[Mapping[str, str | None]] = list(csv.DictReader(io.StringIO(text, newline="")))
    totals = csv_totals(rows, tuple(columns.values()))
    values: dict[str, Number | None] = {
        key: (
            None
            if key not in columns or totals[columns[key]]["total"] is None
            else float(totals[columns[key]]["total"])
        )
        for key in keys
    }
    return StageValues("CSV", values, len(rows))


def build_report(
    summaries: Mapping[str, dict],
    rows: list[dict],
    keys: list[str],
    *,
    tolerance: float,
    screen: tuple[Mapping[str, Any], Mapping[str, tuple[str, ...]]] | None = None,
    csv_input: tuple[str, Mapping[str, str]] | None = None,
) -> dict:
    """네 단계 값을 만들어 reconcile을 돌리고 meta를 붙인다.

    Args:
        summaries: RAW 재계산 결과.
        rows: DB 저장 행.
        keys: 대조할 게이트 키.
        tolerance: 허용 절대 차.
        screen: (화면 DTO, 키별 경로) 또는 None.
        csv_input: (CSV 본문, 키별 열) 또는 None.

    Returns:
        dict: reconcile 결과와 meta(매핑표·미매핑 키·DB 판본·비교한 단계). 원문·정답·사례 ID 없음.
    """
    raw, status, targets, evaluated = raw_stage(summaries, keys)
    db, versions = db_stage(rows, keys)
    stages = [raw, db]
    if screen is not None:
        payload, paths = screen
        found = screen_payload_values(payload, {k: tuple(v) for k, v in paths.items()})
        stages.append(StageValues("SCREEN", {key: found.get(key) for key in keys}, None))
    if csv_input is not None:
        stages.append(csv_stage(csv_input[0], csv_input[1], keys))
    result = reconcile(
        stages, run_status=status, targets=targets, evaluated=evaluated, tolerance=tolerance
    )
    return {
        "meta": {
            "metric_map": {key: [m, list(path), db] for key, (m, path, db) in METRIC_MAP.items()},
            "unmapped_keys": [key for key in keys if key not in METRIC_MAP],
            "db_scoring_versions": versions,
            "stages": [stage.stage for stage in stages],
            "tolerance": tolerance,
            "git_sha": git_sha(),
        },
        "result": result,
    }


def main(argv: list[str] | None = None, reader: Reader | None = None) -> int:
    """인자를 읽어 대조 결과 JSON을 --out에 쓴다.

    Args:
        argv: 명령행 인자(테스트 주입용).
        reader: DB 읽기 함수(테스트는 fake로 교체).

    Returns:
        int: 0 성공, 1 입력·DB·출력 오류. 오류 원문·연결 문자열은 출력하지 않는다.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--gold-category", type=Path, required=True)
    parser.add_argument("--gold-urgency", type=Path, required=True)
    parser.add_argument("--metric-keys", help="쉼표 구분 게이트 키. 기본은 gates-v1.json 키 전부")
    parser.add_argument(
        "--tolerance",
        type=float,
        default=DEFAULT_TOLERANCE,
        help="허용 절대 차. 기본값은 DB numeric 저장의 15자리 반올림을 흡수한다",
    )
    parser.add_argument("--screen-json", type=Path)
    parser.add_argument("--screen-paths", type=Path, help='{"게이트 키": ["경로", ...]} JSON')
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--csv-columns", type=Path, help='{"게이트 키": "열 이름"} JSON')
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    url = args.database_url or os.environ.get("DATABASE_URL")
    if (
        not url
        or args.tolerance < 0
        or (args.screen_json is None) != (args.screen_paths is None)
        or (args.csv is None) != (args.csv_columns is None)
    ):
        print(
            "CS_RECONCILE_INVALID_ARGS: DB 연결, 0 이상의 --tolerance, --screen-json·--screen-paths와 "
            "--csv·--csv-columns는 짝으로 주세요."
        )
        return 1
    try:
        run_id = UUID(args.run_id)
        keys = (
            [key.strip() for key in args.metric_keys.split(",") if key.strip()]
            if args.metric_keys
            else list(dict.fromkeys(gate.metric_key for gate in load_gates()))
        )
        if not keys:
            raise ValueError("CS_RECONCILE_NO_KEYS")
        screen = (
            (
                json.loads(args.screen_json.read_text(encoding="utf-8")),
                json.loads(args.screen_paths.read_text(encoding="utf-8")),
            )
            if args.screen_json
            else None
        )
        csv_input = (
            (
                args.csv.read_text(encoding="utf-8-sig"),
                json.loads(args.csv_columns.read_text(encoding="utf-8")),
            )
            if args.csv
            else None
        )
        summaries, rows = (reader or read_database)(
            url, run_id, args.gold_category, args.gold_urgency
        )
        report = build_report(
            summaries, rows, keys, tolerance=args.tolerance, screen=screen, csv_input=csv_input
        )
        args.out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    except (ValueError, OSError, KeyError, TypeError, csv.Error, psycopg.Error):
        print("CS_RECONCILE_FAILED: 실행 ID·정답 잠금·DB 연결·화면·CSV 파일을 확인하세요.")
        return 1
    summary = report["result"]
    print(json.dumps({"consistent": summary["consistent"], "summary": summary["summary"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
