"""KAN-189 PR-A RFP HTTP 기반(접근·저장소·멱등)을 실제 마이그레이션의 전용 PostgreSQL에서 tft_app으로 확인한다.

행은 모두 합성이다. 마이그레이션 적용 방식은 tests/tft/test_rfp_schema_db.py와 같다.
"""

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.sql import SQL, Identifier

from backend.api.tft.projects.access import can_create_question, can_reply, can_view_rfp
from backend.api.tft.rfp import commands, repository
from backend.shared.access import Principal
from backend.shared.errors import ApplicationError
from tests.tft.test_rfp_schema_db import MIGRATIONS, RERUNNABLE, apply

NOW = datetime(2026, 10, 30, tzinfo=UTC)
SALES, RESPONSIBLE, OUTSIDER = uuid4(), uuid4(), uuid4()


@pytest.fixture(scope="module")
def admin():
    dsn = os.getenv("AUTH_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("전용 PostgreSQL 검사는 AUTH_TEST_DATABASE_URL이 필요합니다.")
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.info.dbname == "kan77_auth_test", "전용 테스트 DB만 사용할 수 있습니다."
        for role in ("tft_migrator", "tft_app", "cs_app", "platform_app"):
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                conn.execute(SQL("CREATE ROLE {} NOLOGIN").format(Identifier(role)))
        conn.execute("ALTER ROLE tft_app LOGIN PASSWORD 'test-only'")
        if conn.execute("SELECT to_regnamespace('tft')").fetchone()[0] is None:
            conn.execute("CREATE SCHEMA tft AUTHORIZATION tft_migrator")
            conn.execute("REVOKE ALL ON SCHEMA tft FROM PUBLIC")
            conn.execute("GRANT USAGE ON SCHEMA tft TO tft_app")
            conn.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE tft_migrator IN SCHEMA tft "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tft_app"
            )
        if conn.execute("SELECT to_regclass('tft.projects')").fetchone()[0] is None:
            for name in MIGRATIONS[:2]:
                apply(conn, Path("infra/migrations/tft") / name)
        for name in RERUNNABLE:
            apply(conn, Path("infra/migrations/tft") / name)
    return dsn


@pytest.fixture
def app_dsn(admin):
    return make_conninfo(admin, user="tft_app", password="test-only")


@pytest.fixture
def chain(admin):
    """검토 부서(RND_FW·TS_TRF)와 담당 sales·책임자가 있는 프로젝트 → 요구 판본 1까지 만든다."""
    ids = {k: uuid4() for k in ("project", "document", "docver", "clause", "requirement")}
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.projects (id, source, source_instance, data_origin, derivation_kind, "
            "known_at, company_code, name, sales_user_id, responsible_user_id, review_departments) "
            "VALUES (%s,'test','kan189','synthetic','PROVIDED',%s,'HOST','합성 프로젝트',%s,%s,%s)",
            (ids["project"], NOW, SALES, RESPONSIBLE, ["RND_FW", "TS_TRF"]),
        )
        conn.execute(
            "INSERT INTO tft.documents (id, project_id, kind, title, data_origin, derivation_kind, "
            "known_at, created_by) VALUES (%s,%s,'RFP_SPEC','합성 규격서','synthetic','PROVIDED',%s,%s)",
            (ids["document"], ids["project"], NOW, uuid4()),
        )
        conn.execute(
            "INSERT INTO tft.document_versions (id, document_id, version, mime, storage_key, sha256, "
            "size, edit_trace_support, data_origin, derivation_kind, known_at, uploaded_by) "
            "VALUES (%s,%s,1,'application/vnd.openxmlformats-officedocument.wordprocessingml.document',"
            "'k',%s,10,true,'synthetic','PROVIDED',%s,%s)",
            (ids["docver"], ids["document"], "0" * 64, NOW, uuid4()),
        )
        conn.execute(
            "INSERT INTO tft.clauses (id, document_version_id, clause_no, depth, ordinal, text, "
            "data_origin, derivation_kind, known_at) VALUES (%s,%s,'2.5.C',2,0,'BIL 95 kV',"
            "'synthetic','DERIVED',%s)",
            (ids["clause"], ids["docver"], NOW),
        )
        conn.execute(
            "INSERT INTO tft.requirements (id, project_id, code, data_origin, derivation_kind, known_at) "
            "VALUES (%s,%s,'R-1','synthetic','DERIVED',%s)",
            (ids["requirement"], ids["project"], NOW),
        )
        conn.execute(
            "INSERT INTO tft.requirement_versions (requirement_id, version, document_version_id, "
            "clause_id, quote, operator, standard_system, necessity, status, reason, provenance_kind, "
            "data_origin, derivation_kind, known_at) VALUES (%s,1,%s,%s,'BIL 95 kV','GTE','IEEE',"
            "'MANDATORY','SPEC_GAP','자사 정격 부족','ai_draft','synthetic','DERIVED',%s)",
            (ids["requirement"], ids["docver"], ids["clause"], NOW),
        )
    return ids


def run(dsn, work):
    """tft_app 연결 하나에서 work(conn)를 돌리고 커밋한다(트랜잭션마다 now()가 다르다).

    Args:
        dsn: tft_app DSN.
        work: async 함수(conn) → 결과.

    Returns:
        object: work 결과.
    """

    async def main():
        async with await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
            return await work(conn)

    return asyncio.run(main())


def person(user_id=None, roles=("sales",), dept=None):
    return Principal(user_id=user_id or uuid4(), roles=frozenset(roles), dept=dept)


def internal_question(chain, dept="RND_FW", issue="RND_FEASIBILITY"):
    async def work(conn):
        project = await repository.project_scope(conn, chain["project"])
        return await repository.insert_question(
            conn,
            project=project,
            requirement_id=chain["requirement"],
            clause_id=chain["clause"],
            page=None,
            issue_type=issue,
            question_text="15 kV 정격 개발 가능 여부",
            internal_assumption=None,
            target="INTERNAL",
            target_dept=dept,
            actor=SALES,
        )

    return work


def reply(question, version, feasibility="FEASIBLE", **extra):
    async def work(conn):
        locked = await repository.get_question(conn, question["id"], lock=True)
        return await repository.insert_reply(
            conn,
            locked,
            version=version,
            reply_text="가능",
            received_at=NOW,
            source_ref=None,
            rnd_feasibility=feasibility,
            earliest_available_on=extra.get("on"),
            schedule_fits=extra.get("fits"),
            actor=OUTSIDER,
        )

    return work


def select(chain, version=1, waiver=None, path="EXTERNAL_PRODUCT"):
    async def work(conn):
        requirement = await repository.latest_requirement_version(
            conn, chain["project"], chain["requirement"], lock=True
        )
        state, _ = await repository.rnd_check(conn, chain["requirement"])
        return await repository.insert_selection(
            conn,
            requirement,
            version=version,
            path=path,
            candidate_codes=["P-A"],
            role_split="협력사가 조립품을 공급한다",
            conditions=[],
            note=None,
            rnd_check=state,
            waiver_reason=waiver,
            actor=SALES,
        )

    return work


def test_review_department_staff_can_read_and_internal_replies_follow_the_target(app_dsn, chain):
    """검토 부서 dept_staff는 조회·질문할 수 있고, INTERNAL 회신은 대상 부서·담당 sales·책임자만, CUSTOMER는 sales만."""
    project = run(app_dsn, lambda conn: repository.project_scope(conn, chain["project"]))
    rnd = person(roles=("dept_staff",), dept="RND_FW")
    tech = person(roles=("dept_staff",), dept="TS_TRF")
    other = person(roles=("dept_staff",), dept="CERT")
    sales, responsible = person(SALES), person(RESPONSIBLE, roles=("manager",))
    assert [can_view_rfp(p, project) for p in (rnd, tech, other, sales)] == [
        True,
        True,
        False,
        True,
    ]
    assert can_create_question(tech, project) and not can_create_question(other, project)
    internal = {"target": "INTERNAL", "target_dept": "RND_FW"}
    assert [can_reply(p, project, internal) for p in (rnd, tech, sales, responsible)] == [
        True,
        False,
        True,
        True,
    ]
    customer = {"target": "CUSTOMER", "target_dept": None}
    assert [can_reply(p, project, customer) for p in (sales, responsible, rnd)] == [
        True,
        False,
        False,
    ]


def test_internal_question_reply_round_trip_and_stale_versions(app_dsn, chain):
    """INTERNAL 질문을 저장·필터하고, 회신은 REPLIED·판본+1과 R&D 칸을 남기며 다시 보내거나 옛 판본이면 409다."""
    question = run(app_dsn, internal_question(chain))
    assert (question["status"], question["version"], question["target_dept"]) == (
        "OPEN",
        1,
        "RND_FW",
    )
    found = run(
        app_dsn,
        lambda conn: repository.list_questions(
            conn, chain["project"], target="INTERNAL", target_dept="RND_FW"
        ),
    )
    assert [row["id"] for row in found] == [question["id"]]
    assert (
        run(
            app_dsn,
            lambda conn: repository.list_questions(conn, chain["project"], target="CUSTOMER"),
        )
        == []
    )
    with pytest.raises(ApplicationError) as stale:
        run(app_dsn, reply(question, version=2))
    assert stale.value.code == "TFT_VERSION_CONFLICT"
    replied = run(
        app_dsn,
        reply(question, 1, "FEASIBLE_WITH_SCHEDULE", on=datetime(2027, 3, 1).date(), fits=True),
    )
    assert (replied["status"], replied["version"], replied["rnd_feasibility"]) == (
        "REPLIED",
        2,
        "FEASIBLE_WITH_SCHEDULE",
    )
    with pytest.raises(ApplicationError) as again:
        run(app_dsn, reply(replied, version=2))
    assert again.value.code == "TFT_VERSION_CONFLICT"


def test_rnd_check_is_computed_from_questions_answers_and_waiver(app_dsn, chain):
    """rnd_check는 저장값이 아니라 R&D 질문·답변·면제 선택에서 계산한다."""

    def state():
        return run(app_dsn, lambda conn: repository.rnd_check(conn, chain["requirement"]))

    assert state() == ("NOT_REQUIRED", None)
    first = run(app_dsn, internal_question(chain))
    assert state() == ("PENDING", first["id"])
    run(app_dsn, reply(first, 1, "FEASIBLE"))
    assert state()[0] == "CONFIRMED_FEASIBLE"
    second = run(app_dsn, internal_question(chain))
    assert state() == ("PENDING", second["id"])
    selected = run(app_dsn, select(chain, waiver="R&D 회신 지연으로 협력사 경로를 먼저 검토"))
    assert (selected["rnd_check"], selected["rnd_check_waiver_reason"] is not None) == (
        "WAIVED",
        True,
    )
    assert state()[0] == "WAIVED"
    # 열 원천이 아니므로 요구 판본의 T10 열은 그대로다.
    with psycopg.connect(app_dsn) as conn:
        assert conn.execute(
            "SELECT rnd_check FROM tft.requirement_versions WHERE requirement_id=%s",
            (chain["requirement"],),
        ).fetchone() == ("NOT_REQUIRED",)


def test_selections_stack_versions_and_refuse_a_stale_requirement_version(app_dsn, chain):
    """보완 선택은 판본을 쌓고, 읽은 요구 판본이 최신과 다르면 409다."""
    first = run(app_dsn, select(chain))
    second = run(app_dsn, select(chain, path="OWN_DEVELOPMENT"))
    assert [(r["version"], r["requirement_version"], r["path"]) for r in (first, second)] == [
        (1, 1, "EXTERNAL_PRODUCT"),
        (2, 1, "OWN_DEVELOPMENT"),
    ]
    assert first["rnd_check"] == "NOT_REQUIRED" and first["decided_by"] == SALES
    with pytest.raises(ApplicationError) as stale:
        run(app_dsn, select(chain, version=2))
    assert stale.value.code == "TFT_VERSION_CONFLICT"
    latest = run(app_dsn, lambda conn: repository.latest_selection(conn, chain["requirement"]))
    assert latest["version"] == 2


def test_needs_rereview_when_an_rnd_answer_follows_the_selection(app_dsn, admin, chain):
    """선택 뒤에 R&D 답이 오면 재검토, 새 선택이 생기면 꺼지고, 해소 안 된 표시도 재검토다."""

    def needs():
        return run(app_dsn, lambda conn: repository.needs_rereview(conn, chain["requirement"]))

    question = run(app_dsn, internal_question(chain))
    run(app_dsn, reply(question, 1, "NOT_FEASIBLE"))
    assert not needs()  # 보완 선택이 아직 없다.
    run(app_dsn, select(chain))
    assert not needs()  # 답이 선택보다 먼저다.
    late = run(app_dsn, internal_question(chain))
    run(app_dsn, reply(late, 1, "FEASIBLE"))
    assert needs()
    run(app_dsn, select(chain))
    assert not needs()
    proposal = uuid4()
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.change_proposals (id, project_id, requirement_id, origin, current, "
            "proposed, rationale, proposer_kind, data_origin, derivation_kind, known_at) VALUES "
            "(%s,%s,%s,'web','{}','{}','합성','system','synthetic','DERIVED',now())",
            (proposal, chain["project"], chain["requirement"]),
        )
        conn.execute(
            "INSERT INTO tft.rereview_marks (id, project_id, target_kind, target_id, cause_proposal_id) "
            "VALUES (%s,%s,'requirement',%s,%s)",
            (uuid4(), chain["project"], chain["requirement"], proposal),
        )
    assert needs()
    proposals = run(app_dsn, lambda conn: repository.list_change_proposals(conn, chain["project"]))
    assert [row["id"] for row in proposals] == [proposal]


def test_commands_replay_the_same_request_and_refuse_a_reused_key(app_dsn, chain):
    """같은 키·같은 요청은 저장 결과를 재생, 같은 키·다른 요청은 409, 연산이 다르면 따로 센다."""
    actor, key = uuid4(), "key-" + uuid4().hex
    digest = commands.request_hash({"project_id": chain["project"]}, {"question_text": "a"})
    other = commands.request_hash({"project_id": chain["project"]}, {"question_text": "b"})
    assert digest != other

    async def first(conn):
        assert (
            await commands.replay(
                conn, actor=actor, operation="question.create", key=key, digest=digest
            )
            is None
        )
        await commands.remember(
            conn,
            actor=actor,
            operation="question.create",
            key=key,
            digest=digest,
            project_id=chain["project"],
            status_code=201,
            result={"question_id": "q-1"},
        )

    run(app_dsn, first)
    again = run(
        app_dsn,
        lambda conn: commands.replay(
            conn, actor=actor, operation="question.create", key=key, digest=digest
        ),
    )
    assert again == (201, {"question_id": "q-1"})
    with pytest.raises(ApplicationError) as conflict:
        run(
            app_dsn,
            lambda conn: commands.replay(
                conn, actor=actor, operation="question.create", key=key, digest=other
            ),
        )
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    assert (
        run(
            app_dsn,
            lambda conn: commands.replay(
                conn, actor=actor, operation="question.reply", key=key, digest=other
            ),
        )
        is None
    )
    with pytest.raises(ValueError):
        run(
            app_dsn,
            lambda conn: commands.replay(
                conn, actor=actor, operation="export.create", key=key, digest=digest
            ),  # type: ignore[arg-type]
        )
