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
    assert {(e["stage"], e["kind"]) for e in result["excepted"]} == {
        ("classification", "MISMATCH"),
        ("draft", "NO_ADOPTION"),
        ("rfp_extraction", "NO_ADOPTION"),
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
    record("draft", {"model": "m-a"}),
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
    script.write_text('",AI_PROVIDER=gemini,GEMINI_MODEL=m-deploy,GEMINI_MAX_OUTPUT_TOKENS=1024"')
    drift = promotion.deploy_drift(CONFIG, script)
    assert [(i.stage, i.kind, i.deployed, i.adopted) for i in drift] == [
        ("classification", "DEPLOY_DIFFERS", "m-deploy", "m-a"),
        ("draft", "DEPLOY_DIFFERS", "m-deploy", "m-a"),
    ]
    assert not promotion.check(CONFIG, RECORDS, [], drift)["passed"]
    script.write_text('"GEMINI_MODEL=m-a"')
    assert promotion.deploy_drift(CONFIG, script) == []
