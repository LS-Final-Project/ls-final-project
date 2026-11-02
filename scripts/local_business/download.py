"""Pinned GWS stored-file downloads; planning never authenticates or writes (KAN-159).

The caller selects manifest groups before calling. No login, export, upload, DB or SDK.
Only explicit archive members are materialized, never review/gold/expected files.
"""

import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

MIMES = {
    "text/csv",
    "text/markdown",
    "application/json",
    "application/zip",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _require(ok, code):
    if not ok:
        raise ValueError(code)


def _relative(value):
    _require(isinstance(value, str) and value and "\\" not in value, "DOWNLOAD_PATH_INVALID")
    path = PurePosixPath(value)
    _require(
        not path.is_absolute()
        and bool(path.parts)
        and str(path) == value
        and "\x00" not in value
        and all(p not in {".", "..", "data"} for p in path.parts)
        and not any(re.search(r"gold|expected", p, re.I) for p in path.parts),
        "DOWNLOAD_PATH_INVALID",
    )
    return path.parts


def _file(row):
    _relative(row["relative_path"])
    _require(
        isinstance(row["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]),
        "DOWNLOAD_HASH_INVALID",
    )
    _require(type(row["bytes"]) is int and row["bytes"] > 0, "DOWNLOAD_SIZE_INVALID")


def validate_manifest(manifest: dict) -> None:
    """Validate a selected manifest; missing sources remain visible to plan()."""
    _require(manifest.get("schema_version") == 1, "DOWNLOAD_SCHEMA_INVALID")
    groups = manifest.get("groups")
    _require(isinstance(groups, dict) and bool(groups), "DOWNLOAD_GROUPS_INVALID")
    paths, ids = set(), set()
    for group, rows in groups.items():
        _require(
            group in {"corpus", "cs_history", "rfp"} and isinstance(rows, list),
            "DOWNLOAD_GROUPS_INVALID",
        )
        for row in rows:
            _file(row)
            _require(
                isinstance(row["id"], str) and row["id"] and row["id"] not in ids,
                "DOWNLOAD_ID_INVALID",
            )
            ids.add(row["id"])
            _require(
                isinstance(row["drive_id"], str)
                and re.fullmatch(r"[A-Za-z0-9_-]+", row["drive_id"]),
                "DOWNLOAD_DRIVE_ID_INVALID",
            )
            _require(row["mime_type"] in MIMES, "DOWNLOAD_MIME_INVALID")
            if group == "rfp":
                category = row.get("source_category")
                _require(
                    category in {"training", "evaluation", "generated_demo"}
                    and row["mime_type"]
                    == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    and row.get("origin")
                    == ("synthetic" if category == "generated_demo" else "demo")
                    and row["relative_path"].endswith(".docx")
                    and all(
                        isinstance(row.get(k), str) and row[k].strip()
                        for k in ("project_name", "customer_code", "title")
                    ),
                    "DOWNLOAD_RFP_INVALID",
                )
            outputs = [row]
            if row["mime_type"] == "application/zip":
                members = row.get("members")
                _require(isinstance(members, list) and bool(members), "DOWNLOAD_ARCHIVE_INVALID")
                names = set()
                for member in members:
                    _file(member)
                    _relative(member["archive_path"])
                    _require(member["archive_path"] not in names, "DOWNLOAD_ARCHIVE_INVALID")
                    names.add(member["archive_path"])
                outputs += members
            else:
                _require("members" not in row, "DOWNLOAD_ARCHIVE_INVALID")
            for output in outputs:
                path = output["relative_path"]
                _require(path not in paths, "DOWNLOAD_DUPLICATE_PATH")
                paths.add(path)
    _require(
        not any(a != b and b.startswith(a + "/") for a in paths for b in paths),
        "DOWNLOAD_PATH_COLLISION",
    )
    _require(
        isinstance(manifest.get("profiles"), dict)
        and all(
            isinstance(v, list) and all(g in groups for g in v)
            for v in manifest["profiles"].values()
        ),
        "DOWNLOAD_PROFILE_INVALID",
    )
    _require(
        isinstance(manifest.get("missing", []), list)
        and all(
            isinstance(r, dict) and r.get("group") in groups and isinstance(r.get("reason"), str)
            for r in manifest.get("missing", [])
        ),
        "DOWNLOAD_MISSING_INVALID",
    )


def _destination(destination):
    path = Path(destination)
    _require(
        path.is_absolute()
        and "artifacts" in path.parts
        and "data" not in path.parts
        and ".." not in path.parts,
        "DOWNLOAD_DESTINATION_INVALID",
    )
    for current in (path, *path.parents):
        _require(not current.is_symlink(), "DOWNLOAD_SYMLINK_FORBIDDEN")
    return path


@contextmanager
def _directory(path, *, create=False):
    """Walk with dirfds/O_NOFOLLOW, including destination ancestors."""
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _hash(stream):
    h = hashlib.sha256()
    size = 0
    while chunk := stream.read(1024 * 1024):
        h.update(chunk)
        size += len(chunk)
    return h.hexdigest(), size


def _existing(destination, row):
    path = destination / row["relative_path"]
    try:
        with _directory(path.parent) as fd:
            try:
                file = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            except FileNotFoundError:
                return False
            with os.fdopen(file, "rb") as stream:
                _require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "DOWNLOAD_FILE_INVALID")
                actual = _hash(stream)
        _require(actual == (row["sha256"], row["bytes"]), "DOWNLOAD_EXISTING_CONFLICT")
        return True
    except FileNotFoundError:
        return False


def _outputs(manifest):
    return [
        output
        for rows in manifest["groups"].values()
        for row in rows
        for output in [row, *row.get("members", [])]
    ]


def plan(manifest: dict, destination: Path) -> dict:
    """No writes/network. Conflicting files fail before any download starts."""
    validate_manifest(manifest)
    destination = _destination(destination)
    files = [
        {
            "relative_path": r["relative_path"],
            "sha256": r["sha256"],
            "bytes": r["bytes"],
            "action": "skip" if _existing(destination, r) else "download",
        }
        for r in _outputs(manifest)
    ]
    return {
        "ready": not manifest.get("missing"),
        "missing": manifest.get("missing", []),
        "files": files,
        "counts": {"files": len(files), "skip": sum(r["action"] == "skip" for r in files)},
    }


def _fetch(row, temporary):
    """Use existing GWS authentication; never echo its stderr or response bodies."""
    try:
        subprocess.run(
            [
                "gws",
                "drive",
                "files",
                "get",
                "--params",
                json.dumps({"fileId": row["drive_id"], "alt": "media", "supportsAllDrives": True}),
                "--output",
                str(temporary),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=300,
            cwd=temporary.parent,  # GWS requires --output to remain inside its current directory.
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError("DOWNLOAD_GWS_FAILED") from None


def _publish(destination, row, stream):
    path = destination / row["relative_path"]
    with _directory(path.parent, create=True) as fd:
        temporary = ".download-" + secrets.token_hex(16)
        file = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
        )
        try:
            with os.fdopen(file, "wb") as out:
                h, size = hashlib.sha256(), 0
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    _require(size <= row["bytes"], "DOWNLOAD_SIZE_MISMATCH")
                    h.update(chunk)
                    out.write(chunk)
                _require(
                    (h.hexdigest(), size) == (row["sha256"], row["bytes"]), "DOWNLOAD_HASH_MISMATCH"
                )
                out.flush()
                os.fsync(out.fileno())
            try:
                # Atomic no-replace publication; a concurrent writer cannot be overwritten.
                os.link(temporary, path.name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
            except FileExistsError:
                _require(_existing(destination, row), "DOWNLOAD_EXISTING_CONFLICT")
        finally:
            os.unlink(temporary, dir_fd=fd)


def download(manifest: dict, destination: Path) -> dict:
    """Explicit download. Exact existing bytes skip; partial completion is rerunnable."""
    report = plan(manifest, destination)
    _require(report["ready"], "DOWNLOAD_SOURCE_MISSING")
    destination = _destination(destination)
    for rows in manifest["groups"].values():
        for row in rows:
            if not _existing(destination, row):
                with tempfile.TemporaryDirectory(prefix="kan159-gws-") as staging:
                    temporary = Path(staging) / "download"
                    _fetch(row, temporary)
                    _require(
                        not temporary.is_symlink() and temporary.is_file(), "DOWNLOAD_FILE_INVALID"
                    )
                    with temporary.open("rb") as stream:
                        _publish(destination, row, stream)
            if row.get("members"):
                archive = destination / row["relative_path"]
                with _directory(archive.parent) as fd:
                    file = os.open(
                        archive.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
                    )
                    with os.fdopen(file, "rb") as stream:
                        _require(
                            stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "DOWNLOAD_FILE_INVALID"
                        )
                        _require(
                            _hash(stream) == (row["sha256"], row["bytes"]), "DOWNLOAD_HASH_MISMATCH"
                        )
                        stream.seek(0)
                        with zipfile.ZipFile(stream) as bundle:
                            for member in row["members"]:
                                if _existing(destination, member):
                                    continue
                                matches = [
                                    i
                                    for i in bundle.infolist()
                                    if i.filename == member["archive_path"]
                                ]
                                _require(
                                    len(matches) == 1
                                    and not matches[0].is_dir()
                                    and not stat.S_ISLNK(matches[0].external_attr >> 16)
                                    and matches[0].file_size == member["bytes"],
                                    "DOWNLOAD_ARCHIVE_INVALID",
                                )
                                with bundle.open(matches[0]) as source:
                                    _publish(destination, member, source)
    completed = plan(manifest, destination)
    return {**completed, "downloaded": report["counts"]["files"] - report["counts"]["skip"]}
