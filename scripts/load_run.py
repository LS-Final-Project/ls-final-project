"""KAN-155 로컬 부하 시험. 전용 PostgreSQL에서 cs·tft·platform을 각각 별도 프로세스로 띄우고 가짜 모델로 잰다.

실제 모델·Microsoft·Google은 부르지 않는다. 운영 경로(web → platform 중계 → cs/tft)의 중계는 Google 서비스 ID
토큰이 필요해 로컬에서 그대로 띄울 수 없으므로, 업무 요청마다 플랫폼 세션 확인(GET /me)을 먼저 보내 중계가 플랫폼
DB에 주는 부하(요청당 세션 조회)를 근사한다. 업무 API는 로컬에서만 믿는 X-Team4-Principal 헤더로 직접 부른다.

    uv run --frozen python -m scripts.load_run prepare --admin-url postgresql://postgres:…@127.0.0.1:55434/kan155_load \
        --state /tmp/kan155/state.json
    uv run --frozen python -m scripts.load_run run --admin-url … --state … --profile normal --out /tmp/kan155/normal.json

DB는 loopback 주소의 전용 컨테이너(예 pgvector/pgvector:0.8.7-pg17-trixie, POSTGRES_DB=team4, 포트 55434)여야 한다.
prepare는 빈 DB에서만 시작하며 표식 테이블 public.kan155_load를 만들고, run은 그 표식이 있는 DB에서만 돈다.
그래서 같은 이름의 로컬 compose DB나 운영 DB에는 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[1]
# 역할 SQL(infra/sql/001_service_roles.sql)이 DB 이름 team4를 요구한다. 로컬 compose DB와 이름이 같으므로
# 빈 DB에서 시작할 때 만든 표식 테이블이 있는 DB에만 쓴다.
DB_NAME = "team4"
MARKER = "public.kan155_load"
APP_PASSWORD = "load-only"
WORKER_ROLE = "load_cs_worker"
SESSION_COOKIE = "__Host-team4_session"
TENANT = UUID("15515515-5155-4155-8155-155155155155")
CS_DEPT = "GCS_L1"

# KAN-155 가정안(Jira 댓글 10930): 사용자 160명, 동시 활동 약 27명·약 2 rps, SSE 10, 분석 작업 3.
USERS = {"cs_agent": 30, "dept_staff": 90, "manager": 12, "sales": 10}
TICKETS = 300


@dataclass(frozen=True)
class Profile:
    """한 번의 부하 조건. rps는 화면 조작(업무 요청) 도착률이고 각 조작은 세션 확인 1회를 더한다."""

    name: str
    rps: float
    sse_streams: int
    concurrent_jobs: int
    duration_s: float = 60.0
    # Cloud Run 서비스 한 인스턴스의 동시 요청 상한(infra/deploy_private.py --concurrency=20).
    instance_concurrency: int = 20


PROFILES = {
    "normal": Profile("normal", rps=2.0, sse_streams=10, concurrent_jobs=3),
    "triple": Profile("triple", rps=6.0, sse_streams=30, concurrent_jobs=9),
    # 첫 한계를 찾는 단계: SSE를 인스턴스 상한보다 많이 연다.
    "sse-limit": Profile("sse-limit", rps=2.0, sse_streams=40, concurrent_jobs=3),
    # Cloud Run이 최대 2 인스턴스까지 늘었을 때를 프로세스 하나의 슬롯 40으로 근사한다(CPU는 늘지 않음).
    "triple-max2": Profile(
        "triple-max2", rps=6.0, sse_streams=30, concurrent_jobs=9, instance_concurrency=40
    ),
}

# 가짜 모델 응답 시간(초). 분류는 gemini-3.6-flash v3 채택 실측의 평균 지연(valid 603건 6.81초,
# eval/policy/adoptions/classification-20261031-flash36-v3-out4096.md)이다. 초안·검증은 아직 가정값이며
# 실제 모델 소규모 측정(완료 조건)으로 고친다.
FAKE_LATENCY = {"classification": 6.8, "draft": 6.0, "verification": 2.5}

# 합격선 제안(Jira 댓글 10930). 3배 부하는 읽기·쓰기 구분 없이 p95.
THRESHOLDS = {
    "normal": {"read_p95": 0.8, "write_p95": 1.5, "failure_rate": 0.005},
    "triple": {"read_p95": 2.0, "write_p95": 2.0, "failure_rate": 0.02},
    "triple-max2": {"read_p95": 2.0, "write_p95": 2.0, "failure_rate": 0.02},
}

# (이름, 역할, 가중치, 방법, 경로 틀, 쓰기 여부). {ticket}은 합성 문의 하나로 바뀐다.
ACTIONS = (
    ("cs.tickets.list", "cs_agent", 20, "GET", "/api/v1/cs/tickets?limit=50", False),
    ("cs.ticket.detail", "cs_agent", 20, "GET", "/api/v1/cs/tickets/{ticket}", False),
    ("cs.ticket.analysis", "cs_agent", 12, "GET", "/api/v1/cs/tickets/{ticket}/analysis", False),
    ("cs.ticket.workflow", "cs_agent", 8, "GET", "/api/v1/cs/tickets/{ticket}/workflow", False),
    ("cs.tickets.create", "cs_agent", 4, "POST", "/api/v1/cs/tickets", True),
    ("cs.improvements.list", "dept_staff", 10, "GET", "/api/v1/cs/improvement-requests", False),
    ("cs.tickets.dept", "dept_staff", 8, "GET", "/api/v1/cs/tickets?limit=50", False),
    ("cs.stats.overview", "manager", 5, "GET", "/api/v1/cs/stats/overview", False),
    ("tft.projects.list", "sales", 5, "GET", "/api/v1/tft/projects", False),
    (
        "platform.notifications",
        "any",
        8,
        "GET",
        "/api/v1/platform/notifications/unread-count",
        False,
    ),
)


def check_admin_url(url: str) -> dict[str, Any]:
    """loopback의 team4 DB만 받는다(표식 검사는 marked가 한다).

    Args:
        url: 관리자 연결 문자열.

    Returns:
        dict: 해석한 연결 정보.

    Raises:
        ValueError: DB 이름이 team4가 아니거나 loopback 주소가 아닐 때.
    """
    info = conninfo_to_dict(url)
    if info.get("dbname") != DB_NAME or info.get("host") not in {"127.0.0.1", "localhost"}:
        raise ValueError(f"부하 시험은 loopback의 전용 {DB_NAME} DB만 씁니다.")
    return info


def marked(conn: psycopg.Connection) -> bool:
    """이 DB가 부하 시험 전용으로 만들어졌는지(표식 테이블) 본다."""
    return conn.execute("SELECT to_regclass(%s)", (MARKER,)).fetchone()[0] is not None


def app_url(admin_url: str, user: str) -> str:
    """같은 DB의 실행 계정 연결 문자열."""
    return make_conninfo(admin_url, user=user, password=APP_PASSWORD)


def sql_file(path: Path) -> str:
    """psql 메타 명령(\\set 등) 줄을 뺀 SQL 본문. 검사 conftest와 같은 방식이다."""
    return "\n".join(line for line in path.read_text().splitlines() if not line.startswith("\\"))


def apply_schema(admin_url: str) -> None:
    """infra/local/init.sh와 같은 순서로 역할·확장·마이그레이션 전체를 적용한다.

    Raises:
        ValueError: 표식 없는 DB에 이미 스키마가 있을 때(로컬 compose DB 등 다른 DB).
    """
    with psycopg.connect(admin_url, autocommit=True) as conn:
        if marked(conn):
            raise ValueError("이미 준비한 부하 DB입니다. 새 컨테이너에서 다시 준비하세요.")
        if conn.execute("SELECT to_regclass('identity.users')").fetchone()[0] is not None:
            raise ValueError("빈 DB가 아닙니다. 부하 시험 전용 컨테이너를 새로 띄우세요.")
        conn.execute(sql_file(ROOT / "infra/sql/001_service_roles.sql"))
        for role in ("cs_app", "tft_app", "platform_app"):
            conn.execute(f"ALTER ROLE {role} PASSWORD '{APP_PASSWORD}'")
        conn.execute(sql_file(ROOT / "infra/local/extensions.sql"))
        for service in ("cs", "tft", "platform"):
            for migration in sorted((ROOT / "infra/migrations" / service).glob("*.sql")):
                conn.execute(sql_file(migration))
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (WORKER_ROLE,)).fetchone():
            conn.execute(f"CREATE ROLE {WORKER_ROLE} LOGIN PASSWORD '{APP_PASSWORD}'")
            conn.execute(f"GRANT cs_worker TO {WORKER_ROLE}")
            # 역할 SQL이 PUBLIC 접속을 막으므로 worker 로그인에 접속 권한을 따로 준다.
            conn.execute(f"GRANT CONNECT ON DATABASE {DB_NAME} TO {WORKER_ROLE}")
        # 표식은 준비가 모두 끝난 뒤에 만든다(중간 실패한 DB에서 run이 돌지 않게).


def platform_settings(admin_url: str):
    """플랫폼 실행 계정 설정(세션 발급·작업 접수용)."""
    from backend.shared.settings import Settings

    return Settings(
        _env_file=None,
        environment="local",
        database_url=SecretStr(app_url(admin_url, "platform_app")),
    )


def prepare(admin_url: str, state_path: Path) -> dict[str, Any]:
    """스키마·합성 사용자·운영 세션·합성 문의를 만들고 상태 파일(0600)에 적는다.

    Args:
        admin_url: 전용 DB 관리자 연결.
        state_path: 세션 토큰을 담는 상태 파일. 저장소 밖에 둔다.

    Returns:
        dict: 사용자·문의 목록.
    """
    from backend.api.platform.auth.callback import MicrosoftSubject
    from backend.api.platform.auth.sessions import issue_session

    check_admin_url(admin_url)
    apply_schema(admin_url)
    users: list[dict[str, Any]] = []
    with psycopg.connect(admin_url) as conn:
        depts = [
            r[0]
            for r in conn.execute(
                "SELECT code FROM identity.departments WHERE code<>%s ORDER BY code", (CS_DEPT,)
            )
        ]
        conn.execute(
            "DELETE FROM identity.sessions WHERE user_id IN "
            "(SELECT id FROM identity.users WHERE tid=%s)",
            (TENANT,),
        )
        for role, count in USERS.items():
            for index in range(count):
                user_id = uuid4()
                dept = depts[index % len(depts)] if role == "dept_staff" else None
                conn.execute(
                    "INSERT INTO identity.users(id,tid,oid,email,display_name,dept) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (
                        user_id,
                        TENANT,
                        user_id,
                        f"load-{role}-{index}@example.invalid",
                        f"부하 {role} {index}",
                        dept,
                    ),
                )
                conn.execute(
                    "INSERT INTO identity.user_roles(user_id,role) VALUES (%s,%s)", (user_id, role)
                )
                managed: list[str] = []
                if role == "manager":
                    managed = [CS_DEPT, depts[index % len(depts)]]
                    for code in managed:
                        conn.execute(
                            "INSERT INTO identity.manager_departments(user_id,dept_code) "
                            "VALUES (%s,%s)",
                            (user_id, code),
                        )
                users.append({"id": str(user_id), "role": role, "dept": dept, "managed": managed})
        tickets = []
        for index in range(TICKETS):
            ticket_id = uuid4()
            conn.execute(
                "INSERT INTO cs.tickets (id, source, source_instance, external_id, data_origin, "
                "derivation_kind, received_at, known_at, channel, body_language, "
                "customer_external_id, product_code, subject, body) VALUES (%s, 'test', "
                "'kan155load', %s, 'synthetic', 'PROVIDED', now(), now(), 'EMAIL', 'en', "
                "'CUST-0374', 'TRF-OIL-1000', 'Oil leak', 'Oil leakage at gasket.')",
                (ticket_id, f"LOAD-{index}-{ticket_id}"),
            )
            conn.execute(
                "INSERT INTO cs.ticket_source_records (ticket_id, data_origin, customer_tier) "
                "VALUES (%s, 'synthetic', 'GOLD')",
                (ticket_id,),
            )
            tickets.append(str(ticket_id))
    settings = platform_settings(admin_url)
    for user in users:
        grant = asyncio.run(issue_session(settings, MicrosoftSubject(TENANT, UUID(user["id"]))))
        user["token"] = grant.token.get_secret_value()
    state = {"users": users, "tickets": tickets}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.touch(mode=0o600)
    state_path.write_text(json.dumps(state))
    # 표식은 준비가 모두 끝난 뒤에 만든다. 중간에 실패한 DB는 표식이 없어 run도 다시 prepare도 거부한다.
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(f"CREATE TABLE {MARKER} (created_at timestamptz NOT NULL DEFAULT now())")
    return state


# ---- 실행 ----


@dataclass
class Sample:
    """요청 하나의 결과."""

    name: str
    seconds: float
    status: int
    write: bool
    # 세션 확인에서 막혀 업무 요청을 보내지 못한 조작(401·403 포함). 사용자에게는 실패다.
    blocked: bool = False


@dataclass
class Recorder:
    """스레드·코루틴이 함께 쓰는 측정 기록."""

    samples: list[Sample] = field(default_factory=list)
    sse: list[dict[str, Any]] = field(default_factory=list)
    jobs: list[dict[str, Any]] = field(default_factory=list)
    db: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    # 지금 200으로 열려 있는 SSE 수(표본 수집기가 0.5초마다 함께 적는다).
    open_sse: int = 0
    sampler_error: str | None = None

    def add(self, kind: str, value: Any) -> None:
        with self.lock:
            getattr(self, kind).append(value)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def service_env(admin_url: str, service: str) -> dict[str, str]:
    """서비스 프로세스 환경. 상속 환경의 모델·클라우드 설정은 넘기지 않는다."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": str(ROOT),
        "ENVIRONMENT": "local",
        "SERVICE_NAME": service,
        "DEV_AUTH_ENABLED": "false",
        "DATABASE_URL": app_url(admin_url, f"{service}_app"),
    }
    if service == "platform":
        # 운영 세션 쿠키 검사를 켜기 위한 합성 값. 로그인 절차를 타지 않으므로 Microsoft를 부르지 않는다.
        env |= {
            "MICROSOFT_AUTH_ENABLED": "true",
            "MICROSOFT_AUTH_CLIENT_ID": "15515515-0000-4000-8000-000000000155",
            "MICROSOFT_AUTH_CLIENT_SECRET": "load-test-not-a-secret",
            "MICROSOFT_AUTH_APP_ORIGIN": "https://load.invalid",
            "MICROSOFT_AUTH_REDIRECT_URI": "https://load.invalid/api/v1/platform/auth/callback",
        }
    return env


def start_services(admin_url: str, concurrency: int, workdir: Path) -> dict[str, Any]:
    """cs·tft·platform을 각각 uvicorn 프로세스 하나(Cloud Run 인스턴스 하나에 해당)로 띄운다."""
    procs: dict[str, Any] = {}
    try:
        _launch(admin_url, concurrency, workdir, procs)
    except BaseException:
        stop_services(procs)
        raise
    return procs


def stop_services(procs: dict[str, Any]) -> None:
    """서비스 프로세스를 끝낸다. 10초 안에 끝나지 않으면 강제로 끝낸다."""
    for item in procs.values():
        item["proc"].terminate()
    for item in procs.values():
        try:
            item["proc"].wait(timeout=10)
        except subprocess.TimeoutExpired:
            item["proc"].kill()
            item["proc"].wait()
        item["log"].close()


def _launch(admin_url: str, concurrency: int, workdir: Path, procs: dict[str, Any]) -> None:
    """서비스 셋을 띄우고 /health가 200이 될 때까지 기다린다. 띄운 것은 procs에 바로 넣는다."""
    for service in ("cs", "tft", "platform"):
        port = free_port()
        log = (workdir / f"{service}.log").open("w")
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                f"backend.api.{service}.main:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--limit-concurrency",
                str(concurrency),
                "--log-level",
                "warning",
                "--no-access-log",
            ],
            cwd=workdir,  # 저장소의 .env를 읽지 않게 빈 작업 폴더에서 띄운다.
            env=service_env(admin_url, service),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        procs[service] = {"proc": proc, "url": f"http://127.0.0.1:{port}", "log": log}
    deadline = time.monotonic() + 60
    for service, item in procs.items():
        while True:
            if item["proc"].poll() is not None:
                raise RuntimeError(
                    f"{service} 시작 실패: {(workdir / f'{service}.log').read_text()[-2000:]}"
                )
            try:
                if httpx.get(item["url"] + "/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError(f"{service}가 60초 안에 뜨지 않았습니다.")
            time.sleep(0.2)


def principal_headers(user: dict[str, Any]) -> dict[str, str]:
    """로컬 cs/tft가 믿는 사용자 헤더(운영에서는 중계가 세션으로 만든다)."""
    from backend.shared.access import Principal, principal_header

    return principal_header(
        Principal(
            user_id=UUID(user["id"]),
            roles=frozenset({user["role"]}),
            dept=user["dept"],
            managed_departments=frozenset(user["managed"]),
        )
    )


def ticket_body() -> dict[str, Any]:
    return {
        "channel": "EMAIL",
        "body_language": "en",
        "customer_external_id": "CUST-0374",
        "product_code": "TRF-OIL-1000",
        "subject": "Load test inquiry",
        "body": "Synthetic load test inquiry body.",
    }


async def one_action(
    client: httpx.AsyncClient,
    urls: dict[str, str],
    state: dict[str, Any],
    rec: Recorder,
    rng: random.Random,
) -> None:
    """화면 조작 하나: 세션 확인(중계 근사) 뒤 업무 요청."""
    name, role, _, method, path, write = rng.choices(ACTIONS, weights=[a[2] for a in ACTIONS])[0]
    pool = [u for u in state["users"] if role in {"any", u["role"]}]
    user = rng.choice(pool)
    cookie = {"Cookie": f"{SESSION_COOKIE}={user['token']}"}
    started = time.perf_counter()
    try:
        me = await client.get(urls["platform"] + "/api/v1/platform/me", headers=cookie)
        status = me.status_code
    except httpx.HTTPError:
        status = 0
    rec.add("samples", Sample("relay.session", time.perf_counter() - started, status, False))
    if status != 200:
        # 중계가 세션 확인에서 막히면 사용자는 그 조작이 실패한 것이다.
        now = time.perf_counter()
        rec.add("samples", Sample(name, now - started, status, write, blocked=True))
        rec.add(
            "samples",
            Sample("total." + ("write" if write else "read"), now - started, status, write, True),
        )
        return
    service = path.split("/")[3]
    url = urls[service] + path.replace("{ticket}", rng.choice(state["tickets"]))
    headers = cookie if service == "platform" else principal_headers(user)
    begun = time.perf_counter()
    try:
        response = await client.request(
            method, url, headers=headers, json=ticket_body() if write else None
        )
        code = response.status_code
    except httpx.HTTPError:
        code = 0
    now = time.perf_counter()
    rec.add("samples", Sample(name, now - begun, code, write))
    rec.add(
        "samples", Sample("total." + ("write" if write else "read"), now - started, code, write)
    )


async def interactive(urls, state, rec, profile: Profile, stop: float, seed: int) -> None:
    """도착 간격이 지수분포인 열린 부하(응답을 기다리지 않고 다음 조작을 보낸다)."""
    rng = random.Random(seed)
    # 쉬는 keep-alive 연결도 uvicorn --limit-concurrency 슬롯을 차지하므로(열린 연결 수를 센다) 남기지 않는다.
    # Cloud Run --concurrency는 처리 중 요청만 센다.
    limits = httpx.Limits(max_connections=500, max_keepalive_connections=0)
    async with httpx.AsyncClient(timeout=30, limits=limits) as client:
        tasks = []
        while time.monotonic() < stop:
            tasks.append(asyncio.create_task(one_action(client, urls, state, rec, rng)))
            await asyncio.sleep(rng.expovariate(profile.rps))
        await asyncio.gather(*tasks)


async def sse_stream(urls, state, rec, jobs: list[str], stop: float, seed: int) -> None:
    """C/S 상담원 한 명이 분석 작업 진행을 SSE로 본다. 끝나면 1초 뒤 다른 작업에 다시 붙는다."""
    rng = random.Random(seed)
    agents = [u for u in state["users"] if u["role"] == "cs_agent"]
    limits = httpx.Limits(max_keepalive_connections=0)
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=40), limits=limits) as client:
        while time.monotonic() < stop:
            if not jobs:
                await asyncio.sleep(0.2)
                continue
            user, job = rng.choice(agents), rng.choice(jobs[-5:])
            started = time.perf_counter()
            opened_at = time.monotonic()
            record: dict[str, Any] = {"status": 0, "connect_s": None, "events": 0, "held_s": 0.0}
            try:
                async with client.stream(
                    "GET",
                    f"{urls['platform']}/api/v1/platform/jobs/{job}/events",
                    headers={"Cookie": f"{SESSION_COOKIE}={user['token']}"},
                ) as response:
                    record["status"] = response.status_code
                    record["connect_s"] = time.perf_counter() - started
                    if response.status_code == 200:
                        with rec.lock:
                            rec.open_sse += 1
                        try:
                            async for line in response.aiter_lines():
                                if line.startswith("id:"):
                                    record["events"] += 1
                                if time.monotonic() >= stop:
                                    break
                        finally:
                            with rec.lock:
                                rec.open_sse -= 1
            except httpx.HTTPError as error:
                record["error"] = type(error).__name__
            # 부하 시간이 끝난 뒤 하트비트를 기다린 시간은 세지 않는다.
            record["held_s"] = max(0.0, min(time.monotonic(), stop) - opened_at)
            rec.add("sse", record)
            await asyncio.sleep(1)


def slow(provider, seconds: float, rng: random.Random):
    """가짜 공급자 앞에서 실제 모델 응답 시간만큼(±30%) 기다린다."""

    def call(payload):
        time.sleep(seconds * rng.uniform(0.7, 1.3))
        return provider(payload)

    return call


def run_one_job(job_id: UUID, seed: int) -> str:
    """worker 프로세스 하나에서 작업 하나를 가짜 모델로 처리한다(Cloud Run Jobs의 작업 하나에 해당).

    문의 출처가 작업 ID를 CS_JOB_ID 환경 변수에서 읽으므로(backend/api/cs/worker_source.py) 작업마다 프로세스를
    따로 띄운다. DB 연결은 DATABASE_URL(worker 로그인)이다.
    """
    from backend.agent.cs_worker import CsWorkerSettings, execute_job
    from backend.engine.cs.graph import build_graph
    from backend.shared.settings import Settings
    from tests.cs.graph_fakes import (
        FILTERS,
        classification_provider,
        draft_provider,
        fts_searcher,
        verification_provider,
    )

    rng = random.Random(seed)

    def build(_settings, parts, saver):
        return build_graph(
            classification_provider=slow(
                classification_provider, FAKE_LATENCY["classification"], rng
            ),
            draft_provider=slow(draft_provider, FAKE_LATENCY["draft"], rng),
            verification_provider=slow(verification_provider, FAKE_LATENCY["verification"], rng),
            fts_searcher=fts_searcher,
            search_filters=FILTERS,
            allowed_info_codes=parts.allowed_info_codes,
            load_ticket=parts.ticket_source.load_ticket,
            record_attempt=parts.ticket_source.record_attempt,
            checkpointer=saver,
        )

    settings = Settings(_env_file=None, environment="local")
    return execute_job(
        settings, CsWorkerSettings(_env_file=None, enabled=True), job_id, build=build
    )


def worker_env(admin_url: str, job: UUID) -> dict[str, str]:
    """worker 프로세스 환경. 서비스와 같이 상속 환경의 모델·클라우드 설정은 넘기지 않는다."""
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": str(ROOT),
        "CS_JOB_ID": str(job),
        "DATABASE_URL": app_url(admin_url, WORKER_ROLE),
    }


def job_worker(
    admin_url: str,
    state,
    rec: Recorder,
    jobs: list[str],
    tickets: list[str],
    stop: float,
    seed: int,
    workdir: Path,
) -> None:
    """분석 작업을 접수하고 worker 프로세스를 띄워 처리한다. 연속 3회 실패하면 멈춘다."""
    from backend.api.platform.jobs.cs import CsJobRequest, admit_cs_job
    from backend.shared.database import connection

    rng = random.Random(seed)
    settings = platform_settings(admin_url)
    agents = [u for u in state["users"] if u["role"] == "cs_agent"]

    async def admit(ticket: str) -> UUID:
        request = CsJobRequest(
            ticket_id=UUID(ticket),
            input_version=hashlib.sha256(ticket.encode()).hexdigest(),
            idempotency_key=f"load-{uuid4()}",
            requested_by=UUID(rng.choice(agents)["id"]),
        )
        async with connection(settings) as conn, conn.transaction():
            return (await admit_cs_job(conn, request)).job_id

    errors = 0
    while time.monotonic() < stop and errors < 3:
        with rec.lock:
            if not tickets:
                return
            ticket = tickets.pop()
        started = time.perf_counter()
        record: dict[str, Any] = {"result": None}
        try:
            job = asyncio.run(admit(ticket))
            with rec.lock:
                jobs.append(str(job))
            record["admit_s"] = time.perf_counter() - started
            env = worker_env(admin_url, job)
            done = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "scripts.load_run",
                    "job",
                    "--job-id",
                    str(job),
                    "--seed",
                    str(rng.randrange(1 << 30)),
                ],
                cwd=workdir,
                env=env,
                capture_output=True,
                text=True,
                timeout=300,
            )
            lines = done.stdout.strip().splitlines()
            record["result"] = (
                lines[-1]
                if done.returncode == 0 and lines
                else ("WORKER_EXIT_" + str(done.returncode))
            )
            errors = 0 if done.returncode == 0 else errors + 1
        except Exception as error:  # noqa: BLE001 - 부하 중 실패는 집계한다.
            record["result"] = getattr(error, "code", type(error).__name__)
            errors += 1
        record["seconds"] = time.perf_counter() - started
        rec.add("jobs", record)


def db_sampler(admin_url: str, rec: Recorder, stop: threading.Event) -> None:
    """0.5초마다 계정별 DB 연결 수를 센다. 실패하면 오류를 남기고 멈춘다(요약의 표본 수로 드러난다)."""
    try:
        with psycopg.connect(admin_url, autocommit=True) as conn:
            while not stop.is_set():
                rows = conn.execute(
                    "SELECT usename, count(*) FROM pg_stat_activity WHERE datname=%s "
                    "AND pid<>pg_backend_pid() GROUP BY 1",
                    (DB_NAME,),
                ).fetchall()
                # 백그라운드 프로세스는 usename이 비어 있다.
                counts = {r[0]: r[1] for r in rows if r[0]}
                rec.add("db", {"t": time.monotonic(), "open_sse": rec.open_sse, **counts})
                stop.wait(0.5)
    except Exception as error:  # noqa: BLE001 - 표본 수집 실패는 요약에 남긴다.
        rec.sampler_error = type(error).__name__


def sessions_opened(admin_url: str) -> int:
    """이 DB에 지금까지 맺어진 연결 수(pg_stat_database.sessions, PostgreSQL 14+)."""
    with psycopg.connect(admin_url, autocommit=True) as conn:
        return conn.execute(
            "SELECT sessions FROM pg_stat_database WHERE datname=%s", (DB_NAME,)
        ).fetchone()[0]


def percentile(values: list[float], q: float) -> float | None:
    """선형 보간 백분위(docs/evaluation.md KAN-88과 같은 (n-1)q 방식). 빈 목록은 None."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summarize(rec: Recorder, profile: Profile, opened: int, wall: float) -> dict[str, Any]:
    """요청·SSE·작업·DB를 요약하고 합격선과 견준다."""
    groups: dict[str, list[Sample]] = defaultdict(list)
    for sample in rec.samples:
        groups[sample.name].append(sample)
    requests = {}
    for name, samples in sorted(groups.items()):
        seconds = [s.seconds for s in samples]
        failed = [
            s for s in samples if s.blocked or s.status == 0 or s.status >= 500 or s.status == 429
        ]
        requests[name] = {
            "count": len(samples),
            "p50_s": percentile(seconds, 0.5),
            "p95_s": percentile(seconds, 0.95),
            "max_s": max(seconds),
            "failures": len(failed),
            "statuses": dict(sorted(_count(s.status for s in samples).items())),
        }
    meta = {"t", "open_sse"}
    users = {k for row in rec.db for k in row if k not in meta}
    db = {
        "peak_total": max(
            (sum(v for k, v in row.items() if k not in meta) for row in rec.db), default=0
        ),
        "peak_by_user": {u: max(row.get(u, 0) for row in rec.db) for u in sorted(users)},
        "sessions_opened": opened,
        "sessions_opened_per_s": round(opened / wall, 1),
        # 표본이 적거나 오류가 있으면 연결 최고치를 믿지 않는다.
        "samples": len(rec.db),
        "sampler_error": rec.sampler_error,
    }
    held = sum(r["held_s"] for r in rec.sse if r["status"] == 200)
    open_counts = [row.get("open_sse", 0) for row in rec.db]
    sse = {
        "wanted": profile.sse_streams,
        "open_p50": percentile(open_counts, 0.5),
        "open_max": max(open_counts, default=0),
        # 원한 시청 시간(스트림 수 × 부하 시간) 중 실제로 열려 있던 비율. 작업이 끝나 스트림이 닫히면
        # 1초 뒤 다시 붙으므로 슬롯이 넉넉해도 1보다 조금 작다.
        "coverage": (
            round(held / (profile.sse_streams * profile.duration_s), 3)
            if profile.sse_streams
            else None
        ),
        "attempts": len(rec.sse),
        "statuses": dict(sorted(_count(r["status"] for r in rec.sse).items())),
        "connect_p95_s": percentile([r["connect_s"] for r in rec.sse if r["connect_s"]], 0.95),
        "errors": sum(1 for r in rec.sse if "error" in r),
    }
    jobs = {
        "count": len(rec.jobs),
        "results": dict(sorted(_count(str(j["result"]) for j in rec.jobs).items())),
        "p50_s": percentile([j["seconds"] for j in rec.jobs], 0.5),
        "p95_s": percentile([j["seconds"] for j in rec.jobs], 0.95),
    }
    verdict = judge(requests, profile.name)
    return {
        "profile": asdict(profile),
        "fake_latency_s": FAKE_LATENCY,
        "wall_s": round(wall, 1),
        "requests": requests,
        "sse": sse,
        "jobs": jobs,
        "db": db,
        "verdict": verdict,
    }


def _count(values) -> dict[Any, int]:
    counts: dict[Any, int] = defaultdict(int)
    for value in values:
        counts[value] += 1
    return dict(counts)


def judge(requests: dict[str, Any], profile: str) -> dict[str, Any] | None:
    """사용자 체감(세션 확인 + 업무 요청) 읽기·쓰기 p95와 실패율을 합격선과 견준다."""
    limits = THRESHOLDS.get(profile)
    if limits is None:
        return None
    read, write = requests.get("total.read"), requests.get("total.write")
    total = sum(r["count"] for r in (read, write) if r)
    failures = sum(r["failures"] for r in (read, write) if r)
    rate = failures / total if total else None
    checks = {
        "read_p95": (read or {}).get("p95_s"),
        "write_p95": (write or {}).get("p95_s"),
        "failure_rate": rate,
    }
    passed = {k: v is not None and v <= limits[k] for k, v in checks.items()}
    return {
        "limits": limits,
        "measured": checks,
        "passed": passed,
        "all_passed": all(passed.values()),
    }


def run(admin_url: str, state_path: Path, profile: Profile, seed: int) -> dict[str, Any]:
    """서비스를 띄우고 화면 조작·SSE·분석 작업을 동시에 건 뒤 요약을 돌려준다."""
    check_admin_url(admin_url)
    with psycopg.connect(admin_url) as conn:
        if not marked(conn):
            raise ValueError("부하 시험 전용 DB가 아닙니다(prepare로 만든 표식이 없음).")
    state = json.loads(state_path.read_text())
    rec = Recorder()
    jobs: list[str] = []
    tickets = list(state["tickets"])
    random.Random(seed).shuffle(tickets)
    with tempfile.TemporaryDirectory(prefix="kan155-") as tmp:
        procs = start_services(admin_url, profile.instance_concurrency, Path(tmp))
        urls = {name: item["url"] for name, item in procs.items()}
        stop_sampling = threading.Event()
        sampler = threading.Thread(target=db_sampler, args=(admin_url, rec, stop_sampling))
        try:
            before = sessions_opened(admin_url)
            sampler.start()
            started = time.monotonic()
            stop = started + profile.duration_s
            workers = [
                threading.Thread(
                    target=job_worker,
                    args=(admin_url, state, rec, jobs, tickets, stop, seed + i, Path(tmp)),
                    # 강제 종료 때 진행 중인 작업을 기다리지 않는다(서비스 정리는 finally가 한다).
                    daemon=True,
                )
                for i in range(profile.concurrent_jobs)
            ]
            for thread in workers:
                thread.start()

            async def main() -> None:
                await asyncio.gather(
                    interactive(urls, state, rec, profile, stop, seed),
                    *(
                        sse_stream(urls, state, rec, jobs, stop, seed + 100 + i)
                        for i in range(profile.sse_streams)
                    ),
                )

            asyncio.run(main())
            for thread in workers:
                thread.join()
            wall = time.monotonic() - started
            opened = sessions_opened(admin_url) - before - 1
        finally:
            stop_sampling.set()
            if sampler.is_alive():
                sampler.join()
            stop_services(procs)
    return summarize(rec, profile, opened, wall)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "run"):
        command = sub.add_parser(name)
        command.add_argument("--admin-url", required=True)
        command.add_argument("--state", type=Path, required=True)
    run_parser = sub.choices["run"]
    run_parser.add_argument("--profile", choices=sorted(PROFILES), default="normal")
    run_parser.add_argument("--duration", type=float)
    run_parser.add_argument("--seed", type=int, default=155)
    run_parser.add_argument("--out", type=Path)
    job = sub.add_parser("job", help="run이 띄우는 worker 프로세스용(직접 쓰지 않는다)")
    job.add_argument("--job-id", type=UUID, required=True)
    job.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    if args.command == "job":
        print(run_one_job(args.job_id, args.seed))
        return 0
    if args.command == "prepare":
        state = prepare(args.admin_url, args.state)
        print(json.dumps({"users": len(state["users"]), "tickets": len(state["tickets"])}))
        return 0
    # 강제 종료(SIGTERM)에도 finally가 돌아 서비스 프로세스를 정리하게 한다.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    profile = PROFILES[args.profile]
    if args.duration:
        profile = Profile(**{**asdict(profile), "duration_s": args.duration})
    report = run(args.admin_url, args.state, profile, args.seed)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
