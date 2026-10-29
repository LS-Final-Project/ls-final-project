"""KAN-194 9차 3/3 worker 문의 출처 규약과 정책 기본값."""

import json
from types import SimpleNamespace

import pytest

from backend.agent.ticket_source_registry import (
    SOURCE_MODULE,
    CsPolicyDocumentsSettings,
    WorkerParts,
    load_parts,
    policy_documents_from_manifest,
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


def manifest(path, documents, **top):
    """합성 색인 manifest를 쓴다.

    Args:
        path: 파일 경로.
        documents: 문서 행.
        **top: 최상위 값 바꾸기.

    Returns:
        Path: 경로.
    """
    body = {"mode": "execute", "index_version": "chunking-v1", "documents": documents, **top}
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_manifest_gives_only_stored_policy_documents(tmp_path):
    """저장된 POL-* 문서만 doc_id → sha256으로 읽고, 판본·모드·sha 형식이 틀리면 거부한다."""
    rows = [
        {"doc_id": "POL-RETURN", "sha256": "a" * 64, "reason": None, "stored": 5},
        {"doc_id": "POL-ORDER", "sha256": "b" * 64, "reason": "EMPTY", "stored": 0},
        {"doc_id": "POL-SERVICE", "sha256": "c" * 64, "reason": None, "stored": 0},
        {"doc_id": "POL-WARRANTY", "sha256": "f" * 64, "reason": "PARTIAL", "stored": 2},
        {"doc_id": "MAN-001", "sha256": "d" * 64, "reason": None, "stored": 3},
        {"doc_id": None, "sha256": "e" * 64, "reason": None, "stored": 3},
    ]
    path = manifest(tmp_path / "m.json", rows)
    assert policy_documents_from_manifest(path, "chunking-v1") == {"POL-RETURN": "a" * 64}
    with pytest.raises(ValueError, match="CS_POLICY_MANIFEST_INDEX_MISMATCH"):
        policy_documents_from_manifest(path, "chunking-v2")
    with pytest.raises(ValueError, match="CS_POLICY_MANIFEST_INVALID"):
        policy_documents_from_manifest(
            manifest(tmp_path / "d.json", rows, mode="dry-run"), "chunking-v1"
        )
    bad = [{"doc_id": "POL-RETURN", "sha256": "A" * 64, "reason": None, "stored": 1}]
    with pytest.raises(ValueError, match="CS_POLICY_MANIFEST_INVALID"):
        policy_documents_from_manifest(manifest(tmp_path / "b.json", bad), "chunking-v1")


def test_parts_get_policy_documents_from_the_configured_manifest(tmp_path):
    """CS_POLICY_DOCUMENTS_FILE이 있으면 매핑을 채우고, 없으면 None(예약 없음), 모듈 값이 있으면 그대로 둔다."""
    rows = [{"doc_id": "POL-RETURN", "sha256": "a" * 64, "reason": None, "stored": 5}]
    path = manifest(tmp_path / "m.json", rows)

    def module(parts):
        return lambda name: SimpleNamespace(build=lambda settings: parts)

    source = SimpleNamespace()
    configured = CsPolicyDocumentsSettings(_env_file=None, policy_documents_file=path)
    filled = load_parts(SETTINGS, module(WorkerParts(source)), configured)
    assert filled.policy_documents == {"POL-RETURN": "a" * 64}
    unset = CsPolicyDocumentsSettings(_env_file=None)
    assert load_parts(SETTINGS, module(WorkerParts(source)), unset).policy_documents is None
    given = WorkerParts(source, policy_documents={"POL-ORDER": "b" * 64})
    assert load_parts(SETTINGS, module(given), configured).policy_documents == {
        "POL-ORDER": "b" * 64
    }
