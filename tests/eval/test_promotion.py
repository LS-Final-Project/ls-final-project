"""KAN-147 불합격 설정 승격 차단. 첫 검사는 실제 저장소 설정과 채택 기록으로 도는 CI 관문이다."""

import copy

import pytest

from eval import promotion
from eval.adoption import AdoptionRecord


def test_the_repository_config_matches_adopted_records_or_known_exceptions():
    """운영 설정이 ADOPTED 기록과 다르거나 기록이 없으면, 예외 목록에 사유가 있어야 통과한다."""
    result = promotion.check(
        promotion.deployed_config(), promotion.load_adoptions(), promotion.load_exceptions()
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
    # 새 판본을 실측해 ADOPTED로 승인한 기록이 함께 들어오면 통과한다(옛 기록이 남아 있어도 된다).
    newer = record(
        "classification", {"model": "m-a", "prompt_version": "v4"}, decided_on="2026-11-01"
    )
    assert promotion.check(changed, [*RECORDS, newer], [])["passed"]
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
        "reason": "합성",
        "ticket": "KAN-147",
        "recorded_on": "2026-10-30",
    }
    passed = promotion.check(CONFIG, records, [listed])
    assert passed["passed"] and passed["excepted"][0]["reason"] == "합성"
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
