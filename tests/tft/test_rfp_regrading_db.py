"""전용 PostgreSQL에서만 실행하는 저장요구 재채점·선택보존 통합(fake 추출) 회귀."""
# ruff: noqa: F811

import asyncio
from unittest.mock import Mock

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.sql import SQL, Identifier

from backend.api.tft.projects import repository as projects_repository
from backend.api.tft.rfp import draft_repository, repository
from tests.tft.test_rfp_http_base_db import app_dsn, chain  # noqa: F401
from tests.tft.test_rfp_write_routes_db import (  # noqa: F401
    OWNER,
    call,
    client,
    project_body,
)
from tests.tft.test_worker_processor_db import (  # noqa: F401
    SALES,
    FakeExtractor,
    admin,
    make_project,
    profile,
    run,
    worker_dsn,
)

SPEC = b"PART 2 - PRODUCTS\n\n2.1 TRANSFORMERS\n\nA. The transformer rating shall be 1500 kVA minimum.\n"


def read(dsn, fn):
    async def scenario():
        async with await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
            return await fn(conn)

    return asyncio.run(scenario())


def test_regrading_preserves_selection_marks_invalid_and_regenerates_draft_without_model(
    worker_dsn, admin, tmp_path, profile
):
    project = make_project(admin, tmp_path, SPEC)
    run(worker_dsn, project, FakeExtractor())
    with psycopg.connect(admin) as conn:
        requirement, grade = conn.execute(
            "SELECT requirement_id,grade FROM tft.partner_grades WHERE project_id=%s AND candidate_code=%s",
            (project["project"], profile),
        ).fetchone()
        assert grade != "EXCLUDED"
        conn.execute(
            "INSERT INTO tft.supplement_selections(requirement_id,version,requirement_version,path,"
            "candidate_codes,role_split,decided_by,data_origin,derivation_kind,known_at) "
            "VALUES(%s,1,1,'EXTERNAL_PRODUCT',%s,'합성 역할 분담',%s,'synthetic','PROVIDED',now())",
            (requirement, [profile], SALES),
        )
        before = conn.execute(
            "SELECT * FROM tft.supplement_selections WHERE requirement_id=%s", (requirement,)
        ).fetchall()
    assert read(worker_dsn, lambda c: repository.needs_rereview(c, requirement)) is False
    with psycopg.connect(admin) as conn:
        conn.execute(
            "UPDATE tft.projects SET supply_region='UNSUPPORTED_SYNTHETIC_REGION' WHERE id=%s",
            (project["project"],),
        )
    no_model = Mock(side_effect=AssertionError("재실행 모델 호출 금지"))
    summary, _ = run(worker_dsn, project, no_model)
    no_model.assert_not_called()
    assert summary["needs_review"] is True
    assert read(worker_dsn, lambda c: repository.invalid_selection(c, requirement)) is True
    assert read(worker_dsn, lambda c: repository.needs_rereview(c, requirement)) is True
    with psycopg.connect(admin) as conn:
        assert (
            conn.execute(
                "SELECT * FROM tft.supplement_selections WHERE requirement_id=%s", (requirement,)
            ).fetchall()
            == before
        )
        assert conn.execute(
            "SELECT version,grade FROM tft.partner_grades WHERE requirement_id=%s AND candidate_code=%s ORDER BY version",
            (requirement, profile),
        ).fetchall() == [(1, grade), (2, "EXCLUDED")]
        assert (
            conn.execute(
                "SELECT count(*) FROM tft.rereview_marks WHERE project_id=%s", (project["project"],)
            ).fetchone()[0]
            == 0
        )
    draft = read(worker_dsn, lambda c: draft_repository.load(c, project["project"]))
    assert draft is not None and any(
        s.needs_rereview for part in draft.sections for s in part.sentences
    )


def test_regrading_uses_latest_requirement_and_profile_and_appends_in_that_exact_key(
    worker_dsn, admin, tmp_path, profile
):
    project = make_project(admin, tmp_path, SPEC)
    run(worker_dsn, project, FakeExtractor())
    with psycopg.connect(admin) as conn:
        requirement = conn.execute(
            "SELECT requirement_id FROM tft.partner_grades WHERE project_id=%s AND candidate_code=%s",
            (project["project"], profile),
        ).fetchone()[0]
        for table, key, value, version in (
            ("requirement_versions", "requirement_id", requirement, "version"),
            ("partner_profiles", "candidate_code", profile, "profile_version"),
        ):
            cursor = conn.execute(
                SQL("SELECT * FROM tft.{} WHERE {}=%s LIMIT 0").format(
                    Identifier(table), Identifier(key)
                ),
                (value,),
            )
            columns = [col.name for col in cursor.description]
            conn.execute(
                SQL("INSERT INTO tft.{} ({}) SELECT {} FROM tft.{} WHERE {}=%s AND {}=1").format(
                    Identifier(table),
                    SQL(",").join(map(Identifier, columns)),
                    SQL(",").join(
                        SQL("2") if col == version else Identifier(col) for col in columns
                    ),
                    Identifier(table),
                    Identifier(key),
                    Identifier(version),
                ),
                (value,),
            )
        conn.execute("UPDATE tft.requirements SET latest_version=2 WHERE id=%s", (requirement,))
    assert not [
        g
        for g in read(worker_dsn, lambda c: repository.project_grades(c, project["project"]))
        if g["candidate_code"] == profile and g["requirement_id"] == requirement
    ]
    no_model = Mock(side_effect=AssertionError("모델 금지"))
    run(worker_dsn, project, no_model)
    run(worker_dsn, project, no_model)
    no_model.assert_not_called()
    latest = [
        g
        for g in read(worker_dsn, lambda c: repository.project_grades(c, project["project"]))
        if g["candidate_code"] == profile and g["requirement_id"] == requirement
    ]
    assert len(latest) == 1
    assert (
        latest[0]["requirement_version"],
        latest[0]["profile_version"],
        latest[0]["version"],
    ) == (2, 2, 2)
    with psycopg.connect(admin) as conn:
        assert conn.execute(
            "SELECT requirement_version,profile_version,version FROM tft.partner_grades WHERE requirement_id=%s AND candidate_code=%s ORDER BY requirement_version,profile_version,version",
            (requirement, profile),
        ).fetchall() == [(1, 1, 1), (2, 2, 1), (2, 2, 2)]
        conn.execute(
            "DELETE FROM tft.partner_grades WHERE candidate_code=%s AND profile_version=2",
            (profile,),
        )
        conn.execute(
            "DELETE FROM tft.partner_profiles WHERE candidate_code=%s AND profile_version=2",
            (profile,),
        )


def advance_requirement(conn, requirement_id):
    cursor = conn.execute(
        "SELECT * FROM tft.requirement_versions WHERE requirement_id=%s LIMIT 0", (requirement_id,)
    )
    columns = [col.name for col in cursor.description]
    conn.execute(
        SQL(
            "INSERT INTO tft.requirement_versions ({}) SELECT {} FROM tft.requirement_versions WHERE requirement_id=%s AND version=1"
        ).format(
            SQL(",").join(map(Identifier, columns)),
            SQL(",").join(SQL("2") if col == "version" else Identifier(col) for col in columns),
        ),
        (requirement_id,),
    )
    conn.execute("UPDATE tft.requirements SET latest_version=2 WHERE id=%s", (requirement_id,))


@pytest.mark.parametrize(
    "path,codes", [("NONE", []), ("OTHER_OWN_PRODUCT", []), ("EXTERNAL_PRODUCT", ["P-A"])]
)
def test_ungraded_legacy_or_candidate_free_selection_is_not_invalidated_by_version_alone(
    admin, app_dsn, chain, path, codes
):
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.supplement_selections(requirement_id,version,requirement_version,path,candidate_codes,role_split,decided_by,data_origin,derivation_kind,known_at) VALUES(%s,1,1,%s,%s,'합성 기록',%s,'synthetic','PROVIDED',now())",
            (chain["requirement"], path, codes, SALES),
        )
        advance_requirement(conn, chain["requirement"])
    assert read(app_dsn, lambda c: repository.invalid_selection(c, chain["requirement"])) is False
    assert read(app_dsn, lambda c: repository.needs_rereview(c, chain["requirement"])) is False


def test_grade_history_distinguishes_current_loss_exclusion_and_restored_validity(
    admin, app_dsn, chain, profile
):
    with psycopg.connect(admin) as conn:
        conn.execute(
            "INSERT INTO tft.supplement_selections(requirement_id,version,requirement_version,path,candidate_codes,role_split,decided_by,data_origin,derivation_kind,known_at) VALUES(%s,1,1,'EXTERNAL_PRODUCT',%s,'합성 기록',%s,'synthetic','PROVIDED',now())",
            (chain["requirement"], [profile], SALES),
        )
        before = conn.execute(
            "SELECT * FROM tft.supplement_selections WHERE requirement_id=%s",
            (chain["requirement"],),
        ).fetchall()
    grade = {
        "requirement_id": chain["requirement"],
        "grade": "REVIEW",
        "reasons": [],
        "exclusion_reasons": [],
        "mandatory_check": {"passed": [], "failed": [], "unknown": []},
    }

    def store(version, excluded=False):
        values = {
            **grade,
            "grade": "EXCLUDED" if excluded else "REVIEW",
            "exclusion_reasons": ["합성 제외"] if excluded else [],
        }
        read(
            app_dsn,
            lambda c: repository.insert_grade(
                c,
                project_id=chain["project"],
                candidate_code=profile,
                profile_version=1,
                requirement_version=version,
                grade=values,
                data_origin="synthetic",
            ),
        )

    def needs():
        return read(app_dsn, lambda c: repository.needs_rereview(c, chain["requirement"]))

    assert needs() is False  # 최초 미채점
    store(1)
    assert needs() is False
    with psycopg.connect(admin) as conn:
        advance_requirement(conn, chain["requirement"])
    assert needs() is True  # 이전 판정이 있지만 현재 요구 판본의 판정은 없음
    store(2, excluded=True)
    assert needs() is True
    store(2)
    assert needs() is False  # 현행 유효성 회복, 과거 EXCLUDED를 최신 판정으로 쓰지 않음
    with psycopg.connect(admin) as conn:
        assert (
            conn.execute(
                "SELECT * FROM tft.supplement_selections WHERE requirement_id=%s",
                (chain["requirement"],),
            ).fetchall()
            == before
        )


def test_saved_project_constraints_survive_real_detail_get_and_response_based_edit(client, app_dsn):
    expected = {
        "supply_region": "한국",
        "customer_manufacturer_list": ["제조사 A", "제조사 B"],
        "off_list_permission": "FORBIDDEN",
    }
    created = call(client, "POST", "/projects", OWNER, project_body(**expected))
    assert created.status_code == 201, created.text
    project = created.json()["data"]
    url = f"/projects/{project['id']}"
    # 서비스의 보조 p.* 조회가 기본 상세 SELECT 누락을 가리는 경우도 독립적으로 잡는다.
    saved = read(app_dsn, lambda conn: projects_repository.get_project(conn, project["id"]))
    assert {name: saved[name] for name in expected} == expected
    detail_response = call(client, "GET", url, OWNER)
    assert detail_response.status_code == 200, detail_response.text
    detail = detail_response.json()["data"]
    assert {name: detail[name] for name in expected} == expected
    # 편집폼과 같이 실제 GET 응답의 판본·세 값을 그대로 수정 요청에 사용한다.
    body = {"version": detail["version"], **{name: detail[name] for name in expected}}
    edited = call(client, "PATCH", url, OWNER, body)
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"]["version"] == detail["version"] + 1
    after_response = call(client, "GET", url, OWNER)
    assert after_response.status_code == 200, after_response.text
    after = after_response.json()["data"]
    assert after["version"] == detail["version"] + 1
    assert {name: after[name] for name in expected} == expected
