"""Synthetic files only; no actual credentials, Docker, database or model calls."""

import json
from types import SimpleNamespace

import pytest

from scripts.local_business import __main__ as cli
from scripts.local_business import config


def test_save_ignores_and_preserves_stale_other_temporary_file(tmp_path):
    path = tmp_path / "enabled.json"
    stale = tmp_path / "enabled.json.new"
    stale.write_text("preserve interrupted earlier writer")
    config.save(path, {"mode": "real"})
    assert json.loads(path.read_text()) == {"mode": "real"}
    assert path.stat().st_mode & 0o777 == 0o600
    assert stale.read_text() == "preserve interrupted earlier writer"
    assert set(tmp_path.iterdir()) == {path, stale}


def test_save_failure_preserves_destination_and_removes_only_own_temp(tmp_path):
    path = tmp_path / "enabled.json"
    path.write_text("previous complete value")
    unrelated = tmp_path / "another-writer.tmp"
    unrelated.write_text("preserve")
    with pytest.raises(TypeError):
        config.save(path, {"not_json": object()})
    assert path.read_text() == "previous complete value"
    assert unrelated.read_text() == "preserve"
    assert set(tmp_path.iterdir()) == {path, unrelated}


def test_enable_rejects_original_symlink_before_loading_prices(tmp_path, monkeypatch):
    key = tmp_path / "synthetic-key"
    key.write_text("not-a-real-key")
    key.chmod(0o600)
    link = tmp_path / "key-link"
    link.symlink_to(key)
    monkeypatch.setattr(cli, "owned", lambda _: {"namespace": "team4-kan159-offline"})
    from backend.shared import ai

    monkeypatch.setattr(ai, "load_price_table", lambda _: pytest.fail("Must reject before prices"))
    args = SimpleNamespace(key_file=str(link), prices_file=str(tmp_path / "unused"))
    with pytest.raises(ValueError, match="LOCAL_PRIVATE_KEY_FILE_REQUIRED"):
        cli.enable(tmp_path, args)
    assert link.is_symlink() and key.read_text() == "not-a-real-key"


@pytest.mark.parametrize("mode,size", [(0o644, 16), (0o600, 4097)])
def test_enable_retains_private_permission_and_size_checks(tmp_path, monkeypatch, mode, size):
    key = tmp_path / "synthetic-key"
    key.write_text("x" * size)
    key.chmod(mode)
    monkeypatch.setattr(cli, "owned", lambda _: {"namespace": "team4-kan159-offline"})
    args = SimpleNamespace(key_file=str(key), prices_file=str(tmp_path / "unused"))
    with pytest.raises(ValueError, match="LOCAL_PRIVATE_KEY_FILE_REQUIRED"):
        cli.enable(tmp_path, args)


def test_replace_failure_keeps_old_value_and_cleans_only_current_temp(tmp_path, monkeypatch):
    from pathlib import Path

    path = tmp_path / "enabled.json"
    path.write_text("old value")
    stale = tmp_path / "enabled.json.new"
    stale.write_text("preserve")

    def fail_replace(self, target):
        assert self.parent == path.parent and target == path
        assert self.stat().st_mode & 0o777 == 0o600
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="synthetic replace failure"):
        config.save(path, {"new": "value"})
    assert path.read_text() == "old value" and stale.read_text() == "preserve"
    assert set(tmp_path.iterdir()) == {path, stale}
