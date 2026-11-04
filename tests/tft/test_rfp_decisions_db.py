"""KAN-218·219·220 결정 패널 저장(전용 PostgreSQL). 행은 모두 합성이다."""

# ruff: noqa: F811

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from tests.tft.test_rfp_http_base_db import admin, app_dsn, chain  # noqa: F401
from tests.tft.test_rfp_routes_db import OUTSIDER, OWNER, call, client, gap  # noqa: F401
from tests.tft.test_rfp_schema_db import RERUNNABLE, apply


def decision_row(conn, chain, *, action="confirm_met", undoes=None, key=None):
    decision = uuid4()
    conn.execute(
        "INSERT INTO tft.requirement_decisions (id, project_id, requirement_id, base_version, "
        "result_version, action, summary, undoes, actor_user_id, idempotency_key, request_hash, "
        "result) VALUES (%s,%s,%s,1,1,%s,'충족 확정',%s,%s,%s,%s,'{}')",
        (
            decision,
            chain["project"],
            chain["requirement"],
            action,
            undoes,
            uuid4(),
            key or uuid4().hex,
            "a" * 64,
        ),
    )
    return decision


def test_decision_tables_survive_entire_migration_reapplication(admin, chain):
    """이전 마이그레이션이 다시 적용돼도 결정·편차·철회 행이 그대로다(새 CHECK 값을 쓰지 않으므로)."""
    with psycopg.connect(admin) as conn:
        decision = decision_row(conn, chain, action="record_deviation")
        conn.execute(
            "INSERT INTO tft.deviations (id, project_id, requirement_id, requirement_version, text, "
            "state, decision_id, decided_by) VALUES (%s,%s,%s,1,'기준온도 120 °C 값만 보유','RECORDED',%s,%s)",
            (uuid4(), chain["project"], chain["requirement"], decision, uuid4()),
        )
        question = uuid4()
        conn.execute(
            "INSERT INTO tft.customer_questions (id, project_id, requirement_id, issue_type, "
            "question_text, target, data_origin, derivation_kind, known_at, created_by) "
            "VALUES (%s,%s,%s,'EMPTY_VALUE','값을 알려 주십시오','CUSTOMER','synthetic','PROVIDED',now(),%s)",
            (question, chain["project"], chain["requirement"], uuid4()),
        )
        conn.execute(
            "INSERT INTO tft.question_withdrawals (question_id, decision_id, withdrawn_by) "
            "VALUES (%s,%s,%s)",
            (question, decision, uuid4()),
        )
    fingerprint = (
        "SELECT (SELECT count(*) FROM tft.requirement_decisions WHERE requirement_id=%(r)s), "
        "(SELECT json_agg(text) FROM tft.deviations WHERE requirement_id=%(r)s), "
        "(SELECT count(*) FROM tft.question_withdrawals WHERE question_id=%(q)s)"
    )
    with psycopg.connect(admin, autocommit=True) as conn:
        before = conn.execute(fingerprint, {"r": chain["requirement"], "q": question}).fetchone()
        for name in RERUNNABLE:
            apply(conn, Path("infra/migrations/tft") / name)
        after = conn.execute(fingerprint, {"r": chain["requirement"], "q": question}).fetchone()
    assert before == after == (1, ["기준온도 120 °C 값만 보유"], 1)


def test_decision_rows_are_append_only_and_undo_names_its_target(admin, app_dsn, chain):
    with psycopg.connect(admin) as conn:
        decision = decision_row(conn, chain)
    with psycopg.connect(app_dsn) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "UPDATE tft.requirement_decisions SET summary='x' WHERE id=%s", (decision,)
            )
    with psycopg.connect(admin) as conn:
        # 되돌리기는 대상이 있어야 하고, 다른 결정은 대상이 없어야 한다.
        with pytest.raises(psycopg.errors.CheckViolation):
            decision_row(conn, chain, action="undo")
    with psycopg.connect(admin) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            decision_row(conn, chain, action="assign", undoes=decision)
    with psycopg.connect(admin) as conn:
        assert decision_row(conn, chain, action="undo", undoes=decision)
        rows = conn.execute(
            "SELECT action FROM tft.requirement_decisions WHERE requirement_id=%s ORDER BY created_at",
            (chain["requirement"],),
        ).fetchall()
    assert [r[0] for r in rows][-2:] == ["confirm_met", "undo"]


def decide(client, chain, action, version=1, key=None, who=OWNER, **body):
    url = f"/requirements/{chain['requirement']}/decision"
    payload = {"version": version, "action": action, **body}
    return call(client, "POST", url, who, payload, key or uuid4().hex)


def ok(response):
    assert response.status_code == 200, response.text
    return response.json()["data"]


def latest(admin, chain):
    with psycopg.connect(admin) as conn:
        return conn.execute(
            "SELECT v.version, v.status, v.next_owner_field, v.provenance_kind "
            "FROM tft.requirements r JOIN tft.requirement_versions v "
            "ON v.requirement_id=r.id AND v.version=r.latest_version WHERE r.id=%s",
            (chain["requirement"],),
        ).fetchone()


def test_confirm_met_approves_at_once_and_replays_the_same_key(client, chain, admin):
    key = uuid4().hex
    first = ok(decide(client, chain, "confirm_met", key=key))
    assert (first["summary"], first["requirement_version"]) == ("충족 확정", 2)
    assert latest(admin, chain)[:2] == (2, "EVIDENCE_FOUND")
    assert ok(decide(client, chain, "confirm_met", key=key)) == first
    conflict = decide(client, chain, "confirm_met", version=2, key=key)
    assert conflict.status_code == 409
    with psycopg.connect(admin) as conn:
        statuses = conn.execute(
            "SELECT status FROM tft.change_proposals WHERE requirement_id=%s",
            (chain["requirement"],),
        ).fetchall()
        actions = conn.execute(
            "SELECT action FROM tft.rfp_audit_events WHERE entity_id IN "
            "(SELECT id::text FROM tft.change_proposals WHERE requirement_id=%s)",
            (chain["requirement"],),
        ).fetchall()
    # 제안이 승인 대기로 남지 않고, 작성과 승인 감사가 같이 남는다.
    assert statuses == [("APPROVED",)]
    assert sorted(a[0] for a in actions) == ["proposal.approve", "proposal.create"]
    # 읽은 판본이 낡으면 409, 범위 밖 사용자는 404다.
    assert decide(client, chain, "confirm_met").status_code == 409
    assert decide(client, chain, "confirm_met", version=2, who=OUTSIDER).status_code == 404


def test_deviation_is_recorded_and_undo_withdraws_it(client, chain, admin):
    made = ok(decide(client, chain, "record_deviation", text="기준온도 120 °C 값만 보유"))
    assert made["requirement_version"] == 1
    undone = ok(decide(client, chain, "undo", decision_id=made["decision_id"]))
    assert undone["summary"] == "되돌림: 편차표에 기재"
    again = decide(client, chain, "undo", decision_id=made["decision_id"])
    assert again.status_code == 409
    with psycopg.connect(admin) as conn:
        rows = conn.execute(
            "SELECT state FROM tft.deviations WHERE requirement_id=%s ORDER BY created_at",
            (chain["requirement"],),
        ).fetchall()
    assert [r[0] for r in rows] == ["RECORDED", "WITHDRAWN"]
    assert decide(client, chain, "record_deviation").status_code == 422


def test_check_request_and_rnd_question_go_to_review_departments(client, chain, admin):
    refused = decide(client, chain, "request_check", text="성적서 확인", target_dept="CERT")
    assert refused.status_code == 422
    made = ok(decide(client, chain, "request_check", text="성적서 확인", target_dept="TS_TRF"))
    assert made["summary"] == "기술지원에 확인 요청"
    rnd = ok(decide(client, chain, "ask_rnd"))
    assert rnd["summary"] == "R&D에 개발 문의"
    with psycopg.connect(admin) as conn:
        rows = conn.execute(
            "SELECT issue_type, target, target_dept, status FROM tft.customer_questions "
            "WHERE id = ANY(%s) ORDER BY issue_type",
            ([made["question_id"], rnd["question_id"]],),
        ).fetchall()
    assert rows == [
        ("RND_FEASIBILITY", "INTERNAL", "RND_FW", "OPEN"),
        ("TECH_ADVICE", "INTERNAL", "TS_TRF", "OPEN"),
    ]


def test_query_confirms_the_draft_or_replaces_an_edited_one(client, chain, admin):
    def draft(text):
        question = uuid4()
        with psycopg.connect(admin) as conn:
            conn.execute(
                "INSERT INTO tft.customer_questions (id, project_id, requirement_id, clause_id, "
                "issue_type, question_text, target, status, data_origin, derivation_kind, known_at, "
                "created_by) VALUES (%s,%s,%s,%s,'EMPTY_VALUE',%s,'CUSTOMER','DRAFT','synthetic',"
                "'DERIVED',now(),%s)",
                (question, chain["project"], chain["requirement"], chain["clause"], text, uuid4()),
            )
        return question

    first = draft("값을 알려 주십시오")
    same = ok(decide(client, chain, "add_query"))
    assert same["question_id"] == str(first)
    second = draft("값을 알려 주십시오")
    edited = ok(decide(client, chain, "add_query", text="시험 전압과 시간을 알려 주십시오"))
    assert edited["question_id"] != str(second)
    with psycopg.connect(admin) as conn:
        statuses = dict(
            conn.execute(
                "SELECT question_text, status FROM tft.customer_questions WHERE id = ANY(%s::uuid[])",
                ([str(first), edited["question_id"]],),
            ).fetchall()
        )
        withdrawn = conn.execute(
            "SELECT question_id FROM tft.question_withdrawals WHERE question_id=%s", (second,)
        ).fetchone()
    assert statuses == {"값을 알려 주십시오": "OPEN", "시험 전압과 시간을 알려 주십시오": "OPEN"}
    assert withdrawn == (second,)


def test_partner_selection_uses_the_supplement_rules(client, gap):
    refused = decide(client, gap, "select_partner", text="S-A 조립", candidate_codes=["P-A", "P-Z"])
    assert refused.status_code == 422
    made = ok(decide(client, gap, "select_partner", text="P-A 조립", candidate_codes=["P-A"]))
    assert made["summary"] == "P-A 선정"


def test_assignment_sets_the_owner_department_and_undo_restores_it(client, chain, admin):
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.requirement_versions SET kind='TASK', next_owner_field='SALES' "
            "WHERE requirement_id=%s",
            (chain["requirement"],),
        )
        conn.execute(
            "INSERT INTO tft.supplement_options (id, requirement_id, requirement_version, "
            "deficiency_cause, customer_permission, path, allowed, candidate_codes, data_origin, "
            "derivation_kind, known_at) VALUES (%s,%s,1,'TASK','UNSTATED','TASK_SUBCONTRACT',true,"
            "'{}','synthetic','DERIVED',now())",
            (uuid4(), chain["requirement"]),
        )
    made = ok(decide(client, chain, "assign", holder="OWN", owner="TECH"))
    assert made["summary"] == "자사 수행, 기술 담당"
    assert latest(admin, chain)[::2] == (2, "TECH")
    ok(decide(client, chain, "undo", version=2, decision_id=made["decision_id"]))
    assert latest(admin, chain)[::2] == (3, "SALES")
    partner = ok(decide(client, chain, "assign", version=3, holder="PARTNER", owner="SALES"))
    assert (partner["summary"], partner["requirement_version"]) == ("협력사 수행, 영업 담당", 3)
    # 공동 수행은 과업 요구의 보완 경로가 아니라 받지 않는다.
    assert (
        decide(client, chain, "assign", version=3, holder="JOINT", owner="TECH").status_code == 422
    )


def test_verdict_change_writes_a_judgement_and_assign_needs_a_task(client, chain, admin):
    assert decide(client, chain, "change_verdict", verdict="ASSIGN").status_code == 422
    made = ok(decide(client, chain, "change_verdict", verdict="DEVIATION"))
    assert made["summary"] == "판정 변경: 편차"
    assert latest(admin, chain)[:2] == (2, "SPEC_GAP")
