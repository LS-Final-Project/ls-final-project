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
    api_updates = [
        part.split("\n", 2)[0:2]
        for part in source.split("ls_push_run run services update team4-platform")[1:]
    ]
    assert len(api_updates) == 2
    for update in api_updates:
        text = "\n".join(update)
        assert "NOTIFICATION_PUSH_ENABLED=false" in text
        assert "PRIVATE_KEY" not in text and "--update-secrets" not in text


def test_create_and_update_are_parked_and_all_preparation_failures_stay_closed():
    source = Path("infra/setup_notification_push.sh").read_text()
    full = source.split("# Inspect first;", 1)[1]
    listing = full.index("LS_PUSH_SCHEDULERS=$(gcloud scheduler jobs list")
    first_pause = full.index("ls_push_run scheduler jobs pause")
    first_update = full.index("ls_push_run run services update")
    worker = full.index("ls_push_run run jobs deploy")
    scheduler = full.index('ls_push_run scheduler jobs "$LS_PUSH_SCHEDULER_ACTION"')
    final_pause = full.index("ls_push_run scheduler jobs pause", first_pause + 1)
    confirm = full.index('[[ "$LS_PUSH_STATE" == PAUSED ]]')
    public_config = full.index("ls_push_run run services update", first_update + 1)
    assert (
        listing
        < first_pause
        < first_update
        < worker
        < scheduler
        < final_pause
        < confirm
        < public_config
    )
    assert "LS_PUSH_SCHEDULER_ACTION=create" in full
    assert "LS_PUSH_SCHEDULER_ACTION=update" in full[:first_pause]
    target = full[scheduler:final_pause]
    assert '--uri="$NOTIFICATION_PUSH_SERVICE_URL/health"' in target
    assert "/internal/v1/platform/notifications/push" not in source
    # Both create/update use the same inert target. A failure before the final
    # pause may leave a Scheduler enabled, but it cannot invoke push dispatch.
    assert "--paused" not in target
    assert "set -euo pipefail" in source
    assert "|| true" not in source and "scheduler jobs resume" not in source
    for part in full.split("ls_push_run run services update")[1:]:
        assert "NOTIFICATION_PUSH_ENABLED=false" in part.split("\n", 2)[1]


def test_parked_health_target_accepts_get_only_without_running_application():
    tree = ast.parse(Path("backend/api/common/application.py").read_text())
    routes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_api_route"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "app"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "/health"
    ]
    assert len(routes) == 1
    methods = next(keyword.value for keyword in routes[0].keywords if keyword.arg == "methods")
    assert ast.literal_eval(methods) == ["GET"]
