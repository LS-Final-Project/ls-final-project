"""O1/O2 are inspected as text/AST only: never import/execute operational scripts."""

import ast
from pathlib import Path


def test_vapid_registration_has_explicit_apply_and_secret_stays_in_stdin():
    source = Path("infra/register_vapid_secret.py").read_text()
    ast.parse(source)
    assert '"--apply"' in source and "apply: bool = False" in source
    assert '"--data-file=-"' in source and "input=payload" in source
    assert '"versions", "access"' not in source
    assert "private_bytes(" not in source and ".write_text(" not in source
    assert "notification-push-runtime@" in source


def test_scheduler_is_paused_and_job_never_executes_during_setup():
    source = Path("infra/setup_notification_push.sh").read_text()
    assert "LS_PUSH_APPLY=false" in source and "--apply)" in source
    assert "scheduler jobs pause" in source
    assert "run jobs execute" not in source and "scheduler jobs run" not in source
    assert "--max-retries=0" in source and "--tasks=1 --parallelism=1" in source
    assert "NOTIFICATION_PUSH_PRIVATE_KEY=notification-vapid-key:1" in source
    api_update = source.split("run services update team4-platform", 1)[1].split(
        "LS_PUSH_SCHEDULER_ACTION", 1
    )[0]
    assert "PRIVATE_KEY" not in api_update and "--update-secrets" not in api_update
