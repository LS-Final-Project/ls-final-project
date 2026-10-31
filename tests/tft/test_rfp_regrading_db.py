"""전용 PostgreSQL에서만 실행하는 저장요구 재채점·선택보존 통합(fake 추출) 회귀."""
# ruff: noqa: F811

import asyncio
from unittest.mock import Mock

import psycopg
from psycopg.rows import dict_row
from psycopg.sql import SQL, Identifier

from backend.api.tft.rfp import draft_repository, repository
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
