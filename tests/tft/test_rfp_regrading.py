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
