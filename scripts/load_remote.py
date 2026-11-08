"""KAN-155 배포 환경 부하 측정. `infra/load_env.py`가 띄운 `-load` 환경에 실제 중계 경로로 부하를 건다.

로컬 시험(scripts/load_run.py)과 같은 합성 사용자·부하 조건·요약·합격선을 쓴다. 다른 점:
- 요청은 측정용 웹(team4-web-load)으로 보낸다. 웹 → 플랫폼 중계(세션 조회·ID 토큰) → cs/tft를 실제로 지난다.
  쓰기는 세션 쿠키·CSRF·Origin을 붙인다. 세션 확인 요청을 따로 보내지 않는다(중계가 요청마다 한다).
- 분석 작업은 화면과 같은 API로 접수하고(RFP 처리, C/S 분석), 플랫폼 Scheduler(1분)·Cloud Run Job이 처리한다.
  모드 a는 RFP만(가짜 모델), 모드 b는 RFP와 C/S 모두 실제 Gemini다. b는 `--max-jobs`로 작업 수를 막는다(비용 상한).
- DB 표본은 측정 인스턴스(team4-sql-load) 프록시의 관리자 연결로 센다.

    # 1) 측정 인스턴스 프록시(예: 55437)를 띄운 뒤, 빈 team4 DB에 스키마·사용자·세션·DB 비밀번호 Secret
    uv run --frozen python -m scripts.load_remote prepare --admin-url postgresql://postgres@127.0.0.1:55437/team4 --state <밖>/state.json
    # 2) infra/load_env.py deploy --mode a --apply 뒤, RFP 프로젝트·문서
    uv run --frozen python -m scripts.load_remote seed --state … --rfp-docx <RFP.docx> --projects 120
    # 3) infra/load_env.py start --apply 뒤 측정
    uv run --frozen python -m scripts.load_remote run --admin-url … --state … --profile normal --mode a --out <밖>/a-normal.json

prepare는 load_run.prepare를 그대로 쓴다: 빈 DB에서만 시작하고 끝에 표식 테이블을 만든다. 그래서 운영 DB(이미
스키마가 있음)에는 쓰지 못한다. run·seed도 표식이 있는 DB·측정용 웹 주소에서만 돈다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql

from infra import load_env
from scripts import load_run as L

TARGET = load_env.url("web", True)
# 운영과 같은 로그인 이름(infra/deploy_private.py·deploy_workers.py) → 측정용 Secret 이름 앞부분.
LOGINS = {
    "cs_app": "cs",
    "tft_app": "tft",
    "platform_app": "platform",
    "cs_worker_login": "cs-worker",
    "tft_worker_login": "tft-worker",
}
TERMINAL = {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"}
# 로컬 ACTIONS에 RFP 화면 조회를 더한다. {project}는 시드한 프로젝트 하나다.
ACTIONS = (
    *L.ACTIONS,
    ("tft.project.detail", "sales", 4, "GET", "/api/v1/tft/projects/{project}", False),
    (
        "tft.requirements",
        "sales",
        4,
        "GET",
        "/api/v1/tft/projects/{project}/requirements?limit=50",
        False,
    ),
    ("tft.catalog", "sales", 2, "GET", "/api/v1/tft/catalog/own-products", False),
)


def headers(user: dict[str, Any], write: bool = False) -> dict[str, str]:
    """측정용 웹이 받는 운영 세션 쿠키. 쓰기는 CSRF(세션 결속 HMAC)·Origin을 더한다."""
    from backend.api.platform.auth.http import csrf_token

    out = {"Cookie": f"{L.SESSION_COOKIE}={user['token']}"}
    if write:
        out |= {"X-CSRF-Token": csrf_token(user["token"]), "Origin": TARGET}
    return out


def store_secret(name: str, value: str, account: str) -> None:
    """Secret에 새 판본을 더한다(없으면 만든다). 값은 stdin으로만 넘기고 출력하지 않는다."""
    base = ["--project", load_env.PROJECT, f"--account={account}", "--quiet"]
    exists = subprocess.run(
        ["gcloud", "secrets", "describe", name, *base], capture_output=True, check=False
    )
    if exists.returncode:
        created = subprocess.run(
            [
                "gcloud",
                "secrets",
                "create",
                name,
                "--replication-policy=automatic",
                f"--labels={load_env.LABEL}",
                *base,
            ],
            capture_output=True,
            check=False,
        )
        if created.returncode:
            raise SystemExit(f"Secret 생성 실패: {name}")
    added = subprocess.run(
        ["gcloud", "secrets", "versions", "add", name, "--data-file=-", *base],
        input=value.encode(),
        capture_output=True,
        check=False,
    )
    if added.returncode:
        raise SystemExit(f"Secret 판본 추가 실패: {name}")


def prepare(admin_url: str, state_path: Path, account: str) -> dict[str, Any]:
    """빈 측정 DB에 load_run.prepare(스키마·사용자·세션·문의·표식)를 하고, 운영과 같은 이름의 worker 로그인을
    만든 뒤 실행 계정 비밀번호를 새로 정해 측정용 Secret에 넣는다(load_run의 고정 비밀번호를 남기지 않는다)."""
    state = L.prepare(admin_url, state_path)
    with psycopg.connect(admin_url, autocommit=True) as conn:
        for login, group in (("cs_worker_login", "cs_worker"), ("tft_worker_login", "tft_worker")):
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (login,)).fetchone():
                conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(login)))
                conn.execute(
                    sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), sql.Identifier(login))
                )
                conn.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE team4 TO {}").format(sql.Identifier(login))
                )
        for login, secret in LOGINS.items():
            password = secrets.token_urlsafe(32)
            conn.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(login), sql.Literal(password)
                )
            )
            store_secret(f"{secret}-db-password{load_env.SUFFIX}", password, account)
        # 로컬 시험용 worker 로그인은 측정 환경에서 쓰지 않는다.
        conn.execute(sql.SQL("ALTER ROLE {} NOLOGIN").format(sql.Identifier(L.WORKER_ROLE)))
    return state


def seed(state_path: Path, docx: Path, count: int) -> list[dict[str, str]]:
    """영업 합성 사용자들이 RFP 프로젝트를 만들고 문서를 올린다(실제 화면과 같은 API). 상태 파일에 더한다."""
    state = json.loads(state_path.read_text())
    sales = [u for u in state["users"] if u["role"] == "sales"]
    body = docx.read_bytes()
    projects = state.setdefault("projects", [])
    with httpx.Client(base_url=TARGET, timeout=60) as client:
        for index in range(len(projects), count):
            user = sales[index % len(sales)]
            created = client.post(
                "/api/v1/tft/projects",
                headers={**headers(user, True), "Idempotency-Key": f"load-{uuid4()}"},
                json={
                    "name": f"부하 측정 {index}",
                    "company_code": "LOAD",
                    "data_origin": "synthetic",
                },
            )
            created.raise_for_status()
            project = created.json()["data"]["id"]
            uploaded = client.post(
                f"/api/v1/tft/projects/{project}/documents",
                headers={**headers(user, True), "Idempotency-Key": f"load-{uuid4()}"},
                data={
                    "kind": "RFP_SPEC",
                    "title": f"부하 측정 규격서 {index}",
                    "data_origin": "synthetic",
                },
                files={
                    "file": (
                        docx.name,
                        body,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
            )
            uploaded.raise_for_status()
            projects.append({"id": project, "owner": user["id"]})
            state_path.write_text(json.dumps(state))
    return projects


async def one_action(
    client: httpx.AsyncClient, state: dict[str, Any], rec: L.Recorder, rng: random.Random
) -> None:
    """화면 조작 하나. 중계가 세션을 확인하므로 업무 요청 하나가 사용자 체감 시간이다."""
    name, role, _, method, path, write = rng.choices(ACTIONS, weights=[a[2] for a in ACTIONS])[0]
    user = rng.choice([u for u in state["users"] if role in {"any", u["role"]}])
    path = path.replace("{ticket}", rng.choice(state["tickets"]))
    if "{project}" in path:
        if not state.get("projects"):
            return
        path = path.replace("{project}", rng.choice(state["projects"])["id"])
    extra = {"Idempotency-Key": f"load-{uuid4()}"} if write else {}
    started = time.perf_counter()
    try:
        response = await client.request(
            method,
            path,
            headers={**headers(user, write), **extra},
            json=L.ticket_body() if write else None,
        )
        code = response.status_code
    except httpx.HTTPError:
        code = 0
    seconds = time.perf_counter() - started
    rec.add("samples", L.Sample(name, seconds, code, write))
    rec.add("samples", L.Sample("total." + ("write" if write else "read"), seconds, code, write))


async def interactive(state, rec, profile: L.Profile, stop: float, seed: int) -> None:
    rng = random.Random(seed)
    async with httpx.AsyncClient(base_url=TARGET, timeout=30) as client:
        tasks = []
        while time.monotonic() < stop:
            tasks.append(asyncio.create_task(one_action(client, state, rec, rng)))
            await asyncio.sleep(rng.expovariate(profile.rps))
        await asyncio.gather(*tasks)


async def sse_stream(state, rec, jobs: list[dict[str, str]], stop: float, seed: int) -> None:
    """작업을 낸 사람이 진행을 SSE로 본다(작업 상태는 접수한 사람만 볼 수 있다). 끝나면 1초 뒤 다시 붙는다."""
    rng = random.Random(seed)
    users = {u["id"]: u for u in state["users"]}
    async with httpx.AsyncClient(base_url=TARGET, timeout=httpx.Timeout(30, read=40)) as client:
        while time.monotonic() < stop:
            if not jobs:
                await asyncio.sleep(0.2)
                continue
            job = rng.choice(jobs[-5:])
            started, opened_at = time.perf_counter(), time.monotonic()
            record: dict[str, Any] = {"status": 0, "connect_s": None, "events": 0, "held_s": 0.0}
            try:
                async with client.stream(
                    "GET",
                    f"/api/v1/platform/jobs/{job['id']}/events",
                    headers=headers(users[job["by"]]),
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
            record["held_s"] = max(0.0, min(time.monotonic(), stop) - opened_at)
            rec.add("sse", record)
            await asyncio.sleep(1)


def job_worker(state, rec, jobs, budget: dict[str, Any], mode: str, stop: float, seed: int) -> None:
    """작업 하나를 접수하고 끝날 때까지 2초마다 상태를 본다. 접수부터 끝까지가 사용자가 기다리는 시간이다.

    모드 a는 시드한 RFP 프로젝트를 하나씩 처리한다. 모드 b는 RFP·C/S를 번갈아 낸다. budget이 0이 되면 멈춘다.
    """
    rng = random.Random(seed)
    users = {u["id"]: u for u in state["users"]}
    agents = [u for u in state["users"] if u["role"] == "cs_agent"]
    with httpx.Client(base_url=TARGET, timeout=30) as client:
        while time.monotonic() < stop:
            with rec.lock:
                if budget["left"] <= 0:
                    return
                budget["left"] -= 1
                kind = "rfp" if mode == "a" or budget["left"] % 2 else "cs"
                if kind == "rfp":
                    if not budget["projects"]:
                        return
                    project = budget["projects"].pop()
                    user, path = (
                        users[project["owner"]],
                        f"/api/v1/tft/projects/{project['id']}/process",
                    )
                else:
                    user, path = (
                        rng.choice(agents),
                        f"/api/v1/cs/tickets/{budget['tickets'].pop()}/analysis",
                    )
            started = time.perf_counter()
            record: dict[str, Any] = {"kind": kind, "result": None}
            try:
                accepted = client.post(
                    path,
                    headers={**headers(user, True), "Idempotency-Key": f"load-{uuid4()}"},
                    # 중계는 변경 요청에 application/json을 요구한다(C/S 분석은 본문을 읽지 않는다).
                    json={},
                )
                record["admit_s"] = time.perf_counter() - started
                accepted.raise_for_status()
                job = accepted.json()["data"]
                with rec.lock:
                    jobs.append({"id": job["job_id"], "by": user["id"]})
                status, first_running = job["status"], None
                deadline = time.monotonic() + 1800
                while status not in TERMINAL and time.monotonic() < deadline:
                    time.sleep(2)
                    status = client.get(
                        f"/api/v1/platform/jobs/{job['job_id']}", headers=headers(user)
                    ).json()["data"]["status"]
                    if status == "RUNNING" and first_running is None:
                        first_running = time.perf_counter() - started
                record["result"] = status
                # 접수 → 처리 시작(Scheduler 대기 + Job 기동)과 전체 시간을 따로 남긴다.
                record["wait_s"] = first_running
            except Exception as error:  # noqa: BLE001 - 부하 중 실패는 집계한다.
                record["result"] = getattr(error, "code", type(error).__name__)
            record["seconds"] = time.perf_counter() - started
            rec.add("jobs", record)


def run(
    admin_url: str, state_path: Path, profile: L.Profile, mode: str, max_jobs: int, seed: int
) -> dict[str, Any]:
    with psycopg.connect(admin_url) as conn:
        if not L.marked(conn):
            raise ValueError("측정 전용 DB가 아닙니다(prepare 표식 없음).")
    state = json.loads(state_path.read_text())
    used = set(state.get("used_projects", []))
    projects = [p for p in state.get("projects", []) if p["id"] not in used]
    tickets = list(state["tickets"])
    random.Random(seed).shuffle(tickets)
    budget: dict[str, Any] = {"left": max_jobs, "projects": projects, "tickets": tickets}
    rec = L.Recorder()
    jobs: list[dict[str, str]] = []
    stop_sampling = threading.Event()
    sampler = threading.Thread(target=L.db_sampler, args=(admin_url, rec, stop_sampling))
    try:
        before = L.sessions_opened(admin_url)
        sampler.start()
        started = time.monotonic()
        stop = started + profile.duration_s
        workers = [
            threading.Thread(
                target=job_worker,
                args=(state, rec, jobs, budget, mode, stop, seed + i),
                daemon=True,
            )
            for i in range(profile.concurrent_jobs)
        ]
        for thread in workers:
            thread.start()

        async def main() -> None:
            await asyncio.gather(
                interactive(state, rec, profile, stop, seed),
                *(
                    sse_stream(state, rec, jobs, stop, seed + 100 + i)
                    for i in range(profile.sse_streams)
                ),
            )

        asyncio.run(main())
        for thread in workers:
            thread.join()
        wall = time.monotonic() - started
        opened = L.sessions_opened(admin_url) - before - 1
    finally:
        stop_sampling.set()
        if sampler.is_alive():
            sampler.join()
        # 처리한 프로젝트는 다시 처리하지 않는다(두 번째 처리에는 사유가 필요하다).
        state["used_projects"] = sorted(
            used | {p["id"] for p in state.get("projects", [])} - {p["id"] for p in projects}
        )
        state_path.write_text(json.dumps(state))
    report = L.summarize(rec, profile, opened, wall)
    report["fake_latency_s"] = (
        None if mode == "b" else {"rfp_extraction": "fake provider (no wait)"}
    )
    report["mode"], report["target"] = mode, TARGET
    report["jobs"]["by_kind"] = {
        kind: {
            "count": len(rows),
            "results": L._count(str(r["result"]) for r in rows),
            "wait_p50_s": L.percentile([r["wait_s"] for r in rows if r.get("wait_s")], 0.5),
            "p50_s": L.percentile([r["seconds"] for r in rows], 0.5),
            "p95_s": L.percentile([r["seconds"] for r in rows], 0.95),
        }
        for kind in ("rfp", "cs")
        if (rows := [r for r in rec.jobs if r.get("kind") == kind])
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--admin-url", required=True)
    prep.add_argument("--state", type=Path, required=True)
    prep.add_argument("--account", default=load_env.ACCOUNT)
    seeding = sub.add_parser("seed")
    seeding.add_argument("--state", type=Path, required=True)
    seeding.add_argument("--rfp-docx", type=Path, required=True)
    seeding.add_argument("--projects", type=int, default=120)
    measure = sub.add_parser("run")
    measure.add_argument("--admin-url", required=True)
    measure.add_argument("--state", type=Path, required=True)
    measure.add_argument("--profile", choices=sorted(L.PROFILES), default="normal")
    measure.add_argument("--mode", choices=("a", "b"), required=True)
    measure.add_argument("--duration", type=float, default=600)
    measure.add_argument("--max-jobs", type=int, help="작업 수 상한. 모드 b(실제 모델)는 필수")
    measure.add_argument("--seed", type=int, default=155)
    measure.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        state = prepare(args.admin_url, args.state, args.account)
        print(json.dumps({"users": len(state["users"]), "tickets": len(state["tickets"])}))
        return 0
    if args.command == "seed":
        print(json.dumps({"projects": len(seed(args.state, args.rfp_docx, args.projects))}))
        return 0
    if args.mode == "b" and not args.max_jobs:
        parser.error("모드 b는 비용 상한으로 --max-jobs가 필요합니다.")
    profile = L.Profile(**{**asdict(L.PROFILES[args.profile]), "duration_s": args.duration})
    report = run(args.admin_url, args.state, profile, args.mode, args.max_jobs or 10_000, args.seed)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
