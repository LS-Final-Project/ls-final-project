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
PLACEHOLDER = re.compile(r"<\s*insert\b[^<>]*>|\bTBD\b|\[\s*\]|_{3,}", re.I)
# 확정 수치: 단위가 붙은 숫자. 조건으로 시작하는 구절(at 60 Hz)과 규격 번호(IEC 60076)는 아니다.
QUANTITY = re.compile(r"^(?!\s*(?:at|per|for|in|with|under)\b).*\d\s*[a-zµΩ%°]", re.I)
# 한글 미정 표시는 일반 계약 문장에도 나온다("납기는 별도 협의"). 그래서 값 전체가 그 말일 때만 본다.
# 앞에 라벨 하나는 허용한다("용량: 미정"). 값이 함께 있는 문장을 미정으로 읽으면 판정이 사라진다.
OPEN_VALUE = re.compile(
    r"\s*(?:[^.。;:：\n]{1,30}[:：]\s*)?"
    r"(?:미정|(?:추후|차후|별도)\s*(?:협의|결정|확정|통보)|협의\s*(?:후|하여)\s*(?:결정|확정))"
    r"(?:임|입니다|이다)?\s*[.。]?\s*"
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


def has_placeholder(text: str | None) -> bool:
    """문장 안에 미정 항목이 있는지. 조항에 물어볼 것이 있는지를 판단한다.

    Args:
        text: 조항 문장이나 인용.

    Returns:
        bool: 미정 표시가 어디든 있으면 True.
    """
    if text is None:
        return False
    return PLACEHOLDER.search(text) is not None or OPEN_VALUE.fullmatch(text) is not None


def _marker_is_for_another_item(value: str) -> bool:
    """앞 구절에 확정 수치가 있고 표지는 이름을 가진 다른 항목에 붙었는지.

    기본은 미정이다. 조건·규격의 숫자(at 40 C, IEC 60076)는 값이 아니므로 이 둘이 함께일 때만 참이다.

    Args:
        value: 미정 표지가 든 값.

    Returns:
        bool: "630A, delivery TBD" 꼴이면 True.
    """
    parts = re.split(r"[.;,]\s+", value)
    marked = [i for i, part in enumerate(parts) if PLACEHOLDER.search(part)]
    # 표지가 둘 이상이면 어느 쪽이 값 자리인지 모른다.
    if len(marked) != 1:
        return False
    named = re.search(r"[^\W\d_]", PLACEHOLDER.split(parts[marked[0]])[0]) is not None
    return named and any(QUANTITY.search(part) for part in parts[: marked[0]])


def is_placeholder(value: str | None) -> bool:
    """값 자리에 실제 값 대신 미정 표시가 있는지.

    "Rated current 630 A. Delivery TBD."의 TBD는 다른 항목의 것이고, "<Insert value> kV"는 값 자리
    자체가 비어 있다. 문장 어디든 미정이 있는지는 has_placeholder가 본다.
    ponytail: 구절로만 가른다. "630 A <Insert date>"처럼 한 구절에 섞이면 미정이다(비교 제외 쪽).

    Args:
        value: 요구값이나 값의 인용.

    Returns:
        bool: 자리표시자면 True.
    """
    if value is None:
        return False
    if OPEN_VALUE.fullmatch(value) is not None:
        return True
    return PLACEHOLDER.search(value) is not None and not _marker_is_for_another_item(value)


# 형식(form) 문구에서 읽는 닫힌 축. 하이픈은 공백으로 보고 읽는다.
# 축 충돌은 협력사를 제외하니 그 형식을 명시한 말만 둔다. 한글도 같다. 단독 유입(빗물 유입), 단독 몰드(몰드 케이스는 MCCB),
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


# 표시 이름. 코드값은 저장·판정에 그대로 쓰고 초안·DOCX에 나갈 때만 바꾼다.
# 속성 이름은 화면 frontend/src/rfp/labels.ts ATTRIBUTE_LABEL과 같다.
ATTRIBUTE_TEXT: dict[str, str] = {
    "form": "형식",
    "rated_max_voltage": "정격 최대전압",
    "bil": "기준 충격 절연",
    "short_time_withstand_current": "단시간 내전류",
    "rated_current": "정격 전류",
    "arc_resistant": "아크 내성",
    "internal_arc_classification": "내부 아크 등급",
    "secondary_voltage": "2차 전압",
    "primary_voltage": "1차 전압",
    "rated_power": "정격 용량",
    "insulation_class": "절연 등급",
    "impedance": "임피던스",
    "tap": "탭",
    "forced_air_capacity_increase": "강제 공랭 용량 증가",
    "protection_functions": "보호 기능",
    "arc_flash_reduction": "아크플래시 저감",
    "winding_material": "권선 재질",
    "winding_temperature_sensor": "권선 온도 센서",
    "communication_interface": "통신 규격",
    "warranty_months": "보증 기간",
    "rated_operational_voltage": "정격 사용 전압",
    "busbar_rated_current": "모선 정격 전류",
    "power_frequency_withstand": "상용주파 내전압",
    "breaking_capacity_icu": "정격 한계 차단 용량",
    "breaking_capacity_ics": "정격 사용 차단 용량",
    "rated_short_circuit_breaking_current": "정격 단락 차단 전류",
    "frame_size": "프레임 크기",
    "number_of_poles": "극수",
    "vector_group": "결선",
    "protection_degree": "보호 등급",
}
CONDITION_TEXT: dict[str, str] = {
    "duration": "지속 시간",
    "ambient_temperature": "주위 온도",
    "temperature_rise": "온도 상승",
    "reference_temperature": "기준 온도",
    "frequency": "주파수",
    "altitude": "고도",
    "voltage": "전압",
    "current_type": "전류 종류",
    "duty": "사용 조건",
    "cooling": "냉각 방식",
    "tap_type": "탭 방식",
    "arc_current": "아크 전류",
}
# 협력사 필수 확인 항목. 화면 RfpSupplement.tsx checkLabel과 같다.
RESPONSIBILITY_TEXT: dict[str, str] = {
    "assembly": "조립 책임",
    "supply": "공급 책임",
    "install": "설치 책임",
    "test": "시험 책임",
    "calc": "계산 책임",
    "training": "교육 책임",
    "integration": "통합 책임",
    "warranty": "보증 책임",
}
CHECK_TEXT: dict[str, str] = {
    "supply_region": "공급 지역",
    "manufacturer_list": "제조사 목록",
    "contract_scope": "계약 범위 확인",
    "certification": "인증",
}


def check_text(name: str) -> str:
    """협력사 필수 확인 항목 이름을 표시 이름으로 바꾼다.

    Args:
        name: 등급 판정의 항목 이름(코드 또는 원문 속성).

    Returns:
        str: 표시 이름. 코드꼴인데 이름이 없으면 일반 이름, 원문 속성은 그대로다.
    """
    _, dot, tail = name.partition("integrator_capable.")
    if dot:
        return RESPONSIBILITY_TEXT.get(tail, "통합 수행 항목")
    for table in (ATTRIBUTE_TEXT, CONDITION_TEXT, CHECK_TEXT):
        if name in table:
            return table[name]
    return "사양 항목" if "_" in name or "." in name else name
