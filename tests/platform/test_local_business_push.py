"""KAN-201 로컬 OS 알림 키: namespace마다 한 번 만들고 0600, 운영 검증기를 통과, 끄면 쓰지 않는다."""

import stat

from pydantic import SecretStr

from backend.api.platform.notifications.push_settings import PushWorkerSettings
from scripts.local_business import push


def test_keys_are_created_once_private_and_valid(tmp_path):
    (tmp_path / "auth").mkdir()
    assert push.ensure_keys(tmp_path) is True
    path = tmp_path / "auth" / push.CONFIG
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    first = path.read_text()
    assert push.ensure_keys(tmp_path) is False and path.read_text() == first
    keys = push.load(tmp_path, {})
    assert keys is not None
    assert push.api_settings(keys).enabled is True
    worker = PushWorkerSettings(
        _env_file=None,
        enabled=True,
        private_key=SecretStr(keys["private_key"]),
        public_key=keys["public_key"],
        subject=keys["subject"],
    )
    assert worker.enabled is True
    assert push.load(tmp_path, {"local_push": False}) is None
