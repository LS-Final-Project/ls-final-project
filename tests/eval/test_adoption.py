"""KAN-146 채택 기록·결정 메모. 합성 결과 JSON(분류·초안·임베딩)으로 기록과 게이트 요약을 검사한다."""

import hashlib
import json
from datetime import date

import pytest

from eval.adoption import build_record, render_decision_note
from eval.release_gate import Gate, load_gates

SECRET = "원문-누출-표식"
SPEC = {
    "model": "m-a",
    "prompt_version": "v2",
    "prompt_fingerprint": "f" * 64,
    "max_output_tokens": 4096,
}


def classification_run(started_at, *, macro_f1=0.95, violations=0, spec=SPEC):
    """분류 실행기 결과 모양의 합성 JSON 바이트.

    Args:
        started_at: meta.started_at.
        macro_f1: 채택 후보 category macro F1.
        violations: 채택 후보 SCHEMA_VIOLATION 수(VALID는 10 - violations).
        spec: 채택 후보 명세(name 제외).

    Returns:
        bytes: 결과 JSON.
    """

    def row(name, f1, bad):
        return {
            "name": name,
            "category": {"accuracy": 0.9, "macro_f1": f1},
            "urgency": {
                "macro_f1": 0.8,
                "non_none_recall": 0.9,
                "critical": {"support": 2, "missed": 0, "by_gold": {SECRET: 1}},
            },
            "outcomes": {"VALID": 10 - bad, "SCHEMA_VIOLATION": bad, "HELD": 3},
            "leakage": {"note": SECRET},
        }

    return json.dumps(
        {
            "meta": {
                "candidates": [{"name": "a", **spec}, {"name": "b", **SPEC, "model": "m-b"}],
                "targets": 10,
                "target_ids_sha256": SECRET,
                "started_at": started_at,
                "git_sha": "abc1234",
            },
            "result": {"candidates": [row("a", macro_f1, violations), row("b", 0.5, 4)]},
        }
    ).encode()


def embedding_run(hit):
    """임베딩 실행기 결과 모양의 합성 JSON 바이트(hit_at_k 키는 JSON 저장 후의 문자열).

    Args:
        hit: 채택 후보 hit@5.

    Returns:
        bytes: 결과 JSON.
    """
    return json.dumps(
        {
            "meta": {
                "candidates": [{"name": "e", "model": "emb", "dimension": 768}],
                "chunks": 30,
                "queries": 12,
                "started_at": "2026-10-29T01:00:00+00:00",
                "git_sha": "def5678",
            },
            "result": {
                "candidates": [
                    {"name": "e", "hit_at_k": {"1": 0.5, "5": hit}, "mrr": 0.6},
                    {"name": "FTS", "hit_at_k": {"5": 0.7}, "mrr": 0.4},
                ]
            },
        }
    ).encode()


def draft_run():
    """초안 실행기 결과 모양의 합성 JSON 바이트(per_case에 사례 ID가 있다).

    Returns:
        bytes: 결과 JSON.
    """
    labels = {"CONCISE": {"f1": 0.8}, "DETAILED": {"f1": None}, "INFO_FIRST": {"f1": 1.0}}
    return json.dumps(
        {
            "meta": {
                "candidates": [{"name": "d", **SPEC}],
                "cases": 6,
                "started_at": "2026-10-29T02:00:00+00:00",
                "git_sha": "0a0a0a0",
            },
            "result": {
                "candidates": [
                    {
                        "name": "d",
                        "schema": {"schema_violation_rate": 0.0},
                        "forbidden": {"rate": 0.0},
                        "grounding": {"grounded_sentence_rate": 0.75, "grounded_case_rate": 1.0},
                        "info_requests": labels,
                        "per_case": [{"case_id": SECRET}],
                    }
                ]
            },
        }
    ).encode()


def record(files, task="classification", adopt="a", **overrides):
    """기본 인자로 기록을 만든다.

    Args:
        files: 결과 바이트 목록.
        task: 과제.
        adopt: 채택 후보.
        **overrides: build_record 인자 덮어쓰기.

    Returns:
        AdoptionRecord: 기록.
    """
    kwargs = {
        "gates": load_gates(),
        "decided_by": "평가 담당",
        "rationale": "정확도와 비용을 함께 본 결정이다.",
        "rejected": {},
        "decided_on": date(2026, 10, 29),
    } | overrides
    return build_record(files, task=task, adopted_candidate=adopt, **kwargs)


def test_classification_record_scopes_gates_to_the_latest_run():
    """분류 기록은 근거 실행 전부를 담고, 가장 늦은 실행으로 분류 게이트 5개만 판정한다."""
    old = classification_run("2026-10-28T00:00:00+00:00", macro_f1=0.5)
    new = classification_run("2026-10-29T00:00:00+00:00", violations=2)
    result = record([new, old], rejected={"b": "macro F1 0.5로 낮다"})
    assert result.status == "PROPOSED" and result.adopted == SPEC
    assert [e["run_file_sha256"] for e in result.evidence] == [
        hashlib.sha256(raw).hexdigest() for raw in (new, old)
    ]
    assert result.evidence[0]["metrics"] == {
        "category_macro_f1": 0.95,
        "urgency_macro_f1": 0.8,
        "urgency_non_none_recall": 0.9,
        "critical_miss_count": 0,
        "schema_violation_rate": 0.2,
    }
    gates = result.gates
    assert gates["run_file_sha256"] == hashlib.sha256(new).hexdigest()
    assert {g["metric_key"]: g["status"] for g in gates["gates"]} == {
        "category_macro_f1": "PASS",
        "critical_miss_count": "PASS",
        "urgency_macro_f1": "PASS",
        "urgency_non_none_recall": "PASS",
        "schema_violation_rate": "FAIL",
    }
    assert gates["counts"] == {"PASS": 4, "FAIL": 1, "NOT_EVALUABLE": 0} and gates["blocker"]
    assert set(gates["excluded"]) == {
        "hit_at_5",
        "priority_rule_exact",
        "dept_rule_policy_match",
        "dept_pipeline_exact",
    }
    assert result.rejected == [{"candidate_name": "b", "reason": "macro F1 0.5로 낮다"}]


def test_embedding_and_draft_records():
    """임베딩은 hit_at_5 게이트만, 초안은 원시 스키마 위반 게이트만 본다. 맞는 게이트가 없으면 평가 0건이다."""
    embedding = record([embedding_run(0.95)], task="embedding", adopt="e")
    assert embedding.adopted == {"model": "emb", "dimension": 768}
    assert embedding.evidence[0]["metrics"] == {"hit_at_5": 0.95, "mrr": 0.6}
    assert embedding.evidence[0]["targets"] == 12
    assert [g["metric_key"] for g in embedding.gates["gates"]] == ["hit_at_5"]
    assert not embedding.gates["blocker"]
    draft = record([draft_run()], task="draft", adopt="d")
    assert draft.evidence[0]["metrics"]["info_f1"] == {
        "CONCISE": 0.8,
        "DETAILED": None,
        "INFO_FIRST": 1.0,
    }
    assert [(g["metric_key"], g["status"]) for g in draft.gates["gates"]] == [
        ("schema_violation_rate", "PASS")
    ]
    assert len(draft.gates["excluded"]) == 8 and not draft.gates["blocker"]
    unmatched = record([draft_run()], task="draft", adopt="d", gates=[Gate("hit_at_5", ">=", 0.9)])
    assert unmatched.gates["gates"] == [] and not unmatched.gates["blocker"]
    assert unmatched.gates["counts"] == {"PASS": 0, "FAIL": 0, "NOT_EVALUABLE": 0}
    assert "잠근 합격선이 없어" in render_decision_note(unmatched)


def test_note_renders_tables_from_the_record():
    """메모는 기록 값을 그대로 표에 쓰고 합격 판정이 아니라고 적는다. 표 칸의 '|'는 이스케이프한다."""
    result = record(
        [classification_run("2026-10-29T00:00:00+00:00", violations=2)],
        rejected={"b": "느림 | 비쌈"},
    )
    note = render_decision_note(result)
    assert note.startswith("# 설정 채택 결정 메모: classification · a\n")
    assert "표본 10건 기준 기록이며 합격 판정이 아니다" in note
    assert "PASS 4 · FAIL 1 · NOT_EVALUABLE 0, blocker 있음" in note
    assert "| schema_violation_rate | == | 0.0 | 0.2 | FAIL | True |" in note
    assert "| b | 느림 \\| 비쌈 |" in note
    assert "| model | m-a |" in note
    assert note.rstrip().endswith("정확도와 비용을 함께 본 결정이다.")


def test_record_and_note_carry_no_raw_text_or_case_ids():
    """행의 원문·사례 ID·대상 ID 해시는 기록과 메모에 들어가지 않는다."""
    for result in (
        record([classification_run("2026-10-29T00:00:00+00:00")]),
        record([draft_run()], task="draft", adopt="d"),
    ):
        assert SECRET not in result.to_json() and SECRET not in render_decision_note(result)


@pytest.mark.parametrize(
    ("files", "task", "adopt", "overrides"),
    [
        ([], "classification", "a", {}),
        ([b"{}"], "classification", "a", {}),
        ([b"not json"], "classification", "a", {}),
        (["c"], "classification", "missing", {}),
        (["c"], "embedding", "a", {}),
        (["c"], "search", "a", {}),
        (["c"], "classification", "a", {"rejected": {"zzz": "없는 후보"}}),
        (["c"], "classification", "a", {"rejected": {"a": "채택 후보 자신"}}),
        (["c"], "classification", "a", {"rejected": {"b": " "}}),
        (["c"], "classification", "a", {"decided_by": " "}),
        (["c"], "classification", "a", {"rationale": ""}),
        (["c", "changed"], "classification", "a", {}),
    ],
)
def test_invalid_inputs_are_rejected(files, task, adopt, overrides):
    """빈 입력·깨진 JSON·모르는 후보·과제 불일치·search·잘못된 기각·빈 결정자·사유·실행마다 다른 설정은 거부한다."""
    raw = {
        "c": classification_run("2026-10-29T00:00:00+00:00"),
        "changed": classification_run("2026-10-30T00:00:00+00:00", spec=SPEC | {"model": "m-x"}),
    }
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        record([raw.get(item, item) for item in files], task=task, adopt=adopt, **overrides)


def test_latest_run_is_chosen_by_time_not_by_string():
    """UTC 오프셋이 다른 시작 시각도 실제 시각으로 비교해 최신 실행으로 게이트를 본다."""
    later = classification_run("2026-10-29T08:00:00+09:00")  # UTC 2026-10-28T23:00
    latest = classification_run("2026-10-29T00:00:00+00:00", violations=2)
    result = record([later, latest])
    assert result.gates["run_file_sha256"] == hashlib.sha256(latest).hexdigest()


@pytest.mark.parametrize("value", [[0.95], "0.95", True])
def test_non_numeric_metric_value_is_invalid_input(value):
    """숫자·None이 아닌 지표 값은 TypeError나 float 변환 통과가 아니라 EVAL_INVALID_INPUT이다."""
    data = json.loads(embedding_run(0.95))
    data["result"]["candidates"][0]["hit_at_k"]["5"] = value
    with pytest.raises(ValueError, match="^EVAL_INVALID_INPUT$"):
        record([json.dumps(data).encode()], task="embedding", adopt="e")


def test_adopted_record_needs_a_waiver_for_each_blocker_fail():
    """ADOPTED인데 blocker FAIL이 남으면 게이트별 예외 승인이 있어야 하고, 메모에 예외 표가 나온다."""
    from dataclasses import replace

    from eval.adoption import blocker_waiver_errors

    proposed = record([classification_run("2026-10-29T00:00:00+00:00", violations=2)])
    assert blocker_waiver_errors(proposed) == []
    adopted = replace(proposed, status="ADOPTED")
    assert blocker_waiver_errors(adopted) == ["schema_violation_rate"]
    waiver = {
        "metric_key": "schema_violation_rate",
        "value": 0.2,
        "reason": "합성 정답 불일치 2건, 사람이 확인",
        "decided_by": "결정자",
        "decided_on": "2026-10-29",
    }
    waived = replace(adopted, waivers=[waiver])
    assert blocker_waiver_errors(waived) == []
    note = render_decision_note(waived)
    assert "blocker FAIL 1건은 사람이 예외로 승인해 채택했다" in note
    assert (
        "| schema_violation_rate | 0.2 | 합성 정답 불일치 2건, 사람이 확인 | 결정자 | 2026-10-29 |"
        in note
    )
    assert "예외 승인 없음." in render_decision_note(proposed)
    assert json.loads(waived.to_json())["waivers"] == [waiver]
