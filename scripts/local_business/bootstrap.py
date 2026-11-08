"""Explicit local setup; paid workers require personal opt-in. Preserve existing work."""

import argparse
import copy
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from scripts.cs_import_provided import Target
from scripts.local_business import download
from scripts.local_business.config import ROOT, clean_env, policy_mapping, save, state_path
from scripts.local_business.permit import (
    require_concurrency_snapshot,
    require_unlimited_snapshot,
    validate_concurrency,
    validate_limits,
)
from scripts.local_business.register_inputs import FIELDS, register_inputs
from scripts.local_business.setup import PG_IMAGE, docker, owned
from scripts.local_business.stack import no_active_driver, running_drivers

WEB_IMAGE = "node:24-bookworm-slim"


def require(condition, code):
    if not condition:
        raise ValueError("LOCAL_BOOTSTRAP_" + code)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def selected_manifest(manifest, profile):
    """Select missing entries together with groups; never hide a selected missing input."""
    groups = {"full": {"corpus", "cs_history", "rfp"}, "cs": {"corpus", "cs_history"}}
    require(profile in groups, "PROFILE_INVALID")
    selected = copy.deepcopy(manifest)
    selected["groups"] = {k: v for k, v in manifest["groups"].items() if k in groups[profile]}
    selected["profiles"] = {profile: sorted(groups[profile])}
    selected["missing"] = [r for r in manifest.get("missing", []) if r["group"] in groups[profile]]
    if profile == "cs":
        selected["pipeline"]["rfp"] = []
    download.validate_manifest(selected)
    return selected


def rfp_inputs(manifest):
    """Ignore only known transport fields; reject contradictions before any POST."""
    rows = manifest["pipeline"]["rfp"]
    for row in rows:
        expected = "synthetic" if row["source_category"] == "generated_demo" else "demo"
        require(row.get("origin") == expected, "RFP_ORIGIN_MISMATCH")
        require(not set(row) - FIELDS - {"origin"}, "RFP_FIELDS_INVALID")
    return [{k: v for k, v in row.items() if k in FIELDS} for row in rows]


def tool_env():
    """Resolve required tools explicitly, including Homebrew directories on macOS."""
    paths = []
    for tool in ("docker", "git", "node", "npm", "openssl"):
        executable = shutil.which(tool)
        require(executable is not None, "MISSING_" + tool.upper())
        paths.append(str(Path(executable).absolute().parent))
    return clean_env({"PATH": os.pathsep.join(dict.fromkeys(paths + os.defpath.split(os.pathsep)))})


@contextmanager
def namespace_lock(state):
    # The lock lives outside state: fresh() must see a genuinely absent namespace directory.
    state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state.parent / ("." + state.name + ".bootstrap.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("LOCAL_BOOTSTRAP_ALREADY_RUNNING") from None
        yield
    finally:
        os.close(fd)


def command(args, log, *, data=None):
    log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with log.open("wb") as out, log.with_suffix(".stderr.log").open("wb") as errors:
        result = subprocess.run(
            args, input=data, stdout=out, stderr=errors, env=tool_env(), cwd=ROOT
        )
    require(result.returncode == 0, "COMMAND_FAILED_PRESERVED")
    return log.read_bytes()


def source_proof(state, config):
    """Bind namespace settings to Git, saved archive and the actual read-only loader."""
    head = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    require(head == config["source_sha"], "SOURCE_HEAD_MISMATCH")
    archive = subprocess.check_output(["git", "-C", str(ROOT), "archive", head])
    require(digest(archive) == config["source_archive_sha256"], "ARCHIVE_MISMATCH")
    path = "scripts/cs_import_provided.py"
    blob = subprocess.check_output(["git", "-C", str(ROOT), "show", f"{head}:{path}"])
    require(blob == (state / "source" / path).read_bytes(), "LOADER_MISMATCH")
    return digest(blob)


def runtime(state, config, module_args, role, inputs, log, *, extra=()):
    """No external route, keys, inherited env or writable source in the input loader."""
    require(role in {"postgres", "cs_app", "local_cs_worker", "tft_app"}, "ROLE_INVALID")
    args = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--network",
        "container:" + config["namespace"] + "-pg",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=64m",
        "--workdir",
        "/tmp",
    ]
    for source, target, writable in (
        (state / "source", "/workspace", False),
        (inputs, "/input", False),
        (state / "corpus", "/local/corpus", True),
        (state / "settings.json", "/target/settings.json", False),
        (state / "private/bootstrap-receipts", "/receipt", True),
    ):
        require("," not in str(source) and not source.is_symlink(), "MOUNT_INVALID")
        args += [
            "--mount",
            f"type=bind,src={source},dst={target}" + ("" if writable else ",readonly"),
        ]
    args += [
        "--entrypoint",
        "/usr/bin/env",
        config["images"]["runtime"],
        "-i",
        "PATH=/app/.venv/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME=/tmp",
        "PYTHONPATH=/workspace",
        "PYTHONDONTWRITEBYTECODE=1",
        "ENVIRONMENT=local",
        f"DATABASE_URL=postgresql://{role}@127.0.0.1:5432/team4",
        *extra,
        "python",
        *module_args,
    ]
    return command(args, log)


def once(journal, path, name, action):
    """Never repeat a possibly committed write after process or host interruption."""
    status = journal["steps"].get(name)
    if status == "DONE":
        return
    require(status is None, "INTERRUPTED_STAGE_REQUIRES_REVIEW")
    journal["steps"][name] = "UNKNOWN"
    save(path, journal)
    action()
    journal["steps"][name] = "DONE"
    save(path, journal)


def input_hash(manifest, relative):
    rows = [r for group in manifest["groups"].values() for r in group]
    rows += [member for row in list(rows) for member in row.get("members", [])]
    matches = [r["sha256"] for r in rows if r["relative_path"] == relative]
    require(len(matches) == 1, "PIPELINE_PATH_INVALID")
    return matches[0]


def load_history(state, config, manifest, inputs, loader_hash):
    query = (
        "BEGIN READ ONLY; SELECT json_build_object('system_id', "
        "(SELECT system_identifier::text FROM pg_control_system()), 'db_oid', "
        "(SELECT oid::bigint FROM pg_database WHERE datname=current_database())); COMMIT;"
    )
    raw = docker(
        "exec",
        config["namespace"] + "-pg",
        "psql",
        "-XAt",
        "-U",
        "postgres",
        "-d",
        "team4",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        query,
    )
    values = [json.loads(line) for line in raw.decode().splitlines() if line.startswith("{")]
    require(len(values) == 1, "DB_IDENTITY_INVALID")
    target = Target(
        config["namespace"],
        config["owner"],
        config["images"]["pg"],
        config["images"]["runtime"],
        values[0]["system_id"],
        values[0]["db_oid"],
        config["source_sha"],
        loader_hash,
    )
    history = manifest["pipeline"]["history"]
    args = ["-m", "scripts.cs_import_provided"]
    for flag, key in (
        ("manifest", "manifest"),
        ("source-tickets", "source_tickets"),
        ("cases", "cases"),
        ("ref", "ref"),
    ):
        args += ["--" + flag, "/input/" + history[key]]
    args += [
        "--manifest-sha256",
        input_hash(manifest, history["manifest"]),
        "--cases-sha256",
        input_hash(manifest, history["cases"]),
        "--apply",
        "--confirm-target",
        target.token(),
        "--target-state",
        "/target",
        "--namespace",
        target.namespace,
        "--expected-owner",
        target.owner,
        "--expected-pg-image",
        target.pg_image,
        "--expected-runtime-image",
        target.runtime_image,
        "--expected-system-id",
        target.system_id,
        "--expected-db-oid",
        str(target.db_oid),
        "--expected-source-sha",
        target.expected_source_sha,
        "--expected-loader-sha256",
        target.expected_loader_sha256,
        "--receipt",
        "/receipt/history.json",
    ]
    runtime(state, config, args, "postgres", inputs, state / "logs/bootstrap-history.log")
    require(
        (state / "private/bootstrap-receipts/history.json").is_file(), "HISTORY_RECEIPT_MISSING"
    )


def load_corpus(state, config, manifest, inputs):
    directory = "/input/" + manifest["pipeline"]["corpus_dir"]
    runtime(
        state,
        config,
        [
            "-m",
            "scripts.cs_index_docs",
            "--docs",
            directory,
            "--doc-index",
            directory + "/doc_index.csv",
            "--data-origin",
            "synthetic",
            "--manifest-out",
            "/local/corpus/cs-index-execute.json",
            "--execute",
        ],
        "cs_app",
        inputs,
        state / "logs/bootstrap-corpus.log",
    )
    policy_mapping(state / "corpus/cs-index-execute.json")
    runtime(
        state,
        config,
        [
            "-c",
            "import os; from scripts.local_business.dispatcher import corpus_check; corpus_check(os.environ['DATABASE_URL'])",
        ],
        "local_cs_worker",
        inputs,
        state / "logs/bootstrap-corpus-verified.log",
    )


def load_partners(state, config, inputs):
    runtime(
        state,
        config,
        [
            "-m",
            "scripts.rfp_seed_partner_profiles",
            "--apply",
            "--confirm-target",
            "host=127.0.0.1 port=5432 dbname=team4",
        ],
        "tft_app",
        inputs,
        state / "logs/bootstrap-partners.log",
    )
    output = runtime(
        state,
        config,
        ["-m", "scripts.rfp_seed_partner_profiles", "--check"],
        "tft_app",
        inputs,
        state / "logs/bootstrap-partners-check.log",
    )
    summary = json.loads(output.decode().splitlines()[-1])
    # The seed file is the source of truth (14 profiles since #618); a fixed count went stale.
    expected = len(json.loads((ROOT / "backend/api/tft/partners/profiles.json").read_text()))
    require(
        summary.get("SAME") == expected and summary.get("MISSING") == summary.get("DIFFERENT") == 0,
        "PARTNER_CHECK_FAILED",
    )


def load_units(state, config, inputs):
    output = runtime(
        state,
        config,
        ["-m", "scripts.local_business.bootstrap_units"],
        "postgres",
        inputs,
        state / "logs/bootstrap-units.log",
    )
    result = json.loads(output.decode().splitlines()[-1])
    require(
        result["tickets_reused"] == 22
        and result["tickets_inserted"]
        == result["tickets_updated"]
        == result["confirmed_links_created"]
        == 0
        and result["units_inserted"] + result["units_skipped"] == 34
        and result["links_inserted"] + result["links_skipped"] == 10,
        "ASSUMED_UNITS_CHECK_FAILED",
    )


def load_candidates(state, config, inputs):
    """KAN-162 QA: draft the provided groups already past the repeat threshold (proposal 4.4 ⑩)."""
    output = runtime(
        state,
        config,
        ["-m", "scripts.local_business.bootstrap_candidates"],
        "postgres",
        inputs,
        state / "logs/bootstrap-candidates.log",
    )
    result = json.loads(output.decode().splitlines()[-1])
    require(
        result["created"] + result["already_requested"] + result["skipped"] == result["eligible"],
        "CANDIDATES_CHECK_FAILED",
    )


def start_ready(state, config):
    from scripts.local_business.__main__ import stop_tunnel

    no_active_driver(config)
    stop_tunnel(state)  # Verify ownership before replacing this namespace's listener.
    command(
        [
            os.sys.executable,
            "-m",
            "scripts.local_business",
            "start",
            "--namespace",
            config["namespace"],
        ],
        state / "logs/bootstrap-start.log",
    )


def bootstrap(namespace, profile="full", port=38443, *, plan_only=False):
    require(
        os.environ.get("ENVIRONMENT", "local") != "production" and "K_SERVICE" not in os.environ,
        "LOCAL_ONLY",
    )
    os.umask(0o077)
    state = state_path(namespace)
    require(not state.is_symlink(), "STATE_SYMLINK")
    manifest = selected_manifest(
        json.loads((ROOT / "docs/local-business-downloads.json").read_text()), profile
    )
    manifest_hash = digest(json.dumps(manifest, sort_keys=True).encode())
    inputs = ROOT / "artifacts/local-inputs" / manifest_hash
    report = download.plan(manifest, inputs)
    if plan_only:
        return report
    require(report["ready"], "INPUTS_MISSING")
    tool_env()
    with namespace_lock(state):
        journal_path = state / "private/bootstrap.json"
        if state.exists():
            config = owned(state)
            no_active_driver(config)
            require(journal_path.is_file(), "EXISTING_NAMESPACE_NOT_BOOTSTRAPPED")
            journal = json.loads(journal_path.read_text())
            require(journal["profile"] == profile, "PROFILE_MISMATCH")
            # Completed work uses its saved data and archive, even after a later git pull.
            if journal.get("complete"):
                require(journal["source_sha"] == config["source_sha"], "JOURNAL_SOURCE_MISMATCH")
                required = {"history", "assumed_units", "corpus", "partners"} | (
                    {"rfp"} if profile == "full" else set()
                )
                require(
                    all(journal["steps"].get(step) == "DONE" for step in required),
                    "JOURNAL_INCOMPLETE",
                )
                start_ready(state, config)
                return {
                    "url": config["web_origin"],
                    "reused": True,
                    "source_sha": config["source_sha"],
                    "model_enabled": False,
                }
            require(journal["manifest_sha256"] == manifest_hash, "INPUT_VERSION_CHANGED")
            require("UNKNOWN" not in journal["steps"].values(), "INTERRUPTED_STAGE_REQUIRES_REVIEW")
        else:
            # Download validation completes before any Docker/DB setup mutation.
            dirty = subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(ROOT),
                    "status",
                    "--porcelain",
                    "--",
                    "Dockerfile",
                    "scripts",
                    "docs/local-business-downloads.json",
                    "backend",
                    "infra",
                    "frontend",
                    "pyproject.toml",
                    "uv.lock",
                ]
            )
            require(not dirty, "SOURCE_DIRTY_COMMIT_FIRST")
            require(shutil.which("gws") is not None, "MISSING_GWS")
            download.download(manifest, inputs)
            logroot = ROOT / "artifacts/bootstrap-logs" / namespace
            for image in (PG_IMAGE, WEB_IMAGE):
                result = subprocess.run(
                    ["docker", "image", "inspect", image], capture_output=True, env=tool_env()
                )
                if result.returncode:
                    command(
                        ["docker", "pull", image],
                        logroot / ("pull-pg.log" if image == PG_IMAGE else "pull-node.log"),
                    )
            archive = subprocess.check_output(["git", "-C", str(ROOT), "archive", "HEAD"])
            runtime_image = namespace + "-runtime"
            command(
                ["docker", "build", "--target", "dependencies", "-t", runtime_image, "-"],
                logroot / "runtime-build.log",
                data=archive,
            )
            command(
                [
                    os.sys.executable,
                    "-m",
                    "scripts.local_business",
                    "setup",
                    "--namespace",
                    namespace,
                    "--runtime-image",
                    runtime_image,
                    "--web-image",
                    WEB_IMAGE,
                    "--web-port",
                    str(port),
                ],
                logroot / "setup.log",
            )
            config = owned(state)
            journal = {
                "profile": profile,
                "manifest_sha256": manifest_hash,
                "source_sha": config["source_sha"],
                "steps": {},
                "complete": False,
            }
            save(journal_path, journal)
        loader_hash = source_proof(state, config)
        download.download(manifest, inputs)  # Exact hash skip; never replace edited local inputs.
        (state / "private/bootstrap-receipts").mkdir(exist_ok=True, mode=0o700)
        start_ready(state, config)
        for name, action in (
            ("history", lambda: load_history(state, config, manifest, inputs, loader_hash)),
            ("assumed_units", lambda: load_units(state, config, inputs)),
            ("improvement_candidates", lambda: load_candidates(state, config, inputs)),
            ("corpus", lambda: load_corpus(state, config, manifest, inputs)),
            ("partners", lambda: load_partners(state, config, inputs)),
        ):
            no_active_driver(config)
            once(journal, journal_path, name, action)
        if profile == "full":
            once(
                journal,
                journal_path,
                "rfp",
                lambda: register_inputs(state, rfp_inputs(manifest), inputs),
            )
        journal["complete"] = True
        save(journal_path, journal)
        return {
            "url": config["web_origin"],
            "reused": False,
            "source_sha": config["source_sha"],
            "model_enabled": False,
        }


def enable_selected(
    namespace,
    owner,
    key_file,
    max_jobs,
    *,
    max_calls=20,
    reserve_usd="0.15",
    budget_usd="1.50",
    unlimited=False,
    concurrent_jobs=1,
    concurrent_calls=6,
):
    """Explicit opt-in only; verify a live driver before advertising model readiness."""
    state = state_path(namespace)
    with namespace_lock(state):
        config = owned(state)
        no_active_driver(config)
        if unlimited:
            require_unlimited_snapshot(state)
        validate_concurrency(concurrent_jobs, concurrent_calls)
        require_concurrency_snapshot(state)
        args = [
            os.sys.executable,
            "-m",
            "scripts.local_business",
            "enable-real",
            "--namespace",
            namespace,
            "--owner",
            owner,
            "--key-file",
            str(key_file),
            "--prices-file",
            str(state / "source/backend/shared/ai_prices.json"),
            "--model",
            "gemini-3.6-flash",
            "--reserve-usd",
            reserve_usd,
            "--concurrent-jobs",
            str(concurrent_jobs),
            "--concurrent-calls",
            str(concurrent_calls),
        ]
        if unlimited:
            args += ["--unlimited"]
        else:
            args += [
                "--max-jobs",
                str(max_jobs),
                "--max-calls",
                str(max_calls),
                "--budget-usd",
                budget_usd,
            ]
        marker = state / "control/enabled.json"
        require(not marker.exists(), "STALE_MODEL_MARKER_REQUIRES_REVIEW")
        try:
            command(args, state / "logs/bootstrap-enable.log")
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                drivers = running_drivers(config)
                if not drivers:
                    break
                if marker.is_file():
                    data = json.loads(marker.read_text())
                    ready_limits = isinstance(data, dict) and (
                        (
                            unlimited
                            and data.get("limit_mode") == "unlimited"
                            and "remaining_jobs" in data
                            and data["remaining_jobs"] is None
                        )
                        or (
                            not unlimited
                            and data.get("limit_mode", "limited") == "limited"
                            and type(data.get("remaining_jobs")) is int
                            and data["remaining_jobs"] == max_jobs
                        )
                    )
                    if (
                        len(drivers) == 1
                        and isinstance(data, dict)
                        and data.get("mode") == "real"
                        and data.get("owner") == owner
                        and ready_limits
                        and type(data.get("concurrent_jobs")) is int
                        and data["concurrent_jobs"] == concurrent_jobs
                        and type(data.get("concurrent_calls")) is int
                        and data["concurrent_calls"] == concurrent_calls
                        and 0 <= time.time() - marker.stat().st_mtime < 5
                    ):
                        return
                time.sleep(0.2)
            raise ValueError("LOCAL_BOOTSTRAP_MODEL_DRIVER_NOT_READY")
        except BaseException:
            command(
                [
                    os.sys.executable,
                    "-m",
                    "scripts.local_business",
                    "disable-real",
                    "--namespace",
                    namespace,
                ],
                state / "logs/bootstrap-disable-after-failure.log",
            )
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", default="team4-kan159-my-pc")
    parser.add_argument("--profile", choices=("full", "cs"), default="full")
    parser.add_argument("--port", type=int, default=38443)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--enable-model", choices=("cs", "tft"))
    parser.add_argument("--key-file", type=Path)
    parser.add_argument(
        "--unlimited",
        action="store_true",
        help="With --enable-model: remove tool job/call/USD caps; preserve UNKNOWN blocking",
    )
    parser.add_argument("--max-jobs", type=int, help="Limited mode default: 1")
    parser.add_argument("--concurrent-jobs", type=int, default=1)
    parser.add_argument("--concurrent-calls", type=int, default=6)
    parser.add_argument("--max-calls", type=int, help="Limited mode default: 20")
    parser.add_argument("--reserve-usd", default="0.15")
    parser.add_argument("--budget-usd", help="Limited mode default: 1.50")
    args = parser.parse_args()
    if bool(args.enable_model) != bool(args.key_file) or (args.plan and args.enable_model):
        parser.error("--enable-model and --key-file must be supplied together, without --plan")
    if args.unlimited and (
        not args.enable_model
        or any(v is not None for v in (args.max_jobs, args.max_calls, args.budget_usd))
    ):
        parser.error(
            "--unlimited requires --enable-model and cannot be combined with finite job/call/USD caps"
        )
    args.max_jobs = 1 if args.max_jobs is None else args.max_jobs
    args.max_calls = 20 if args.max_calls is None else args.max_calls
    args.budget_usd = "1.50" if args.budget_usd is None else args.budget_usd
    try:
        validate_concurrency(args.concurrent_jobs, args.concurrent_calls)
        if args.enable_model == "cs" and args.concurrent_jobs != 1:
            parser.error("C/S supports one job at a time")
        validate_limits(
            args.max_jobs, args.max_calls, args.reserve_usd, args.budget_usd, args.unlimited
        )
    except ValueError:
        parser.error("Invalid local limits or amounts")
    try:
        if args.unlimited and state_path(args.namespace).exists():
            state = state_path(args.namespace)
            owned(state)
            require_unlimited_snapshot(state)
        if args.enable_model and state_path(args.namespace).exists():
            state = state_path(args.namespace)
            owned(state)
            require_concurrency_snapshot(state)
        result = bootstrap(args.namespace, args.profile, args.port, plan_only=args.plan)
        if args.enable_model:
            enable_selected(
                args.namespace,
                args.enable_model,
                args.key_file,
                args.max_jobs,
                max_calls=args.max_calls,
                reserve_usd=args.reserve_usd,
                budget_usd=args.budget_usd,
                unlimited=args.unlimited,
                concurrent_jobs=args.concurrent_jobs,
                concurrent_calls=args.concurrent_calls,
            )
            result["model_enabled"] = True
            result["model_owner"] = args.enable_model
    except Exception as error:
        # Raw subprocess/HTTP exceptions can include source data or credentials.
        import re

        value = str(error)
        code = (
            value
            if re.fullmatch(r"(?:LOCAL_|DOWNLOAD_)[A-Z0-9_]+", value)
            else "LOCAL_BOOTSTRAP_FAILED_PRESERVED"
        )
        print(json.dumps({"code": code}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
