#!/usr/bin/env python3
"""KAN-159: preview C/S·RFP worker Cloud Run Jobs from a successful build; user runs --apply."""

import argparse
import json
import os
import re
import shlex
import subprocess

PROJECT = "proj-skkuls0106-183255377938"
ACCOUNT = "skkuls0106@skkukdp.re.kr"
REGION = "asia-northeast3"
REGISTRY = f"{REGION}-docker.pkg.dev/{PROJECT}/team4"
SQL = f"{PROJECT}:{REGION}:team4-sql"
BUCKET = f"{PROJECT}-team4-rfp-documents"
WORKERS = ("cs", "tft")
# 플랫폼의 CS_WORKER_JOBS_CONTAINER·TFT_WORKER_JOBS_CONTAINER 기본값과 같아야 한다.
CONTAINER = "worker"
ENVIRONMENT = {
    "cs": {
        "CS_WORKER_ENABLED": "true",
        # 이미지 안의 파일이다(Dockerfile이 eval을 /app/eval로 복사). 없으면 worker가 시작에서 멈춘다.
        "CS_POLICY_DOCUMENTS_FILE": "/app/eval/policy/cs_policy_documents.json",
        # team4-cs 서비스와 같은 모델 설정(deploy_private.py).
        "AI_PROVIDER": "gemini",
        "GEMINI_MODEL": "gemini-3.6-flash",
        "GEMINI_MAX_OUTPUT_TOKENS": "1024",
    },
    "tft": {
        "TFT_WORKER_ENABLED": "true",
        "TFT_DOCUMENT_BUCKET": BUCKET,
        "TFT_EXTRACTION_PROVIDER": "gemini",
        "TFT_EXTRACTION_MODEL": "gemini-3.6-flash",
    },
}


def job_commands(
    build: dict, selected: tuple[str, ...] = WORKERS, *, account: str = ACCOUNT
) -> list[list[str]]:
    """성공한 빌드의 digest로 Job 배포 명령을 만든다.

    Args:
        build: gcloud builds describe 결과.
        selected: 배포할 worker.
        account: gcloud 계정.

    Returns:
        list[list[str]]: worker마다 gcloud run jobs deploy 명령.
    """
    if build.get("status") != "SUCCESS":
        raise ValueError("Build must have status SUCCESS.")
    revision = build.get("substitutions", {}).get("COMMIT_SHA", "")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Build must record a full Git commit SHA.")
    images = {
        image["name"]: image.get("digest", "")
        for image in build.get("results", {}).get("images", [])
    }
    commands = []
    for worker in selected:
        if worker not in WORKERS:
            raise ValueError("Select an existing worker name.")
        image = f"{REGISTRY}/{worker}-worker"
        digest = images.get(f"{image}:{revision}", "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError(f"Build has no valid result digest for {worker} at this revision.")
        environment = {
            "ENVIRONMENT": "production",
            "DEV_AUTH_ENABLED": "false",
            "SERVICE_NAME": worker,
            # 비밀번호는 DSN에 넣지 않는다. libpq가 PGPASSWORD를 읽는다.
            "DATABASE_URL": f"postgresql:///team4?host=/cloudsql/{SQL}&user={worker}_worker_login",
            **ENVIRONMENT[worker],
        }
        command = [
            "gcloud",
            "run",
            "jobs",
            "deploy",
            f"{worker}-worker",
            f"--project={PROJECT}",
            f"--region={REGION}",
            f"--service-account={worker}-worker-runtime@{PROJECT}.iam.gserviceaccount.com",
            f"--set-cloudsql-instances={SQL}",
            "--tasks=1",
            "--parallelism=1",
            # 재시도는 전송 장부와 lease가 맡는다. 플랫폼 밖에서 다시 돌지 않게 한다.
            "--max-retries=0",
            "--task-timeout=3600s",
            f"--labels=commit-sha={revision}",
            "--quiet",
            f"--container={CONTAINER}",
            f"--image={image}@{digest}",
            "--cpu=1",
            "--memory=1Gi",
            "--set-env-vars=" + ",".join(f"{k}={v}" for k, v in environment.items()),
            f"--set-secrets=PGPASSWORD={worker}-worker-db-password:1,"
            "GEMINI_API_KEY=gemini-api-key:1",
        ]
        if account:
            command.append(f"--account={account}")
        commands.append(command)
    return commands


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build_id", help="Successful Seoul Cloud Build UUID of one worker image")
    parser.add_argument("--worker", choices=WORKERS, required=True)
    parser.add_argument("--apply", action="store_true", help="Deploy; default only prints commands")
    parser.add_argument("--account", default=os.environ.get("TEAM4_GCLOUD_ACCOUNT", ACCOUNT))
    arguments = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", arguments.build_id):
        parser.error("Expected a Cloud Build UUID.")
    result = subprocess.run(
        [
            "gcloud",
            "builds",
            "describe",
            arguments.build_id,
            f"--project={PROJECT}",
            f"--region={REGION}",
            f"--account={arguments.account}",
            "--format=json(status,substitutions.COMMIT_SHA,results.images)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        print("Unable to read build metadata. Check account, region and build ID.")
        return 1
    try:
        commands = job_commands(
            json.loads(result.stdout), (arguments.worker,), account=arguments.account
        )
    except (ValueError, KeyError, TypeError) as error:
        print(f"Invalid build metadata: {error}")
        return 1
    for command in commands:
        print(shlex.join(command), flush=True)
        if arguments.apply and subprocess.run(command, check=False).returncode:
            print("Deployment stopped.")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
