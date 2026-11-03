"""Bootstrap boundaries without Docker, supplied data, network or paid calls."""

import json
import os
from unittest.mock import Mock

import pytest

from scripts.local_business import bootstrap as module


def manifest():
    return json.loads((module.ROOT / "docs/local-business-downloads.json").read_text())


def test_selected_missing_blocks_full_but_not_cs(tmp_path):
    source = manifest()
    source["missing"] = [
        {"group": "rfp", "relative_path": "rfp/not-shared.docx", "reason": "not shared"}
    ]
    destination = tmp_path / "artifacts/inputs"
    assert not module.download.plan(module.selected_manifest(source, "full"), destination)["ready"]
    assert module.download.plan(module.selected_manifest(source, "cs"), destination)["ready"]
    assert not destination.exists()


def test_registration_projection_keeps_eval_and_synthetic_boundaries():
    rows = module.rfp_inputs(manifest())
    assert len(rows) == 23
    assert all(not set(row) - module.FIELDS for row in rows)
    assert sum(row["source_category"] == "evaluation" for row in rows) == 4
    assert sum(row["source_category"] == "generated_demo" for row in rows) == 1


def test_registration_rejects_origin_contradiction():
    source = manifest()
    source["pipeline"]["rfp"][0]["origin"] = "synthetic"
    with pytest.raises(ValueError, match="RFP_ORIGIN_MISMATCH"):
        module.rfp_inputs(source)


def test_partial_write_is_not_repeated(tmp_path):
    path = tmp_path / "private/bootstrap.json"
    journal = {"steps": {}}
    effect = []

    def interrupted():
        effect.append("committed")
        raise RuntimeError("connection lost after commit")

    with pytest.raises(RuntimeError):
        module.once(journal, path, "history", interrupted)
    assert json.loads(path.read_text())["steps"]["history"] == "UNKNOWN"
    with pytest.raises(ValueError, match="INTERRUPTED_STAGE"):
        module.once(json.loads(path.read_text()), path, "history", interrupted)
    assert effect == ["committed"]


def test_completed_stage_preserves_later_business_work(tmp_path):
    action = Mock()
    module.once({"steps": {"history": "DONE"}}, tmp_path / "journal", "history", action)
    action.assert_not_called()


def test_homebrew_paths_explicit_no_secret_inheritance(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-inherit")
    monkeypatch.setenv("PATH", "/untrusted")
    locations = {
        tool: "/opt/homebrew/bin/" + tool for tool in ("docker", "git", "node", "npm", "openssl")
    }
    monkeypatch.setattr(module.shutil, "which", lambda tool: locations.get(tool))
    environment = module.tool_env()
    assert environment["PATH"].split(os.pathsep)[0] == "/opt/homebrew/bin"
    assert "/untrusted" not in environment["PATH"]
    assert "GEMINI_API_KEY" not in environment
    assert environment["HOME"]


def test_lock_does_not_create_namespace_or_allow_second_driver(tmp_path):
    state = tmp_path / "new-state"
    with module.namespace_lock(state):
        assert not state.exists()
        with pytest.raises(ValueError, match="ALREADY_RUNNING"):
            with module.namespace_lock(state):
                pytest.fail("second driver entered")


def test_source_proof_rejects_settings_self_assertion(tmp_path, monkeypatch):
    monkeypatch.setattr(module.subprocess, "check_output", lambda *a, **kw: "a" * 40 + "\n")
    with pytest.raises(ValueError, match="SOURCE_HEAD_MISMATCH"):
        module.source_proof(tmp_path, {"source_sha": "b" * 40})


def test_source_proof_binds_git_archive_and_loader(tmp_path, monkeypatch):
    path = tmp_path / "source/scripts/cs_import_provided.py"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"reviewed loader")
    config = {"source_sha": "a" * 40, "source_archive_sha256": module.digest(b"archive")}
    monkeypatch.setattr(
        module.subprocess,
        "check_output",
        Mock(side_effect=["a" * 40 + "\n", b"archive", b"reviewed loader"]),
    )
    assert module.source_proof(tmp_path, config) == module.digest(b"reviewed loader")
    path.write_bytes(b"changed loader")
    monkeypatch.setattr(
        module.subprocess,
        "check_output",
        Mock(side_effect=["a" * 40 + "\n", b"archive", b"reviewed loader"]),
    )
    with pytest.raises(ValueError, match="LOADER_MISMATCH"):
        module.source_proof(tmp_path, config)


def test_runtime_has_no_keys_external_network_or_writable_source(tmp_path, monkeypatch):
    capture = Mock(return_value=b"")
    monkeypatch.setattr(module, "command", capture)
    state = tmp_path / "state"
    config = {"namespace": "team4-kan159-fixture", "images": {"runtime": "sha256:" + "a" * 64}}
    module.runtime(
        state,
        config,
        ["-m", "scripts.cs_import_provided"],
        "postgres",
        tmp_path / "inputs",
        tmp_path / "log",
    )
    args = capture.call_args.args[0]
    assert args[args.index("--network") + 1] == "container:team4-kan159-fixture-pg"
    assert args[args.index("--entrypoint") + 1] == "/usr/bin/env"
    assert f"type=bind,src={state / 'source'},dst=/workspace,readonly" in args
    assert not any("key-file" in arg or "auth" in arg or "docker.sock" in arg for arg in args)


def test_plan_does_not_start_download_or_docker(tmp_path, monkeypatch):
    call = Mock(side_effect=AssertionError("unexpected mutation"))
    monkeypatch.setattr(module.download, "download", call)
    monkeypatch.setattr(module, "command", call)
    monkeypatch.setattr(module, "state_path", lambda ns: tmp_path / ns)
    result = module.bootstrap("team4-kan159-fixture", plan_only=True)
    assert result["ready"] and result["counts"]["files"] == 142
    call.assert_not_called()


def test_completed_namespace_never_downloads_or_imports_again(tmp_path, monkeypatch):
    state = tmp_path / "team4-kan159-fixture"
    journal = {
        "profile": "full",
        "source_sha": "a" * 40,
        "complete": True,
        "steps": {
            step: "DONE" for step in ("history", "assumed_units", "corpus", "partners", "rfp")
        },
    }
    module.save(state / "private/bootstrap.json", journal)
    config = {"source_sha": "a" * 40, "web_origin": "https://localhost:40443"}
    monkeypatch.setattr(module, "state_path", lambda ns: state)
    monkeypatch.setattr(module, "owned", lambda p: config)
    monkeypatch.setattr(module, "tool_env", lambda: {})
    monkeypatch.setattr(module, "no_active_driver", lambda c: None)
    ready = Mock()
    monkeypatch.setattr(module, "start_ready", ready)
    forbid = Mock(side_effect=AssertionError("must preserve prior work"))
    monkeypatch.setattr(module.download, "download", forbid)
    monkeypatch.setattr(module, "load_history", forbid)
    result = module.bootstrap(state.name)
    assert result["reused"] and result["source_sha"] == "a" * 40
    ready.assert_called_once_with(state, config)
    forbid.assert_not_called()


def test_model_enable_exit_without_live_marker_is_failure_and_disables(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setattr(module, "state_path", lambda ns: state)
    monkeypatch.setattr(module, "owned", lambda p: {})
    monkeypatch.setattr(module, "no_active_driver", lambda c: None)
    monkeypatch.setattr(module, "running_drivers", lambda c: [])
    for name in ("permit", "dispatcher", "worker", "runtime"):
        path = state / "source/scripts/local_business" / (name + ".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("CONCURRENCY_PROTOCOL = 1\n")
    commands = Mock(return_value=b"started")
    monkeypatch.setattr(module, "command", commands)
    with pytest.raises(ValueError, match="MODEL_DRIVER_NOT_READY"):
        module.enable_selected("team4-kan159-fixture", "cs", tmp_path / "key", 1)
    assert len(commands.call_args_list) == 2
    assert "enable-real" in commands.call_args_list[0].args[0]
    assert "disable-real" in commands.call_args_list[1].args[0]


def test_host_production_flag_is_not_silently_sanitized(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "")
    with pytest.raises(ValueError, match="LOCAL_ONLY"):
        module.bootstrap("team4-kan159-fixture", plan_only=True)


def test_stderr_warning_cannot_corrupt_success_json(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "tool_env", lambda: {"PATH": os.defpath})
    log = tmp_path / "private/result.log"
    result = module.command(
        [
            os.sys.executable,
            "-c",
            "import sys; print('{\"SAME\":12}'); print('late warning',file=sys.stderr)",
        ],
        log,
    )
    assert json.loads(result) == {"SAME": 12}
    assert log.with_suffix(".stderr.log").read_text().strip() == "late warning"
