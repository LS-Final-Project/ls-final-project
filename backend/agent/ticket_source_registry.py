"""C/S worker가 쓸 문의 출처와 정책 입력을 모은다(KAN-194 9차 3/3).

문의 원문·시도 저장(TicketSource)은 C/S 쪽(KAN-107)이 고정 모듈 `backend.api.cs.worker_source`의
`build(settings) -> WorkerParts`로 준다. 모듈 경로를 환경 변수로 받지 않는다(임의 모듈 import 차단). 모듈이 없으면
worker는 작업을 `CS_WORKER_SOURCE_MISSING`으로 실패 처리한다.

허용 정보 코드와 검색 조건은 모듈이 비워 두면 여기서 정책으로 채운다.
- 허용 정보 코드: `eval/policy/cs_required_info.csv` required_fields의 합집합(백엔드 정책 폴더에 정보 코드북이 없다).
- 검색 조건: 색인 판본 INDEX_VERSION(backend/engine/search/chunking.py), 상위 20건. 제품 계열 좁힘은 그래프 retrieve가 한다.
- 정책 문서 매핑(KAN-194 11차): `CS_POLICY_DOCUMENTS_FILE`(색인 manifest, cs_index_docs --manifest-out)이 있으면
  POL-* doc_id → sha256을 읽어 그래프의 정책 문서 1자리 예약에 쓴다. 청크 행에 doc_id가 없어 manifest로만 잇는다.
  없으면 예약하지 않는다.
"""

import csv
import importlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType

from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.agent.cs_runner import TicketSource
from backend.engine.search.chunking import INDEX_VERSION
from backend.engine.search.model import SearchFilters
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings

SOURCE_MODULE = "backend.api.cs.worker_source"
REQUIRED_INFO = Path(__file__).resolve().parents[2] / "eval" / "policy" / "cs_required_info.csv"
SHA256 = re.compile(r"[0-9a-f]{64}")


class CsPolicyDocumentsSettings(BaseSettings):
    """정책 문서 매핑을 읽을 색인 manifest 경로(선택). 없으면 정책 문서 예약을 하지 않는다."""

    model_config = SettingsConfigDict(env_prefix="CS_", extra="ignore")
    policy_documents_file: Path | None = None


@dataclass(frozen=True)
class WorkerParts:
    """worker가 그래프에 꽂는 입력. ticket_source만 필수이고 나머지는 None이면 정책 기본값이다."""

    ticket_source: TicketSource
    allowed_info_codes: frozenset[str] | None = None
    search_filters: SearchFilters | None = None
    # 정책 문서 doc_id → document_sha256. None이면 CS_POLICY_DOCUMENTS_FILE의 manifest에서 채운다.
    policy_documents: Mapping[str, str] | None = None


def policy_info_codes(path: Path = REQUIRED_INFO) -> frozenset[str]:
    """정책표의 필요 정보 코드 합집합.

    Args:
        path: cs_required_info.csv 경로.

    Returns:
        frozenset[str]: required_fields(세미콜론 구분)의 합집합. 비면 ValueError다.
    """
    with path.open(encoding="utf-8-sig", newline="") as handle:
        codes = frozenset(
            code.strip()
            for row in csv.DictReader(handle)
            for code in row["required_fields"].split(";")
            if code.strip()
        )
    if not codes:
        raise ValueError("CS_WORKER_POLICY_EMPTY")
    return codes


def policy_documents_from_manifest(path: Path, index_version: str) -> dict[str, str]:
    """색인 manifest에서 저장된 정책 문서(POL-*)의 doc_id → sha256을 읽는다.

    Args:
        path: cs_index_docs --manifest-out 파일.
        index_version: 검색 조건의 색인 판본. manifest와 다르면 ValueError다.

    Returns:
        dict[str, str]: 실패 사유 없이 청크가 저장된 POL-* 문서만. 형식이 틀리면 ValueError다.
    """
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("index_version") != index_version:
        raise ValueError("CS_POLICY_MANIFEST_INDEX_MISMATCH")
    if manifest.get("mode") != "execute" or not isinstance(manifest.get("documents"), list):
        raise ValueError("CS_POLICY_MANIFEST_INVALID")
    mapping: dict[str, str] = {}
    for row in manifest["documents"]:
        doc_id = row.get("doc_id") if isinstance(row, dict) else None
        if not isinstance(doc_id, str) or not doc_id.startswith("POL-"):
            continue
        sha = row.get("sha256")
        if row.get("reason") is not None or not row.get("stored"):
            continue
        if not isinstance(sha, str) or not SHA256.fullmatch(sha):
            raise ValueError("CS_POLICY_MANIFEST_INVALID")
        mapping[doc_id] = sha
    return mapping


def load_parts(
    settings: Settings,
    import_module: Callable[[str], ModuleType] = importlib.import_module,
    policy: CsPolicyDocumentsSettings | None = None,
) -> WorkerParts:
    """C/S 모듈의 문의 출처를 받고 비운 정책 입력을 채운다.

    Args:
        settings: worker 설정(모듈 build에 그대로 넘긴다).
        import_module: 검사에서 바꿔 끼우는 import 함수.
        policy: 정책 문서 manifest 설정. None이면 환경(CS_POLICY_DOCUMENTS_FILE)에서 읽는다.

    Returns:
        WorkerParts: 허용 정보 코드·검색 조건까지 채운 입력. 모듈이 없으면 ApplicationError
            CS_WORKER_SOURCE_MISSING이다(모듈 안의 다른 import 오류는 그대로 올린다).
    """
    try:
        module = import_module(SOURCE_MODULE)
    except ModuleNotFoundError as error:
        if error.name != SOURCE_MODULE:
            raise
        raise ApplicationError(
            503, "CS_WORKER_SOURCE_MISSING", "The C/S ticket source is not connected."
        ) from None
    parts: WorkerParts = module.build(settings)
    filters = parts.search_filters or SearchFilters(
        document_sha256s=None, index_version=INDEX_VERSION, limit=20
    )
    manifest = (policy or CsPolicyDocumentsSettings()).policy_documents_file
    documents = parts.policy_documents
    if documents is None and manifest is not None:
        documents = policy_documents_from_manifest(manifest, filters.index_version)
    return replace(
        parts,
        allowed_info_codes=parts.allowed_info_codes or policy_info_codes(),
        search_filters=filters,
        policy_documents=documents,
    )
