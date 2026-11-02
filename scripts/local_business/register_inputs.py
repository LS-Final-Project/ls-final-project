"""Register approved RFP download rows through local sales HTTP; never process documents.

The bootstrap projects its approved download manifest onto this module's exact schema.
No download, document parsing, database writes, model calls or credential creation live here.
The private ledger is intentionally fail-closed after an ambiguous POST or interrupted attempt.
"""

import hashlib
import json
import os
import re
import ssl
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from uuid import UUID, uuid4

import httpx

from scripts.local_business.config import save
from scripts.local_business.setup import owned
from scripts.local_business.stack import no_active_driver

ORIGINS = {"training": "demo", "evaluation": "demo", "generated_demo": "synthetic"}
FIELDS = {
    "id",
    "relative_path",
    "sha256",
    "bytes",
    "source_category",
    "project_name",
    "customer_code",
    "title",
    "note",
}
LIMIT = 25 * 1024 * 1024


def _fail(reason):
    raise ValueError("LOCAL_REGISTER_" + reason)


def _regular(path):
    if path.is_symlink() or not path.is_file():
        _fail("FILE_INVALID")


def _bytes(path, row):
    _regular(path)
    if path.resolve() != path.absolute():
        _fail("INPUT_PATH")
    if path.stat().st_size != row["bytes"]:
        _fail("SIZE_MISMATCH")
    data = path.read_bytes()
    if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
        _fail("HASH_MISMATCH")
    return data


def _inputs(inputs, root):
    root = root.absolute()
    if not root.is_dir() or root.resolve() != root:
        _fail("DOWNLOAD_ROOT_INVALID")
    rows, ids, hashes, names = [], set(), set(), set()
    for raw in inputs:
        if not isinstance(raw, dict) or set(raw) - FIELDS or FIELDS - {"note"} - raw.keys():
            _fail("INPUT_SCHEMA")
        row = {**raw, "note": raw.get("note", "")}
        for field, maximum in (
            ("id", 100),
            ("project_name", 200),
            ("customer_code", 40),
            ("title", 300),
            ("note", 2000),
        ):
            v = row[field]
            if not isinstance(v, str) or len(v) > maximum or (field != "note" and not v.strip()):
                _fail("INPUT_TEXT")
            if v != v.strip() or any(ord(c) < 32 for c in v):
                _fail("INPUT_TEXT")
        if row["source_category"] not in ORIGINS:
            _fail("SOURCE_CATEGORY")
        if not isinstance(row["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", row["sha256"]):
            _fail("INPUT_HASH")
        if type(row["bytes"]) is not int or not 0 < row["bytes"] <= LIMIT:
            _fail("INPUT_SIZE")
        value = row["relative_path"]
        if not isinstance(value, str) or "\\" in value:
            _fail("INPUT_PATH")
        relative = Path(value)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.suffix.lower() not in {".docx", ".pdf"}
        ):
            _fail("INPUT_PATH")
        path = root / relative
        if path.resolve() != path or not path.is_relative_to(root):
            _fail("INPUT_PATH")
        _bytes(path, row)  # Validate every input before login or any business write.
        if row["id"] in ids or row["sha256"] in hashes or row["project_name"] in names:
            _fail("DUPLICATE_INPUT")
        ids.add(row["id"])
        hashes.add(row["sha256"])
        names.add(row["project_name"])
        rows.append((row, path))
    return rows


@contextmanager
def _ledger_lock(state):
    private = state / "private"
    if private.is_symlink():
        _fail("PRIVATE_PATH")
    private.mkdir(mode=0o700, exist_ok=True)
    if private.stat().st_mode & 0o077:
        _fail("PRIVATE_PERMISSIONS")
    lock = private / "register-inputs.lock"
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        _fail("LOCKED_REVIEW_INTERRUPTED_OWNER")
    try:
        os.close(fd)
        yield private / "register-inputs.json"
    finally:
        lock.unlink()


def _client(state, origin):
    cert = state / "auth/tls-cert.pem"
    if cert.resolve() != cert.absolute():
        _fail("TLS_CERT_PATH")
    _regular(cert)
    tls = ssl.create_default_context(cafile=str(cert))
    return httpx.Client(
        base_url=origin,
        verify=tls,
        follow_redirects=False,
        trust_env=False,
        timeout=30,
        transport=None,
    )


class _Chooser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = {
                "action": attrs.get("action"),
                "method": attrs.get("method"),
                "states": [],
                "sales": 0,
            }
            self.forms.append(self.current)
        if self.current is not None:
            if tag == "input" and attrs.get("name") == "state":
                self.current["states"].append(attrs.get("value"))
            if tag == "button" and attrs.get("name") == "code" and attrs.get("value") == "local-3":
                self.current["sales"] += 1

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


def _redirect(response, origin, status):
    if response.status_code != status:
        _fail("LOGIN_STATUS")
    location = response.headers.get("location", "")
    target = urljoin(origin + "/", location)
    if not location or not target.startswith(origin + "/") or urlsplit(target).fragment:
        _fail("REDIRECT_FORBIDDEN")
    return target


def _login(client, origin):
    start = client.get("/api/v1/platform/auth/login")
    target = _redirect(start, origin, 302)
    parsed = urlsplit(target)
    query = parse_qs(parsed.query, keep_blank_values=True)
    if parsed.path != "/local/authorize" or set(query) != {"state"} or len(query["state"]) != 1:
        _fail("LOCAL_AUTHORIZE_INVALID")
    state = query["state"][0]
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", state):
        _fail("LOCAL_STATE_INVALID")
    chooser = client.get(target)
    if chooser.status_code != 200:
        _fail("CHOOSER_STATUS")
    parser = _Chooser()
    parser.feed(chooser.text)
    expected = {
        "action": "/api/v1/platform/auth/callback",
        "method": "post",
        "states": [state],
        "sales": 1,
    }
    if parser.forms != [expected]:
        _fail("CHOOSER_INVALID")
    callback = client.post(
        "/api/v1/platform/auth/callback",
        data={"state": state, "code": "local-3"},
        headers={"Origin": origin, "Referer": target},
    )
    if _redirect(callback, origin, 303) not in {origin + "/", origin + "/account"}:
        _fail("LOGIN_RETURN_INVALID")
    me = client.get("/api/v1/platform/me")
    identity = _data(me, 200)
    if "sales" not in identity.get("roles", []) or not me.headers.get("x-csrf-token"):
        _fail("SALES_SESSION_REQUIRED")
    _uuid(identity.get("id"))
    return me.headers["x-csrf-token"], identity["id"]


def _data(response, status):
    if response.status_code != status:
        _fail("HTTP_STATUS_" + str(response.status_code))
    body = response.json()
    if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
        _fail("RESPONSE_SCHEMA")
    return body["data"]


def _uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        _fail("RESPONSE_UUID")
    return value


def _list(client, endpoint):
    rows, seen, cursor = [], set(), None
    while True:
        path = endpoint + ("?" + urlencode({"cursor": cursor}) if cursor else "")
        data = _data(client.get(path), 200)
        if not isinstance(data.get("items"), list):
            _fail("LIST_SCHEMA")
        rows.extend(data["items"])
        cursor = data.get("next_cursor")
        if cursor is None:
            return rows
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            _fail("CURSOR_INVALID")
        seen.add(cursor)


def _project(data, row):
    _uuid(data.get("id"))
    if (
        data.get("name") != row["project_name"]
        or data.get("company_code") != row["customer_code"]
        or data.get("data_origin") != ORIGINS[row["source_category"]]
    ):
        _fail("PROJECT_MISMATCH")
    return data


def _document(data, row):
    _uuid(data.get("document_id"))
    _uuid(data.get("document_version_id"))
    if (
        data.get("sha256") != row["sha256"]
        or data.get("size") != row["bytes"]
        or data.get("data_origin") != ORIGINS[row["source_category"]]
        or data.get("kind") != "RFP_SPEC"
        or data.get("title") != row["title"]
    ):
        _fail("DOCUMENT_MISMATCH")
    return data


def _post(client, endpoint, entry, operation, ledger, ledger_path, headers, validate, **kwargs):
    if operation in entry:
        _fail("POST_ALREADY_ATTEMPTED")
    if not re.fullmatch(r"/api/v1/tft/projects(?:/[0-9a-f-]{36}/documents)?", endpoint):
        _fail("POST_PATH")
    attempt = {"key": str(uuid4()), "status": "UNKNOWN", "endpoint": endpoint}
    entry[operation] = attempt
    # Record key/uncertain state before transmission for process-interruption recovery.
    save(ledger_path, ledger)
    response = client.post(
        endpoint, headers={**headers, "Idempotency-Key": attempt["key"]}, **kwargs
    )
    attempt["http_status"] = response.status_code
    if 400 <= response.status_code < 500 and response.status_code not in {408, 409, 429}:
        attempt["status"] = "REJECTED"
        save(ledger_path, ledger)
        _fail("POST_REJECTED")
    value = validate(_data(response, 201))
    attempt.update(status="SUCCEEDED", response=value)
    save(ledger_path, ledger)
    return value


def register_inputs(state: Path, inputs: list[dict], download_root: Path) -> dict:
    """Register approved rows once; caller must also hold its bootstrap namespace lock.

    UNKNOWN/REJECTED attempts need operator review, not automatic retries. A stale lock after
    process death likewise requires review. A confirmed create with no upload attempt resumes
    only its existing project. Atomic writes and locks are not fsynced: after an unclean OS
    shutdown or power loss, an operator must reconcile server records before restarting.
    Skip verification compares server hash metadata; it does not download document binaries.
    Returns metadata only, never document contents or session secrets.
    """
    if (
        os.environ.get("ENVIRONMENT", "").strip().lower() == "production"
        or "K_SERVICE" in os.environ
    ):
        _fail("ENVIRONMENT")
    state = state.absolute()
    if state.resolve() != state:
        _fail("STATE_PATH")
    rows = _inputs(inputs, download_root)
    with _ledger_lock(state) as ledger_path:
        config = owned(state)
        no_active_driver(config)
        origin = config["web_origin"]
        parsed = urlsplit(origin)
        if (
            parsed.hostname != "localhost"
            or not parsed.port
            or parsed.port > 65535
            or origin != f"https://localhost:{parsed.port}"
        ):
            _fail("ORIGIN")
        target = {k: config[k] for k in ("namespace", "owner", "source_sha", "web_origin")}
        ledger = {"version": 1, "target": target, "items": {}}
        if ledger_path.is_symlink():
            _fail("LEDGER_PATH")
        if ledger_path.exists():
            _regular(ledger_path)
            if ledger_path.stat().st_mode & 0o077:
                _fail("LEDGER_PERMISSIONS")
            ledger = json.loads(ledger_path.read_text())
            if ledger.get("version") != 1 or ledger.get("target") != target:
                _fail("LEDGER_TARGET")
        for old in ledger["items"].values():
            if any(
                old.get(op, {}).get("status") in {"UNKNOWN", "REJECTED"}
                for op in ("create", "upload")
            ):
                _fail("UNRESOLVED_POST_REVIEW_REQUIRED")
        for row, _ in rows:
            old = ledger["items"].get(row["id"])
            if old and old["input"] != row:
                _fail("LEDGER_INPUT_CHANGED")
        results = []
        with _client(state, origin) as client:
            csrf, actor = _login(client, origin)
            headers = {"Origin": origin, "X-CSRF-Token": csrf}
            projects = _list(client, "/api/v1/tft/projects")
            existing = []
            for project in projects:
                pid = _uuid(project.get("id"))
                docs = _list(client, f"/api/v1/tft/projects/{pid}/documents")
                existing.extend((pid, doc) for doc in docs)
            for row, path in rows:
                entry = ledger["items"].setdefault(row["id"], {"input": row})
                if entry.get("create", {}).get("status") == "SUCCEEDED":
                    entry.setdefault("project_id", entry["create"]["response"]["id"])
                if entry.get("upload", {}).get("status") == "SUCCEEDED":
                    entry.setdefault("document_id", entry["upload"]["response"]["document_id"])
                matches = [
                    (pid, doc) for pid, doc in existing if doc.get("sha256") == row["sha256"]
                ]
                if len(matches) > 1:
                    _fail("DUPLICATE_SERVER_HASH")
                if matches:
                    pid, doc = matches[0]
                    project = _project(_data(client.get(f"/api/v1/tft/projects/{pid}"), 200), row)
                    if project["id"] != pid:
                        _fail("PROJECT_UUID_MISMATCH")
                    _document(doc, row)
                    if (
                        entry.get("project_id", pid) != pid
                        or entry.get("document_id", doc["document_id"]) != doc["document_id"]
                    ):
                        _fail("LEDGER_SERVER_MISMATCH")
                    status = "SKIPPED_VERIFIED"
                else:
                    if entry.get("document_id") or entry.get("upload"):
                        _fail("SAVED_DOCUMENT_MISSING")
                    pid = entry.get("project_id")
                    if pid:
                        _uuid(pid)
                        project = _project(
                            _data(client.get(f"/api/v1/tft/projects/{pid}"), 200), row
                        )
                    else:
                        if any(p.get("name") == row["project_name"] for p in projects):
                            _fail("UNTRACKED_PROJECT_NAME")
                        payload = {
                            "name": row["project_name"],
                            "company_code": row["customer_code"],
                            "data_origin": ORIGINS[row["source_category"]],
                            "purpose": "INITIAL_PROPOSAL",
                            "client_name": None,
                            "customer_goal": None,
                            "review_departments": [],
                            "supply_region": None,
                            "customer_manufacturer_list": None,
                            "off_list_permission": "UNSTATED",
                        }
                        no_active_driver(config)
                        project = _post(
                            client,
                            "/api/v1/tft/projects",
                            entry,
                            "create",
                            ledger,
                            ledger_path,
                            headers,
                            lambda d: _project(d, row),
                            json=payload,
                        )
                        pid = project["id"]
                        entry["project_id"] = pid
                        save(ledger_path, ledger)
                    if project["id"] != pid:
                        _fail("PROJECT_UUID_MISMATCH")
                    if _list(client, f"/api/v1/tft/projects/{pid}/documents"):
                        _fail("PROJECT_NOT_EMPTY")
                    binary = _bytes(path, row)
                    no_active_driver(config)
                    doc = _post(
                        client,
                        f"/api/v1/tft/projects/{pid}/documents",
                        entry,
                        "upload",
                        ledger,
                        ledger_path,
                        headers,
                        lambda d: _document(d, row),
                        data={
                            "kind": "RFP_SPEC",
                            "title": row["title"],
                            "note": row["note"],
                            "data_origin": ORIGINS[row["source_category"]],
                        },
                        files={
                            "file": (
                                path.name,
                                binary,
                                "application/pdf"
                                if path.suffix.lower() == ".pdf"
                                else "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            )
                        },
                    )
                    status = "REGISTERED"
                entry.update(project_id=pid, document_id=doc["document_id"])
                save(ledger_path, ledger)
                results.append(
                    {
                        "id": row["id"],
                        "project_id": pid,
                        "document_id": doc["document_id"],
                        "sha256": doc["sha256"],
                        "bytes": doc["size"],
                        "status": status,
                    }
                )
        return {"target": target, "actor_id": actor, "items": results, "process_requests": 0}
