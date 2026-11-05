"""Synthetic opaque bytes and fake HTTP only: no Docker, live DB, model or credentials."""

import hashlib
import json
import ssl
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from scripts.local_business import register_inputs as mod


class Server:
    def __init__(self, state):
        self.state = state
        self.projects = {}
        self.docs = {}
        self.requests = []
        self.business = []
        self.fault = None
        self.roles = ["sales"]
        self.csrf = "fake-csrf"
        self.redirect = None
        self.after_create = None
        self.origin = "https://localhost:39443"

    def data(self, value, status=200, headers=None):
        return httpx.Response(status, json={"data": value}, headers=headers)

    def handle(self, request):
        self.requests.append(request)
        path, method = request.url.path, request.method
        assert request.url.host == "localhost" and request.url.port == 39443
        if path == "/api/v1/platform/auth/login":
            return httpx.Response(
                302,
                headers={
                    "location": self.redirect or self.origin + "/local/authorize?state=" + "a" * 43,
                    "set-cookie": "binding=fake-binding; Path=/; Secure; HttpOnly",
                },
            )
        if path == "/local/authorize":
            assert "binding=fake-binding" in request.headers["cookie"]
            return httpx.Response(
                200,
                text='<form method="post" action="/api/v1/platform/auth/callback">'
                '<input name="state" value="' + "a" * 43 + '">'
                '<button name="code" value="local-3">Sales</button></form>',
            )
        if path == "/api/v1/platform/auth/callback":
            assert method == "POST" and request.headers["origin"] == self.origin
            assert "binding=fake-binding" in request.headers["cookie"]
            assert parse_qs(request.content.decode()) == {"state": ["a" * 43], "code": ["local-3"]}
            return httpx.Response(
                303,
                headers={
                    "location": self.origin + "/",
                    "set-cookie": "session=fake-session; Path=/; Secure; HttpOnly",
                },
            )
        assert "session=fake-session" in request.headers["cookie"]
        if path == "/api/v1/platform/me":
            return self.data(
                {"id": "00000000-0000-4000-8000-000000000003", "roles": self.roles},
                headers={"X-CSRF-Token": self.csrf},
            )
        if method == "GET":
            if path == "/api/v1/tft/projects":
                return self.data({"items": list(self.projects.values()), "next_cursor": None})
            pid = path.split("/")[5]
            if path.endswith("/documents"):
                return self.data({"items": self.docs.get(pid, []), "next_cursor": None})
            return self.data(self.projects[pid])
        assert method == "POST"
        assert path == "/api/v1/tft/projects" or path.endswith("/documents")
        assert request.headers["origin"] == self.origin
        assert request.headers["x-csrf-token"] == self.csrf
        ledger = json.loads((self.state / "private/register-inputs.json").read_text())
        attempts = [
            item[op]
            for item in ledger["items"].values()
            for op in ("create", "upload")
            if op in item
        ]
        assert any(
            a["key"] == request.headers["idempotency-key"] and a["status"] == "UNKNOWN"
            for a in attempts
        )
        self.business.append(request)
        if self.fault == "timeout":
            raise httpx.ReadTimeout("synthetic unknown", request=request)
        if self.fault == "malformed":
            return httpx.Response(201, text="not JSON")
        if self.fault == "redirect":
            return httpx.Response(307, headers={"location": "https://external.invalid/write"})
        if self.fault == "reject":
            return httpx.Response(422, json={"error": {"code": "synthetic"}})
        if path == "/api/v1/tft/projects":
            value = json.loads(request.content)
            value["id"] = str(uuid4())
            self.projects[value["id"]] = value
            if self.after_create:
                self.after_create()
            return self.data(value, 201)
        pid = path.split("/")[5]
        # Only fake multipart bytes; never parse a real document in a test or registrar.
        boundary = request.headers["content-type"].split("boundary=")[1]
        parts = {}
        for part in request.content.split(("--" + boundary).encode())[1:-1]:
            headers, value = part.lstrip(b"\r\n").split(b"\r\n\r\n", 1)
            name = headers.decode().split('name="')[1].split('"')[0]
            parts[name] = value[:-2]
        content = parts["file"]
        doc = {
            "document_id": str(uuid4()),
            "document_version_id": str(uuid4()),
            "title": parts["title"].decode(),
            "kind": parts["kind"].decode(),
            "data_origin": parts["data_origin"].decode(),
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        self.docs.setdefault(pid, []).append(doc)
        return self.data(doc, 201)


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    (state / "auth").mkdir(parents=True)
    root = tmp_path / "download"
    root.mkdir()
    binary = b"opaque synthetic bytes, deliberately not a parsed DOCX"
    (root / "sample.docx").write_bytes(binary)
    row = {
        "id": "D10",
        "relative_path": "sample.docx",
        "sha256": hashlib.sha256(binary).hexdigest(),
        "bytes": len(binary),
        "source_category": "evaluation",
        "project_name": "Training D10",
        "customer_code": "DEMO-D10",
        "title": "Evaluation registration only",
    }
    # Ephemeral public self-signed CA for SSLContext setup, no real keys or network.
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    (state / "auth/tls-cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    server = Server(state)
    config = {
        "namespace": "team4-kan159-fake",
        "owner": "fake-owner",
        "source_sha": "a" * 40,
        "web_origin": server.origin,
    }
    monkeypatch.setattr(mod, "owned", lambda _: config)
    monkeypatch.setattr(mod, "no_active_driver", lambda _: None)
    real_client = httpx.Client

    def client(**kwargs):
        assert isinstance(kwargs["verify"], ssl.SSLContext)
        assert kwargs["verify"].check_hostname and kwargs["verify"].verify_mode == ssl.CERT_REQUIRED
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        kwargs["transport"] = httpx.MockTransport(server.handle)
        return real_client(**kwargs)

    monkeypatch.setattr(mod.httpx, "Client", client)
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    return state, root, row, server, config


def run(env, rows=None):
    state, root, row, _, _ = env
    return mod.register_inputs(state, [row] if rows is None else rows, root)


def test_register_and_rerun_verify_hash_without_repeating_posts(env):
    state, _, _, server, _ = env
    result = run(env)
    assert result["items"][0]["status"] == "REGISTERED"
    assert len(server.business) == 2 and result["process_requests"] == 0
    assert run(env)["items"][0]["status"] == "SKIPPED_VERIFIED"
    assert len(server.business) == 2
    ledger = state / "private/register-inputs.json"
    assert ledger.stat().st_mode & 0o777 == 0o600
    assert (state / "private").stat().st_mode & 0o777 == 0o700
    assert "fake-session" not in ledger.read_text() and "fake-csrf" not in ledger.read_text()
    assert not (state / "private/register-inputs.lock").exists()


@pytest.mark.parametrize(
    "category,origin",
    [("training", "demo"), ("evaluation", "demo"), ("generated_demo", "synthetic")],
)
def test_fixed_origin_and_kind_without_content_parsing(env, category, origin):
    _, _, row, server, _ = env
    row["source_category"] = category
    run(env)
    assert next(iter(server.projects.values()))["data_origin"] == origin
    assert next(iter(server.docs.values()))[0]["kind"] == "RFP_SPEC"
    assert all("process" not in r.url.path and "clauses" not in r.url.path for r in server.requests)


@pytest.mark.parametrize("fault", ["timeout", "malformed", "redirect", "reject"])
def test_post_ambiguity_or_rejection_blocks_rerun_before_network(env, fault):
    _, _, _, server, _ = env
    server.fault = fault
    with pytest.raises((ValueError, httpx.ReadTimeout)):
        run(env)
    assert len(server.business) == 1
    count = len(server.requests)
    server.fault = None
    with pytest.raises(ValueError, match="UNRESOLVED_POST"):
        run(env)
    assert len(server.requests) == count


def test_confirmed_create_can_resume_only_upload_after_local_interruption(env):
    _, root, _, server, _ = env
    path = root / "sample.docx"
    original = path.read_bytes()
    server.after_create = lambda: path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="SIZE_MISMATCH"):
        run(env)
    assert len(server.business) == 1
    path.write_bytes(original)
    server.after_create = None
    run(env)
    assert len(server.projects) == 1 and len(server.business) == 2
    assert server.business[-1].url.path.endswith("/documents")


def test_confirmed_create_response_survives_missing_project_shortcut(env):
    state, root, _, server, _ = env
    original = (root / "sample.docx").read_bytes()
    server.after_create = lambda: (root / "sample.docx").write_bytes(b"changed")
    with pytest.raises(ValueError):
        run(env)
    ledger_path = state / "private/register-inputs.json"
    ledger = json.loads(ledger_path.read_text())
    del ledger["items"]["D10"]["project_id"]
    mod.save(ledger_path, ledger)
    (root / "sample.docx").write_bytes(original)
    run(env)
    assert len(server.business) == 2


def test_successful_ledger_cannot_silently_recreate_deleted_document(env):
    _, _, _, server, _ = env
    run(env)
    server.docs.clear()
    with pytest.raises(ValueError, match="SAVED_DOCUMENT_MISSING"):
        run(env)
    assert len(server.business) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"relative_path": "../escape.docx"},
        {"relative_path": "/tmp/escape.docx"},
        {"sha256": "0" * 64},
        {"bytes": True},
        {"source_category": "real"},
        {"title": "x" * 301},
        {"customer_code": "x" * 41},
        {"extra": "metadata"},
    ],
)
def test_bad_inputs_rejected_before_http(env, change):
    _, _, row, server, _ = env
    row.update(change)
    with pytest.raises(ValueError):
        run(env)
    assert not server.requests


def test_all_rows_preflight_before_first_write(env):
    _, _, row, server, _ = env
    with pytest.raises(ValueError):
        run(env, [row, {**row, "id": "bad", "sha256": "0" * 64}])
    assert not server.requests


def test_duplicate_rows_and_symlink_escape_refused(env):
    _, root, row, server, _ = env
    with pytest.raises(ValueError, match="DUPLICATE_INPUT"):
        run(env, [row, row])
    (root / "link.docx").symlink_to(root / "sample.docx")
    row["relative_path"] = "link.docx"
    with pytest.raises(ValueError, match="INPUT_PATH"):
        run(env)
    assert not server.requests


@pytest.mark.parametrize(
    "redirect",
    [
        "https://external.invalid/a",
        "https://localhost:1234/local/authorize?state=x",
        "//external.invalid/a",
    ],
)
def test_external_and_cross_port_redirect_never_followed(env, redirect):
    _, _, _, server, _ = env
    server.redirect = redirect
    with pytest.raises(ValueError, match="REDIRECT_FORBIDDEN"):
        run(env)
    assert len(server.requests) == 1 and not server.business


@pytest.mark.parametrize("roles,csrf", [(["cs_agent"], "token"), (["sales"], "")])
def test_role_and_csrf_checks_not_relaxed(env, roles, csrf):
    _, _, _, server, _ = env
    server.roles, server.csrf = roles, csrf
    with pytest.raises(ValueError, match="SALES_SESSION_REQUIRED"):
        run(env)
    assert not server.business


def test_module_lock_blocks_parallel_caller(env):
    state, _, _, server, _ = env
    with mod._ledger_lock(state):
        with pytest.raises(ValueError, match="LOCKED"):
            run(env)
    assert not server.requests


def test_active_driver_and_unowned_state_fail_before_http(env, monkeypatch):
    _, _, _, server, _ = env

    def reject(_):
        raise ValueError("SYNTHETIC_OWNERSHIP_FAILURE")

    monkeypatch.setattr(mod, "owned", reject)
    with pytest.raises(ValueError, match="OWNERSHIP"):
        run(env)
    assert not server.requests


def test_invalid_origin_and_tls_cert_refused(env):
    state, _, _, server, config = env
    config["web_origin"] = "https://outside.invalid:39443"
    with pytest.raises(ValueError, match="ORIGIN"):
        run(env)
    config["web_origin"] = server.origin
    (state / "auth/tls-cert.pem").write_text("invalid public certificate")
    with pytest.raises(ssl.SSLError):
        run(env)
    assert not server.requests


def test_server_hash_or_project_identity_drift_stops_without_writes(env):
    _, _, _, server, _ = env
    run(env)
    next(iter(server.docs.values()))[0]["size"] += 1
    with pytest.raises(ValueError, match="DOCUMENT_MISMATCH"):
        run(env)
    assert len(server.business) == 2


@pytest.mark.parametrize("guard", ["owned", "no_active_driver"])
def test_runtime_guard_failure_prevents_http(env, monkeypatch, guard):
    def reject(_):
        raise ValueError("GUARD_FAILED")

    monkeypatch.setattr(mod, guard, reject)
    with pytest.raises(ValueError, match="GUARD_FAILED"):
        run(env)
    assert not env[3].requests


def test_paginated_projects_are_verified_without_duplicate_posts(env, monkeypatch):
    run(env)
    server = env[3]
    original = server.handle
    pages = []

    def handle(request):
        if request.method == "GET" and request.url.path == "/api/v1/tft/projects":
            cursor = request.url.params.get("cursor")
            pages.append(cursor)
            if cursor is None:
                return server.data({"items": [], "next_cursor": "page2"})
            assert cursor == "page2"
        return original(request)

    monkeypatch.setattr(server, "handle", handle)
    assert run(env)["items"][0]["status"] == "SKIPPED_VERIFIED"
    assert pages == [None, "page2"]
    assert len(server.business) == 2


def test_upload_response_mismatch_is_unknown_and_blocks_rerun(env, monkeypatch):
    server = env[3]
    original = server.handle

    def handle(request):
        response = original(request)
        if request.method == "POST" and request.url.path.endswith("/documents"):
            body = response.json()
            body["data"]["sha256"] = "0" * 64
            return httpx.Response(201, json=body)
        return response

    monkeypatch.setattr(server, "handle", handle)
    with pytest.raises(ValueError):
        run(env)
    count = len(server.requests)
    with pytest.raises(ValueError):
        run(env)
    assert len(server.requests) == count
    assert len(server.business) == 2


@pytest.mark.parametrize("name,value", [("ENVIRONMENT", " production "), ("K_SERVICE", "")])
def test_production_rejected_before_http(env, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="ENVIRONMENT"):
        run(env)
    assert not env[3].requests
