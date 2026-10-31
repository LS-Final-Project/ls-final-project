"""KAN-157 이전 앱 호환 검사. 새 마이그레이션을 적용한 DB에서 이전 리비전 코드가 계속 도는지 본다.

배포 뒤 문제가 생겨 Cloud Run 트래픽을 이전 리비전으로 되돌려도, DB는 되돌리지 않는다(마이그레이션은 CD가
자동 실행하지 않고 되돌리는 SQL도 없다). 그래서 새 스키마가 이전 코드와 맞아야 롤백이 안전하다.

    # 1) 정적 검사: 이전 리비전 뒤로 마이그레이션 파일이 추가만 됐는지, 이전 표를 깨는 문장이 없는지
    uv run --frozen python -m scripts.compat_check migrations --base origin/main

    # 2) 실행 검사: 새 마이그레이션을 적용한 전용 DB에서 이전 리비전의 tests 전체를 돌린다
    uv run --frozen python -m scripts.compat_check run --base origin/main \
        --db postgresql://postgres:test-only@127.0.0.1:55433/kan77_auth_test

실행 검사는 loopback의 빈 kan77_auth_test DB(로컬 검사 DB와 같은 컨테이너)만 받는다. 현재 저장소의 CI DB 검사
목록으로 마이그레이션을 적용하고(검사 준비가 마이그레이션 적용의 기준이다), 스키마·권한 스냅샷을 뜬 뒤 이전
리비전 worktree에서 tests를 돌리고 다시 스냅샷을 떠 비교한다. 이전 검사의 준비 단계가 옛 권한을 다시 줘서
통과한 것처럼 보이는 경우를 스냅샷 차이로 잡는다. 모델·GCP는 부르지 않는다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = "infra/migrations"
WORKFLOW = ROOT / ".github/workflows/application-checks.yml"
DB_NAME = "kan77_auth_test"
NAME = r"[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*"
CREATE = re.compile(rf"create\s+(?:table|view)\s+(?:if\s+not\s+exists\s+)?({NAME})", re.I)
FUNCTION = re.compile(rf"create\s+(?:or\s+replace\s+)?function\s+({NAME})", re.I)
ALTER = re.compile(rf"alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?({NAME})(.*)", re.I | re.S)
REVOKE = re.compile(r"revoke\s+.+?\s+on\s+(.+?)\s+from\s+(.+)$", re.I | re.S)
ROLE = re.compile(r"[a-z_][a-z0-9_]*")
CREATE_ROLE = re.compile(r"create\s+role\s+([a-z_][a-z0-9_]*)", re.I)
BOOTSTRAP = "infra/sql"
ALL_IN_SCHEMA = re.compile(r"all\s+tables\s+in\s+schema\s+([a-z_][a-z0-9_]*)", re.I)
# 표가 아닌 대상(스키마·함수·시퀀스)의 권한 회수. 이전 앱 역할에서 회수하면 대상과 상관없이 확인한다.
NON_TABLE_TARGET = re.compile(
    r"^\s*(?:schema|function|procedure|routine|sequence|all\s+(?:sequences|functions|routines)\s+in)\b",
    re.I,
)
# 이전 표에 걸면 이전 코드가 깨지는 변경. 새 표에는 무엇을 해도 이전 코드와 상관없다.
BREAKING = (
    (re.compile(r"\bdrop\s+column\b", re.I), "DROP COLUMN"),
    (re.compile(r"\brename\b", re.I), "RENAME"),
    (re.compile(r"\balter\s+column\s+\w+\s+(?:set\s+data\s+)?type\b", re.I), "ALTER COLUMN TYPE"),
    (re.compile(r"\balter\s+column\s+\w+\s+set\s+not\s+null\b", re.I), "SET NOT NULL"),
    (re.compile(r"\balter\s+column\s+\w+\s+drop\s+default\b", re.I), "DROP DEFAULT"),
    (re.compile(r"\bset\s+schema\b", re.I), "SET SCHEMA"),
)
# 이전 표에 걸면 이전 코드의 쓰기가 거절될 수 있는 변경. 넓히는지 좁히는지는 실행 검사로 본다.
REVIEWED = (
    (re.compile(r"\bdrop\s+constraint\b", re.I), "DROP CONSTRAINT"),
    (
        re.compile(
            r"\badd\s+(?:constraint\b|primary\s+key|unique\b|foreign\s+key|check\b|exclude\b)", re.I
        ),
        "ADD CONSTRAINT",
    ),
    (re.compile(r"\bvalidate\s+constraint\b", re.I), "VALIDATE CONSTRAINT"),
    (re.compile(r"\b(?:enable|force)\s+row\s+level\s+security\b", re.I), "ROW LEVEL SECURITY"),
)
# ADD [COLUMN] 열 정의. 제약 추가(ADD CONSTRAINT/PRIMARY KEY/…)는 열이 아니다.
ADD_COLUMN = re.compile(
    r"\badd\s+(?!constraint\b|primary\b|unique\b|foreign\b|check\b|exclude\b)(?:column\s+)?", re.I
)
SNAPSHOT = """
WITH s AS (
  SELECT nspname FROM pg_namespace
  WHERE nspname NOT LIKE 'pg\\_%' AND nspname NOT IN ('information_schema', 'public')
)
SELECT 'col ' || table_schema || '.' || table_name || '.' || column_name || ' ' || data_type
       || ' null=' || is_nullable || ' def=' || coalesce(column_default, '')
  FROM information_schema.columns WHERE table_schema IN (SELECT nspname FROM s)
UNION ALL
SELECT 'grant ' || table_schema || '.' || table_name || ' ' || grantee || ' ' || privilege_type
  FROM information_schema.role_table_grants WHERE table_schema IN (SELECT nspname FROM s)
UNION ALL
SELECT 'colgrant ' || table_schema || '.' || table_name || '.' || column_name || ' ' || grantee
       || ' ' || privilege_type
  FROM information_schema.column_privileges WHERE table_schema IN (SELECT nspname FROM s)
UNION ALL
SELECT 'con ' || conrelid::regclass || ' ' || conname || ' ' || pg_get_constraintdef(oid)
  FROM pg_constraint WHERE connamespace::regnamespace::text IN (SELECT nspname FROM s)
UNION ALL
SELECT 'fn ' || p.oid::regprocedure || ' ' || md5(pg_get_functiondef(p.oid))
  FROM pg_proc p WHERE pronamespace::regnamespace::text IN (SELECT nspname FROM s) AND prokind = 'f'
UNION ALL
SELECT 'schema ' || nspname || ' ' || coalesce(nspacl::text, '') FROM pg_namespace
  WHERE nspname IN (SELECT nspname FROM s)
UNION ALL
SELECT 'fnacl ' || p.oid::regprocedure || ' ' || coalesce(p.proacl::text, '')
  FROM pg_proc p WHERE pronamespace::regnamespace::text IN (SELECT nspname FROM s)
UNION ALL
SELECT 'rel ' || c.oid::regclass || ' kind=' || c.relkind::text || ' rls=' || c.relrowsecurity
       || ' force=' || c.relforcerowsecurity || ' acl=' || coalesce(c.relacl::text, '')
  FROM pg_class c WHERE relnamespace::regnamespace::text IN (SELECT nspname FROM s)
   AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
UNION ALL
SELECT 'view ' || c.oid::regclass || ' ' || md5(pg_get_viewdef(c.oid))
  FROM pg_class c WHERE relnamespace::regnamespace::text IN (SELECT nspname FROM s)
   AND c.relkind IN ('v', 'm')
UNION ALL
SELECT 'index ' || schemaname || '.' || indexname || ' ' || indexdef
  FROM pg_indexes WHERE schemaname IN (SELECT nspname FROM s)
UNION ALL
SELECT 'policy ' || polrelid::regclass || ' ' || polname || ' ' || polcmd::text || ' '
       || coalesce(pg_get_expr(polqual, polrelid), '') || ' '
       || coalesce(pg_get_expr(polwithcheck, polrelid), '')
  FROM pg_policy
UNION ALL
SELECT 'trigger ' || tgrelid::regclass || ' ' || tgname || ' ' || pg_get_triggerdef(oid)
  FROM pg_trigger WHERE NOT tgisinternal
UNION ALL
SELECT 'enum ' || t.typnamespace::regnamespace || '.' || t.typname || ' ' || e.enumlabel
  FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid
UNION ALL
SELECT 'defacl ' || pg_get_userbyid(defaclrole) || ' '
       || coalesce(defaclnamespace::regnamespace::text, '*') || ' ' || defaclobjtype::text || ' '
       || defaclacl::text
  FROM pg_default_acl
UNION ALL
SELECT 'role ' || rolname || ' login=' || rolcanlogin || ' inherit=' || rolinherit
  FROM pg_roles WHERE rolname NOT LIKE 'pg\\_%'
UNION ALL
SELECT 'member ' || pg_get_userbyid(roleid) || ' -> ' || pg_get_userbyid(member)
  FROM pg_auth_members
ORDER BY 1
"""


@dataclass(frozen=True)
class Finding:
    """정적 검사 지적. BLOCK은 이전 코드가 깨지는 변경, REVIEW는 실행 검사로 확인할 변경이다."""

    level: str
    file: str
    table: str
    reason: str


def git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def statements(sql: str) -> list[str]:
    """주석을 지우고 세미콜론으로 나눈다. DO 블록 안의 문장도 각각 한 덩어리가 된다.

    문자열 안의 세미콜론·--는 구분하지 않는다(현재 마이그레이션에는 없다). 오탐·미탐은 실행 검사가 받친다.
    """
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    text = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
    return [part.strip() for part in text.split(";") if part.strip()]


def created_tables(sql: str) -> set[str]:
    """CREATE TABLE/VIEW로 만드는 schema.name들."""
    return {match.lower() for match in CREATE.findall(sql)}


def created_functions(sql: str) -> set[str]:
    """CREATE [OR REPLACE] FUNCTION으로 만드는 schema.name들."""
    return {match.lower() for match in FUNCTION.findall(sql)}


def created_roles(sql: str) -> set[str]:
    """CREATE ROLE로 만드는 역할(이전 리비전 앱이 쓰던 역할을 고르는 데 쓴다).

    GRANT … TO는 동적 SQL(format('… TO %I', …))의 변수 이름까지 잡으므로 쓰지 않는다. 마이그레이션 전용
    역할(*_migrator)은 실행 중인 앱이 쓰지 않으므로 뺀다.
    """
    roles = {role.lower() for role in CREATE_ROLE.findall(sql)}
    return {role for role in roles if not role.endswith("_migrator")}


def classify_file_changes(name_status: str) -> list[Finding]:
    """이전 리비전 뒤 마이그레이션 파일 변경. 추가(A) 말고는 모두 막는다.

    공용 DB에는 이미 적용한 파일을 다시 실행하지 않으므로, 고친 파일은 운영과 검사 DB의 스키마를 갈라 놓는다.
    """
    findings = []
    for line in name_status.splitlines():
        if not line.strip():
            continue
        status, *paths = line.split("\t")
        if not status.startswith("A"):
            findings.append(
                Finding("BLOCK", paths[-1], "", f"migration file {status} (added only)")
            )
    return findings


def check_statements(
    file: str,
    sql: str,
    base_tables: set[str],
    base_roles: set[str],
    base_functions: frozenset[str] = frozenset(),
) -> list[Finding]:
    """새 마이그레이션 한 파일에서 이전 표(base_tables)에 거는 위험 문장을 찾는다.

    Args:
        file: 저장소 상대 경로(보고용).
        sql: 파일 내용.
        base_tables: 이전 리비전 마이그레이션이 만든 schema.name들.
        base_roles: 이전 리비전에서 권한을 받던 역할. 새로 생긴 역할(worker 등)에서 회수한 것은 이전 코드와
            상관없으므로 보지 않는다.
        base_functions: 이전 리비전 마이그레이션이 만든 함수 schema.name들.

    Returns:
        list[Finding]: BLOCK(표 삭제, 열 삭제·이름 변경·형 변경·NOT NULL 강제·DEFAULT 삭제·스키마 이동,
            DEFAULT 없는 NOT NULL 열 추가)과 REVIEW(제약 추가·삭제·검증, 행 보안, 이전 앱 역할의 권한 회수,
            이전 함수의 교체·삭제, 인덱스·타입 삭제와 타입 변경, 이전 표의 정책 — 실행 검사로 확인한다).
    """
    findings = []
    for statement in statements(sql):
        flat = " ".join(statement.split())
        for match in re.finditer(
            rf"drop\s+(?:table|view)\s+(?:if\s+exists\s+)?({NAME})", flat, re.I
        ):
            if match.group(1).lower() in base_tables:
                findings.append(Finding("BLOCK", file, match.group(1).lower(), "DROP TABLE"))
        alter = ALTER.search(flat)
        if alter and alter.group(1).lower() in base_tables:
            table, body = alter.group(1).lower(), alter.group(2)
            for pattern, reason in BREAKING:
                if pattern.search(body):
                    findings.append(Finding("BLOCK", file, table, reason))
            for piece in ADD_COLUMN.split(body)[1:]:
                piece = re.split(r",\s*(?=add\b|alter\b|drop\b)", piece, maxsplit=1, flags=re.I)[0]
                # CHECK 식의 "x IS NOT NULL"은 열 제약이 아니다.
                piece = re.sub(r"\bis\s+not\s+null\b", "", piece, flags=re.I)
                if re.search(r"\bnot\s+null\b", piece, re.I) and not re.search(
                    r"\bdefault\b", piece, re.I
                ):
                    findings.append(
                        Finding("BLOCK", file, table, "ADD COLUMN NOT NULL without DEFAULT")
                    )
            for pattern, reason in REVIEWED:
                if pattern.search(body):
                    findings.append(Finding("REVIEW", file, table, reason))
        for match in re.finditer(
            rf"(?:create\s+or\s+replace|drop)\s+function\s+(?:if\s+exists\s+)?({NAME})", flat, re.I
        ):
            if match.group(1).lower() in base_functions:
                findings.append(
                    Finding("REVIEW", file, match.group(1).lower(), "FUNCTION REPLACED")
                )
        for match in re.finditer(
            rf"\b(drop\s+index|drop\s+type|alter\s+type)\s+(?:if\s+exists\s+)?(?:concurrently\s+)?({NAME})",
            flat,
            re.I,
        ):
            reason = " ".join(match.group(1).upper().split())
            findings.append(Finding("REVIEW", file, match.group(2).lower(), reason))
        policy = re.search(rf"\b(?:create|alter|drop)\s+policy\b.*?\bon\s+({NAME})", flat, re.I)
        if policy and policy.group(1).lower() in base_tables:
            findings.append(Finding("REVIEW", file, policy.group(1).lower(), "POLICY"))
        revoke = REVOKE.search(flat)
        if revoke and set(ROLE.findall(revoke.group(2).lower())) & base_roles:
            target = revoke.group(1)
            schema = ALL_IN_SCHEMA.search(target)
            if NON_TABLE_TARGET.search(target):
                hit = [" ".join(target.lower().split())]
            elif schema:
                hit = sorted(t for t in base_tables if t.split(".")[0] == schema.group(1).lower())
            else:
                hit = sorted(t for t in re.findall(NAME, target.lower()) if t in base_tables)
            roles = ",".join(sorted(set(ROLE.findall(revoke.group(2).lower())) & base_roles))
            for table in hit:
                findings.append(Finding("REVIEW", file, table, f"REVOKE FROM {roles}"))
    return findings


def migration_report(base: str) -> dict:
    """base 리비전 뒤 마이그레이션의 정적 검사 결과."""
    base_files = [
        p
        for p in git("ls-tree", "-r", "--name-only", base, MIGRATIONS).split()
        if p.endswith(".sql")
    ]
    base_tables: set[str] = set()
    base_roles: set[str] = set()
    base_functions: set[str] = set()
    for path in base_files:
        sql = git("show", f"{base}:{path}")
        base_tables |= created_tables(sql)
        base_roles |= created_roles(sql)
        base_functions |= created_functions(sql)
    # 앱 역할은 최초 구성 SQL(infra/sql)이 만든다.
    for path in git("ls-tree", "-r", "--name-only", base, BOOTSTRAP).split():
        if path.endswith(".sql"):
            base_roles |= created_roles(git("show", f"{base}:{path}"))
    name_status = git("diff", "--name-status", base, "HEAD", "--", MIGRATIONS)
    findings = classify_file_changes(name_status)
    added = [
        line.split("\t")[-1]
        for line in name_status.splitlines()
        if line.startswith("A") and line.endswith(".sql")
    ]
    for path in added:
        # 작업 트리가 아니라 커밋된 HEAD 내용을 본다.
        findings += check_statements(
            path, git("show", f"HEAD:{path}"), base_tables, base_roles, frozenset(base_functions)
        )
    return {
        "base": git("rev-parse", "--short", base).strip(),
        "base_tables": len(base_tables),
        "base_roles": sorted(base_roles),
        "base_functions": len(base_functions),
        "added": len(added),
        "block": [asdict(f) for f in findings if f.level == "BLOCK"],
        "review": [asdict(f) for f in findings if f.level == "REVIEW"],
    }


# 실행 검사가 작업 트리에서 읽는 경로. 커밋하지 않은 변경이 있으면 배포할 HEAD와 다른 내용을 검증하게 된다.
RUN_INPUTS = (MIGRATIONS, "tests", ".github/workflows", "backend", "pyproject.toml", "uv.lock")


def dirty_inputs(porcelain: str) -> list[str]:
    """git status --porcelain 출력에서 실행 검사 입력에 걸리는 변경 경로."""
    paths = [line[3:].split(" -> ")[-1] for line in porcelain.splitlines() if line.strip()]
    return [p for p in paths if p.startswith(RUN_INPUTS)]


def check_db_url(url: str) -> None:
    """loopback의 전용 검사 DB만 받는다."""
    info = conninfo_to_dict(url)
    if info.get("dbname") != DB_NAME or info.get("host") not in {"127.0.0.1", "localhost"}:
        raise ValueError(f"호환 검사는 loopback의 전용 {DB_NAME} DB만 씁니다.")


def db_test_list(workflow: str) -> list[str]:
    """CI가 전용 DB로 돌리는 검사 목록(마이그레이션 적용의 기준)."""
    match = re.search(r"pytest -q (tests/platform/test_authorization_store\.py[^\n]*)", workflow)
    if not match:
        raise ValueError("CI DB 검사 목록을 찾지 못했습니다.")
    return match.group(1).split()


def unapplied_migrations(root: Path = ROOT) -> list[str]:
    """검사 준비가 적용하지 않는 마이그레이션. 있으면 실행 검사가 그 파일을 못 보고 통과할 수 있다.

    cs·tft 검사 준비는 폴더 전체를 glob으로 적용하고, platform 검사 준비는 파일 이름을 하나씩 적는다.
    """
    tests = "\n".join(p.read_text() for p in (root / "tests").rglob("*.py"))
    missing = []
    for path in sorted((root / MIGRATIONS).glob("*/*.sql")):
        glob = f'Path("{MIGRATIONS}/{path.parent.name}").glob("*.sql")'
        if glob not in tests and path.name not in tests:
            missing.append(str(path.relative_to(root)))
    return missing


def snapshot(url: str) -> list[str]:
    with psycopg.connect(url) as conn:
        return [row[0] for row in conn.execute(SNAPSHOT)]


def snapshot_diff(before: list[str], after: list[str]) -> dict[str, list[str]]:
    """검사 전후 스키마 차이. 비어 있어야 이전 검사가 새 스키마 그대로에서 돈 것이다."""
    old, new = set(before), set(after)
    return {"removed": sorted(old - new), "added": sorted(new - old)}


# 하위 검사에 넘기지 않는 환경 변수. uv가 다른 리비전의 가상 환경을 쓰거나, 셸의 모델·클라우드 키가 새지 않게 한다.
DROPPED_ENV = ("UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV")
SECRET_ENV = re.compile(r"(API_KEY|SECRET|TOKEN|PASSWORD|CREDENTIALS)", re.I)


def test_env(url: str, source: dict[str, str] | None = None) -> dict[str, str]:
    """검사 프로세스 환경. 전용 DB 주소만 더하고 가상 환경 지정·비밀값은 뺀다."""
    env = {
        key: value
        for key, value in (os.environ if source is None else source).items()
        if key not in DROPPED_ENV and not SECRET_ENV.search(key)
    }
    return {**env, "AUTH_TEST_DATABASE_URL": url}


def pytest_run(args: list[str], cwd: Path, url: str) -> tuple[int, str]:
    env = test_env(url)
    result = subprocess.run(
        ["uv", "run", "--frozen", "pytest", "-q", "-p", "no:cacheprovider", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return result.returncode, (lines[-1] if lines else result.stderr.strip()[-500:])


def run_report(base: str, url: str) -> dict:
    """새 마이그레이션 DB에서 base 리비전의 tests 전체를 돌린 결과."""
    check_db_url(url)
    dirty = dirty_inputs(git("status", "--porcelain", "--untracked-files=all"))
    if dirty:
        raise RuntimeError(f"커밋하지 않은 변경이 있어 HEAD를 검증할 수 없습니다: {dirty[:5]}")
    missing = unapplied_migrations()
    if missing:
        raise RuntimeError(f"검사 준비가 적용하지 않는 마이그레이션이 있습니다: {missing}")
    with psycopg.connect(url) as conn:
        if conn.execute("SELECT to_regnamespace('identity')").fetchone()[0] is not None:
            raise ValueError("빈 DB에서 시작해야 합니다(새 컨테이너를 띄우세요).")
    code, summary = pytest_run(db_test_list(WORKFLOW.read_text()), ROOT, url)
    if code:
        raise RuntimeError(
            f"현재 리비전의 DB 검사가 실패해 마이그레이션 적용을 믿을 수 없습니다: {summary}"
        )
    before = snapshot(url)
    with tempfile.TemporaryDirectory(prefix="kan157-compat-") as tmp:
        tree = Path(tmp, "base")
        git("worktree", "add", "--detach", str(tree), base)
        try:
            base_code, base_summary = pytest_run(["tests"], tree, url)
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(tree)], cwd=ROOT, check=False
            )
            git("worktree", "prune")
    diff = snapshot_diff(before, snapshot(url))
    return {
        "base": git("rev-parse", "--short", base).strip(),
        "head": git("rev-parse", "--short", "HEAD").strip(),
        "current_db_tests": summary,
        "base_tests": base_summary,
        "base_tests_passed": base_code == 0,
        "snapshot_lines": len(before),
        "snapshot_changed": diff,
        "compatible": base_code == 0 and not diff["removed"] and not diff["added"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    static = sub.add_parser("migrations", help="마이그레이션 정적 검사")
    static.add_argument("--base", required=True, help="이전 리비전(예 origin/main)")
    live = sub.add_parser("run", help="새 스키마에서 이전 리비전 tests 실행")
    live.add_argument("--base", required=True)
    live.add_argument(
        "--db", required=True, help="loopback의 빈 kan77_auth_test 관리자 연결 문자열"
    )
    args = parser.parse_args()
    if args.command == "migrations":
        report = migration_report(args.base)
        ok = not report["block"]
    else:
        report = run_report(args.base, args.db)
        ok = report["compatible"]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
