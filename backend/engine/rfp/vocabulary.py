"""자사·협력사 사양의 속성 어휘와 제품 계열(KAN-185). 이 모듈이 단일 원본이다.

- 사양 사실(`partners.schemas.SpecFact.attribute`)과 추출 요구의 `attribute_key`가 같은 어휘를 쓴다. 전에는 Literal
  Attribute와 own_spec_facts.json의 속성이 두 겹이었다(파일은 이 어휘의 부분집합이어야 한다, 검사가 확인).
- 제품 계열은 own_spec_facts.json의 `family` 값이다.
- 어휘에 없으면 `OTHER`다. 엔진(engine)은 api를 import하지 않으므로 이 모듈은 다른 모듈에 기대지 않는다.
"""

import re
from typing import Literal, get_args

AttributeKey = Literal[
    "form",
    "rated_max_voltage",
    "bil",
    "short_time_withstand_current",
    "rated_current",
    "arc_resistant",
    "internal_arc_classification",
    "secondary_voltage",
    "primary_voltage",
    "rated_power",
    "insulation_class",
    "impedance",
    "tap",
    "forced_air_capacity_increase",
    "protection_functions",
    "arc_flash_reduction",
    # KAN-186 시연 A를 요구별로 나눈 속성(14번 2.5.L·G.1·E)과 상업 조건(보증, 18번).
    "winding_material",
    "winding_temperature_sensor",
    "communication_interface",
    "warranty_months",
    # 자사 사양 23종 정규화(KAN-186 scripts/rfp_own_spec_facts.py). 같은 숫자라도 다른 속성은 합치지 않는다:
    # 정격 사용 전압(Ue)은 정격 최대전압과, 모선 정격 전류는 정격 전류와 다른 속성이다.
    "rated_operational_voltage",
    "busbar_rated_current",
    "power_frequency_withstand",
    "breaking_capacity_icu",
    "breaking_capacity_ics",
    "rated_short_circuit_breaking_current",
    "frame_size",
    "number_of_poles",
    "vector_group",
    "protection_degree",
]
EquipmentFamily = Literal[
    "CONTROL",
    "DRIVE",
    "ESS",
    "HMI",
    "LV_BREAKER",
    # 저압 배전반·스위치기어 조립품(차단기 자체는 LV_BREAKER). 자사 제품이 없어 협력사만 채점된다.
    "LV_SWITCHGEAR",
    "METER",
    "MV_EQUIP",
    "PLC",
    "PV",
    "TRANSFORMER",
    "UPS",
]
# 값에 붙는 조건 이름. 자사·협력사 사양 근거의 conditions와 같은 이름이라 조건 비교가 맞물린다(자유 문구면 어긋난다).
ConditionName = Literal[
    "duration",
    "ambient_temperature",
    "temperature_rise",
    "reference_temperature",
    "frequency",
    "altitude",
    "voltage",
    "current_type",
    "duty",
    "cooling",
    "tap_type",
    "arc_current",
]
Other = Literal["OTHER"]
OTHER: Other = "OTHER"
# 추출 응답 스키마용 단일 Literal. Gemini 구조화 출력은 enum 둘의 anyOf(AttributeKey | Other)를 400
# INVALID_ARGUMENT로 거절한다(2026-10-30 실측). 그래서 OTHER를 같은 enum 안에 넣는다.
# 중첩 Literal은 하나로 펼쳐진다(PEP 586). 목록은 위 원본 그대로다.
AttributeKeyOrOther = Literal[AttributeKey, Other]
FamilyOrOther = Literal[EquipmentFamily, Other]
ConditionNameOrOther = Literal[ConditionName, Other]
# 값이 아직 정해지지 않았다는 표시(<Insert value>, TBD, [ ], ___). 판정·질문이 같은 규칙을 쓴다.
PLACEHOLDER = re.compile(
    r"<\s*insert\b[^<>]*>|\bTBD\b|\[\s*\]|_{3,}"
    # 한글은 \\b가 조사 앞에서 끊기지 않고 미정산 같은 낱말에도 걸려 글자로 막는다.
    r"|(?<![가-힣])미정(?:임|입니다|이다|으로)?(?![가-힣])"
    r"|(?:추후|차후|별도)\s*(?:협의|결정|확정|통보)"
    r"|협의\s*(?:후|하여)\s*(?:결정|확정)",
    re.I,
)
# 문장으로 적힌 값의 비교용 표기(canonical_value). 탭은 ±NxS% 또는 +Nx-MxS%(위 N·아래 M단, 단 간격 S%),
# 보호 기능은 L·S·I·G 순서의 문자 집합이다(업계 표기라 알파벳순이 아니다).
CANONICAL_FORMS: dict[str, re.Pattern[str]] = {
    "tap": re.compile(r"(?:±(\d+)|\+(\d+)x-(\d+))x(\d+(?:\.\d+)?)%"),
    "protection_functions": re.compile(r"(?=.)L?S?I?G?"),
}
ATTRIBUTE_KEYS: frozenset[str] = frozenset(get_args(AttributeKey))
EQUIPMENT_FAMILIES: frozenset[str] = frozenset(get_args(EquipmentFamily))
CONDITION_NAMES: frozenset[str] = frozenset(get_args(ConditionName))


def is_canonical(attribute_key: str, value: str) -> bool:
    """값이 그 속성의 정해진 비교용 표기인지.

    Args:
        attribute_key: 속성 어휘.
        value: 비교용 표기.

    Returns:
        bool: 표기가 정해진 속성이고 형식이 맞으면 True.
    """
    form = CANONICAL_FORMS.get(attribute_key)
    return form is not None and form.fullmatch(value) is not None


def read_canonical(attribute_key: str, text: str) -> str | None:
    """자사·협력사 값 표기를 비교용 표기로 읽는다(±2×2.5% → ±2x2.5%).

    Args:
        attribute_key: 속성 어휘.
        text: 사양 근거의 값.

    Returns:
        str | None: 비교용 표기. 정해진 표기로 읽지 못하면 None.
    """
    compact = re.sub(r"\s+", "", text).replace("×", "x")
    return compact if is_canonical(attribute_key, compact) else None


def is_placeholder(value: str | None) -> bool:
    """값 자리에 실제 값 대신 미정 표시가 있는지.

    Args:
        value: 요구값.

    Returns:
        bool: 자리표시자면 True.
    """
    return value is not None and PLACEHOLDER.search(value) is not None


# 형식(form) 문구에서 읽는 닫힌 축. 하이픈은 공백으로 보고 읽는다.
# 축 충돌은 협력사를 제외하니 그 형식을 명시한 말만 둔다. 한글도 같다. 단독 유입(빗물 유입), 몰드(몰드 케이스),
# 퓨즈(제어회로 퓨즈), 개폐는 형식을 말하지 않는다. 부하개폐기(load break, load interrupter)는
# 퓨즈가 없을 수 있고, epoxy는 코팅·도장에도 쓰며, metal-clad·metal-enclosed는 포함 관계로 볼 여지가 있다.
FORM_AXES: dict[str, dict[str, tuple[str, ...]]] = {
    "switching": {
        "FUSE_SWITCH": (
            "fused",
            "fusible",
            "퓨즈 개폐기",
            "퓨즈 부하개폐기",
            "퓨즈부 개폐기",
            "퓨즈부 부하개폐기",
            "퓨즈 내장",
        ),
        "VCB": ("vcb", "vacuum circuit breaker", "진공 차단기"),
        "ACB": ("acb", "air circuit breaker", "기중 차단기", "공기 차단기"),
        "MCCB": ("mccb", "molded case", "moulded case", "배선용 차단기", "몰드 케이스"),
    },
    "mounting": {
        "FIXED": ("fixed mount", "고정식", "고정형"),
        "DRAWOUT": ("drawout", "draw out", "withdrawable", "인출식", "인출형"),
    },
    "insulation": {
        "CAST_RESIN": (
            "cast resin",
            "몰드 변압기",
            "몰드형 변압기",
            "몰드식 변압기",
            "수지 몰드",
            "캐스트 레진",
        ),
        "OIL": ("oil immersed", "liquid filled", "유입식", "유입형", "유입 변압기"),
    },
}
_FORM_NEGATION = re.compile(
    r"\b(?:not|non|no|un|without|w/o|except|excluding|other than|instead of|rather than)\b"
    # 외는 옥외·외함에 걸리지 않게 낱말로 설 때만 본다.
    r"|아닌|아니|제외|없는|없이|불가|금지|이외|대신|(?<![가-힣])외(?:에|의)?(?![가-힣])"
)


def _form_word(word: str) -> str:
    """축 단어의 검색식. 한글은 조사가 붙고 띄어쓰기가 갈려 앞 글자만 막는다."""
    if word.isascii():
        return rf"\b{word}\b"
    return "(?<![가-힣a-z0-9])" + word.replace(" ", r"\s*")


def form_axes(text: str) -> dict[str, str]:
    """형식 문구에서 축별 값을 읽는다.

    Args:
        text: 형식 값.

    Returns:
        dict[str, str]: 축과 값. 부정어가 있으면 빈 값이고, 한 문구에 같은 축 값이 둘이면 그 축은 뺀다.
    """
    plain = text.lower().replace("-", " ")
    if _FORM_NEGATION.search(plain):
        return {}
    axes = {}
    for axis, values in FORM_AXES.items():
        found = [
            value
            for value, words in values.items()
            if any(re.search(_form_word(word), plain) for word in words)
        ]
        if len(found) == 1:
            axes[axis] = found[0]
    return axes


def form_conflict(wanted: str, offered: str) -> bool:
    """두 형식 문구가 같은 축에 다른 값을 적었는지 본다.

    Args:
        wanted: 요구의 형식.
        offered: 후보의 형식.

    Returns:
        bool: 충돌이면 True. 축을 못 읽거나 한쪽에만 있으면 충돌이 아니다.
    """
    theirs = form_axes(offered)
    return any(theirs.get(axis, value) != value for axis, value in form_axes(wanted).items())
