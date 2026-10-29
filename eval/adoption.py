"""후보 비교 결과에서 과제별 채택 설정을 고정하는 기록과 결정 메모(KAN-146).

채택은 사람이 정한다. 이 모듈은 scripts/cs_candidate_run·cs_draft_run·cs_embedding_run 결과 JSON에서 채택 후보의
설정·지표 요약·근거 실행을 묶고, 그 과제 지표에 해당하는 합격선만 release_gate.evaluate_gates로 판정해 기록한다.
기록에는 원문·사례 ID·키를 넣지 않는다. 결과 파일에 task 필드가 없으므로 meta 모양으로 과제를 판정한다.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Literal

from eval.release_gate import Gate, MetricValue, evaluate_gates

Task = Literal["classification", "draft", "embedding"]
Status = Literal["PROPOSED", "ADOPTED"]
# 과제별 meta 표본 수 키. search·ocr는 결과 JSON을 내는 실행기가 생기면 더한다.
SAMPLE_KEYS: dict[str, str] = {
    "classification": "targets",
    "draft": "cases",
    "embedding": "queries",
}


@dataclass(frozen=True)
class AdoptionRecord:
    """과제 하나의 채택 설정 기록. status는 항상 PROPOSED로 만들고 ADOPTED 전환은 사람이 파일을 고친다."""

    task: Task
    adopted: dict[str, Any]
    evidence: list[dict[str, Any]]
    gates: dict[str, Any]
    rejected: list[dict[str, str]]
    decided_by: str
    decided_on: str
    rationale: str
    status: Status = "PROPOSED"

    def to_json(self) -> str:
        """기록을 JSON 문자열로 만든다.

        Returns:
            str: 키 순서를 고정한 들여쓰기 JSON(끝 줄바꿈 포함).
        """
        return json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n"


def _task_of(meta: Mapping[str, Any]) -> str:
    """결과 meta 모양으로 과제를 판정한다.

    Args:
        meta: 결과 JSON의 meta.

    Returns:
        str: classification(targets)·draft(cases)·embedding(chunks·queries). 모르는 모양은 ValueError다.
    """
    if "targets" in meta:
        return "classification"
    if "cases" in meta:
        return "draft"
    if "chunks" in meta and "queries" in meta:
        return "embedding"
    raise ValueError


def _rate(part: int, whole: int) -> float | None:
    """비율. 분모 0이면 None이다.

    Args:
        part: 분자.
        whole: 분모.

    Returns:
        float | None: 비율.
    """
    return part / whole if whole else None


def _summary(task: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """후보 행에서 과제별 지표 요약(5키 이내)을 꺼낸다. 값은 행 그대로이며 분류 schema_violation_rate만 계산한다.

    Args:
        task: 과제.
        row: 결과 JSON의 후보 행.

    Returns:
        dict: 분류는 category_macro_f1·urgency_macro_f1·urgency_non_none_recall·critical_miss_count·
            schema_violation_rate(SCHEMA_VIOLATION / (VALID + SCHEMA_VIOLATION), 응답 수 분모), 초안은
            schema_violation_rate·forbidden_rate·grounded_sentence_rate·grounded_case_rate·info_f1(라벨별),
            임베딩은 hit_at_5·mrr. 행 모양이 다르면 KeyError·TypeError다.
    """
    if task == "classification":
        outcomes = row["outcomes"]
        violations = outcomes["SCHEMA_VIOLATION"]
        return {
            "category_macro_f1": row["category"]["macro_f1"],
            "urgency_macro_f1": row["urgency"]["macro_f1"],
            "urgency_non_none_recall": row["urgency"]["non_none_recall"],
            "critical_miss_count": row["urgency"]["critical"]["missed"],
            "schema_violation_rate": _rate(violations, outcomes["VALID"] + violations),
        }
    if task == "draft":
        return {
            "schema_violation_rate": row["schema"]["schema_violation_rate"],
            "forbidden_rate": row["forbidden"]["rate"],
            "grounded_sentence_rate": row["grounding"]["grounded_sentence_rate"],
            "grounded_case_rate": row["grounding"]["grounded_case_rate"],
            "info_f1": {label: value["f1"] for label, value in row["info_requests"].items()},
        }
    # JSON으로 저장되면 hit_at_k 키는 문자열이다. 오류 행은 hit_at_k가 None이다.
    return {"hit_at_5": (row["hit_at_k"] or {}).get("5"), "mrr": row["mrr"]}


def _run(raw: bytes, task: str, candidate: str) -> dict[str, Any]:
    """결과 파일 하나에서 후보의 설정·행·근거를 꺼낸다.

    Args:
        raw: 결과 파일 바이트(sha256도 이 바이트로 계산한다).
        task: 기대 과제.
        candidate: 후보 이름.

    Returns:
        dict: names(행 이름 전체), spec(meta.candidates 명세에서 name 제외, 후보가 없으면 None), evidence.
            과제가 다르거나 모양이 틀리면 ValueError·KeyError·TypeError다.
    """
    data = json.loads(raw)
    meta = data["meta"]
    if _task_of(meta) != task:
        raise ValueError
    rows = {row["name"]: row for row in data["result"]["candidates"]}
    specs = {spec["name"]: spec for spec in meta["candidates"]}
    if candidate not in rows:
        return {"names": set(rows), "spec": None}
    return {
        "names": set(rows),
        "spec": {key: value for key, value in specs[candidate].items() if key != "name"},
        "evidence": {
            "run_file_sha256": hashlib.sha256(raw).hexdigest(),
            "candidate_name": candidate,
            "metrics": _summary(task, rows[candidate]),
            "targets": meta[SAMPLE_KEYS[task]],
            "run_started_at": meta["started_at"],
            "git_sha": meta["git_sha"],
        },
    }


def _gate_summary(evidence: Mapping[str, Any], gates: list[Gate]) -> dict[str, Any]:
    """근거 실행 하나의 지표로 그 과제 지표에 해당하는 게이트만 판정한다.

    Args:
        evidence: 게이트를 볼 근거 실행.
        gates: 잠근 합격선 전체.

    Returns:
        dict: run_file_sha256, evaluate_gates 결과(gates·counts·blocker), excluded(과제 지표가 아니라 뺀
            게이트 키). 평가한 게이트가 없으면 blocker는 False이고 counts는 0이다.
    """
    metrics = evidence["metrics"]
    numeric = {k: v for k, v in metrics.items() if not isinstance(v, dict)}
    scoped = [gate for gate in gates if gate.metric_key in numeric]
    values = [
        MetricValue(key, "all", None if value is None else float(value), evidence["targets"])
        for key, value in numeric.items()
    ]
    return {
        "run_file_sha256": evidence["run_file_sha256"],
        **evaluate_gates(values, scoped),
        "excluded": [gate.metric_key for gate in gates if gate.metric_key not in numeric],
    }


def build_record(
    run_files: list[bytes],
    *,
    task: str,
    adopted_candidate: str,
    gates: list[Gate],
    decided_by: str,
    rationale: str,
    rejected: Mapping[str, str],
    decided_on: date | None = None,
) -> AdoptionRecord:
    """결과 파일들에서 채택 후보의 기록을 만든다(status PROPOSED).

    Args:
        run_files: 결과 JSON 파일 바이트 목록.
        task: classification·draft·embedding.
        adopted_candidate: 채택할 후보 이름.
        gates: 잠근 합격선(load_gates). 과제 지표에 해당하는 것만 평가한다.
        decided_by: 결정자.
        rationale: 사람이 쓴 사유 한 단락.
        rejected: {기각 후보 이름: 사람이 쓴 사유}. 결과에 있는 다른 후보여야 한다.
        decided_on: 결정일. None이면 오늘이다.

    Returns:
        AdoptionRecord: evidence는 후보가 있는 실행 전부(입력 순서), 게이트는 run_started_at이 가장 늦은 실행으로
            본다. 빈 입력, 모르는 과제·후보, 과제 불일치, 실행마다 다른 후보 설정, 모르는 기각 후보, 빈 결정자·
            사유는 EVAL_INVALID_INPUT ValueError다.
    """
    try:
        if not run_files or not decided_by.strip() or not rationale.strip():
            raise ValueError
        runs = [_run(raw, task, adopted_candidate) for raw in run_files]
        found = [run for run in runs if run["spec"] is not None]
        names = set().union(*(run["names"] for run in runs))
        if (
            not found
            or any(run["spec"] != found[0]["spec"] for run in found)
            or not set(rejected) <= names - {adopted_candidate}
            or not all(isinstance(reason, str) and reason.strip() for reason in rejected.values())
        ):
            raise ValueError
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ValueError("EVAL_INVALID_INPUT") from None
    evidence = [run["evidence"] for run in found]
    latest = max(evidence, key=lambda item: item["run_started_at"])
    return AdoptionRecord(
        task=task,  # type: ignore[arg-type]
        adopted=found[0]["spec"],
        evidence=evidence,
        gates=_gate_summary(latest, gates),
        rejected=[{"candidate_name": name, "reason": reason} for name, reason in rejected.items()],
        decided_by=decided_by,
        decided_on=(decided_on or date.today()).isoformat(),
        rationale=rationale.strip(),
    )


def _cell(value: Any) -> str:
    """Markdown 표 칸. 값은 기록 그대로 쓰고 None은 '—'이며 '|'·줄바꿈은 표를 깨지 않게 바꾼다.

    Args:
        value: 칸 값.

    Returns:
        str: 표 칸 문자열.
    """
    if value is None:
        return "—"
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, dict | list) else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _table(header: list[str], rows: list[list[Any]]) -> str:
    """Markdown 표.

    Args:
        header: 머리 칸.
        rows: 행 목록.

    Returns:
        str: 표 문자열(행이 없으면 '없음').
    """
    if not rows:
        return "없음"
    lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
    lines += ["| " + " | ".join(_cell(value) for value in row) + " |" for row in rows]
    return "\n".join(lines)


def render_decision_note(record: AdoptionRecord) -> str:
    """기록을 결정 메모 Markdown으로 만든다. 수치는 기록 그대로이며 추정하지 않는다.

    Args:
        record: 채택 기록.

    Returns:
        str: 제목, 표본·게이트 문구, 채택 설정·근거 실행·게이트·기각 후보 표, 사유.
    """
    gates = record.gates
    counts = gates["counts"]
    latest = next(e for e in record.evidence if e["run_file_sha256"] == gates["run_file_sha256"])
    verdict = (
        "이 과제에 잠근 합격선이 없어 게이트를 평가하지 않았다."
        if not gates["gates"]
        else f"게이트 결과 PASS {counts['PASS']} · FAIL {counts['FAIL']} · "
        f"NOT_EVALUABLE {counts['NOT_EVALUABLE']}, blocker {'있음' if gates['blocker'] else '없음'}."
    )
    adopted = record.evidence[0]["candidate_name"]
    return (
        "\n\n".join(
            [
                f"# 설정 채택 결정 메모: {record.task} · {adopted}",
                f"상태 {record.status} · 결정자 {_cell(record.decided_by)} · 결정일 {record.decided_on}",
                f"> 최신 근거 실행의 표본 {latest['targets']}건 기준 기록이며 합격 판정이 아니다. {verdict}",
                "## 채택 설정",
                _table(["항목", "값"], [[key, value] for key, value in record.adopted.items()]),
                "## 근거 실행",
                _table(
                    ["결과 파일 sha256", "표본", "시작", "git", "지표"],
                    [
                        [
                            e["run_file_sha256"][:12],
                            e["targets"],
                            e["run_started_at"],
                            e["git_sha"],
                            e["metrics"],
                        ]
                        for e in record.evidence
                    ],
                ),
                f"## 게이트 (근거 {gates['run_file_sha256'][:12]})",
                _table(
                    ["지표", "비교", "합격선", "값", "판정", "blocker"],
                    [
                        [
                            g["metric_key"],
                            g["comparison"],
                            g["threshold"],
                            g["value"],
                            g["status"],
                            g["blocker"],
                        ]
                        for g in gates["gates"]
                    ],
                ),
                f"과제 밖이라 제외한 게이트: {_cell(', '.join(gates['excluded']) or None)}",
                "## 기각 후보",
                _table(
                    ["후보", "사유"],
                    [[item["candidate_name"], item["reason"]] for item in record.rejected],
                ),
                "## 사유",
                record.rationale,
            ]
        )
        + "\n"
    )
