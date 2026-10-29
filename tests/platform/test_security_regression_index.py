"""KAN-149: docs/security-regression.md의 검사 이름이 실제로 있는지 확인한다(이름이 바뀌면 표도 고친다).

백엔드 검사 이미지에는 docs/가 openapi만 들어가므로, 전체 checkout으로 도는 auth-database 작업에서 확인한다.
"""

import re
from pathlib import Path

import pytest

DOC = Path("docs/security-regression.md")


@pytest.mark.skipif(not DOC.exists(), reason="docs/security-regression.md not in the backend image")
def test_every_listed_security_test_exists():
    listed = re.findall(r"`(tests/[\w/]+\.py)::(\w+)`", DOC.read_text())
    assert len(listed) >= 30
    missing = [
        f"{path}::{name}"
        for path, name in listed
        if not re.search(rf"^(async )?def {name}\(", Path(path).read_text(), re.MULTILINE)
    ]
    assert missing == []
