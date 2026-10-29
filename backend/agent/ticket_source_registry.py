"""C/S worker가 쓸 문의 출처와 정책 입력을 모은다(KAN-194 9차 3/3).

문의 원문·시도 저장(TicketSource)은 C/S 쪽(KAN-107)이 고정 모듈 `backend.api.cs.worker_source`의
`build(settings) -> WorkerParts`로 준다. 모듈 경로를 환경 변수로 받지 않는다(임의 모듈 import 차단). 모듈이 없으면
worker는 작업을 `CS_WORKER_SOURCE_MISSING`으로 실패 처리한다.

허용 정보 코드와 검색 조건은 모듈이 비워 두면 여기서 정책으로 채운다.
- 허용 정보 코드: `eval/policy/cs_required_info.csv` required_fields의 합집합(백엔드 정책 폴더에 정보 코드북이 없다).
- 검색 조건: 색인 판본 chunking-v1, 상위 20건. 제품 계열 좁힘은 그래프 retrieve가 한다.
"""

import csv
import importlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType

from backend.agent.cs_runner import TicketSource
from backend.engine.search.chunking import INDEX_VERSION
from backend.engine.search.model import SearchFilters
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings

SOURCE_MODULE = "backend.api.cs.worker_source"
REQUIRED_INFO = Path(__file__).resolve().parents[2] / "eval" / "policy" / "cs_required_info.csv"


@dataclass(frozen=True)
class WorkerParts:
    """worker가 그래프에 꽂는 입력. ticket_source만 필수이고 나머지는 None이면 정책 기본값이다."""

    ticket_source: TicketSource
    allowed_info_codes: frozenset[str] | None = None
    search_filters: SearchFilters | None = None


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


def load_parts(
    settings: Settings, import_module: Callable[[str], ModuleType] = importlib.import_module
) -> WorkerParts:
    """C/S 모듈의 문의 출처를 받고 비운 정책 입력을 채운다.

    Args:
        settings: worker 설정(모듈 build에 그대로 넘긴다).
        import_module: 검사에서 바꿔 끼우는 import 함수.

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
    return replace(
        parts,
        allowed_info_codes=parts.allowed_info_codes or policy_info_codes(),
        search_filters=parts.search_filters
        or SearchFilters(document_sha256s=None, index_version=INDEX_VERSION, limit=20),
    )
