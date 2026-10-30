"""자사·협력사 사양의 속성 어휘와 제품 계열(KAN-185). 이 모듈이 단일 원본이다.

- 사양 사실(`partners.schemas.SpecFact.attribute`)과 추출 요구의 `attribute_key`가 같은 어휘를 쓴다. 전에는 Literal
  Attribute와 own_spec_facts.json의 속성이 두 겹이었다(파일은 이 어휘의 부분집합이어야 한다, 검사가 확인).
- 제품 계열은 own_spec_facts.json의 `family` 값이다.
- 어휘에 없으면 `OTHER`다. 엔진(engine)은 api를 import하지 않으므로 이 모듈은 다른 모듈에 기대지 않는다.
"""

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
    "METER",
    "MV_EQUIP",
    "PLC",
    "PV",
    "TRANSFORMER",
    "UPS",
]
Other = Literal["OTHER"]
OTHER: Other = "OTHER"
# 추출 응답 스키마용 단일 Literal. Gemini 구조화 출력은 enum 둘의 anyOf(AttributeKey | Other)를 400
# INVALID_ARGUMENT로 거절한다(2026-10-30 실측). 그래서 OTHER를 같은 enum 안에 넣는다.
# 중첩 Literal은 하나로 펼쳐진다(PEP 586). 목록은 위 원본 그대로다.
AttributeKeyOrOther = Literal[AttributeKey, Other]
FamilyOrOther = Literal[EquipmentFamily, Other]
ATTRIBUTE_KEYS: frozenset[str] = frozenset(get_args(AttributeKey))
EQUIPMENT_FAMILIES: frozenset[str] = frozenset(get_args(EquipmentFamily))
