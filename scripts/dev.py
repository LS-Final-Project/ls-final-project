#!/usr/bin/env python3
"""Local Jira branch and commit helpers. Uses only the Python standard library."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys


KEY = re.compile(r"(?<![A-Za-z0-9])KAN-[1-9][0-9]*(?![A-Za-z0-9])")


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], text=True, capture_output=True, check=check)


def issue_keys(text: str) -> list[str]:
    return list(dict.fromkeys(KEY.findall(text)))


def setup() -> None:
    root = Path(git("rev-parse", "--show-toplevel").stdout.strip())
    existing = git("config", "--get", "core.hooksPath", check=False).stdout.strip()
    if existing and existing != ".githooks":
        raise ValueError(f"기존 core.hooksPath={existing} 설정을 보존했습니다. 기존 훅과 통합한 뒤 설정하세요.")
    default_hook = Path(git("rev-parse", "--git-path", "hooks/prepare-commit-msg").stdout.strip())
    if not existing and default_hook.exists():
        raise ValueError("기존 prepare-commit-msg 훅을 보존했습니다. 기존 훅과 통합한 뒤 설정하세요.")
    hook = root / ".githooks" / "prepare-commit-msg"
    if not hook.is_file():
        raise ValueError("이 브랜치에 .githooks/prepare-commit-msg가 없습니다. 자동화 변경을 먼저 받아 주세요.")
    hook.chmod(hook.stat().st_mode | 0o111)
    git("config", "--local", "team4.python", Path(sys.executable).as_posix())
    git("config", "--local", "core.hooksPath", ".githooks")
    print("설정 완료. 새 KAN 작업 브랜치의 커밋 메시지에 Jira 번호를 자동으로 넣습니다.")


def start(key: str, slug: str) -> None:
    if not re.fullmatch(r"KAN-[1-9][0-9]*", key):
        raise ValueError("Jira에서 확인한 실제 번호를 KAN-63 형태로 입력하세요.")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("작업명은 backend-skeleton처럼 영문 소문자, 숫자와 하이픈을 사용하세요.")
    branch = f"{key}-{slug}"
    git("check-ref-format", "--branch", branch)
    if git("status", "--porcelain").stdout.strip():
        raise ValueError("작업 중인 변경이 있습니다. 자신의 변경을 정리한 뒤 실행하세요. 자동으로 stash하거나 삭제하지 않습니다.")
    if git("show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0:
        raise ValueError(f"{branch} 브랜치가 이미 있습니다. 기존 브랜치를 확인하세요.")
    git("fetch", "origin", "dev")
    git("switch", "--no-track", "-c", branch, "origin/dev")
    print(f"작업 브랜치: {branch}")
    print("작업 후 git push -u origin HEAD로 올리고 dev 대상 Draft PR을 만드세요.")


def prepare_commit(message_path: str, source: str = "", *_: str) -> None:
    # Preserve Git-generated merge/squash messages and reused commit messages.
    if source in {"merge", "squash", "commit"}:
        return
    for state in ("rebase-merge", "rebase-apply"):
        if Path(git("rev-parse", "--git-path", state).stdout.strip()).exists():
            return
    branch = git("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if branch.returncode:
        return  # Detached HEAD, for example during an automated operation.
    keys = issue_keys(branch.stdout.strip())
    if not keys:
        raise ValueError("브랜치에 Jira 번호가 없습니다. KAN-63-task-name 형태의 작업 브랜치를 사용하세요.")
    path = Path(message_path)
    text = path.read_bytes().decode("utf-8", errors="surrogateescape")
    lines = text.splitlines(keepends=True)
    index = next((i for i, line in enumerate(lines) if line.strip() and not line.lstrip().startswith("#")), None)
    if index is None:
        text = " ".join(keys) + " \n" + text
    else:
        subject = lines[index].rstrip("\r\n")
        if subject.startswith(("fixup! ", "squash! ", "amend! ")):
            return  # Keep autosquash target matching intact.
        existing = issue_keys(subject)
        wrong = [key for key in existing if key not in keys]
        if wrong:
            raise ValueError(f"커밋 제목의 {', '.join(wrong)} 번호가 현재 브랜치와 다릅니다. 자동 교체하지 않았습니다.")
        missing = [key for key in keys if key not in existing]
        if not missing:
            return
        lines[index] = " ".join(missing) + " " + lines[index]
        text = "".join(lines)
    path.write_bytes(text.encode("utf-8", errors="surrogateescape"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="이 저장소의 로컬 커밋 훅 설정")
    begin = commands.add_parser("start", help="origin/dev에서 Jira 작업 브랜치 생성")
    begin.add_argument("key")
    begin.add_argument("slug")
    hook = commands.add_parser("prepare-commit-msg", help=argparse.SUPPRESS)
    hook.add_argument("hook_args", nargs="+")
    args = parser.parse_args()
    try:
        if args.command == "setup":
            setup()
        elif args.command == "start":
            start(args.key, args.slug)
        else:
            prepare_commit(*args.hook_args)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        print(f"오류: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
