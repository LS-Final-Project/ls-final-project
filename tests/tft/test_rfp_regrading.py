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
    monkeypatch.setattr(repository, "project_grades", AsyncMock(return_value=[]))
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
