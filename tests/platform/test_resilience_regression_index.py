"""KAN-150: docs/resilience-regression.md의 검사 이름이 실제로 있는지 확인한다(이름이 바뀌면 표도 고친다).

백엔드 검사 이미지에는 docs/가 openapi만 들어가므로, 전체 checkout으로 도는 auth-database 작업에서 확인한다.
"""

import re
from pathlib import Path

import pytest

DOC = Path("docs/resilience-regression.md")
SECTIONS = (
    "작업 전달 중복",
    "체크포인트 재개",
    "토큰·권한 철회",
    "메일 결과 불명",
    "DB 상태 불변식",
)


@pytest.mark.skipif(
    not DOC.exists(), reason="docs/resilience-regression.md not in the backend image"
)
def test_every_listed_resilience_test_exists():
    text = DOC.read_text()
    listed = re.findall(r"`(tests/[\w/]+\.py)::(\w+)`", text)
    assert len(listed) >= 40
    missing = [
        f"{path}::{name}"
        for path, name in listed
        if not re.search(rf"^(async )?def {name}\(", Path(path).read_text(), re.MULTILINE)
    ]
    assert missing == []
    # 완료 조건 다섯 가지가 모두 절로 있다.
    assert all(section in text for section in SECTIONS)
