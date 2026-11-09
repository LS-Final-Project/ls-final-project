#!/usr/bin/env python3
"""KAN-155 배포 환경 부하 측정용 `-load` 환경. 기본은 명령 미리보기, `--apply`만 바꾼다.

운영 서비스·worker Job 설정을 읽어 이름·DB·주소만 바꾼 복사본을 띄운다. 운영과 같은 이미지 digest·자원·동시
요청을 쓰고, DB는 별도 Cloud SQL 인스턴스(team4-sql-load, 운영과 같은 등급)라 운영 데이터·CPU를 건드리지 않는다.
메일 수집·발송, 사서함, 푸시 알림, C/S 요약·고장 추천은 끈다. 순서:

  infra    : Cloud SQL 인스턴스·DB·문서 버킷(운영자가 postgres 비밀번호를 정한다, 아래 안내)
  (scripts.load_remote prepare : 스키마·합성 사용자·세션·DB 비밀번호 Secret)
  deploy --mode a|b : 서비스 4개·worker Job 2개·IAM·Scheduler(멈춘 상태). a=RFP 가짜 모델, b=실제 Gemini
  start / stop      : 작업 배분 Scheduler 재개·정지
  teardown          : 만든 것 전부 삭제(데이터 삭제, 되돌릴 수 없음)

비밀값을 읽거나 출력하지 않는다. 운영 리소스는 읽기(describe·get-iam-policy)만 한다.
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from typing import Any

PROJECT = "proj-skkuls0106-183255377938"
ACCOUNT = "skkuls0106@skkukdp.re.kr"
REGION = "asia-northeast3"
# Cloud Run 주소는 서비스 이름 + 프로젝트·리전 고정 부분이라 만들기 전에 정해진다.
URL_SUFFIX = "gew6ci4pgq-du.a.run.app"
SUFFIX = "-load"
SERVICES = ("web", "platform", "cs", "tft")
WORKERS = ("cs", "tft")
SQL = "team4-sql"
SQL_LOAD = SQL + SUFFIX
BUCKET = f"{PROJECT}-team4-rfp-documents"
BUCKET_LOAD = BUCKET + SUFFIX
SCHEDULER = "jobs-dispatch"
SCHEDULER_LOAD = SCHEDULER + SUFFIX
INVOKER = f"jobs-dispatch-invoker@{PROJECT}.iam.gserviceaccount.com"
DB_SECRETS = ("cs", "tft", "platform", "cs-worker", "tft-worker")
LABEL = "purpose=kan155-load"

# 측정 환경에서 끄는 기능. 외부 메일·푸시를 보내지 않고, 화면 조회가 모델을 부르지 않게 한다.
OFF = {
    "web": {"MAILBOX_AUTH_ENABLED": "false"},
    "platform": {
        "MAIL_SYNC_ENABLED": "false",
        "MAIL_SEND_ENABLED": "false",
        "MAILBOX_AUTH_ENABLED": "false",
        "NOTIFICATION_PUSH_ENABLED": "false",
        # EVALUATION_DISPATCH_ENABLED는 C/S·RFP 작업 전송 전체를 여닫으므로 운영 값 그대로 둔다(dispatch.allowed_kinds).
        "CS_WORKER_JOBS_JOB": "cs-worker" + SUFFIX,
        "TFT_WORKER_JOBS_JOB": "tft-worker" + SUFFIX,
    },
    "cs": {
        "CS_MAIL_ENABLED": "false",
        "CS_SUMMARY_ENABLED": "false",
        "CS_FAULT_SUGGESTION_ENABLED": "false",
    },
    "tft": {},
}
# 대충 고른 값이 아니라 운영 설정을 그대로 쓰고 이 줄만 바꾼다. 서비스 설정의 실행 상태 칸은 뺀다.
# namespace(프로젝트 번호)를 넘기면 gcloud가 Resource Manager API로 번호를 풀려고 한다(이 프로젝트에서 꺼져 있음). --project로 정한다.
DROP_METADATA = (
    "uid",
    "resourceVersion",
    "generation",
    "creationTimestamp",
    "selfLink",
    "namespace",
)
DROP_ANNOTATIONS = re.compile(
    r"^(serving\.knative\.dev/(creator|lastModifier)|run\.googleapis\.com/(client-name|client-version|operation-id|urls|build-.*))$"
)


def url(service: str, load: bool) -> str:
    return f"https://team4-{service}{SUFFIX if load else ''}-{URL_SUFFIX}"


def rewrite(value: str) -> str:
    """운영 주소·Cloud SQL·버킷을 측정용으로 바꾼다(문자열 값 하나)."""
    for service in SERVICES:
        value = value.replace(url(service, False), url(service, True))
    value = re.sub(rf":{SQL}(?![\w-])", f":{SQL_LOAD}", value)
    return re.sub(rf"{BUCKET}(?![\w-])", BUCKET_LOAD, value)


def _env(container: dict[str, Any], overrides: dict[str, str]) -> None:
    env = []
    for item in container.get("env", []):
        item = dict(item)
        if "value" in item:
            item["value"] = rewrite(item["value"])
        ref = item.get("valueFrom", {}).get("secretKeyRef")
        if ref and ref["name"].endswith("-db-password"):
            # 측정 DB 계정 비밀번호. prepare가 판본을 더하므로 최신 판본을 쓴다.
            item["valueFrom"] = {"secretKeyRef": {"name": ref["name"] + SUFFIX, "key": "latest"}}
        if item["name"] not in overrides:
            env.append(item)
    env += [{"name": k, "value": v} for k, v in overrides.items()]
    container["env"] = env


def _metadata(metadata: dict[str, Any], name: str) -> dict[str, Any]:
    out = {k: v for k, v in metadata.items() if k not in DROP_METADATA}
    out["name"] = name
    labels = {
        k: v
        for k, v in out.get("labels", {}).items()
        if not k.startswith(("cloud.googleapis.com", "run.googleapis.com"))
    }
    key, value = LABEL.split("=")
    out["labels"] = {**labels, key: value}
    out["annotations"] = {
        k: rewrite(v)
        for k, v in out.get("annotations", {}).items()
        if not DROP_ANNOTATIONS.match(k)
    }
    return out


def service_spec(
    spec: dict[str, Any], service: str, concurrency: int | None = None
) -> dict[str, Any]:
    """운영 서비스 설정(gcloud run services describe --format=json) → 측정용 설정(services replace 입력).

    concurrency: 웹·플랫폼의 인스턴스당 동시 요청을 바꿔 잴 때(운영 20). 실시간 연결(SSE)이 칸을 오래 잡는 두 서비스만 바꾼다.
    """
    out = {"apiVersion": spec["apiVersion"], "kind": spec["kind"]}
    out["metadata"] = _metadata(spec["metadata"], f"team4-{service}{SUFFIX}")
    template = json.loads(json.dumps(spec["spec"]["template"]))
    template["metadata"] = _metadata(template.get("metadata", {}), "")
    template["metadata"].pop("name")
    for container in template["spec"]["containers"]:
        overrides = dict(OFF[service])
        if service == "platform":
            overrides |= {
                "MICROSOFT_AUTH_APP_ORIGIN": url("web", True),
                "MICROSOFT_AUTH_REDIRECT_URI": url("web", True) + "/api/v1/platform/auth/callback",
            }
        _env(container, overrides)
    if concurrency and service in ("web", "platform"):
        template["spec"]["containerConcurrency"] = concurrency
    out["spec"] = {"template": template, "traffic": [{"latestRevision": True, "percent": 100}]}
    return out


def job_spec(spec: dict[str, Any], worker: str, mode: str) -> dict[str, Any]:
    """운영 worker Job 설정 → 측정용 Job. mode a는 RFP 추출을 가짜 모델로 둔다(C/S 작업은 a에서 만들지 않는다)."""
    out = {"apiVersion": spec["apiVersion"], "kind": spec["kind"]}
    out["metadata"] = _metadata(spec["metadata"], f"{worker}-worker{SUFFIX}")
    template = json.loads(json.dumps(spec["spec"]["template"]))
    template["metadata"] = _metadata(template.get("metadata", {}), "")
    template["metadata"].pop("name")
    overrides = (
        {"TFT_EXTRACTION_PROVIDER": "fake" if mode == "a" else "gemini"} if worker == "tft" else {}
    )
    for container in template["spec"]["template"]["spec"]["containers"]:
        _env(container, overrides)
    out["spec"] = {"template": template}
    return out


def gcloud(arguments: list[str], account: str) -> list[str]:
    return ["gcloud", *arguments, f"--project={PROJECT}", f"--account={account}", "--quiet"]


def read_json(arguments: list[str], account: str) -> Any:
    """읽기 전용 gcloud 호출(미리보기에서도 부른다)."""
    result = subprocess.run(
        gcloud([*arguments, "--format=json"], account), capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise SystemExit(f"읽기 실패: gcloud {' '.join(arguments[:4])}")
    return json.loads(result.stdout)


def infra_commands(account: str) -> list[list[str]]:
    """운영과 같은 등급의 별도 인스턴스와 문서 버킷. 백업은 끈다(측정 뒤 지운다)."""
    prod = read_json(["sql", "instances", "describe", SQL], account)
    settings = prod["settings"]
    create = [
        "sql",
        "instances",
        "create",
        SQL_LOAD,
        f"--database-version={prod['databaseVersion']}",
        f"--tier={settings['tier']}",
        f"--region={REGION}",
        f"--edition={settings.get('edition', 'ENTERPRISE').lower()}",
        f"--storage-size={settings.get('dataDiskSizeGb', '10')}",
        "--no-backup",
        # 정식 sql instances create는 --labels를 받지 않는다(beta만). 이름(-load)으로 구분한다.
    ]
    return [
        gcloud(create, account),
        gcloud(["sql", "databases", "create", "team4", f"--instance={SQL_LOAD}"], account),
        gcloud(
            [
                "storage",
                "buckets",
                "create",
                f"gs://{BUCKET_LOAD}",
                f"--location={REGION}",
                "--uniform-bucket-level-access",
                "--public-access-prevention",
            ],
            account,
        ),
    ]


def copy_policy(kind: str, source: str, target: str, account: str) -> list[str]:
    """운영 리소스의 IAM 정책을 측정용 리소스에 그대로 붙인다(같은 실행 계정이 같은 권한을 갖게)."""
    reads = {
        "service": ["run", "services", "get-iam-policy", source, f"--region={REGION}"],
        "job": ["run", "jobs", "get-iam-policy", source, f"--region={REGION}"],
        "secret": ["secrets", "get-iam-policy", source],
        "bucket": ["storage", "buckets", "get-iam-policy", f"gs://{source}"],
    }
    policy = read_json(reads[kind], account)
    policy.pop("etag", None)
    path = os.path.join(tempfile.mkdtemp(prefix="kan155-iam-"), f"{target}.json")
    with open(path, "w") as handle:
        json.dump(policy, handle)
    writes = {
        "service": ["run", "services", "set-iam-policy", target, path, f"--region={REGION}"],
        "job": ["run", "jobs", "set-iam-policy", target, path, f"--region={REGION}"],
        "secret": ["secrets", "set-iam-policy", target, path],
        "bucket": ["storage", "buckets", "set-iam-policy", f"gs://{target}", path],
    }
    return gcloud(writes[kind], account)


def write_spec(spec: dict[str, Any]) -> str:
    path = os.path.join(tempfile.mkdtemp(prefix="kan155-spec-"), spec["metadata"]["name"] + ".json")
    with open(path, "w") as handle:
        json.dump(spec, handle)
    return path


def deploy_commands(mode: str, account: str, concurrency: int | None = None) -> list[list[str]]:
    """서비스·Job 복사본, IAM, 버킷·Secret 권한, 멈춘 Scheduler."""
    commands = [copy_policy("bucket", BUCKET, BUCKET_LOAD, account)]
    for name in DB_SECRETS:
        commands.append(
            copy_policy("secret", f"{name}-db-password", f"{name}-db-password{SUFFIX}", account)
        )
    for worker in WORKERS:
        spec = job_spec(
            read_json(
                ["run", "jobs", "describe", f"{worker}-worker", f"--region={REGION}"], account
            ),
            worker,
            mode,
        )
        commands.append(
            gcloud(["run", "jobs", "replace", write_spec(spec), f"--region={REGION}"], account)
        )
        commands.append(copy_policy("job", f"{worker}-worker", f"{worker}-worker{SUFFIX}", account))
    for service in SERVICES:
        spec = service_spec(
            read_json(
                ["run", "services", "describe", f"team4-{service}", f"--region={REGION}"], account
            ),
            service,
            concurrency,
        )
        commands.append(
            gcloud(["run", "services", "replace", write_spec(spec), f"--region={REGION}"], account)
        )
        commands.append(
            copy_policy("service", f"team4-{service}", f"team4-{service}{SUFFIX}", account)
        )
    platform = url("platform", True)
    schedulers = {
        job["name"].rsplit("/", 1)[-1]
        for job in read_json(["scheduler", "jobs", "list", f"--location={REGION}"], account)
    }
    verb = "update" if SCHEDULER_LOAD in schedulers else "create"
    commands.append(
        gcloud(
            [
                "scheduler",
                "jobs",
                verb,
                "http",
                SCHEDULER_LOAD,
                f"--location={REGION}",
                "--schedule=* * * * *",
                f"--uri={platform}/internal/v1/platform/jobs/dispatch",
                "--http-method=POST",
                "--attempt-deadline=180s",
                f"--oidc-service-account-email={INVOKER}",
                f"--oidc-token-audience={platform}",
            ],
            account,
        )
    )
    if verb == "create":
        commands.append(
            gcloud(["scheduler", "jobs", "pause", SCHEDULER_LOAD, f"--location={REGION}"], account)
        )
    return commands


def teardown_commands(account: str) -> list[list[str]]:
    """측정용으로 만든 것만 이름으로 지운다. 운영 이름(접미사 없는 것)은 만들지 않는다."""
    commands = [
        gcloud(["scheduler", "jobs", "delete", SCHEDULER_LOAD, f"--location={REGION}"], account)
    ]
    commands += [
        gcloud(["run", "services", "delete", f"team4-{s}{SUFFIX}", f"--region={REGION}"], account)
        for s in SERVICES
    ]
    commands += [
        gcloud(["run", "jobs", "delete", f"{w}-worker{SUFFIX}", f"--region={REGION}"], account)
        for w in WORKERS
    ]
    commands += [
        gcloud(["secrets", "delete", f"{n}-db-password{SUFFIX}"], account) for n in DB_SECRETS
    ]
    commands.append(gcloud(["storage", "rm", "--recursive", f"gs://{BUCKET_LOAD}"], account))
    commands.append(gcloud(["sql", "instances", "delete", SQL_LOAD], account))
    for command in commands:
        assert any(SUFFIX in part for part in command), command
    return commands


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("step", choices=("infra", "deploy", "start", "stop", "teardown"))
    parser.add_argument("--mode", choices=("a", "b"), help="deploy: a=RFP 가짜 모델, b=실제 Gemini")
    parser.add_argument(
        "--concurrency", type=int, help="deploy: 웹·플랫폼 인스턴스당 동시 요청(운영 20)"
    )
    parser.add_argument("--apply", action="store_true", help="실행. 없으면 명령만 출력")
    parser.add_argument("--account", default=os.environ.get("TEAM4_GCLOUD_ACCOUNT", ACCOUNT))
    args = parser.parse_args(argv)
    if args.step == "deploy" and not args.mode:
        parser.error("deploy에는 --mode a|b가 필요합니다.")
    if args.step == "infra":
        commands = infra_commands(args.account)
    elif args.step == "deploy":
        commands = deploy_commands(args.mode, args.account, args.concurrency)
    elif args.step in ("start", "stop"):
        verb = "resume" if args.step == "start" else "pause"
        commands = [
            gcloud(
                ["scheduler", "jobs", verb, SCHEDULER_LOAD, f"--location={REGION}"], args.account
            )
        ]
    else:
        commands = teardown_commands(args.account)
    for command in commands:
        print(shlex.join(command), flush=True)
        if args.apply and subprocess.run(command, check=False).returncode:
            print("멈췄습니다. 앞 명령까지만 적용됐습니다.", file=sys.stderr)
            return 1
    if args.step == "infra":
        print(
            f"# 다음: gcloud sql users set-password postgres --instance={SQL_LOAD} --prompt-for-password"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
