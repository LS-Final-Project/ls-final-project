"""DB 시험 파일이 CI auth-database 작업의 pytest 목록에 모두 있는지 본다.

목록은 파일을 하나씩 적는 방식이라, 빠진 파일은 실패가 아니라 실행 자체가 안 된다.
backend-* 작업은 DB가 없어 이 시험들을 건너뛰므로 초록불로 병합된다.
"""

import re
import sys
from pathlib import Path

WORKFLOW = Path(".github/workflows/application-checks.yml")
COMMAND = re.compile(r"pytest -q (tests/platform/test_authorization_store\.py[^\n]*)")


def listed(workflow: str) -> set[str]:
    """auth-database 작업이 부르는 시험 파일.

    Args:
        workflow: 작업 파일 내용.

    Returns:
        set[str]: pytest 인자로 적힌 경로.
    """
    match = COMMAND.search(workflow)
    if match is None:
        raise ValueError("auth-database pytest 명령을 찾지 못했습니다.")
    return set(match.group(1).split())


def problems(root: Path) -> list[str]:
    """목록과 저장소의 DB 시험 파일을 대조한다.

    Args:
        root: 저장소 위치.

    Returns:
        list[str]: 빠진 파일과 없는 파일 설명. 맞으면 빈 목록이다.
    """
    names = listed((root / WORKFLOW).read_text(encoding="utf-8"))
    found = {p.relative_to(root).as_posix() for p in (root / "tests").rglob("*_db.py")}
    gone = {name for name in names if not (root / name).exists()}
    return [f"auth-database 목록에 없음: {name}" for name in sorted(found - names)] + [
        f"목록에 있지만 파일이 없음: {name}" for name in sorted(gone)
    ]


def main() -> int:
    found = problems(Path("."))
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
