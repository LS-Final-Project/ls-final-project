"""Namespace ownership, relative state and explicit corpus readiness. No paid I/O."""

import json
import os
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAMESPACE = re.compile(r"team4-kan159-[a-z0-9][a-z0-9-]{0,30}")
POLICIES = frozenset({"POL-ORDER", "POL-RETURN", "POL-SERVICE", "POL-WARRANTY"})


def save(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as out:
            temporary = Path(out.name)
            json.dump(value, out, indent=2)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def state_path(namespace: str) -> Path:
    if not NAMESPACE.fullmatch(namespace):
        raise ValueError("LOCAL_NAMESPACE_INVALID")
    return ROOT / "artifacts" / "local-business" / namespace


def settings(state: Path) -> dict:
    config = json.loads((state / "settings.json").read_text())
    if not NAMESPACE.fullmatch(config.get("namespace", "")):
        raise ValueError("LOCAL_NAMESPACE_INVALID")
    if config.get("web_origin") != f"https://localhost:{config['ports']['web']}":
        raise ValueError("LOCAL_ORIGIN_INVALID")
    return config


def clean_env(extra: dict[str, str]) -> dict[str, str]:
    # No .env, inherited key, tracing, OAuth, proxy, Cloud Run or dispatch flag.
    return {"PATH": os.defpath, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1", **extra}


def policy_mapping(path: Path) -> dict[str, str]:
    from backend.agent.ticket_source_registry import policy_documents_from_manifest
    from backend.engine.search.chunking import INDEX_VERSION

    manifest = json.loads(path.read_text())
    rows = manifest.get("documents", [])
    if not rows or any(row.get("reason") is not None or not row.get("stored") for row in rows):
        raise ValueError("LOCAL_CORPUS_DOCUMENT_FAILED")
    mapping = policy_documents_from_manifest(path, INDEX_VERSION)
    if not POLICIES <= mapping.keys():
        raise ValueError("CS_POLICY_DOCUMENTS_MISSING")
    return mapping
