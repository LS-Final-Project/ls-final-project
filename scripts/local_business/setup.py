"""Fresh owned namespace only. Never reset, import a clone or seed business results."""

import hashlib
import io
import json
import os
import secrets
import shutil
import subprocess
import tarfile
import time
from pathlib import Path
from uuid import uuid4

from scripts.local_business.config import ROOT, save, state_path

PG_IMAGE = "pgvector/pgvector:0.8.7-pg17-trixie"


def docker(*args, data=None):
    result = subprocess.run(["docker", *map(str, args)], input=data, capture_output=True)
    if result.returncode:
        raise RuntimeError("LOCAL_DOCKER_COMMAND_FAILED")
    return result.stdout


def inspect(name):
    return json.loads(docker("inspect", name))[0]


def fresh(namespace: str, *, runtime_image: str, web_image: str, web_port: int = 38443):
    if type(web_port) is not int or not 1 <= web_port <= 65535 or web_port in {38081, 38082, 38083}:
        raise ValueError("LOCAL_PORTS_INVALID")
    os.umask(0o077)
    dirty = subprocess.check_output(
        [
            "git",
            "-C",
            str(ROOT),
            "status",
            "--porcelain",
            "--",
            "backend",
            "infra",
            "scripts",
            "frontend",
            "pyproject.toml",
            "uv.lock",
        ]
    )
    if dirty:
        raise ValueError("LOCAL_SOURCE_DIRTY_COMMIT_BEFORE_SETUP")
    state = state_path(namespace)
    if state.exists():
        raise ValueError("LOCAL_STATE_EXISTS_USE_START")
    names = set(docker("ps", "-a", "--format", "{{.Names}}").decode().splitlines())
    volumes = set(docker("volume", "ls", "--format", "{{.Name}}").decode().splitlines())
    networks = set(docker("network", "ls", "--format", "{{.Name}}").decode().splitlines())
    if any(n.startswith(namespace + "-") for n in names | volumes | networks):
        raise ValueError("LOCAL_NAMESPACE_EXISTS_PRESERVE")
    # Explicit images must already be installed/built; silently pulling or using old source is forbidden.
    image_ids = {
        key: json.loads(docker("image", "inspect", image))[0]["Id"]
        for key, image in {"pg": PG_IMAGE, "runtime": runtime_image, "web": web_image}.items()
    }
    state.mkdir(parents=True, mode=0o700)
    for directory in ("auth", "private", "control", "files", "corpus", "run", "logs"):
        (state / directory).mkdir(mode=0o700)
    owner = str(uuid4())
    config = {
        "namespace": namespace,
        "owner": owner,
        "images": image_ids,
        "ports": {"web": web_port, "cs": 38081, "tft": 38082, "platform": 38083},
        "web_origin": f"https://localhost:{web_port}",
        "setup_complete": False,
        "source_sha": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip(),
    }
    # A single Git archive supplies infra, backend, scripts and frontend source. No ignored .env.
    source = state / "source"
    source.mkdir(mode=0o700)
    archive = subprocess.check_output(["git", "-C", str(ROOT), "archive", config["source_sha"]])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tree:
        if any(member.issym() or member.islnk() for member in tree.getmembers()):
            raise ValueError("LOCAL_SOURCE_SYMLINK_FORBIDDEN")
        tracked = {
            member.name: hashlib.sha256(tree.extractfile(member).read()).hexdigest()
            for member in tree.getmembers()
            if member.isfile()
        }
        tree.extractall(source, filter="data")
    (state / "private/source.tar").write_bytes(archive)
    config["source_archive_sha256"] = hashlib.sha256(archive).hexdigest()
    # Build this archived frontend with the feature flag explicitly enabled. Never accept an old dist.
    build_env = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "VITE_BUSINESS_UI_ENABLED": "true",
    }
    for command in (["npm", "ci", "--no-audit", "--no-fund"], ["npm", "run", "build"]):
        result = subprocess.run(
            command, cwd=source / "frontend", env=build_env, capture_output=True
        )
        (state / "logs" / ("frontend-" + command[1] + ".log")).write_bytes(
            result.stdout + result.stderr
        )
        if result.returncode:
            raise RuntimeError("LOCAL_FRONTEND_BUILD_FAILED_PRESERVED")
    # Build dependencies are never needed by the running static/relay server.
    shutil.rmtree(source / "frontend/node_modules")
    for filename, digest in tracked.items():
        if hashlib.sha256((source / filename).read_bytes()).hexdigest() != digest:
            raise ValueError("LOCAL_BUILD_CHANGED_TRACKED_SOURCE")
    source_files = {
        str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(source.rglob("*"))
        if p.is_file() and not p.is_relative_to(source / "frontend/dist")
    }
    save(state / "source-manifest.json", {"tracked": tracked, "files": source_files})
    config["frontend_build"] = {
        "source_sha": config["source_sha"],
        "business_ui": True,
        "files": {
            str(p.relative_to(source / "frontend/dist")): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((source / "frontend/dist").rglob("*"))
            if p.is_file()
        },
    }
    save(state / "settings.json", config)
    passwords = {
        role: secrets.token_urlsafe(24)
        for role in (
            "admin",
            "cs_app",
            "tft_app",
            "platform_app",
            "local_cs_worker",
            "local_tft_worker",
        )
    }
    env = {
        "POSTGRES_DB": "team4",
        "POSTGRES_PASSWORD": passwords["admin"],
        "LOCAL_CS_PASSWORD": passwords["cs_app"],
        "LOCAL_TFT_PASSWORD": passwords["tft_app"],
        "LOCAL_PLATFORM_PASSWORD": passwords["platform_app"],
    }
    (state / "private/db.env").write_text("".join(k + "=" + v + "\n" for k, v in env.items()))
    (state / "private/db.env").chmod(0o600)
    worker_dsns = {
        role: f"postgresql://{role}:{passwords[role]}@{namespace}-pg:5432/team4"
        for role in ("platform_app", "local_cs_worker", "local_tft_worker")
    }
    save(state / "private/worker-db.json", worker_dsns)
    labels = [
        "--label",
        "team4.local.namespace=" + namespace,
        "--label",
        "team4.local.owner=" + owner,
    ]
    docker("network", "create", "--internal", *labels, namespace + "-internal")
    docker("network", "create", *labels, namespace + "-worker-egress")
    docker("volume", "create", *labels, namespace + "-pgdata")
    command = [
        "run",
        "-d",
        "--pull=never",
        "--name",
        namespace + "-pg",
        *labels,
        "--network",
        namespace + "-internal",
        "--env-file",
        state / "private/db.env",
        "--mount",
        f"type=volume,src={namespace}-pgdata,dst=/var/lib/postgresql/data",
    ]
    for source, target in [
        ("infra/local/init.sh", "/docker-entrypoint-initdb.d/10-team4.sh"),
        ("infra/local", "/setup/local"),
        ("infra/sql", "/setup/sql"),
        ("infra/migrations", "/setup/migrations"),
    ]:
        command += ["--mount", f"type=bind,src={state / 'source' / source},dst={target},readonly"]
    docker(*command, image_ids["pg"])
    for _ in range(90):
        container = inspect(namespace + "-pg")
        logs = docker("logs", namespace + "-pg")
        (state / "logs/setup-pg.log").write_bytes(logs)
        if not container["State"]["Running"]:
            raise RuntimeError("LOCAL_FRESH_INIT_FAILED_PRESERVED")
        if b"PostgreSQL init process complete" in logs:
            break
        time.sleep(1)
    else:
        raise RuntimeError("LOCAL_FRESH_INIT_TIMEOUT_PRESERVED")
    for _ in range(60):
        ready = subprocess.run(
            [
                "docker",
                "exec",
                namespace + "-pg",
                "pg_isready",
                "-h",
                "127.0.0.1",
                "-U",
                "postgres",
                "-d",
                "team4",
            ],
            capture_output=True,
        )
        if ready.returncode == 0:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("LOCAL_FRESH_FINAL_SERVER_NOT_READY_PRESERVED")
    # Standard migrations create NOLOGIN worker roles. Only new-namespace setup adds separate logins.
    sql = "BEGIN;\n"
    for role in ("local_cs_worker", "local_tft_worker"):
        member = "cs_worker" if role == "local_cs_worker" else "tft_worker"
        sql += f"CREATE ROLE {role} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '{passwords[role]}';\nGRANT {member} TO {role};\n"
    sql += "COMMIT;\n"
    docker(
        "exec",
        "-i",
        namespace + "-pg",
        "psql",
        "-X",
        "-U",
        "postgres",
        "-d",
        "team4",
        "-v",
        "ON_ERROR_STOP=1",
        data=sql.encode(),
    )
    query = "BEGIN READ ONLY; SELECT count(*) FROM cs.tickets; SELECT count(*) FROM tft.projects; SELECT count(*) FROM identity.users WHERE email LIKE '%@example.invalid'; COMMIT;"
    counts = (
        docker(
            "exec",
            namespace + "-pg",
            "psql",
            "-X",
            "-U",
            "postgres",
            "-d",
            "team4",
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            query,
        )
        .decode()
        .splitlines()
    )
    if counts != ["BEGIN", "0", "0", "4", "COMMIT"]:
        raise RuntimeError("LOCAL_SETUP_UNEXPECTED_BUSINESS_DATA")
    for file in ("caller-key.pem", "tls-key.pem"):
        subprocess.run(
            [
                "openssl",
                "genpkey",
                "-algorithm",
                "RSA",
                "-pkeyopt",
                "rsa_keygen_bits:2048",
                "-out",
                str(state / "auth" / file),
            ],
            check=True,
            capture_output=True,
        )
    subprocess.run(
        [
            "openssl",
            "pkey",
            "-in",
            str(state / "auth/caller-key.pem"),
            "-pubout",
            "-out",
            str(state / "auth/caller-public.pem"),
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "openssl",
            "req",
            "-new",
            "-x509",
            "-key",
            str(state / "auth/tls-key.pem"),
            "-out",
            str(state / "auth/tls-cert.pem"),
            "-days",
            "7",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    config["setup_complete"] = True
    save(state / "settings.json", config)
    return state


def owned(state: Path):
    from scripts.local_business.config import settings

    config = settings(state)
    if not config["setup_complete"]:
        raise ValueError("LOCAL_SETUP_INCOMPLETE_PRESERVED")
    build = config.get("frontend_build", {})
    if build.get("source_sha") != config["source_sha"] or build.get("business_ui") is not True:
        raise ValueError("LOCAL_FRONTEND_PROVENANCE_MISSING")
    dist = state / "source/frontend/dist"
    if {str(p.relative_to(dist)) for p in dist.rglob("*") if p.is_file()} != set(build["files"]):
        raise ValueError("LOCAL_FRONTEND_PROVENANCE_MISMATCH")
    for filename, digest in build["files"].items():
        if (
            hashlib.sha256((state / "source/frontend/dist" / filename).read_bytes()).hexdigest()
            != digest
        ):
            raise ValueError("LOCAL_FRONTEND_PROVENANCE_MISMATCH")
    archive = (state / "private/source.tar").read_bytes()
    if hashlib.sha256(archive).hexdigest() != config["source_archive_sha256"]:
        raise ValueError("LOCAL_SOURCE_PROVENANCE_MISMATCH")
    manifest = json.loads((state / "source-manifest.json").read_text())
    with tarfile.open(fileobj=io.BytesIO(archive)) as tree:
        original = {
            member.name: hashlib.sha256(tree.extractfile(member).read()).hexdigest()
            for member in tree.getmembers()
            if member.isfile()
        }
    if original != manifest["tracked"]:
        raise ValueError("LOCAL_SOURCE_PROVENANCE_MISMATCH")
    source = state / "source"
    actual_files = {
        str(p.relative_to(source))
        for p in source.rglob("*")
        if p.is_file() and not p.is_relative_to(dist)
    }
    if any(p.is_symlink() for p in source.rglob("*")) or actual_files != set(manifest["files"]):
        raise ValueError("LOCAL_SOURCE_PROVENANCE_MISMATCH")
    for filename, digest in manifest["files"].items():
        if hashlib.sha256((source / filename).read_bytes()).hexdigest() != digest:
            raise ValueError("LOCAL_SOURCE_PROVENANCE_MISMATCH")
    if not manifest["tracked"].items() <= manifest["files"].items():
        raise ValueError("LOCAL_SOURCE_PROVENANCE_MISMATCH")
    namespace = config["namespace"]
    container = inspect(namespace + "-pg")
    if (
        container["Config"]["Labels"].get("team4.local.owner") != config["owner"]
        or container["Image"] != config["images"]["pg"]
        or not any(m.get("Name") == namespace + "-pgdata" for m in container["Mounts"])
    ):
        raise ValueError("LOCAL_NAMESPACE_OWNERSHIP_MISMATCH")
    return config
