"""로컬 OS 알림(웹 푸시, KAN-201). 운영의 Secret Manager 키·Cloud Scheduler·Cloud Run Job 대신
namespace마다 만든 시험용 VAPID 키와 플랫폼 프로세스 안의 짧은 주기 루프로 같은 push_worker를 돌린다.

키는 state/auth/push.json(0600, git 제외)에만 있고 로그에 쓰지 않는다. settings.json의 "local_push": false면 쓰지 않는다.
"""

import asyncio
import base64
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

CONFIG = "push.json"
SUBJECT = "mailto:local-push@example.invalid"
INTERVAL_SECONDS = 5
log = logging.getLogger(__name__)


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def ensure_keys(state: Path) -> bool:
    """키 파일이 없으면 P-256 키 쌍을 만들어 0600으로 쓴다. 새로 만들었으면 True."""
    path = state / "auth" / CONFIG
    # setup이 만든 auth 폴더(0700)가 없으면 만들지 않는다(권한이 다른 폴더를 새로 만들지 않게).
    if path.exists() or not path.parent.is_dir():
        return False
    private = ec.generate_private_key(ec.SECP256R1())
    value = {
        "private_key": b64(private.private_numbers().private_value.to_bytes(32, "big")),
        "public_key": b64(
            private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        ),
        "subject": SUBJECT,
    }
    temporary = path.with_name(f".{CONFIG}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(value, out)
    temporary.replace(path)
    return True


def load(state: Path, config: dict[str, Any]) -> dict[str, str] | None:
    if config.get("local_push", True) is False:
        return None
    path = state / "auth" / CONFIG
    return json.loads(path.read_text()) if path.exists() else None


def api_settings(keys: dict[str, str]) -> Any:
    """구독·공개 키 경로를 여는 API 설정. Cloud 칸은 검증만 통과하는 로컬 자리값이며 쓰이지 않는다."""
    from backend.api.platform.notifications.push_settings import PushSettings

    return PushSettings(
        _env_file=None,
        enabled=True,
        public_key=keys["public_key"],
        service_url="https://local-push.local.run.app",
        invoker_email="local-push@local-push.iam.gserviceaccount.com",
        project_number="100000",
    )


def start_worker(common: Any, keys: dict[str, str]) -> threading.Thread:
    """운영 Job과 같은 push_worker.run을 별도 스레드에서 짧은 주기로 돈다(전송이 동기라 API 루프를 막지 않게)."""
    from pydantic import SecretStr

    from backend.api.platform.notifications import push_worker
    from backend.api.platform.notifications.push_settings import PushWorkerSettings

    push = PushWorkerSettings(
        _env_file=None,
        enabled=True,
        private_key=SecretStr(keys["private_key"]),
        public_key=keys["public_key"],
        subject=keys["subject"],
    )

    def loop() -> None:
        stop = threading.Event()
        while not stop.wait(INTERVAL_SECONDS):
            try:
                asyncio.run(push_worker.run(common, push))
            except Exception:  # noqa: BLE001 — 로컬 보조 루프, 이유에 비밀이 섞일 수 있어 종류만 남긴다
                log.warning("LOCAL_PUSH_WORKER_FAILED")

    thread = threading.Thread(target=loop, name="local-push", daemon=True)
    thread.start()
    return thread
