"""KAN-182 RFP 저장 구조를 실제 마이그레이션으로 만든 전용 PostgreSQL에서 확인한다.

`AUTH_TEST_DATABASE_URL`(kan77_auth_test) 전용 DB에 `001_service_roles.sql`과 같은 tft 역할·기본 권한을
만들고 KAN-119·122·182 마이그레이션을 적용한다. 행은 모두 합성이다.
"""

import json
import os
import re
from datetime import UTC, datetime, time
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.sql import SQL, Identifier

from backend.api.tft.partners.loader import PROFILES, load_profiles
from backend.api.tft.partners.schemas import PartnerProfile

MIGRATIONS = (
    "20261023_KAN-119_projects.sql",
    "20261023_KAN-122_staffing_requests.sql",
    "20261029_KAN-182_rfp_response.sql",
    "20261030_KAN-182_rfp_keys.sql",
    "20261101_KAN-186_supplement_paths.sql",
    "20261102_KAN-186_rnd_check.sql",
    "20261103_KAN-186_project_columns.sql",
    "20261105_KAN-199_requirement_assembly_clause.sql",
    "20261106_KAN-199_question_drafts.sql",
    "20261107_KAN-188_requirement_semantic_fields.sql",
    "20261104_KAN-199_tft_worker.sql",
    "20261109_KAN-188_draft_execution.sql",
    "20261110_KAN-188_worker_output_status.sql",
    "20261111_KAN-188_draft_rewrite.sql",
    "20261112_KAN-187_project_constraints.sql",
)
# 다시 적용해도 깨지지 않는 KAN-182 마이그레이션(매번 적용한다).
RERUNNABLE = MIGRATIONS[2:]
NOW = datetime(2026, 10, 29, tzinfo=UTC)


def apply(conn, path):
    sql = Path(path).read_text()
    conn.execute("\n".join(line for line in sql.splitlines() if not line.startswith("\\")))


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
            # 001_service_roles.sql과 같은 기본 권한. 마이그레이션의 REVOKE가 실제로 일하는지 보려고 둔다.
            conn.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE tft_migrator IN SCHEMA tft "
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO tft_app"
            )
        if conn.execute("SELECT to_regclass('tft.projects')").fetchone()[0] is None:
            for name in MIGRATIONS[:2]:
                apply(conn, Path("infra/migrations/tft") / name)
        # 다시 적용해도 깨지지 않는다(ADD COLUMN IF NOT EXISTS·CREATE TABLE IF NOT EXISTS).
        for name in RERUNNABLE:
            apply(conn, Path("infra/migrations/tft") / name)
    return dsn


@pytest.fixture
def app_dsn(admin):
    return make_conninfo(admin, user="tft_app", password="test-only")


def p(**extra):
    return {"data_origin": "synthetic", "derivation_kind": "DERIVED", "known_at": NOW, **extra}


@pytest.fixture
def chain(admin):
    """프로젝트 → 문서 판본 → 조항 → 요구 판본까지 한 줄을 만든다."""
    ids = {k: uuid4() for k in ("project", "document", "docver", "clause", "requirement")}
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.projects (id, source, source_instance, data_origin, derivation_kind, "
            "known_at, company_code, name) VALUES (%s,'test','kan182','synthetic','PROVIDED',%s,'HOST',%s)",
            (ids["project"], NOW, "14번 유닛 변전소"),
        )
        conn.execute(
            "INSERT INTO tft.documents (id, project_id, kind, title, data_origin, derivation_kind, "
            "known_at, created_by) VALUES (%s,%s,'RFP_SPEC','14번 규격서','synthetic','PROVIDED',%s,%s)",
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
            "data_origin, derivation_kind, known_at) VALUES (%s,%s,'2.5.C',2,0,'33%%','synthetic','DERIVED',%s)",
            (ids["clause"], ids["docver"], NOW),
        )
        conn.execute(
            "INSERT INTO tft.clause_deleted_values (clause_id, ordinal, text, position) VALUES (%s,0,'50',0)",
            (ids["clause"],),
        )
        conn.execute(
            "INSERT INTO tft.requirements (id, project_id, code, data_origin, derivation_kind, known_at) "
            "VALUES (%s,%s,'R-14',%s,'DERIVED',%s)",
            (ids["requirement"], ids["project"], "synthetic", NOW),
        )
        conn.execute(
            "INSERT INTO tft.requirement_versions (requirement_id, version, document_version_id, "
            "clause_id, quote, operator, standard_system, necessity, status, reason, provenance_kind, "
            "data_origin, derivation_kind, known_at) VALUES (%s,1,%s,%s,'33%%','EQ','IEC','MANDATORY',"
            "'INSUFFICIENT_EVIDENCE','자료 없음','ai_draft','synthetic','DERIVED',%s)",
            (ids["requirement"], ids["docver"], ids["clause"], NOW),
        )
    return ids


def violates(dsn, sql, args):
    with psycopg.connect(dsn) as conn:
        with pytest.raises(psycopg.errors.IntegrityError):
            conn.execute(sql, args)


def test_project_gets_two_separate_status_columns_with_defaults(admin, chain):
    with psycopg.connect(admin) as conn:
        row = conn.execute(
            "SELECT status, output_status, judgement_status, purpose, review_departments "
            "FROM tft.projects WHERE id=%s",
            (chain["project"],),
        ).fetchone()
    # 기존 status는 그대로 두고 새 상태 두 개가 따로 생긴다.
    assert row == ("INITIAL_REVIEW", "INTAKE", "NEEDS_REVIEW", "INITIAL_PROPOSAL", [])


def test_runtime_role_appends_versions_but_cannot_rewrite_or_delete_them(app_dsn, chain):
    with psycopg.connect(app_dsn) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "UPDATE tft.requirement_versions SET status='EVIDENCE_FOUND' WHERE requirement_id=%s",
                (chain["requirement"],),
            )
    with psycopg.connect(app_dsn) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "DELETE FROM tft.clause_deleted_values WHERE clause_id=%s", (chain["clause"],)
            )
    with psycopg.connect(app_dsn) as conn:
        conn.execute(
            "INSERT INTO tft.requirement_versions (requirement_id, version, document_version_id, quote, "
            "operator, standard_system, necessity, status, reason, provenance_kind, decided_by, "
            "decided_at, data_origin, derivation_kind, known_at) VALUES (%s,2,%s,'33%%','EQ','IEC',"
            "'MANDATORY','EVIDENCE_FOUND','승인된 제안','human_input',%s,now(),'synthetic','DERIVED',now())",
            (chain["requirement"], chain["docver"], uuid4()),
        )
        conn.execute(
            "UPDATE tft.requirements SET latest_version=2 WHERE id=%s", (chain["requirement"],)
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "UPDATE tft.requirements SET code='X' WHERE id=%s", (chain["requirement"],)
            )


def test_human_judgement_needs_a_decider(admin, chain):
    violates(
        admin,
        "INSERT INTO tft.requirement_versions (requirement_id, version, document_version_id, quote, "
        "operator, standard_system, necessity, status, reason, provenance_kind, data_origin, "
        "derivation_kind, known_at) VALUES (%s,3,%s,'q','EQ','IEC','MANDATORY','SPEC_GAP','r',"
        "'human_input','synthetic','DERIVED',now())",
        (chain["requirement"], chain["docver"]),
    )


def test_unknown_state_values_are_refused(admin, chain):
    violates(
        admin,
        "INSERT INTO tft.requirement_versions (requirement_id, version, document_version_id, quote, "
        "operator, standard_system, necessity, status, reason, provenance_kind, data_origin, "
        "derivation_kind, known_at) VALUES (%s,4,%s,'q','EQ','IEC','MANDATORY','SCORE_72','r',"
        "'ai_draft','synthetic','DERIVED',now())",
        (chain["requirement"], chain["docver"]),
    )


def test_not_comparable_cannot_claim_a_match(admin, chain):
    violates(
        admin,
        "INSERT INTO tft.product_comparisons (id, requirement_id, requirement_version, product_code, "
        "comparable, result, provenance_kind, data_origin, derivation_kind, known_at) "
        "VALUES (%s,%s,1,'LS-15',false,'MEETS','ai_draft','synthetic','DERIVED',now())",
        (uuid4(), chain["requirement"]),
    )


def test_forbidden_path_is_never_allowed_and_blocking_needs_a_reason(admin, chain):
    base = (
        "INSERT INTO tft.supplement_options (id, requirement_id, requirement_version, "
        "customer_permission, path, allowed, blocked_reason, data_origin, derivation_kind, known_at) "
        "VALUES (%s,%s,1,%s,'EXTERNAL_PRODUCT',%s,%s,'synthetic','DERIVED',now())"
    )
    violates(admin, base, (uuid4(), chain["requirement"], "FORBIDDEN", True, None))
    violates(admin, base, (uuid4(), chain["requirement"], "UNSTATED", False, None))


def test_spec_v1_3_paths_and_causes_are_stored_and_others_refused(admin, chain):
    """KAN-186: 명세 v1.3 경로·원인 값(OWN_TERMS_REVIEW는 추가 요청 중)을 받고, 옛 이름은 거부한다."""
    insert = (
        "INSERT INTO tft.supplement_options (id, requirement_id, requirement_version, "
        "deficiency_cause, customer_permission, path, allowed, blocked_reason, data_origin, "
        "derivation_kind, known_at) VALUES (%s,%s,1,%s,'UNSTATED',%s,%s,%s,'synthetic','DERIVED',now())"
    )
    with psycopg.connect(admin) as conn:
        for cause, path in (
            ("DESIGNATED_MODEL", "CUSTOMER_SPECIFIED_PROCUREMENT"),
            ("COMMERCIAL", "OWN_TERMS_REVIEW"),
            ("CORE_EQUIPMENT_MISSING", "JOINT_PROPOSAL"),
            ("RATING", "OWN_DEVELOPMENT"),
        ):
            conn.execute(insert, (uuid4(), chain["requirement"], cause, path, True, None))
        conn.execute(
            insert,
            (
                uuid4(),
                chain["requirement"],
                "DESIGNATED_MODEL",
                "EXTERNAL_PRODUCT",
                False,
                "DESIGNATED_ITEM",
            ),
        )
    violates(admin, insert, (uuid4(), chain["requirement"], "CORE_EQUIPMENT", "NONE", True, None))
    violates(admin, insert, (uuid4(), chain["requirement"], "RATING", "OWN_TERMS", True, None))


def test_department_codes_match_the_list_the_web_form_is_checked_against(admin):
    """웹 접수 폼은 이 목록의 코드를 보낸다. 표에 없는 값은 경로 2가 422로 거절한다."""
    with psycopg.connect(admin) as conn:
        codes = conn.execute("SELECT dept_code FROM tft.department_kinds").fetchall()
    fixture = Path(__file__).resolve().parents[2] / "docs/openapi/tft-department-codes.json"
    assert sorted(code for (code,) in codes) == json.loads(fixture.read_text())


def test_rnd_check_internal_questions_and_department_kinds_are_stored(admin, app_dsn, chain):
    """KAN-186 T10(명세 v1.3 §5.2·§5.3·§9): R&D 확인 상태, 내부 자문 질문, R&D 답변 칸, 부서 분류."""
    with psycopg.connect(admin) as conn:
        # Existing requirement versions read as NOT_REQUIRED.
        assert conn.execute(
            "SELECT rnd_check FROM tft.requirement_versions WHERE requirement_id=%s",
            (chain["requirement"],),
        ).fetchone() == ("NOT_REQUIRED",)
        kinds = dict(
            conn.execute("SELECT dept_code, dept_kind FROM tft.department_kinds").fetchall()
        )
        assert kinds["RND_FW"] == "RND" and kinds["SALES_OPS"] == "SALES"
    question = (
        "INSERT INTO tft.customer_questions (id, project_id, requirement_id, issue_type, "
        "question_text, target, target_dept, data_origin, derivation_kind, known_at, created_by) "
        "VALUES (%s,%s,%s,%s,'R&D 확인',%s,%s,'synthetic','DERIVED',now(),%s)"
    )
    internal = uuid4()
    with psycopg.connect(admin) as conn:
        conn.execute(
            question,
            (
                internal,
                chain["project"],
                chain["requirement"],
                "RND_FEASIBILITY",
                "INTERNAL",
                "RND_FW",
                uuid4(),
            ),
        )
    # INTERNAL needs a department, CUSTOMER none; internal issue types only for INTERNAL.
    for bad in (
        ("RND_FEASIBILITY", "INTERNAL", None),
        ("EMPTY_VALUE", "CUSTOMER", "RND_FW"),
        ("TECH_ADVICE", "CUSTOMER", None),
    ):
        violates(admin, question, (uuid4(), chain["project"], chain["requirement"], *bad, uuid4()))
    reply = (
        "INSERT INTO tft.question_replies (id, question_id, reply_text, received_at, recorded_by, "
        "rnd_feasibility, earliest_available_on, schedule_fits, data_origin, derivation_kind, known_at) "
        "VALUES (%s,%s,'가능',now(),%s,%s,%s,%s,'synthetic','DERIVED',now())"
    )
    violates(admin, reply, (uuid4(), internal, uuid4(), None, "2027-03-01", None))
    violates(admin, reply, (uuid4(), internal, uuid4(), "MAYBE", None, None))
    with psycopg.connect(admin) as conn:
        conn.execute(
            reply, (uuid4(), internal, uuid4(), "FEASIBLE_WITH_SCHEDULE", "2027-03-01", False)
        )
    # The runtime role reads the department map but cannot change it.
    with psycopg.connect(app_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM tft.department_kinds").fetchone()[0] >= 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("INSERT INTO tft.department_kinds VALUES ('X', 'RND')")
    violates(
        admin,
        "UPDATE tft.requirement_versions SET rnd_check = 'MAYBE' WHERE requirement_id = %s",
        (chain["requirement"],),
    )


def test_strategic_customer_integration_and_designated_item_are_stored(admin, chain):
    """KAN-186 T11(명세 v1.3 §9 A01 남은 열): 전략 고객·통합 책임·지정품·선택의 R&D 확인."""
    who = uuid4()
    with psycopg.connect(admin) as conn:
        row = conn.execute(
            "SELECT strategic_customer, integration_holder, integration_holder_candidates "
            "FROM tft.projects WHERE id=%s",
            (chain["project"],),
        ).fetchone()
        # Existing projects: not entered, integration unresolved.
        assert row == (None, "UNRESOLVED", [])
        conn.execute(
            "UPDATE tft.projects SET strategic_customer=true, strategic_customer_note='매출 상위', "
            "strategic_customer_set_by=%s, strategic_customer_set_at=now(), integration_holder='SINGLE_PARTNER', "
            "integration_holder_candidates='[\"P-A\"]', integration_set_by=%s, integration_set_at=now() "
            "WHERE id=%s",
            (who, who, chain["project"]),
        )
    for bad in (
        "strategic_customer=false, strategic_customer_set_by=NULL, strategic_customer_set_at=NULL",
        "integration_holder='OWN', integration_set_by=NULL, integration_set_at=NULL",
        "integration_holder='NOBODY'",
        "integration_holder_candidates='{}'",
    ):
        violates(admin, f"UPDATE tft.projects SET {bad} WHERE id=%s", (chain["project"],))
    # Specified-item details only with a designated model.
    violates(
        admin,
        "UPDATE tft.requirement_versions SET designated_manufacturer='SEL' WHERE requirement_id=%s",
        (chain["requirement"],),
    )
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.requirement_versions SET designated_model='SEL-751A', "
            "designated_manufacturer='SEL', designated_procurement='SPECIFIED_PROCUREMENT', "
            "designated_clause_id=%s WHERE requirement_id=%s",
            (chain["clause"], chain["requirement"]),
        )
    violates(
        admin,
        "UPDATE tft.requirement_versions SET designated_procurement='BUY' WHERE requirement_id=%s",
        (chain["requirement"],),
    )
    selection = (
        "INSERT INTO tft.supplement_selections (requirement_id, version, requirement_version, path, "
        "role_split, decided_by, rnd_check, rnd_check_waiver_reason, data_origin, derivation_kind, "
        "known_at) VALUES (%s,%s,1,'EXTERNAL_PRODUCT','외부 조달',%s,%s,%s,'synthetic','DERIVED',now())"
    )
    reason = "고객 일정상 R&D 회신을 기다릴 수 없어 외부 조달로 먼저 검토"
    with psycopg.connect(admin) as conn:
        conn.execute(selection, (chain["requirement"], 1, who, "WAIVED", reason))
    # A waiver reason is the record of a WAIVED choice.
    violates(admin, selection, (chain["requirement"], 2, who, "PENDING", reason))
    violates(admin, selection, (chain["requirement"], 3, who, "MAYBE", None))


def test_a_waiver_saved_before_t11_is_carried_over_as_waived(admin, chain):
    """#303 리뷰 P3: T10 뒤 T11 전에 면제 사유만 저장된 선택은 T11이 WAIVED로 채우고 제약을 건다."""
    reason = "고객 일정상 R&D 회신을 기다릴 수 없어 외부 조달로 먼저 검토"
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(
            "ALTER TABLE tft.supplement_selections DROP CONSTRAINT supplement_selections_waiver_check"
        )
        conn.execute(
            "INSERT INTO tft.supplement_selections (requirement_id, version, requirement_version, path, "
            "role_split, decided_by, rnd_check_waiver_reason, data_origin, derivation_kind, known_at) "
            "VALUES (%s,9,1,'EXTERNAL_PRODUCT','외부 조달',%s,%s,'synthetic','DERIVED',now())",
            (chain["requirement"], uuid4(), reason),
        )
        apply(conn, Path("infra/migrations/tft/20261103_KAN-186_project_columns.sql"))
        row = conn.execute(
            "SELECT rnd_check FROM tft.supplement_selections WHERE requirement_id=%s AND version=9",
            (chain["requirement"],),
        ).fetchone()
    assert row == ("WAIVED",)


def test_virtual_partner_profiles_from_the_json_fit_the_table(admin):
    profiles = load_profiles()
    with psycopg.connect(admin) as conn:
        for profile in profiles:
            d = profile.model_dump(mode="json")
            conn.execute(
                "INSERT INTO tft.partner_profiles (candidate_code, profile_version, display_name, "
                "manufacturer_name, profile_source, product_family, products, integrator_capable, "
                "spec_facts, certifications, similar_tasks, supply_regions, lead_time, warranty, "
                "notes, confirmation, data_origin, derivation_kind, known_at) VALUES "
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING",
                (
                    d["candidate_code"],
                    d["profile_version"],
                    d["display_name"],
                    d["manufacturer_name"],
                    d["profile_source"],
                    d["product_family"],
                    json.dumps(d["products"]),
                    json.dumps(d["integrator_capable"]),
                    json.dumps(d["spec_facts"]),
                    json.dumps(d["certifications"]),
                    d["similar_tasks"],
                    d["supply_regions"],
                    json.dumps(d["lead_time"]) if d["lead_time"] is not None else None,
                    d["warranty"],
                    d["notes"],
                    json.dumps(d["confirmation"]),
                    d["data_origin"],
                    d["derivation_kind"],
                    d["known_at"],
                ),
            )
        count = conn.execute("SELECT count(*) FROM tft.partner_profiles").fetchone()[0]
    assert count >= len(profiles) > 0


def test_grade_with_exclusion_reasons_must_be_excluded(admin, chain):
    profile = load_profiles()[0]
    test_virtual_partner_profiles_from_the_json_fit_the_table(admin)
    base = (
        "INSERT INTO tft.partner_grades (id, project_id, candidate_code, profile_version, "
        "requirement_id, requirement_version, grade, exclusion_reasons, mandatory_check, "
        "data_origin, derivation_kind, known_at) VALUES (%s,%s,%s,%s,%s,1,%s,%s,'{}','synthetic',"
        "'DERIVED',now())"
    )
    args = (chain["project"], profile.candidate_code, profile.profile_version, chain["requirement"])
    violates(admin, base, (uuid4(), *args, "REVIEW", json.dumps(["정격 미달"])))
    with psycopg.connect(admin) as conn:
        conn.execute(base, (uuid4(), *args, "EXCLUDED", json.dumps(["정격 미달"])))


def test_proposal_rules(admin, chain):
    base = (
        "INSERT INTO tft.change_proposals (id, project_id, requirement_id, clause_unresolved, origin, "
        "current, proposed, rationale, proposer_kind, proposer_user_id, status, decided_by, "
        "decided_at, decision_note, data_origin, derivation_kind, known_at) VALUES "
        "(%s,%s,%s,%s,%s,'{}','{}','근거',%s,%s,%s,%s,%s,%s,'synthetic','PROVIDED',now())"
    )
    pid, req = chain["project"], chain["requirement"]
    who = uuid4()
    # 조항에 못 맞춘 메모만 요구 없이 남는다.
    violates(
        admin,
        base,
        (uuid4(), pid, None, False, "pdf_annot", "system", None, "PENDING", None, None, None),
    )
    # 사용자 제안은 제안자가 있어야 한다.
    violates(
        admin, base, (uuid4(), pid, req, False, "web", "user", None, "PENDING", None, None, None)
    )
    # 기각은 사유가 있어야 한다.
    violates(
        admin, base, (uuid4(), pid, req, False, "web", "user", who, "REJECTED", who, NOW, None)
    )
    with psycopg.connect(admin) as conn:
        conn.execute(
            base,
            (uuid4(), pid, None, True, "pdf_annot", "system", None, "PENDING", None, None, None),
        )
        conn.execute(
            base, (uuid4(), pid, req, False, "web", "user", who, "REJECTED", who, NOW, "중복")
        )


def test_runtime_role_can_only_decide_a_proposal_not_rewrite_it(app_dsn, admin, chain):
    proposal = uuid4()
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.change_proposals (id, project_id, requirement_id, origin, current, "
            "proposed, rationale, proposer_kind, proposer_user_id, data_origin, derivation_kind, "
            "known_at) VALUES (%s,%s,%s,'web','{}','{\"status\":\"EVIDENCE_FOUND\"}','근거','user',%s,"
            "'synthetic','PROVIDED',now())",
            (proposal, chain["project"], chain["requirement"], uuid4()),
        )
    with psycopg.connect(app_dsn) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE tft.change_proposals SET proposed='{}' WHERE id=%s", (proposal,))
    with psycopg.connect(app_dsn) as conn:
        conn.execute(
            "UPDATE tft.change_proposals SET status='APPROVED', decided_by=%s, decided_at=now(), "
            "version=2 WHERE id=%s",
            (uuid4(), proposal),
        )


def test_other_services_cannot_read_rfp_tables(admin):
    with psycopg.connect(admin) as conn:
        assert not conn.execute(
            "SELECT has_table_privilege('cs_app', 'tft.requirement_versions', 'SELECT')"
        ).fetchone()[0]


def test_rereview_marks_are_cleared_not_rewritten(app_dsn, admin, chain):
    proposal, mark = uuid4(), uuid4()
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.change_proposals (id, project_id, requirement_id, origin, current, "
            "proposed, rationale, proposer_kind, proposer_user_id, data_origin, derivation_kind, "
            "known_at) VALUES (%s,%s,%s,'web','{}','{}','근거','user',%s,'synthetic','PROVIDED',now())",
            (proposal, chain["project"], chain["requirement"], uuid4()),
        )
    with psycopg.connect(app_dsn) as conn:
        conn.execute(
            "INSERT INTO tft.rereview_marks (id, project_id, target_kind, target_id, "
            "cause_proposal_id) VALUES (%s,%s,'comparison',%s,%s)",
            (mark, chain["project"], chain["requirement"], proposal),
        )
        conn.execute(
            "UPDATE tft.rereview_marks SET cleared_by=%s, cleared_at=now() WHERE id=%s",
            (uuid4(), mark),
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE tft.rereview_marks SET target_id=%s WHERE id=%s", (uuid4(), mark))
    # 해소자 없이 해소 시각만 채울 수 없다.
    violates(
        admin,
        "INSERT INTO tft.rereview_marks (id, project_id, target_kind, target_id, cause_proposal_id, "
        "cleared_at) VALUES (%s,%s,'draft_sentence',%s,%s,now())",
        (uuid4(), chain["project"], uuid4(), proposal),
    )


def test_command_records_keep_the_first_result_per_key(app_dsn, chain):
    actor = uuid4()
    base = (
        "INSERT INTO tft.rfp_commands (actor_user_id, operation, idempotency_key, project_id, "
        "request_hash, status_code, result) VALUES (%s,%s,%s,%s,%s,201,'{}')"
    )
    with psycopg.connect(app_dsn) as conn:
        # 접수는 프로젝트가 생기기 전이라 project_id가 비어 있다.
        conn.execute(base, (actor, "project.create", "key-0001", None, "a" * 64))
        conn.execute(base, (actor, "project.process", "key-0001", chain["project"], "b" * 64))
    violates(app_dsn, base, (actor, "project.create", "key-0001", None, "c" * 64))
    violates(app_dsn, base, (actor, "project.create", "short", None, "a" * 64))
    with psycopg.connect(app_dsn) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE tft.rfp_commands SET result='{}' WHERE actor_user_id=%s", (actor,))


def test_proposal_cannot_point_at_another_projects_requirement(admin, chain):
    other = uuid4()
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.projects (id, source, source_instance, data_origin, derivation_kind, "
            "known_at, company_code, name) VALUES (%s,'test','kan182','synthetic','PROVIDED',%s,"
            "'HOST','다른 프로젝트')",
            (other, NOW),
        )
    violates(
        admin,
        "INSERT INTO tft.change_proposals (id, project_id, requirement_id, origin, current, "
        "proposed, rationale, proposer_kind, proposer_user_id, data_origin, derivation_kind, "
        "known_at) VALUES (%s,%s,%s,'web','{}','{}','근거','user',%s,'synthetic','PROVIDED',now())",
        (uuid4(), other, chain["requirement"], uuid4()),
    )


def test_the_same_computed_version_is_stored_once(admin, chain):
    base = (
        "INSERT INTO tft.product_comparisons (id, requirement_id, requirement_version, product_code, "
        "variant, comparable, result, provenance_kind, data_origin, derivation_kind, known_at) "
        "VALUES (%s,%s,1,'LS-15',NULL,true,'MEETS','ai_draft','synthetic','DERIVED',now())"
    )
    with psycopg.connect(admin) as conn:
        conn.execute(base, (uuid4(), chain["requirement"]))
    # variant가 없는 같은 제품·같은 판본을 다시 쓰면 막힌다.
    violates(admin, base, (uuid4(), chain["requirement"]))


def test_rereview_mark_cause_must_be_a_proposal_of_the_same_project(admin, chain):
    proposal, other = uuid4(), uuid4()
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.projects (id, source, source_instance, data_origin, derivation_kind, "
            "known_at, company_code, name) VALUES (%s,'test','kan182','synthetic','PROVIDED',%s,"
            "'HOST','다른 프로젝트')",
            (other, NOW),
        )
        conn.execute(
            "INSERT INTO tft.change_proposals (id, project_id, requirement_id, origin, current, "
            "proposed, rationale, proposer_kind, proposer_user_id, data_origin, derivation_kind, "
            "known_at) VALUES (%s,%s,%s,'web','{}','{}','근거','user',%s,'synthetic','PROVIDED',now())",
            (proposal, chain["project"], chain["requirement"], uuid4()),
        )
    violates(
        admin,
        "INSERT INTO tft.rereview_marks (id, project_id, target_kind, target_id, cause_proposal_id) "
        "VALUES (%s,%s,'requirement',%s,%s)",
        (uuid4(), other, chain["requirement"], proposal),
    )


def test_seed_script_puts_the_file_profiles_and_flags_drift(
    admin, app_dsn, tmp_path, monkeypatch, capsys
):
    """시드 스크립트(KAN-184): --execute 뒤 DB 행을 worker 방식으로 되돌리면 파일과 같고, 재실행은 0행이며,
    같은 판본 내용이 바뀌면 DIFFERENT, 새 판본이면 MISSING으로 종료 1이다. 기본 계획은 원격 DSN이어도 접속하지 않는다."""
    # 백엔드 검사 이미지에는 scripts/가 없어 DB가 있을 때(admin 통과 뒤)만 가져온다.
    from scripts.rfp_seed_partner_profiles import main

    def run(*argv):
        code = main(list(argv))
        out = capsys.readouterr().out
        return code, json.loads(out.strip().splitlines()[-1]) if out.strip() else None

    monkeypatch.setenv("DATABASE_URL", app_dsn)
    profiles = load_profiles()
    code, summary = run("--execute")
    assert code == 0 and summary["SAME"] == len(profiles)
    assert run("--execute") == (
        0,
        {"inserted": 0, "SAME": len(profiles), "MISSING": 0, "DIFFERENT": 0},
    )
    with psycopg.connect(admin, row_factory=dict_row) as conn:
        rows = {
            (r["candidate_code"], r["profile_version"]): r
            for r in conn.execute("SELECT * FROM tft.partner_profiles").fetchall()
        }
    # worker_processor와 같은 변환(known_at.date())으로 되돌려도 파일과 같다.
    assert [
        PartnerProfile.model_validate(
            {
                **rows[(p.candidate_code, p.profile_version)],
                "known_at": rows[(p.candidate_code, p.profile_version)]["known_at"].date(),
            }
        )
        for p in profiles
    ] == profiles
    # 날짜는 그날 00:00 UTC로 저장한다(세션 시간대와 무관한 값).
    assert {rows[(p.candidate_code, p.profile_version)]["known_at"] for p in profiles} == {
        datetime.combine(p.known_at, time(), UTC) for p in profiles
    }
    changed = json.loads(PROFILES.read_text(encoding="utf-8"))
    changed[0]["notes"] = "바뀐 메모"
    changed[1]["profile_version"] += 100
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    code, summary = run("--check", "--profiles", str(path))
    assert code == 1 and (summary["DIFFERENT"], summary["MISSING"]) == (1, 1)
    code, summary = run("--execute", "--profiles", str(path))
    assert code == 1 and summary["inserted"] == 1 and summary["DIFFERENT"] == 1
    with psycopg.connect(admin) as conn:
        kept = conn.execute(
            "SELECT notes FROM tft.partner_profiles WHERE candidate_code=%s AND profile_version=%s",
            (profiles[0].candidate_code, profiles[0].profile_version),
        ).fetchone()[0]
        conn.execute(
            "DELETE FROM tft.partner_profiles WHERE candidate_code=%s AND profile_version=%s",
            (changed[1]["candidate_code"], changed[1]["profile_version"]),
        )
    assert kept == profiles[0].notes
    monkeypatch.setenv("DATABASE_URL", "postgresql://tft_app:x@db.example.invalid:5432/team4")
    assert main([]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["mode"] == "dry-run"


def test_rewrite_command_survives_entire_migration_reapplication(admin, chain):
    actor = uuid4()
    insert = (
        "INSERT INTO tft.rfp_commands(actor_user_id,operation,idempotency_key,project_id,"
        "request_hash,status_code,result) VALUES (%s,%s,'rewrite-rerun',%s,%s,202,%s)"
    )
    payload = json.dumps({"job_id": str(uuid4())})
    with psycopg.connect(admin) as conn:
        conn.execute(insert, (actor, "draft.rewrite", chain["project"], "a" * 64, payload))
    with psycopg.connect(admin, autocommit=True) as conn:
        # T13에서 T19 명령을 거부하지 않으며 전체 재적용 순서도 유지한다.
        for name in RERUNNABLE:
            apply(conn, Path("infra/migrations/tft") / name)
        assert conn.execute(
            "SELECT operation,result FROM tft.rfp_commands WHERE actor_user_id=%s", (actor,)
        ).fetchone() == ("draft.rewrite", json.loads(payload))
    with psycopg.connect(admin) as conn:
        with pytest.raises(psycopg.errors.CheckViolation) as error:
            conn.execute(
                insert, (uuid4(), "unknown.operation", chain["project"], "b" * 64, payload)
            )
        assert error.value.diag.constraint_name == "rfp_commands_operation_check"


def test_reapplied_command_constraints_match_runtime_operations(admin):
    """SQL 입력은 마이그레이션을 적용하는 auth-database 하니스에서만 검사한다."""
    from backend.api.tft.rfp.commands import OPERATIONS

    for name in ("20261106_KAN-199_question_drafts.sql", "20261111_KAN-188_draft_rewrite.sql"):
        sql = (Path("infra/migrations/tft") / name).read_text()
        check = re.search(
            r"ADD CONSTRAINT rfp_commands_operation_check\s+CHECK \(operation IN \((.*?)\)\)",
            sql,
            re.S,
        )
        assert check is not None
        assert set(re.findall(r"'([^']+)'", check[1])) == OPERATIONS


def test_project_constraints_reapply_preserves_inputs_and_worker_only_reads(admin, chain):
    migration = Path("infra/migrations/tft/20261112_KAN-187_project_constraints.sql")
    project = chain["project"]
    with psycopg.connect(admin, autocommit=True) as conn:
        apply(conn, migration)
        conn.execute(
            "UPDATE tft.projects SET supply_region='한국',customer_manufacturer_list=ARRAY['제조사 A'],"
            "off_list_permission='FORBIDDEN' WHERE id=%s",
            (project,),
        )
        apply(conn, migration)
        with conn.transaction():
            conn.execute("SET LOCAL ROLE tft_worker")
            assert conn.execute(
                "SELECT supply_region,customer_manufacturer_list,off_list_permission "
                "FROM tft.projects WHERE id=%s",
                (project,),
            ).fetchone() == ("한국", ["제조사 A"], "FORBIDDEN")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("SET LOCAL ROLE tft_worker")
                conn.execute(
                    "UPDATE tft.projects SET supply_region='다른 지역' WHERE id=%s", (project,)
                )
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE tft.projects SET customer_manufacturer_list=ARRAY[]::text[] WHERE id=%s",
                (project,),
            )
