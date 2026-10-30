"""추출 속성(모델이 쓴 자유 문구)을 자사 사양 어휘(own_spec_facts 속성 이름)로 맞춘다(KAN-199).

결정적 매핑 한 곳이다. 소문자·공백 정리 뒤 (1) 그대로 어휘 이름(공백 → 밑줄)이거나 (2) 아래 동의어 표에 있으면 어휘로
바꾸고, 아니면 원문을 둔다. 동의어 표는 14번 RFP 요구 속성 목록(305행)에서 뜻이 확실한 것만 넣었다. 장비가 달라
뜻이 갈리는 것(예: 계기용 변압기의 'secondary voltage rating')이나 다른 양(예: momentary ≠ short-time)은 넣지 않는다.
"""

import re
from collections.abc import Collection
from functools import cache

from backend.api.tft.rfp.comparison import load_own_products

ALIASES = {
    "basic impulse insulation level": "bil",
    "rated continuous current": "rated_current",
    "rated maximum voltage": "rated_max_voltage",
    "maximum voltage rating": "rated_max_voltage",
    "rated short-time withstand current": "short_time_withstand_current",
    "rated power-frequency withstand voltage": "power_frequency_withstand",
    "forced air cooling capacity increase": "forced_air_capacity_increase",
}


@cache
def own_vocabulary() -> frozenset[str]:
    """자사 사양의 속성 이름.

    Returns:
        frozenset[str]: own_spec_facts의 attribute 값.
    """
    return frozenset(f.attribute for p in load_own_products() for f in p.spec_facts)


def canonical(attribute: str, vocabulary: Collection[str] | None = None) -> str:
    """추출 속성을 자사 어휘로 바꾼다. 확실하지 않으면 원문 그대로다.

    Args:
        attribute: 추출 요구의 속성.
        vocabulary: 자사 어휘(기본은 own_spec_facts).

    Returns:
        str: 어휘 이름 또는 원문.
    """
    words = vocabulary if vocabulary is not None else own_vocabulary()
    text = re.sub(r"\s+", " ", attribute.strip().lower())
    if text.replace(" ", "_") in words:
        return text.replace(" ", "_")
    alias = ALIASES.get(text)
    return alias if alias is not None and alias in words else attribute
