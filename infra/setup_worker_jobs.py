#!/usr/bin/env python3
"""KAN-159: C/S·RFP worker의 실행 계정·권한·전송 주기·서비스 설정. 기본은 미리보기, --apply만 바꾼다.

순서: prepare → (register_db_secrets.py, 이미지 빌드, deploy_workers.py) → grant → enable.
enable은 실제 처리와 모델 호출을 켠다. .env나 비밀값을 읽지 않는다.
"""

import argparse
import os
import shlex
import subprocess

PROJECT = "proj-skkuls0106-183255377938"
ACCOUNT = "skkuls0106@skkukdp.re.kr"
REGION = "asia-northeast3"
BUCKET = f"{PROJECT}-team4-rfp-documents"
WORKERS = ("cs", "tft")
PLATFORM = "team4-platform"
SCHEDULER = "jobs-dispatch"
INVOKER_NAME = "jobs-dispatch-invoker"


def email(name: str) -> str:
    """서비스 계정 주소.

    Args:
        name: 계정 이름.

    Returns:
        str: 주소.
    """
    return f"{name}@{PROJECT}.iam.gserviceaccount.com"


def member(name: str) -> str:
    """IAM member 값.

    Args:
        name: 계정 이름.

    Returns:
        str: serviceAccount 값.
    """
    return f"--member=serviceAccount:{email(name)}"


def prepare(accounts: set[str], schedulers: set[str], url: str) -> list[list[str]]:
    """Job 배포 전 준비: 실행 계정·호출 계정·권한·일시 중지한 Scheduler.

    Args:
        accounts: 이미 있는 서비스 계정 주소.
        schedulers: 이미 있는 Scheduler 작업 이름.
        url: 플랫폼 서비스 주소.

    Returns:
        list[list[str]]: gcloud 인자.
    """
    commands = []
    for name in (*(f"{worker}-worker-runtime" for worker in WORKERS), INVOKER_NAME):
        if email(name) not in accounts:
            commands.append(["iam", "service-accounts", "create", name])
    for worker in WORKERS:
        runtime = f"{worker}-worker-runtime"
        commands.append(
            [
                "projects",
                "add-iam-policy-binding",
                PROJECT,
                member(runtime),
                "--role=roles/cloudsql.client",
                "--condition=None",
            ]
        )
        commands.append(
            [
                "secrets",
                "add-iam-policy-binding",
                "gemini-api-key",
                member(runtime),
                "--role=roles/secretmanager.secretAccessor",
            ]
        )
    commands.append(
        [
            "storage",
            "buckets",
            "add-iam-policy-binding",
            f"gs://{BUCKET}",
            member("tft-worker-runtime"),
            "--role=roles/storage.objectViewer",
        ]
    )
    if SCHEDULER not in schedulers:
        commands.append(
            [
                "scheduler",
                "jobs",
                "create",
                "http",
                SCHEDULER,
                f"--location={REGION}",
                "--schedule=* * * * *",
                f"--uri={url}/internal/v1/platform/jobs/dispatch",
                "--http-method=POST",
                f"--oidc-service-account-email={email(INVOKER_NAME)}",
                f"--oidc-token-audience={url}",
            ]
        )
        commands.append(["scheduler", "jobs", "pause", SCHEDULER, f"--location={REGION}"])
    return commands


def grant() -> list[list[str]]:
    """Job 배포 뒤 호출 권한. 플랫폼은 이 두 Job만 실행하고 서비스 호출은 정해진 계정만 한다.

    Returns:
        list[list[str]]: gcloud 인자.
    """
    commands = [
        [
            "run",
            "jobs",
            "add-iam-policy-binding",
            f"{worker}-worker",
            f"--region={REGION}",
            member("platform-runtime"),
            "--role=roles/run.jobsExecutorWithOverrides",
        ]
        for worker in WORKERS
    ]
    for name in ("cs-runtime", "tft-runtime", INVOKER_NAME):
        commands.append(
            [
                "run",
                "services",
                "add-iam-policy-binding",
                PLATFORM,
                f"--region={REGION}",
                member(name),
                "--role=roles/run.invoker",
            ]
        )
    return commands


def enable(url: str, project_number: str) -> list[list[str]]:
    """처리를 켠다: 서비스 환경 변수와 Scheduler 재개. 평가 전송은 코드 잠금으로 닫혀 있다.

    Args:
        url: 플랫폼 서비스 주소.
        project_number: 프로젝트 번호.

    Returns:
        list[list[str]]: gcloud 인자.
    """
    platform = {
        "JOBS_SWEEP_ENABLED": "true",
        "JOBS_SWEEP_SERVICE_URL": url,
        "JOBS_SWEEP_INVOKER_EMAIL": email(INVOKER_NAME),
        "EVALUATION_DISPATCH_ENABLED": "true",
        "EVALUATION_JOBS_ENABLED": "true",
        "EVALUATION_JOBS_PROJECT_NUMBER": project_number,
        # 설정이 요구하는 값. 평가 전송은 production에서 열리지 않는다.
        "EVALUATION_JOBS_JOB": "evaluation-worker",
    }
    for worker in WORKERS:
        prefix = worker.upper()
        platform |= {
            f"{prefix}_JOBS_ENABLED": "true",
            f"{prefix}_JOBS_SERVICE_URL": url,
            f"{prefix}_JOBS_INVOKER_EMAIL": email(f"{worker}-runtime"),
            f"{prefix}_JOBS_PROCESSOR_CONNECTED": "true",
            f"{prefix}_WORKER_JOBS_ENABLED": "true",
        }
    services = {
        PLATFORM: platform,
        "team4-tft": {
            "TFT_PROCESSING_ENABLED": "true",
            "TFT_PROCESSING_PLATFORM_URL": url,
            "TFT_DOCUMENT_BUCKET": BUCKET,
        },
        "team4-cs": {"CS_ANALYSIS_ENABLED": "true", "CS_ANALYSIS_PLATFORM_URL": url},
    }
    commands = [
        [
            "run",
            "services",
            "update",
            service,
            f"--region={REGION}",
            "--update-env-vars=" + ",".join(f"{k}={v}" for k, v in values.items()),
        ]
        for service, values in services.items()
    ]
    commands.append(["scheduler", "jobs", "resume", SCHEDULER, f"--location={REGION}"])
    return commands


def read(arguments: list[str], account: str) -> str:
    """읽기 전용 gcloud 호출. 미리보기에서도 부른다.

    Args:
        arguments: gcloud 인자.
        account: gcloud 계정.

    Returns:
        str: 표준 출력. 실패하면 RuntimeError다.
    """
    result = subprocess.run(
        ["gcloud", *arguments, f"--project={PROJECT}", f"--account={account}", "--quiet"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"gcloud {' '.join(arguments[:3])} failed.")
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("step", choices=("prepare", "grant", "enable"))
    parser.add_argument("--apply", action="store_true", help="Change GCP; default only prints")
    parser.add_argument("--account", default=os.environ.get("TEAM4_GCLOUD_ACCOUNT", ACCOUNT))
    arguments = parser.parse_args()
    account = arguments.account
    try:
        if arguments.step == "grant":
            commands = grant()
        else:
            url = read(
                [
                    "run",
                    "services",
                    "describe",
                    PLATFORM,
                    f"--region={REGION}",
                    "--format=value(status.url)",
                ],
                account,
            )
            if not url.startswith("https://"):
                raise RuntimeError("Platform service URL was not found.")
            if arguments.step == "prepare":
                listed = read(["iam", "service-accounts", "list", "--format=value(email)"], account)
                jobs = read(
                    [
                        "scheduler",
                        "jobs",
                        "list",
                        f"--location={REGION}",
                        "--format=value(name.basename())",
                    ],
                    account,
                )
                commands = prepare(set(listed.split()), set(jobs.split()), url)
            else:
                number = read(
                    ["projects", "describe", PROJECT, "--format=value(projectNumber)"], account
                )
                commands = enable(url, number)
    except RuntimeError as error:
        print(error)
        return 1
    for command in commands:
        full = ["gcloud", *command, f"--project={PROJECT}", f"--account={account}", "--quiet"]
        print(shlex.join(full), flush=True)
        if arguments.apply and subprocess.run(full, check=False).returncode:
            print("Stopped. Earlier commands may already be applied; rerunning is safe.")
            return 1
    if not arguments.apply:
        print("Preview only. Nothing changed. Add --apply to run these commands.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
