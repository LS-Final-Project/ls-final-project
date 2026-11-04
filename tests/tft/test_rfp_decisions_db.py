"""KAN-218·219·220 결정 패널 저장(전용 PostgreSQL). 행은 모두 합성이다."""

# ruff: noqa: F811

from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from tests.tft.test_rfp_schema_db import RERUNNABLE, admin, app_dsn, apply, chain  # noqa: F401


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
