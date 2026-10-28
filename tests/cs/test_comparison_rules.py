"""KAN-114 판정 전이 규칙을 계약 '상태 전이' 표 그대로 확인한다(DB 없음)."""

from datetime import UTC, datetime, timedelta
from itertools import product
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.api.cs.comparisons import examples, rules
from backend.api.cs.comparisons.schemas import ItemWrite, JudgementWrite
from backend.shared.errors import ApplicationError

NOW = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
JUDGEMENTS = ("UNCONFIRMED", "SUSPECTED", "CONFIRMED")
PROVENANCES = ("ai_draft", "human_input", "system_record")
# 계약 표의 행. 같은 판정 안에서 값·근거만 고치는 쓰기(마지막 세 행)는 표의 조건을 그대로 따른다.
CONTRACT = {
    ("UNCONFIRMED", "SUSPECTED"): {"ai_draft", "human_input"},
    ("UNCONFIRMED", "CONFIRMED"): {"human_input"},
    ("SUSPECTED", "CONFIRMED"): {"human_input"},
    ("CONFIRMED", "SUSPECTED"): {"human_input"},
    ("CONFIRMED", "UNCONFIRMED"): {"human_input"},
    ("SUSPECTED", "UNCONFIRMED"): {"human_input", "system_record"},
    ("UNCONFIRMED", "UNCONFIRMED"): {"human_input", "system_record"},
    ("SUSPECTED", "SUSPECTED"): {"ai_draft", "human_input"},
    ("CONFIRMED", "CONFIRMED"): {"human_input"},
}
KNOWN_CASE = {"evidence_id": "ev-1", "case_id": uuid4(), "known_at": NOW - timedelta(days=1)}
KNOWN_TICKET = {"evidence_id": "ev-2", "case_id": None, "known_at": NOW - timedelta(days=1)}
LATER_CASE = {"evidence_id": "ev-3", "case_id": uuid4(), "known_at": NOW + timedelta(days=1)}
EVIDENCE = {e["evidence_id"]: e for e in (KNOWN_CASE, KNOWN_TICKET, LATER_CASE)}


def item(judgement, **changes):
    row = {
        "kind": "CONDITION",
        "code": "application",
        "judgement": judgement,
        "unconfirmed_reason": "NO_EVIDENCE" if judgement == "UNCONFIRMED" else None,
        "baseline_missing_reason": None,
        "target_missing_reason": None,
        "evidence_ids": [] if judgement == "UNCONFIRMED" else ["ev-1"],
        "reason": "합성 사유",
    }
    return row | changes


def error(old, new, provenance, evidence=None, **changes):
    try:
        rules.check_item(old, item(new, **changes), provenance, evidence or EVIDENCE, NOW)
    except ApplicationError as exc:
        return exc.status_code, exc.code
    return None


@pytest.mark.parametrize(
    ("old", "new", "provenance"), list(product(JUDGEMENTS, JUDGEMENTS, PROVENANCES))
)
def test_every_transition_and_author_follows_the_contract_table(old, new, provenance):
    # 근거·사유를 채워 두어 남는 차이는 표의 허용 여부뿐이다(미확인 항목은 근거가 없다).
    allowed = provenance in CONTRACT.get((old, new), set())
    assert error(old, new, provenance) == (None if allowed else (409, "CS_INVALID_TRANSITION"))


def test_a_model_never_confirms():
    for old in JUDGEMENTS:
        assert error(old, "CONFIRMED", "ai_draft") == (409, "CS_INVALID_TRANSITION")


@pytest.mark.parametrize(
    ("old", "new", "changes", "code"),
    [
        # 확정: 이미 알려진 해결 사례 근거가 있어야 한다.
        ("SUSPECTED", "CONFIRMED", {"evidence_ids": ["ev-2"]}, "CS_EVIDENCE_REQUIRED"),
        ("SUSPECTED", "CONFIRMED", {"evidence_ids": ["ev-3"]}, "CS_EVIDENCE_REQUIRED"),
        ("SUSPECTED", "CONFIRMED", {"reason": None}, "CS_REASON_REQUIRED"),
        (
            "SUSPECTED",
            "CONFIRMED",
            {"target_missing_reason": "NOT_PROVIDED"},
            "CS_VALUE_NOT_PROVIDED",
        ),
        # 의심: 이미 알려진 근거가 있어야 하고 빈 값은 판단하지 않는다.
        ("UNCONFIRMED", "SUSPECTED", {"evidence_ids": []}, "CS_EVIDENCE_REQUIRED"),
        ("UNCONFIRMED", "SUSPECTED", {"evidence_ids": ["ev-3"]}, "CS_EVIDENCE_REQUIRED"),
        (
            "UNCONFIRMED",
            "SUSPECTED",
            {"baseline_missing_reason": "NOT_PROVIDED"},
            "CS_VALUE_NOT_PROVIDED",
        ),
        # 재검토: 확정을 내리거나 의심을 미확인으로 돌리는 사람은 사유를 적는다.
        ("CONFIRMED", "SUSPECTED", {"reason": None}, "CS_REASON_REQUIRED"),
        ("CONFIRMED", "UNCONFIRMED", {"reason": None}, "CS_REASON_REQUIRED"),
        ("SUSPECTED", "UNCONFIRMED", {"reason": None}, "CS_REASON_REQUIRED"),
        ("CONFIRMED", "CONFIRMED", {"reason": None}, "CS_REASON_REQUIRED"),
    ],
)
def test_conditions_of_the_table_are_422(old, new, changes, code):
    assert error(old, new, "human_input", **changes) == (422, code)


def test_conditions_that_pass():
    assert error("UNCONFIRMED", "SUSPECTED", "human_input", evidence_ids=["ev-2"]) is None
    assert error("UNCONFIRMED", "SUSPECTED", "ai_draft", reason=None) is None
    assert error("UNCONFIRMED", "UNCONFIRMED", "human_input", reason=None) is None
    # 아직 알려지지 않은 근거도 미확인 항목에는 남길 수 있다(NOT_YET_KNOWN).
    assert (
        error(
            "UNCONFIRMED",
            "UNCONFIRMED",
            "human_input",
            evidence_ids=["ev-3"],
            unconfirmed_reason="NOT_YET_KNOWN",
        )
        is None
    )


def test_the_system_lowers_a_suspicion_only_when_no_evidence_is_left():
    assert error("SUSPECTED", "UNCONFIRMED", "system_record", evidence_ids=[], reason=None) is None
    assert error("SUSPECTED", "UNCONFIRMED", "system_record", evidence_ids=["ev-2"]) == (
        409,
        "CS_INVALID_TRANSITION",
    )


def test_creation_starts_with_eight_unconfirmed_conditions():
    items = rules.initial_items(NOW)
    assert len(items) == 8 and {i["kind"] for i in items} == {"CONDITION"}
    assert {(i["judgement"], i["unconfirmed_reason"], i["provenance_kind"]) for i in items} == {
        ("UNCONFIRMED", "NO_EVIDENCE", "system_record")
    }


def side(value="정격 부하 운전", ids=("ev-1",)):
    return {"masked_value": value, "missing_reason": None, "evidence_ids": list(ids)}


def condition(code="application", judgement="SUSPECTED", **changes):
    body = {
        "kind": "CONDITION",
        "code": code,
        "judgement": judgement,
        "finding": "DIFFERS",
        "baseline": side(),
        "target": side("정격 상한 근처 운전"),
        "masked_summary": "합성 요약",
        "summary_missing_reason": None,
        "evidence_ids": ["ev-1"],
    }
    return body | changes


EVIDENCE_WRITE = {
    "evidence_id": "ev-1",
    "ticket_id": str(uuid4()),
    "case_id": None,
    "evidence_location": {"source": "ticket", "field": "body"},
}


@pytest.mark.parametrize(
    "body",
    [
        condition(baseline=side(ids=())),
        condition(target=side(ids=("ev-9",))),
        condition(code="unknown_field"),
        condition(finding=None),
        condition(judgement="UNCONFIRMED"),
        condition(baseline=None),
        {"kind": "CAUSE", "code": "FIRMWARE", "judgement": "SUSPECTED", "finding": "SAME"}
        | {"masked_summary": "x", "summary_missing_reason": None, "evidence_ids": ["ev-1"]},
        condition(masked_summary=None),
        condition(baseline={"masked_value": None, "missing_reason": "HIDDEN_BY_SCOPE"}),
    ],
)
def test_write_items_that_break_the_contract_are_rejected(body):
    with pytest.raises(ValidationError):
        ItemWrite.model_validate(body)


def test_write_bodies_reject_duplicates_and_mismatched_locations():
    with pytest.raises(ValidationError):
        JudgementWrite.model_validate({"version": 1, "items": [condition(), condition()]})
    with pytest.raises(ValidationError):
        JudgementWrite.model_validate(
            {
                "version": 1,
                "items": [condition()],
                "evidence": [EVIDENCE_WRITE | {"case_id": str(uuid4())}],
            }
        )


def write(*items, evidence=()):
    return JudgementWrite.model_validate(
        {"version": 1, "items": list(items), "evidence": list(evidence)}
    )


def test_next_version_replaces_only_the_written_items_and_adds_evidence():
    before = rules.initial_items(NOW)
    sources = {"ev-1": {"data_origin": "synthetic", "derivation_kind": "PROVIDED"} | KNOWN_TICKET}
    items, evidence, changes = rules.next_version(
        before, [], write(condition(), evidence=[EVIDENCE_WRITE]), sources, "human_input", None, NOW
    )
    by_code = {i["code"]: i for i in items}
    assert len(items) == 8 and by_code["application"]["judgement"] == "SUSPECTED"
    assert by_code["site_condition"] == before[1]
    assert [e["evidence_id"] for e in evidence] == ["ev-1"]
    assert evidence[0]["evidence_location"] == {"source": "ticket", "field": "body"}
    assert changes == {
        "CONDITION:application": {"from": "UNCONFIRMED", "to": "SUSPECTED", "reason": None}
    }
    cause = {"kind": "CAUSE", "code": "FIRMWARE", "judgement": "SUSPECTED"} | {
        "masked_summary": "합성",
        "summary_missing_reason": None,
        "evidence_ids": ["ev-1"],
    }
    items, _, _ = rules.next_version(items, evidence, write(cause), {}, "human_input", None, NOW)
    assert len(items) == 9 and items[-1]["kind"] == "CAUSE"


def test_next_version_rejects_reused_or_unknown_evidence():
    kept = [{"evidence_id": "ev-1", "data_origin": "synthetic"}]
    sources = {"ev-1": KNOWN_TICKET | {"data_origin": "synthetic", "derivation_kind": "PROVIDED"}}
    with pytest.raises(ApplicationError) as reused:
        rules.next_version(
            [],
            kept,
            write(condition(), evidence=[EVIDENCE_WRITE]),
            sources,
            "human_input",
            None,
            NOW,
        )
    assert reused.value.code == "CS_EVIDENCE_INVALID"
    with pytest.raises(ApplicationError) as unknown:
        rules.next_version(
            rules.initial_items(NOW), [], write(condition()), {}, "human_input", None, NOW
        )
    assert unknown.value.code == "CS_EVIDENCE_INVALID"


def test_sources_must_be_this_groups_tickets_and_cases():
    ticket, case = uuid4(), uuid4()
    group = {"product_code": "SYN-PRD-01", "fault_id": "FLT-901"}
    body = write(
        condition(),
        evidence=[
            EVIDENCE_WRITE
            | {"ticket_id": str(ticket), "case_id": str(case)}
            | {"evidence_location": {"source": "resolution_case", "field": "root_cause"}}
        ],
    )
    tickets = {ticket: {"product_code": "SYN-PRD-01"}}
    cases = {case: {"ticket_id": ticket, "fault_id": "FLT-901"}}
    rules.check_sources(body, tickets, cases, group)
    for broken_tickets, broken_cases in (
        ({}, cases),
        ({ticket: {"product_code": "OTHER"}}, cases),
        (tickets, {}),
        (tickets, {case: {"ticket_id": uuid4(), "fault_id": "FLT-901"}}),
        (tickets, {case: {"ticket_id": ticket, "fault_id": "FLT-902"}}),
    ):
        with pytest.raises(ApplicationError) as exc:
            rules.check_sources(body, broken_tickets, broken_cases, group)
        assert exc.value.code == "CS_EVIDENCE_INVALID"


def test_version_origin_follows_its_evidence():
    assert rules.data_origin([], "demo") == "demo"
    assert rules.data_origin([{"data_origin": "synthetic"}] * 2, "real") == "synthetic"
    assert (
        rules.data_origin([{"data_origin": "synthetic"}, {"data_origin": "real"}], "real") == "demo"
    )


@pytest.mark.parametrize(
    ("evidence_ids", "missing", "removed", "expected"),
    [
        ([], None, False, "NO_EVIDENCE"),
        (["ev-3"], None, False, "NOT_YET_KNOWN"),
        (["ev-3"], None, True, "NOT_YET_KNOWN"),  # 아직 모름이 제외 이력보다 앞선다.
        (["ev-1"], None, True, "EVIDENCE_REMOVED"),
        ([], None, True, "EVIDENCE_REMOVED"),
        (["ev-1"], "NOT_PROVIDED", True, "EVIDENCE_REMOVED"),
        (["ev-1"], "NOT_PROVIDED", False, "VALUE_NOT_PROVIDED"),
        (["ev-1", "ev-3"], "NOT_PROVIDED", False, "VALUE_NOT_PROVIDED"),
        (["ev-1"], None, False, None),
    ],
)
def test_the_unconfirmed_reason_follows_the_evidence(evidence_ids, missing, removed, expected):
    row = item("UNCONFIRMED", evidence_ids=evidence_ids, target_missing_reason=missing)
    keys = {"CONDITION:application"} if removed else set()
    assert rules.expected_reason(row, EVIDENCE, keys, NOW) == expected


def test_a_person_cannot_pick_an_unconfirmed_reason_the_evidence_denies():
    citing = {"evidence_ids": ["ev-1"]}
    assert error("SUSPECTED", "UNCONFIRMED", "human_input", **citing) == (
        422,
        "CS_REASON_MISMATCH",
    )
    with pytest.raises(ApplicationError) as exc:
        rules.check_item("CONFIRMED", item("UNCONFIRMED", **citing), "human_input", EVIDENCE, NOW)
    assert "SUSPECTED로 내리세요" in exc.value.message
    removed = {"unconfirmed_reason": "EVIDENCE_REMOVED"}
    assert error("UNCONFIRMED", "UNCONFIRMED", "human_input", **removed) == (
        422,
        "CS_REASON_MISMATCH",
    )
    rules.check_item(
        "UNCONFIRMED",
        item("UNCONFIRMED", **removed),
        "human_input",
        EVIDENCE,
        NOW,
        {"CONDITION:application"},
    )


CASE = examples.CASE_EVIDENCE
TICKET = examples.TICKET_EVIDENCE | {"known_at": NOW - timedelta(days=2)}
LATER = examples.TICKET_EVIDENCE | {"evidence_id": "ev-9", "known_at": NOW + timedelta(days=1)}


def remove(rows, evidence, evidence_id, removals=()):
    return rules.remove_evidence(rows, evidence, evidence_id, "합성 제외 사유", removals, NOW)


def test_removing_the_last_evidence_of_a_suspicion_lowers_it_by_the_system():
    [row], left, changes, affected = remove([examples.DIFFERS], [CASE], "ev-1")
    assert left == [] and affected == ["CONDITION:application"]
    assert (row["judgement"], row["finding"], row["unconfirmed_reason"]) == (
        "UNCONFIRMED",
        None,
        "EVIDENCE_REMOVED",
    )
    assert (row["provenance_kind"], row["decided_by"], row["decided_at"]) == (
        "system_record",
        None,
        NOW,
    )
    assert (row["baseline_value"], row["baseline_missing_reason"]) == (None, "NOT_PROVIDED")
    assert row["evidence_ids"] == [] and row["target_evidence_ids"] == []
    assert changes == {
        "evidence:ev-1": {"removed": True, "reason": "합성 제외 사유"},
        "CONDITION:application": {
            "from": "SUSPECTED",
            "to": "UNCONFIRMED",
            "reason": None,
            "by": "system_record",
        },
    }


def test_a_suspicion_with_only_later_evidence_left_waits_for_it():
    rows = [examples.DIFFERS | {"evidence_ids": ["ev-1", "ev-9"]}]
    [row], _, _, _ = remove(rows, [CASE, LATER], "ev-1")
    assert (row["judgement"], row["unconfirmed_reason"]) == ("UNCONFIRMED", "NOT_YET_KNOWN")


def test_a_judgement_that_still_holds_is_kept():
    rows = [examples.HIDDEN | {"evidence_ids": ["ev-1", "ev-2", "ev-x"]}]
    extra = TICKET | {"evidence_id": "ev-x"}
    [row], left, changes, _ = remove(rows, [CASE, TICKET, extra], "ev-x")
    assert row["judgement"] == "CONFIRMED" and row["evidence_ids"] == ["ev-1", "ev-2"]
    assert [e["evidence_id"] for e in left] == ["ev-1", "ev-2"]
    assert list(changes) == ["evidence:ev-x"]


@pytest.mark.parametrize(
    ("rows", "evidence", "evidence_id"),
    [
        # 확정의 유일한 해결 사례 근거: 사람 확정은 시스템이 지우지 않는다.
        ([examples.HIDDEN], [CASE, TICKET], "ev-1"),
        # 대상 값의 유일한 근거: 확정 조건(NOT_PROVIDED 없음)이 깨진다.
        ([examples.HIDDEN], [CASE, TICKET], "ev-2"),
        # 알려진 근거가 남은 의심인데 한쪽 값이 비게 된다: 시스템은 근거가 남은 의심을 못 내린다.
        (
            [
                examples.DIFFERS
                | {"evidence_ids": ["ev-1", "ev-2"], "target_evidence_ids": ["ev-2"]}
            ],
            [CASE, TICKET],
            "ev-2",
        ),
    ],
)
def test_removals_that_break_a_judgement_need_a_review_first(rows, evidence, evidence_id):
    with pytest.raises(ApplicationError) as exc:
        remove(rows, evidence, evidence_id)
    assert (exc.value.status_code, exc.value.code) == (409, "CS_INVALID_TRANSITION")


def test_unconfirmed_items_and_signals_follow_the_removal():
    waiting = examples.UNCONFIRMED  # VALUE_NOT_PROVIDED, 근거 ev-1
    rows, _, changes, affected = remove(
        [waiting, examples.SIGNAL], [CASE, examples.DOCUMENT_EVIDENCE], "ev-1"
    )
    assert rows[0]["unconfirmed_reason"] == "EVIDENCE_REMOVED"
    assert changes["CONDITION:operating_condition"]["from"] == "UNCONFIRMED"
    assert rows[1] == examples.SIGNAL and affected == ["CONDITION:operating_condition"]
    _, _, _, signal = remove([examples.SIGNAL], [examples.DOCUMENT_EVIDENCE], "ev-3")
    assert signal == ["DOCUMENT_SIGNAL:VALUE_CONFLICT"]


def test_unknown_or_removed_evidence_is_not_found_and_never_reused():
    with pytest.raises(ApplicationError) as exc:
        remove([examples.DIFFERS], [CASE], "ev-7")
    assert (exc.value.status_code, exc.value.code) == (404, "CS_EVIDENCE_NOT_FOUND")
    removals = [{"evidence_id": "ev-1", "item_keys": ["CONDITION:application"]}]
    sources = {"ev-1": KNOWN_TICKET | {"data_origin": "synthetic", "derivation_kind": "PROVIDED"}}
    with pytest.raises(ApplicationError) as reused:
        rules.next_version(
            rules.initial_items(NOW),
            [],
            write(condition(), evidence=[EVIDENCE_WRITE]),
            sources,
            "human_input",
            None,
            NOW,
            removals,
        )
    assert reused.value.code == "CS_EVIDENCE_INVALID"
