"""KAN-194 9차 3/3 worker 문의 출처 규약과 정책 기본값."""

from types import SimpleNamespace

import pytest

from backend.agent.ticket_source_registry import (
    SOURCE_MODULE,
    WorkerParts,
    load_parts,
    policy_info_codes,
)
from backend.engine.search.model import SearchFilters
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings

SETTINGS = Settings(_env_file=None, environment="test")


def test_policy_codes_are_the_union_of_required_fields(tmp_path):
    """정책표 required_fields(세미콜론 구분)의 합집합이고, 실제 정책표도 비지 않는다."""
    table = tmp_path / "required.csv"
    table.write_text(
        "﻿family,category,required_fields\nA,X,model;serial\nB,Y,serial; site \n",
        encoding="utf-8",
    )
    assert policy_info_codes(table) == {"model", "serial", "site"}
    assert "product_model" in policy_info_codes()


def test_module_parts_get_policy_defaults_only_where_empty():
    """C/S 모듈이 문의 출처만 주면 정책 코드와 검색 조건(상위 20건)을 채우고, 준 값은 그대로 둔다."""
    source = SimpleNamespace()
    given = SearchFilters(document_sha256s=None, index_version="chunking-v1", limit=5)

    def module(parts):
        return lambda name: SimpleNamespace(build=lambda settings: parts)

    filled = load_parts(SETTINGS, module(WorkerParts(ticket_source=source)))
    assert filled.ticket_source is source
    assert filled.allowed_info_codes == policy_info_codes()
    assert (filled.search_filters.limit, filled.search_filters.product_family) == (20, None)
    kept = load_parts(
        SETTINGS,
        module(WorkerParts(source, allowed_info_codes=frozenset({"X"}), search_filters=given)),
    )
    assert (kept.allowed_info_codes, kept.search_filters) == (frozenset({"X"}), given)


def test_missing_module_is_a_fixed_code_but_other_import_errors_surface():
    """C/S 모듈이 없으면 CS_WORKER_SOURCE_MISSING이고, 모듈 안의 다른 import 실패는 숨기지 않는다."""
    with pytest.raises(ApplicationError) as error:
        load_parts(SETTINGS)
    assert error.value.code == "CS_WORKER_SOURCE_MISSING"

    def inner_failure(name):
        raise ModuleNotFoundError("No module named 'somewhere_else'", name="somewhere_else")

    with pytest.raises(ModuleNotFoundError):
        load_parts(SETTINGS, inner_failure)
    assert SOURCE_MODULE == "backend.api.cs.worker_source"
