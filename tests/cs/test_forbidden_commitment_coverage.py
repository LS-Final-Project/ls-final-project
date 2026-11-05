"""KAN-161 금지 약속 정규식의 다국어 범위 회귀. 네트워크·모델·DB 없음.

고정물 fixtures/forbidden_commitments_multilang.json은 2026-11-05 점검
(artifacts/presentation-checks-20261105/C-verifier-regex)의 187문장을 옮긴 것이다. 문장은 모델(Claude)이 쓴
합성 예문이고 실제 고객 자료가 아니다. 16개 언어, 금지 119문장·통제 68문장이다.

기준선(335f244b): 금지 재현율 19/119(en 9/18, ko 10/17, 나머지 14개 언어 각 0/6), 통제 오탐 0/68.
find_forbidden_commitment는 영어·한국어 패턴만 있는 보조 차단이라 나머지 언어 문장은 잡지 못한다.
놓친 100문장은 strict xfail이라 정규식을 넓혀 하나라도 잡히면 XPASS로 실패한다. 그때는 고정물의 caught를
true로 바꾸고 아래 기준선 수치를 고친다.
"""

import json
from pathlib import Path

import pytest

from backend.engine.cs.drafting import find_forbidden_commitment

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "forbidden_commitments_multilang.json").read_text(
        encoding="utf-8"
    )
)
ITEMS = [
    (f"{item['language']}-{item['category']}-{index}", item)
    for index, item in enumerate(FIXTURE["items"])
]
CONTROLS = [pytest.param(item, id=name) for name, item in ITEMS if item["kind"] == "control"]
CAUGHT = [
    pytest.param(item, id=name)
    for name, item in ITEMS
    if item["kind"] == "forbidden" and item["caught"]
]
MISSED = [
    pytest.param(
        item,
        id=name,
        marks=pytest.mark.xfail(
            strict=True, reason=f"정규식 범위 밖: {item['language']} {item.get('variant', 'base')}"
        ),
    )
    for name, item in ITEMS
    if item["kind"] == "forbidden" and not item["caught"]
]


def test_baseline_counts_match_the_fixture():
    """고정물의 기준선이 금지 19/119 적중, 통제 68문장이다."""
    forbidden = [item for _, item in ITEMS if item["kind"] == "forbidden"]
    assert (len(ITEMS), len(forbidden), len(CONTROLS)) == (187, 119, 68)
    assert (len(CAUGHT), len(MISSED)) == (19, 100)
    assert not any(item["caught"] for _, item in ITEMS if item["kind"] == "control")


@pytest.mark.parametrize("item", CONTROLS)
def test_conditional_controls_are_not_flagged(item):
    """조건부 안내 통제 문장은 어느 언어든 걸리지 않는다."""
    assert not find_forbidden_commitment(item["text"])


@pytest.mark.parametrize("item", CAUGHT)
def test_currently_caught_commitments_stay_caught(item):
    """지금 잡는 금지 문장 19개를 정규식 변경 뒤에도 계속 잡는다."""
    assert find_forbidden_commitment(item["text"])


@pytest.mark.parametrize("item", MISSED)
def test_known_gaps_are_still_missed(item):
    """지금 놓치는 금지 문장 100개. 잡히면 XPASS로 실패하니 고정물을 고친다."""
    assert find_forbidden_commitment(item["text"])
