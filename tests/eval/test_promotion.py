"""KAN-147 불합격 설정 승격 차단. 첫 검사는 실제 저장소 설정과 채택 기록으로 도는 CI 관문이다."""

import copy

import pytest

from eval import promotion
from eval.adoption import AdoptionRecord


def test_the_repository_config_matches_adopted_records_or_known_exceptions():
    """운영 설정이 ADOPTED 기록과 다르거나 기록이 없으면, 예외 목록에 사유가 있어야 통과한다."""
    config = promotion.deployed_config()
    result = promotion.check(
        config,
        promotion.load_adoptions(),
        promotion.load_exceptions(),
        promotion.deploy_drift(config),
    )
    assert result["passed"], result
    # 알려진 문제는 숨지 않고 결과에 보인다.
    assert {(e["stage"], e["kind"], e["field"]) for e in result["excepted"]} == {
        ("classification", "MISMATCH", "model"),
        ("classification", "MISMATCH", "max_output_tokens"),
        ("draft", "NO_ADOPTION", None),
        ("rfp_extraction", "NO_ADOPTION", None),
    }


def record(task, adopted, *, status="ADOPTED", decided_on="2026-10-30", gates=(), waivers=()):
    return AdoptionRecord(
        task=task,
        adopted=adopted,
        evidence=[],
        gates={"gates": list(gates)},
        rejected=[],
        decided_by="합성",
        decided_on=decided_on,
        rationale="합성",
        status=status,
        waivers=list(waivers),
    )


CONFIG = {
    "classification": {"model": "m-a", "prompt_version": "v3"},
    "draft": {"model": "m-a", "prompt_version": "v1"},
    "embedding": {"model": "e-1", "dimension": 768},
    "rfp_extraction": {"model": "m-a", "extraction_version": "x2"},
}
RECORDS = [
    record("classification", {"model": "m-a", "prompt_version": "v3"}),
    record("draft", {"model": "m-a", "prompt_version": "v1"}),
    record("embedding", {"model": "e-1", "dimension": 768}),
    record("rfp", {"model": "m-a", "extraction_version": "x2"}),
]


def test_changing_a_setting_without_a_new_adopted_record_blocks_promotion():
    assert promotion.check(CONFIG, RECORDS, [])["passed"]
    changed = copy.deepcopy(CONFIG)
    changed["classification"]["prompt_version"] = "v4"
    result = promotion.check(changed, RECORDS, [])
    assert not result["passed"]
    assert result["blocked"] == [
        {
            "stage": "classification",
            "kind": "MISMATCH",
            "field": "prompt_version",
            "deployed": "v4",
            "adopted": "v3",
        }
    ]
    # 새 판본을 실측해 ADOPTED로 승인한 기록이 함께 들어오면 통과한다.
    newer = record(
        "classification", {"model": "m-a", "prompt_version": "v4"}, decided_on="2026-11-01"
    )
    assert promotion.check(changed, [*RECORDS, newer], [])["passed"]
    # 새 기록이 옛 기록을 대신한다. 옛 채택값(v3)으로 되돌리면 막힌다(#326 리뷰 P3).
    reverted = promotion.check(CONFIG, [*RECORDS, newer], [])
    assert [(i["field"], i["deployed"], i["adopted"]) for i in reverted["blocked"]] == [
        ("prompt_version", "v3", "v4")
    ]
    # PROPOSED(승인 전) 기록은 승격 근거가 아니다.
    proposed = record("classification", {"model": "m-a", "prompt_version": "v4"}, status="PROPOSED")
    assert not promotion.check(changed, [*RECORDS, proposed], [])["passed"]


def test_a_stage_without_an_adopted_record_is_blocked_unless_listed():
    records = [r for r in RECORDS if r.task != "rfp"]
    result = promotion.check(CONFIG, records, [])
    assert [(i["stage"], i["kind"]) for i in result["blocked"]] == [
        ("rfp_extraction", "NO_ADOPTION")
    ]
    listed = {
        "stage": "rfp_extraction",
        "kind": "NO_ADOPTION",
        "field": None,
        "deployed": CONFIG["rfp_extraction"],
        "reason": "합성",
        "ticket": "KAN-147",
        "recorded_on": "2026-10-30",
    }
    passed = promotion.check(CONFIG, records, [listed])
    assert passed["passed"] and passed["excepted"][0]["reason"] == "합성"
    # 채택 기록이 없는 단계도 설정을 또 바꾸면 막힌다(예외는 적어 둔 설정 그대로일 때만).
    changed = copy.deepcopy(CONFIG)
    changed["rfp_extraction"]["model"] = "m-untested"
    moved = promotion.check(changed, records, [listed])
    assert not moved["passed"] and moved["blocked"][0]["deployed"]["model"] == "m-untested"
    # 해소됐는데 남은 예외는 실패다(목록이 낡지 않게).
    stale = promotion.check(CONFIG, RECORDS, [listed])
    assert not stale["passed"] and stale["stale"] == [listed]


def test_an_unwaived_blocker_failure_cannot_be_excepted(tmp_path):
    failing = {"metric_key": "critical_miss_count", "status": "FAIL", "blocker": True}
    # ADOPTED 기록은 만들 때부터 승인 없는 blocker 불합격을 거부한다.
    with pytest.raises(ValueError, match="ADOPTION_BLOCKER_FAIL_WITHOUT_WAIVER"):
        record("classification", CONFIG["classification"], gates=[failing])
    path = tmp_path / "exceptions.json"
    path.write_text(
        '{"exceptions": [{"stage": "classification", "kind": "BLOCKER_UNWAIVED", '
        '"field": "critical_miss_count", "reason": "r", "ticket": "KAN-147", '
        '"recorded_on": "2026-10-30"}]}'
    )
    with pytest.raises(ValueError, match="PROMOTION_EXCEPTION_NOT_ALLOWED"):
        promotion.load_exceptions(path)
    path.write_text('{"exceptions": [{"stage": "classification", "kind": "MISMATCH"}]}')
    with pytest.raises(ValueError, match="PROMOTION_EXCEPTION_FIELDS"):
        promotion.load_exceptions(path)


@pytest.mark.parametrize(
    "text",
    ["no model here", "GEMINI_MODEL=a\nGEMINI_MODEL=b"],
)
def test_the_deploy_model_must_be_readable_in_one_place(tmp_path, text):
    script = tmp_path / "deploy.py"
    script.write_text(text)
    with pytest.raises(ValueError, match="PROMOTION_DEPLOY_MODEL_UNREADABLE"):
        promotion.deploy_model(script)


def test_a_known_mismatch_does_not_cover_a_further_change():
    """알려진 불일치(운영 m-b, 채택 m-a) 위에서 운영값을 또 바꾸면 막힌다(#326 리뷰 P2)."""
    known = {
        "stage": "classification",
        "kind": "MISMATCH",
        "field": "model",
        "deployed": "m-b",
        "adopted": "m-a",
        "reason": "합성",
        "ticket": "KAN-147",
        "recorded_on": "2026-10-30",
    }
    deployed = copy.deepcopy(CONFIG)
    deployed["classification"]["model"] = "m-b"
    assert promotion.check(deployed, RECORDS, [known])["passed"]
    deployed["classification"]["model"] = "m-untested"
    result = promotion.check(deployed, RECORDS, [known])
    assert not result["passed"]
    assert [i["deployed"] for i in result["blocked"]] == ["m-untested"]
    assert result["stale"] == [known]


def test_a_mismatch_exception_must_name_both_values(tmp_path):
    path = tmp_path / "exceptions.json"
    path.write_text(
        '{"exceptions": [{"stage": "classification", "kind": "MISMATCH", "field": "model", '
        '"reason": "r", "ticket": "KAN-147", "recorded_on": "2026-10-30"}]}'
    )
    with pytest.raises(ValueError, match="PROMOTION_EXCEPTION_FIELDS"):
        promotion.load_exceptions(path)


def test_settings_come_from_the_runtime_paths_not_the_local_environment(monkeypatch):
    """실행한 PC의 환경 변수가 검사 결과를 바꾸지 않는다. RFP 추출은 공통 모델이 아니라 추출 전용 설정이다."""
    before = promotion.deployed_config()
    monkeypatch.setenv("GEMINI_MODEL", "local-model")
    monkeypatch.setenv("TFT_EXTRACTION_MODEL", "local-model")
    monkeypatch.setenv("CS_EMBEDDING_MODEL", "local-model")
    assert promotion.deployed_config() == before
    from backend.api.tft.worker_processor import ProcessorSettings
    from backend.shared.ai import AISettings

    assert (
        before["rfp_extraction"]["model"]
        == ProcessorSettings.model_fields["extraction_model"].default
    )
    assert before["classification"]["model"] == AISettings.model_fields["gemini_model"].default


def test_a_deploy_script_overriding_the_default_model_is_reported(tmp_path):
    script = tmp_path / "deploy.py"
    script.write_text('",AI_PROVIDER=gemini,GEMINI_MODEL=m-deploy"')
    drift = promotion.deploy_drift(CONFIG, script)
    assert [(i.stage, i.kind, i.deployed, i.adopted) for i in drift] == [
        ("classification", "DEPLOY_DIFFERS", "m-deploy", "m-a"),
        ("draft", "DEPLOY_DIFFERS", "m-deploy", "m-a"),
    ]
    assert not promotion.check(CONFIG, RECORDS, [], drift)["passed"]
    script.write_text('"GEMINI_MODEL=m-a"')
    assert promotion.deploy_drift(CONFIG, script) == []


def test_the_classification_stage_reads_its_own_limit_and_keeps_the_shared_default():
    """분류는 전용 상한을 쓰고 공통 기본 1024는 그대로다. 전용 모델이 없으면 공통 모델이다."""
    from backend.agent.cs_runner import CLASSIFICATION_DEFAULTS
    from backend.shared.ai import AISettings

    config = promotion.deployed_config()
    shared = AISettings.model_construct()
    assert config["classification"]["max_output_tokens"] == 4096
    assert (
        config["classification"]["max_output_tokens"]
        == (CLASSIFICATION_DEFAULTS["max_output_tokens"])
    )
    assert shared.gemini_max_output_tokens == 1024
    assert config["classification"]["model"] == shared.gemini_model


def test_settings_missing_on_either_side_are_mismatches():
    """채택 기록에 없는 실행 설정과 실행 경로에 없는 채택 항목도 불일치다(채택 때 고정 안 한 설정 변경을 막는다)."""
    extra = copy.deepcopy(CONFIG)
    extra["classification"]["max_output_tokens"] = 1024
    result = promotion.check(extra, RECORDS, [])
    assert result["blocked"] == [
        {
            "stage": "classification",
            "kind": "MISMATCH",
            "field": "max_output_tokens",
            "deployed": 1024,
            "adopted": None,
        }
    ]
    records = [
        *RECORDS[:2],
        record("embedding", {"model": "e-1", "dimension": 768, "k": 5}),
        RECORDS[3],
    ]
    missing = promotion.check(CONFIG, records, [])
    assert [(i["field"], i["deployed"], i["adopted"]) for i in missing["blocked"]] == [
        ("k", None, 5)
    ]


def test_the_draft_prompt_version_comes_from_the_node_that_runs_it():
    """초안 판본은 그래프 노드가 실제로 쓰는 run_draft_attempt 기본값이다. 그래프가 판본을 넘기게 바뀌면 여기서 알린다."""
    from pathlib import Path

    from backend.engine.cs.prompt_registry import prompt_fingerprint

    config = promotion.deployed_config()
    assert config["draft"]["prompt_version"] == promotion.draft_prompt_version()
    assert config["draft"]["prompt_fingerprint"] == prompt_fingerprint(
        "draft", config["draft"]["prompt_version"]
    )
    graph = Path("backend/engine/cs/graph.py").read_text()
    assert "run_draft_attempt(" in graph and "prompt_version" not in graph


def test_editing_the_rfp_extraction_prompt_changes_the_checked_config(monkeypatch):
    from backend.engine.rfp import requirements

    before = promotion.deployed_config()["rfp_extraction"]
    monkeypatch.setattr(requirements, "PROMPT", requirements.PROMPT + "\n- one more rule")
    after = promotion.deployed_config()["rfp_extraction"]
    assert after["extraction_version"] == before["extraction_version"]
    assert after["prompt_fingerprint"] != before["prompt_fingerprint"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 공통 상한은 분류가 쓰지 않는다. 다른 호출을 위해 배포가 1024를 넣어도 불일치가 아니다.
        ('"GEMINI_MODEL=m-a,GEMINI_MAX_OUTPUT_TOKENS=1024"', []),
        ('"GEMINI_MODEL=m-a,CS_WORKER_CLASSIFICATION_MAX_OUTPUT_TOKENS=4096"', []),
        (
            '"GEMINI_MODEL=m-a,CS_WORKER_CLASSIFICATION_MAX_OUTPUT_TOKENS=256"',
            [("classification", "max_output_tokens", 256, 4096)],
        ),
        (
            '"GEMINI_MODEL=m-a,CS_WORKER_CLASSIFICATION_MODEL=m-lite"',
            [("classification", "model", "m-lite", "m-a")],
        ),
        (
            '"GEMINI_MODEL=m-a,TFT_EXTRACTION_MODEL=m-other"',
            [("rfp_extraction", "model", "m-other", "m-a")],
        ),
        (
            '"GEMINI_MODEL=m-a,TFT_EXTRACTION_MAX_OUTPUT_TOKENS=2048"',
            [("rfp_extraction", "max_output_tokens", 2048, 16384)],
        ),
    ],
)
def test_deploy_overrides_of_limits_and_the_extraction_model_are_reported(tmp_path, text, expected):
    config = copy.deepcopy(CONFIG)
    config["classification"]["max_output_tokens"] = 4096
    config["rfp_extraction"]["max_output_tokens"] = 16384
    script = tmp_path / "deploy.py"
    script.write_text(text)
    drift = promotion.deploy_drift(config, script)
    assert [(i.stage, i.field, i.deployed, i.adopted) for i in drift] == expected


def test_adoption_dates_must_be_iso(tmp_path):
    import json
    from dataclasses import asdict

    bad = asdict(record("embedding", {"model": "e-1"}, decided_on="2026-1-5"))
    (tmp_path / "x.json").write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="PROMOTION_ADOPTION_DATE"):
        promotion.load_adoptions(tmp_path)
