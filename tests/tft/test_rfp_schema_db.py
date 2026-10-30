"""KAN-182 RFP 저장 구조를 실제 마이그레이션으로 만든 전용 PostgreSQL에서 확인한다.

`AUTH_TEST_DATABASE_URL`(kan77_auth_test) 전용 DB에 `001_service_roles.sql`과 같은 tft 역할·기본 권한을
만들고 KAN-119·122·182 마이그레이션을 적용한다. 행은 모두 합성이다.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.sql import SQL, Identifier

from backend.api.tft.partners.loader import load_profiles

MIGRATIONS = (
    "20261023_KAN-119_projects.sql",
    "20261023_KAN-122_staffing_requests.sql",
    "20261029_KAN-182_rfp_response.sql",
    "20261030_KAN-182_rfp_keys.sql",
    "20261101_KAN-186_supplement_paths.sql",
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
