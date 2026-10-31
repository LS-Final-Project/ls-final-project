"""제품 계열·문의 유형별 필요 정보 코드와 부족 정보 계산(KAN-107). DB 없음.

필요 정보 표는 정책 `eval/policy/cs_required_info.csv`다. 부족 정보는 초안 입력(`drafting.prepare_input`)과
같은 식으로 센다: 필요 − 고객이 준 정보 − 이미 고객에게 요청한 정보. worker(초안)와 분석 조회가 같은 표를 쓴다.
"""

import csv
import io
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

REQUIRED_INFO = Path(__file__).resolve().parents[3] / "eval" / "policy" / "cs_required_info.csv"


def required_by_family(path: Path = REQUIRED_INFO) -> dict[str, dict[str, frozenset[str]]]:
    """정책표의 제품 계열 → 문의 유형 → 필요 정보 코드.

    Args:
        path: eval/policy/cs_required_info.csv.

    Returns:
        dict: 계열별 유형별 코드 집합. 열이 없으면 ValueError다.
    """
    reader = csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"), newline=""))
    if not {"family", "category", "required_fields"} <= set(reader.fieldnames or ()):
        raise ValueError("CS_REQUIRED_INFO_COLUMNS")
    table: dict[str, dict[str, frozenset[str]]] = {}
    for row in reader:
        codes = frozenset(
            code.strip() for code in row["required_fields"].split(";") if code.strip()
        )
        table.setdefault(row["family"], {})[row["category"]] = codes
    return table


@dataclass(frozen=True)
class MissingInfo:
    """한 문의 유형 기준의 필요·제공·요청함·부족 정보 코드(모두 정렬)."""

    category_code: str
    required: tuple[str, ...]
    provided: tuple[str, ...]
    already_asked: tuple[str, ...]
    missing: tuple[str, ...]


def missing_info(
    category_code: str | None,
    provided_info: Iterable[str] | None,
    *,
    required_by_category: Mapping[str, frozenset[str]],
    already_asked: Iterable[str],
) -> MissingInfo | None:
    """필요 − 제공 − 이미 요청한 정보.

    Args:
        category_code: 주분류. 없으면 셀 수 없다.
        provided_info: 고객이 준 정보 코드(AI 값 또는 정정값).
        required_by_category: 문의 제품 계열의 유형별 필요 정보. 표에 없는 유형은 필요 정보가 없다.
        already_asked: 실제로 고객에게 나간 메시지에서 요청한 코드.

    Returns:
        MissingInfo | None: 주분류가 없으면 None.
    """
    if category_code is None:
        return None
    required = required_by_category.get(category_code, frozenset())
    provided = frozenset(provided_info or ())
    asked = frozenset(already_asked)
    return MissingInfo(
        category_code=category_code,
        required=tuple(sorted(required)),
        provided=tuple(sorted(provided)),
        already_asked=tuple(sorted(asked)),
        missing=tuple(sorted(required - provided - asked)),
    )
