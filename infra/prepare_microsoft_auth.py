#!/usr/bin/env python3
"""공용 Microsoft 인증 설정을 미리본다. --apply는 운영자 터미널에서만 실행한다."""

import argparse
import hmac
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID

PROJECT = "proj-skkuls0106-183255377938"
ACCOUNT = "skkuls0106@skkukdp.re.kr"
REGION = "asia-northeast3"
ORIGIN = "https://team4-web-gew6ci4pgq-du.a.run.app"
CALLBACK = ORIGIN + "/api/v1/platform/auth/callback"
SECRET = "microsoft-client-secret"
RUNTIME = f"platform-runtime@{PROJECT}.iam.gserviceaccount.com"


class PreparationError(Exception):
    """비밀값이나 공급자 원문을 포함하지 않는 준비 실패."""


def gcloud(arguments: list[str], payload: bytes | None = None) -> str:
    """고정 프로젝트·계정으로 실행하고 입력 비밀값과 공급자 출력을 숨긴다.

    Args:
        arguments: 실행할 gcloud 하위 명령과 공개 리소스 인자.
        payload: 새 Secret에만 표준 입력으로 전달할 비밀값.

    Returns:
        str: 호출자가 검사할 표준 출력. 원문을 그대로 사용자에게 출력하지 않는다.
    """
    try:
        result = subprocess.run(
            ["gcloud", *arguments, f"--project={PROJECT}", f"--account={ACCOUNT}", "--quiet"],
            input=payload,
            capture_output=True,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PreparationError(
            "gcloud 실행을 확인하세요. 원격 상태 확인 전 재시도하지 마세요."
        ) from None
    if result.returncode:
        raise PreparationError("gcloud 작업이 실패했습니다. 원격 상태를 확인하세요.")
    return result.stdout.decode("utf-8")


def prepare(client_id: str, client_secret: str, *, apply: bool = False) -> None:
    """서버 Secret과 비활성 인증 설정을 준비하며 로그인 활성화·DB 변경은 하지 않는다.

    Args:
        client_id: 기존 사전 점검 앱의 MS_CLIENT_ID.
        client_secret: 로컬에서 읽은 MS_CLIENT_SECRET. 출력하거나 argv에 넣지 않는다.
        apply: 운영자가 명시했을 때만 Secret·접근 권한·서버 설정을 변경한다.

    Returns:
        None: 기본은 미리보기이며 적용 후에도 Microsoft 인증은 비활성이다.
    """
    try:
        app_id = str(UUID(client_id))
    except ValueError:
        raise PreparationError("유효한 MS_CLIENT_ID를 설정하세요.") from None
    if not client_secret.strip() or len(client_secret.encode()) > 65536:
        raise PreparationError("유효한 MS_CLIENT_SECRET을 로컬에 설정하세요.")
    values = {
        "DEV_AUTH_ENABLED": "false",
        "MICROSOFT_AUTH_ENABLED": "false",
        "MICROSOFT_AUTH_CLIENT_ID": app_id,
        "MICROSOFT_AUTH_APP_ORIGIN": ORIGIN,
        "MICROSOFT_AUTH_REDIRECT_URI": CALLBACK,
    }
    if not apply:
        print(f"PLAN: {SECRET}:1 준비, 접근 권한 추가 대상={RUNTIME}")
        print("PLAN: team4-platform에 인증 설정 연결, MICROSOFT_AUTH_ENABLED=false 유지")
        print(f"Web callback: {CALLBACK}")
        print("미리보기입니다. 클라우드 호출 없음. 적용은 운영자 터미널의 --apply로 수행하세요.")
        return

    service = json.loads(
        gcloud(
            [
                "run",
                "services",
                "describe",
                "team4-platform",
                f"--region={REGION}",
                "--format=json(spec.template.spec)",
            ]
        )
    )["spec"]["template"]["spec"]
    env = {
        item["name"]: item
        for container in service["containers"]
        for item in container.get("env", [])
    }
    if (
        service.get("serviceAccountName") != RUNTIME
        or env.get("ENVIRONMENT", {}).get("value") != "production"
    ):
        raise PreparationError("플랫폼 실행 계정과 production 설정을 먼저 확인하세요.")
    for name in ("DEV_AUTH_ENABLED", "MICROSOFT_AUTH_ENABLED"):
        if (name in env and "value" not in env[name]) or env.get(name, {}).get(
            "value", "false"
        ).lower() not in {"false", "0"}:
            raise PreparationError(
                "이미 활성화됐거나 확인할 수 없는 인증 모드입니다. 설정을 보존합니다."
            )
    for name, value in values.items():
        if name not in {"DEV_AUTH_ENABLED", "MICROSOFT_AUTH_ENABLED"} and name in env:
            if env[name].get("value") != value:
                raise PreparationError("기존 Microsoft 앱 설정이 다릅니다. 설정을 보존합니다.")
    binding = env.get("MICROSOFT_AUTH_CLIENT_SECRET")
    if binding and binding.get("valueFrom", {}).get("secretKeyRef") != {"name": SECRET, "key": "1"}:
        raise PreparationError("기존 Microsoft Secret 연결이 다릅니다. 연결을 보존합니다.")

    existing = {
        name.rsplit("/", 1)[-1]
        for name in gcloud(["secrets", "list", "--format=value(name)"]).splitlines()
    }
    if SECRET not in existing:
        gcloud(
            [
                "secrets",
                "create",
                SECRET,
                "--replication-policy=user-managed",
                f"--locations={REGION}",
                "--data-file=-",
                "--format=none",
            ],
            payload=client_secret.encode(),
        )
        print(f"CREATED: {SECRET}:1")
    else:
        state = gcloud(
            ["secrets", "versions", "describe", "1", f"--secret={SECRET}", "--format=value(state)"]
        ).strip()
        if state != "ENABLED":
            raise PreparationError(
                "기존 microsoft-client-secret:1을 사용할 수 없습니다. 버전을 보존합니다."
            )
        stored = gcloud(["secrets", "versions", "access", "1", f"--secret={SECRET}"])
        if not hmac.compare_digest(stored.encode(), client_secret.encode()):
            raise PreparationError(
                "기존 Secret과 로컬 MS_CLIENT_SECRET이 다릅니다. 기존 값을 보존합니다."
            )
        print(f"KEPT: {SECRET}:1; 일치 확인, 값·버전 변경 없음.")
    gcloud(
        [
            "secrets",
            "add-iam-policy-binding",
            SECRET,
            f"--member=serviceAccount:{RUNTIME}",
            "--role=roles/secretmanager.secretAccessor",
            "--condition=None",
            "--format=none",
        ]
    )
    gcloud(
        [
            "run",
            "services",
            "update",
            "team4-platform",
            f"--region={REGION}",
            "--update-env-vars=" + ",".join(f"{name}={value}" for name, value in values.items()),
            f"--update-secrets=MICROSOFT_AUTH_CLIENT_SECRET={SECRET}:1",
            "--format=none",
        ]
    )
    print(
        "PREPARED: 플랫폼 서버 설정 연결. 인증은 비활성이며 DB·이미지·메일은 변경하지 않았습니다."
    )


def main() -> int:
    """기존 로컬 MS 설정을 읽고 기본 미리보기 또는 운영자의 명시적 적용을 수행한다.

    Args:
        없음. --env-file과 --apply를 명령행에서 읽는다.

    Returns:
        int: 준비 성공은 0, 입력·원격 상태 확인이 필요하면 1.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env.integrations"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from dotenv import dotenv_values

    try:
        values = dotenv_values(args.env_file, interpolate=False) if args.env_file.exists() else {}
        prepare(
            os.environ.get("MS_CLIENT_ID") or values.get("MS_CLIENT_ID") or "",
            os.environ.get("MS_CLIENT_SECRET") or values.get("MS_CLIENT_SECRET") or "",
            apply=args.apply,
        )
    except PreparationError as error:
        print(f"FAIL: {error} 비밀값은 출력하지 않았습니다.", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError):
        print(
            "FAIL: Microsoft 설정 준비 미완료. 입력·원격 상태를 확인하세요. 비밀값은 출력하지 않았습니다.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
