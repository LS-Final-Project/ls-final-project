"""KAN-187 저장된 사실만 재채점한다. 모델·원문 재추출은 하지 않는다."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

from backend.api.tft.rfp import repository
from backend.engine.rfp.requirements import ExtractedRequirement


def test_new_requirement_preserves_actual_extraction_semantics_without_dto_changes():
    item = ExtractedRequirement(
        clause_no="1.1.A",
        quote="Synthetic requirement.",
        kind="SPEC",
        equipment="enclosure",
        task=None,
        attribute="function",
        attribute_key="protection_functions",
        value="original",
        canonical_value="overcurrent",
        certification_listed=False,
        unit=None,
        operator="TEXT",
        standard_system="NONE",
        necessity="MANDATORY",
        designated_model=None,
    )
    conn = AsyncMock()
    asyncio.run(
        repository.insert_requirement(
            conn,
            project_id=uuid4(),
            code="synthetic",
            document_version={"id": uuid4(), "data_origin": "synthetic"},
            clause=None,
            spec=item.model_dump(),
            judgement={
                "status": "SPEC_GAP",
                "reason": "합성",
                "unresolved": [],
                "next_owner": "TECH",
            },
        )
    )
    query, params = conn.execute.await_args_list[1].args
    assert "kind, certification_listed, canonical_value" in query
    assert query.count("%s") == len(params)
    assert params[-4:-1] == ("SPEC", False, "overcurrent")


def test_processed_document_rerun_regrades_actual_saved_version_with_zero_models(monkeypatch):
    from contextlib import asynccontextmanager
    from datetime import date
    from types import SimpleNamespace
    from unittest.mock import Mock

    from backend.agent.processor_registry import TftJob
    from backend.api.tft import worker_processor as worker
    from backend.api.tft.partners.loader import PROFILE_FAMILIES, load_profiles, profile_row
    from backend.api.tft.rfp.jobs import input_version
    from backend.shared.settings import Settings

    profile = load_profiles()[0]
    requirement_id, document_id, project_id = uuid4(), uuid4(), uuid4()
    row = ExtractedRequirement(
        clause_no="1.1.A",
        quote="Synthetic enclosure requirement.",
        kind="SPEC",
        equipment="enclosure",
        task=None,
        attribute="rated current",
        attribute_key="rated_current",
        value="1000",
        unit="A",
        operator="GTE",
        standard_system="IEC",
        necessity="MANDATORY",
        designated_model=None,
        equipment_family=next(iter(PROFILE_FAMILIES[profile.product_family])),
    ).model_dump() | {
        "requirement_id": requirement_id,
        "version": 3,
        "data_origin": "synthetic",
        "status": "SPEC_GAP",
    }

    class Connection:
        execute = AsyncMock()

        @asynccontextmanager
        async def transaction(self):
            yield

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    conn = Connection()
    monkeypatch.setattr(worker.psycopg.AsyncConnection, "connect", AsyncMock(return_value=conn))
    monkeypatch.setattr(
        repository,
        "all_document_versions",
        AsyncMock(
            return_value=[
                {
                    "id": document_id,
                    "latest": True,
                    "kind": "RFP_SPEC",
                    "title": "synthetic",
                }
            ]
        ),
    )
    monkeypatch.setattr(repository, "document_processed", AsyncMock(return_value=True))
    monkeypatch.setattr(repository, "stored_grading_requirements", AsyncMock(return_value=[row]))
    monkeypatch.setattr(
        repository,
        "project_constraints",
        AsyncMock(
            return_value={
                "customer_manufacturer_list": [profile.manufacturer_name],
                "off_list_permission": "FORBIDDEN",
                "supply_region": "UNSUPPORTED_SYNTHETIC_REGION",
            }
        ),
    )
    monkeypatch.setattr(
        repository,
        "partner_profiles",
        AsyncMock(
            return_value=[{**profile.model_dump(), "known_at": profile_row(profile)["known_at"]}]
        ),
    )
    monkeypatch.setattr(repository, "prior_grades_for_regrading", AsyncMock(return_value=[]))
    monkeypatch.setattr(worker.draft_repository, "generated", AsyncMock(return_value=None))
    insert = AsyncMock()
    monkeypatch.setattr(repository, "insert_grade", insert)
    draft = AsyncMock(return_value=SimpleNamespace(sections=[]))
    monkeypatch.setattr(worker.drafts, "generate", draft)
    read = AsyncMock(side_effect=AssertionError("원문 재읽기 금지"))
    monkeypatch.setattr(worker, "read_document", read)
    extraction = Mock(side_effect=AssertionError("추출 모델 호출 금지"))
    rewrite = Mock(side_effect=AssertionError("재작성 모델 호출 금지"))
    processor = worker.make_processor(
        Settings(_env_file=None, environment="test"),
        provider=extraction,
        draft_provider=rewrite,
        store=None,
        products=[],
        today=lambda: date(2026, 10, 30),
    )
    job = TftJob(
        job_id=uuid4(),
        kind="rfp.process",
        project_id=project_id,
        input_version=input_version([document_id]),
        actor_id=uuid4(),
    )
    summary = processor(job, lambda _: None)
    extraction.assert_not_called()
    rewrite.assert_not_called()
    read.assert_not_awaited()
    insert.assert_awaited_once()
    saved = insert.await_args.kwargs
    assert saved["requirement_version"] == 3
    assert saved["profile_version"] == profile.profile_version
    assert saved["grade"]["grade"] == "EXCLUDED"
    assert "supply_region" in saved["grade"]["mandatory_check"]["failed"]
    draft.assert_awaited_once_with(conn, project_id, job.actor_id, job.job_id)
    assert summary["counts"]["requirements"] == 0  # 새 추출은 0, 저장 요구의 재채점은 실행했다.
    worker.draft_repository.generated.return_value = 1
    processor(job, lambda _: None)
    assert insert.await_count == 1  # 이미 커밋된 같은 job 재시도는 등급을 또 쌓지 않는다.
    extraction.assert_not_called()
    rewrite.assert_not_called()


def test_legacy_missing_semantics_are_never_inferred_or_upgraded():
    from backend.api.tft import worker_processor as worker
    from backend.api.tft.partners import grading
    from backend.api.tft.partners.loader import load_profiles

    row = {"requirement_id": uuid4(), "kind": None, "certification_listed": None}
    assert worker.stored_extraction(row) is None
    profile = load_profiles()[0]
    context = grading.RfpContext(
        manufacturer_list=None,
        off_list_permission="UNSTATED",
        supply_region=None,
        requires_integrator=False,
    )
    for old, expected in (
        ("EXCLUDED", "EXCLUDED"),
        ("REFERENCE_ONLY", "REFERENCE_ONLY"),
        ("MANDATORY_CONFIRMED", "REVIEW"),
    ):
        result = worker.legacy_grade(
            row,
            profile,
            context,
            {"grade": old, "exclusion_reasons": ["과거 미충족"] if old == "EXCLUDED" else []},
        )
        assert result.grade == expected
        assert "extraction_semantics" in result.mandatory_check.unknown


def test_grade_append_uses_real_requirement_version_and_never_rewrites_a_grade():
    conn = AsyncMock()
    grade = {
        "requirement_id": uuid4(),
        "grade": "REVIEW",
        "reasons": [],
        "exclusion_reasons": [],
        "mandatory_check": {"passed": [], "failed": [], "unknown": []},
    }
    asyncio.run(
        repository.insert_grade(
            conn,
            project_id=uuid4(),
            candidate_code="P-A",
            profile_version=2,
            requirement_version=3,
            grade=grade,
            data_origin="synthetic",
        )
    )
    query, values = conn.execute.await_args.args
    assert query.count("%s") == len(values)
    assert values[3] == values[-3] == 2  # 같은 프로필 판본 안에서만 append
    assert values[5] == values[-1] == 3
    assert "coalesce(max(version),0)+1" in query
    assert "UPDATE" not in query and "DELETE" not in query


def test_worker_store_whitelist_keeps_all_three_actual_extraction_facts(monkeypatch):
    from types import SimpleNamespace

    from backend.api.tft import worker_processor as worker

    item = ExtractedRequirement(
        clause_no="1.1.A",
        quote="Synthetic.",
        kind="COMPLIANCE",
        equipment="enclosure",
        task=None,
        attribute="UL 50",
        value=None,
        unit=None,
        operator="TEXT",
        standard_system="UL",
        necessity="MANDATORY",
        designated_model=None,
        certification_listed=True,
        canonical_value=None,
    )
    insert = AsyncMock(return_value=uuid4())
    monkeypatch.setattr(repository, "insert_requirement", insert)
    asyncio.run(
        worker.store_requirements(
            AsyncMock(),
            uuid4(),
            [({"id": uuid4(), "data_origin": "synthetic"}, {}, item)],
            [
                SimpleNamespace(
                    status="SPEC_GAP",
                    reason="합성",
                    unresolved=[],
                    next_owner="TECH",
                    candidates=[],
                )
            ],
        )
    )
    saved = insert.await_args.kwargs["spec"]
    assert (saved["kind"], saved["certification_listed"], saved["canonical_value"]) == (
        "COMPLIANCE",
        True,
        None,
    )


def test_candidate_membership_uses_current_input_but_grade_keeps_actual_saved_time():
    from datetime import UTC, datetime

    from backend.api.tft.partners.loader import load_profiles, profile_row
    from backend.api.tft.rfp.routes_read import partner_candidate

    profile = load_profiles()[0]
    known = datetime(2026, 10, 30, tzinfo=UTC)
    grade = {
        "requirement_id": uuid4(),
        "requirement_version": 3,
        "profile_version": profile.profile_version,
        "version": 2,
        "known_at": known,
        "grade": "REVIEW",
        "reasons": ["과거 판정"],
        "exclusion_reasons": [],
        "mandatory_check": {"passed": [], "failed": [], "unknown": []},
    }
    row = {**profile.model_dump(), "known_at": profile_row(profile)["known_at"]}
    candidate = partner_candidate(
        row, [grade], [grade], manufacturer_list=["  " + profile.manufacturer_name.upper() + " "]
    )
    assert candidate.on_customer_manufacturer_list is True
    assert candidate.grades[0].grade == "REVIEW"
    assert (
        candidate.grades[0].requirement_version,
        candidate.grades[0].version,
        candidate.grades[0].known_at,
    ) == (3, 2, known)
    assert (
        partner_candidate(
            row, [grade], [grade], manufacturer_list=None
        ).on_customer_manufacturer_list
        is None
    )
