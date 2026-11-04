"""Explicit local real-Outlook mail mode. Off unless `mail-on` wrote state/auth/mail.json (0600).

Cloud Tasks, Cloud Storage and Cloud Scheduler have no local service, so one HTTP transport
stands in for them: tasks become local HTTP calls, objects become files, and a background loop
plays the scheduler. Microsoft sign-in and Graph stay real. Graph sendMail is refused unless
every recipient is on the allow list that `mail-on` wrote.
"""

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import secrets
import stat
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import UUID

import httpx

from scripts.local_business.auth import CS_ORIGIN, CS_SA, PLATFORM_ORIGIN, PLATFORM_SA

CONFIG = "mail.json"
BUCKET = "local-mail"
SYNC_QUEUE = "projects/team4-local/locations/local/queues/mail-sync"
SEND_QUEUE = "projects/team4-local/locations/local/queues/mail-send"
PREFIX = "/internal/v1/platform/mail"
MAILBOX_CALLBACK = "/api/v1/platform/auth/mailbox/callback"
DEFAULT_ALLOW = ["@outlook.kr"]  # demo accounts (manager-01, csdesk-01, csdesk-02)
log = logging.getLogger("local_business.mail")


def allowed(address: str, allow: list[str]) -> bool:
    """`a@b.com` matches only itself; `@b.com` matches any address at that exact domain."""
    # Checked as sent: no trimming, exactly one "@", and the domain compared whole.
    if address.count("@") != 1 or address != address.strip():
        return False
    address = address.lower()
    domain = "@" + address.rpartition("@")[2]
    return any(address == rule or domain == rule for rule in allow)


def private_file(path: Path) -> Path:
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)
    ):
        raise ValueError("LOCAL_MAIL_SECRET_FILE_PERMISSIONS")
    return path


def load(state: Path) -> dict | None:
    """Mail settings, or None when mail mode is off. Never logs or returns the reason text."""
    path = state / "auth" / CONFIG
    if not path.exists():
        return None
    value = json.loads(private_file(path).read_text(encoding="utf-8"))
    try:
        UUID(value["client_id"])
        valid = (
            value["client_secret"].strip()
            and len(base64.b64decode(value["encryption_key"], validate=True)) == 32
            and (value.get("sync") or value.get("send"))
            and (not value.get("send") or value.get("allow"))
        )
    except (KeyError, ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise ValueError("LOCAL_MAIL_CONFIG_INVALID")
    return value


def client_app(secret_file: str | None, env: dict) -> dict:
    """Microsoft app id/secret from a 0600 JSON file or LOCAL_MAIL_CLIENT_ID/_SECRET."""
    if secret_file:
        source = json.loads(
            private_file(Path(secret_file).expanduser()).read_text(encoding="utf-8")
        )
    else:
        source = {
            "client_id": env.get("LOCAL_MAIL_CLIENT_ID", ""),
            "client_secret": env.get("LOCAL_MAIL_CLIENT_SECRET", ""),
        }
    try:
        client_id = str(UUID(source.get("client_id", "")))
    except ValueError:
        raise ValueError("LOCAL_MAIL_CLIENT_REQUIRED") from None
    if not isinstance(source.get("client_secret"), str) or not source["client_secret"].strip():
        raise ValueError("LOCAL_MAIL_CLIENT_REQUIRED")
    return {"client_id": client_id, "client_secret": source["client_secret"]}


def write(
    state: Path, *, app: dict, sync: bool, send: bool, allow: list[str], interval: int
) -> dict:
    """Write state/auth/mail.json with 0600. Keeps the existing mailbox key so stored links still open."""
    if not (sync or send):
        raise ValueError("LOCAL_MAIL_MODE_REQUIRED")
    allow = [rule.strip().lower() for rule in allow if rule.strip()]
    if send and (not allow or any("@" not in rule for rule in allow)):
        raise ValueError("LOCAL_MAIL_ALLOW_LIST_INVALID")
    if not 60 <= interval <= 3600:
        raise ValueError("LOCAL_MAIL_INTERVAL_INVALID")
    path = state / "auth" / CONFIG
    key = None
    if path.exists():
        with contextlib.suppress(ValueError, KeyError):
            key = load(state)["encryption_key"]
    value = {
        **app,
        "encryption_key": key or base64.b64encode(secrets.token_bytes(32)).decode(),
        "sync": sync,
        "send": send,
        "allow": allow if send else [],
        "interval_seconds": interval,
    }
    temporary = path.with_name(f".{CONFIG}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(value, out)
    temporary.replace(path)
    return value


def recipients(body: bytes) -> list[str]:
    message = json.loads(body).get("message", {})
    return [
        r["emailAddress"]["address"]
        for key in ("toRecipients", "ccRecipients", "bccRecipients")
        for r in message.get(key) or []
    ]


class LocalMailTransport(httpx.AsyncBaseTransport):
    """Routes the mail workers' outbound calls. Microsoft calls go out unchanged except blocked sends."""

    def __init__(self, *, ports: dict, token, objects: Path, allow: list[str], real=None):
        self.ports = ports
        self.token = token  # (audience, service account) -> local ID token
        self.objects = objects
        self.allow = [rule.lower() for rule in allow]
        self.real = real or httpx.AsyncHTTPTransport()
        # Workers close their clients; this one stays open for local task calls.
        self.client = httpx.AsyncClient(transport=self.real, timeout=120, follow_redirects=False)
        self.names: set[str] = set()
        self.running: set[asyncio.Task] = set()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "cloudtasks.googleapis.com":
            return self.task(json.loads(await request.aread())["task"])
        if host == "storage.googleapis.com":
            return self.storage(request, await request.aread())
        if host == urlsplit(CS_ORIGIN).hostname:
            request.url = request.url.copy_with(
                scheme="http", host="127.0.0.1", port=self.ports["cs"]
            )
        elif host == "graph.microsoft.com" and request.method != "GET":
            # The workers only write to Graph through sendMail; any other write is refused.
            send = request.url.path == "/v1.0/me/sendMail"
            addresses = recipients(await request.aread()) if send else []
            if not addresses or not all(allowed(a, self.allow) for a in addresses):
                # Recorded by the send worker as a failed intent (GRAPH_FORBIDDEN); nothing leaves.
                log.warning("LOCAL_MAIL_SEND_BLOCKED recipients=%d", len(addresses))
                return httpx.Response(403, json={"error": {"code": "LOCAL_RECIPIENT_NOT_ALLOWED"}})
        elif host not in {"graph.microsoft.com", "login.microsoftonline.com", "127.0.0.1"}:
            raise httpx.ConnectError("LOCAL_MAIL_HOST_NOT_ALLOWED", request=request)
        return await self.real.handle_async_request(request)

    def task(self, task: dict) -> httpx.Response:
        if task["name"] in self.names:
            return httpx.Response(409, json={"error": {"status": "ALREADY_EXISTS"}})
        self.names.add(task["name"])
        job = asyncio.get_running_loop().create_task(self.run(task))
        self.running.add(job)
        job.add_done_callback(self.running.discard)
        return httpx.Response(200, json={"name": task["name"]})

    async def run(self, task: dict) -> None:
        when = task.get("scheduleTime")
        if when:
            due = datetime.fromisoformat(when.replace("Z", "+00:00"))
            await asyncio.sleep(max((due - datetime.now(UTC)).total_seconds(), 0))
        await self.call(task["httpRequest"])

    async def call(self, http: dict, tries: int = 3) -> int | None:
        """POST to the local platform as Cloud Tasks would, retrying a 503 a few times."""
        oidc = http["oidcToken"]
        if not http["url"].startswith(PLATFORM_ORIGIN + PREFIX + "/"):
            raise ValueError("LOCAL_MAIL_TASK_TARGET_INVALID")
        url = f"http://127.0.0.1:{self.ports['platform']}" + http["url"][len(PLATFORM_ORIGIN) :]
        body = base64.b64decode(http.get("body", ""))
        status = None
        for attempt in range(tries):
            token = self.token(oidc["audience"], oidc["serviceAccountEmail"])
            try:
                response = await self.client.post(
                    url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {token}",
                    },
                )
                status = response.status_code
            except httpx.HTTPError:
                status = None
            if status is not None and status != 503:
                return status
            if attempt + 1 < tries:
                await asyncio.sleep(10 * (attempt + 1))
        log.warning("LOCAL_MAIL_TASK_FAILED path=%s status=%s", urlsplit(url).path, status)
        return status

    def storage(self, request: httpx.Request, body: bytes) -> httpx.Response:
        if request.method == "POST":
            name = request.url.params.get("name", "")
        else:
            name = unquote(request.url.raw_path.decode().split("?", 1)[0].rsplit("/o/", 1)[-1])
        root = self.objects.resolve()
        path = (root / name).resolve()
        if not name.startswith("mail/") or not path.is_relative_to(root):
            return httpx.Response(400)
        if request.method == "POST":
            if path.exists():
                return httpx.Response(412)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(body)
        elif not path.exists():
            return httpx.Response(404)
        data = path.read_bytes()
        if request.url.params.get("alt") == "media":
            return httpx.Response(200, content=data)
        md5 = base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode()
        return httpx.Response(200, json={"name": name, "size": str(len(data)), "md5Hash": md5})


def configure_platform(
    app, mail: dict, identity, ports: dict, objects: Path, web_origin: str
) -> None:
    """Real mailbox connect plus opt-in sync/send on the local platform app."""
    from pydantic import SecretStr

    from backend.api.platform.auth.mailbox_http import configure_mailbox_auth
    from backend.api.platform.auth.mailbox_settings import MailboxAuthSettings
    from backend.api.platform.auth.mailbox_tokens import MailboxTokenProvider
    from backend.api.platform.auth.microsoft import PersonalMicrosoftOAuth
    from backend.api.platform.mail.business import CsCaller
    from backend.api.platform.mail.send_worker import MailSendSettings, configure_mail_send
    from backend.api.platform.mail.worker import MailSyncSettings, configure_mail_sync

    redirect = web_origin + MAILBOX_CALLBACK
    configure_mailbox_auth(
        app,
        MailboxAuthSettings(
            _env_file=None,
            enabled=True,
            encryption_key=SecretStr(mail["encryption_key"]),
            key_version="local-v1",
            redirect_uri=redirect,
        ),
    )
    # Sign-in stays the synthetic local login; only the mailbox consent uses the real app.
    app.state.mailbox_oauth = PersonalMicrosoftOAuth(
        UUID(mail["client_id"]), SecretStr(mail["client_secret"]), redirect
    )
    app.state.mailbox_tokens = MailboxTokenProvider(
        settings=app.state.settings,
        adapter=app.state.mailbox_oauth,
        cipher=app.state.mailbox_cipher,
    )
    transport = LocalMailTransport(
        ports=ports, token=identity.token, objects=objects, allow=mail.get("allow", [])
    )

    async def no_google_token() -> str:
        return "local-fixture"

    fixed = {
        "tasks_token": no_google_token,
        "storage_token": no_google_token,
        "transport": transport,
    }
    invoker = identity.caller(PLATFORM_ORIGIN, PLATFORM_SA)
    interval = int(mail.get("interval_seconds", 60))
    sync_on = bool(mail.get("sync"))
    send_on = bool(mail.get("send"))
    if sync_on:
        configure_mail_sync(
            app,
            MailSyncSettings(
                _env_file=None,
                enabled=True,
                queue=SYNC_QUEUE,
                service_url=PLATFORM_ORIGIN,
                invoker_email=PLATFORM_SA,
                interval_seconds=interval,
                object_bucket=BUCKET,
            ),
            caller=invoker,
            **fixed,
        )
    if send_on:
        configure_mail_send(
            app,
            MailSendSettings(
                _env_file=None,
                enabled=True,
                queue=SEND_QUEUE,
                service_url=PLATFORM_ORIGIN,
                invoker_email=PLATFORM_SA,
                cs_caller_email=CS_SA,
                object_bucket=BUCKET,
            ),
            invoker=invoker,
            cs_caller=identity.caller(PLATFORM_ORIGIN, CS_SA),
            **fixed,
        )
    # Mail intake and reply notices reach the local C/S instead of its unresolvable public name.
    app.state.cs_caller = CsCaller(CS_ORIGIN, app.state.business_token_provider, transport)
    app.state.local_mail_transport = transport
    jobs = [path for path, on in (("/dispatch", sync_on), ("/send-sweep", send_on)) if on]

    async def scheduler() -> None:
        # ponytail: one interval for both jobs; split if receive and send need different cadences.
        while True:
            await asyncio.sleep(interval)
            for path in jobs:
                try:
                    await transport.call(
                        {
                            "url": PLATFORM_ORIGIN + PREFIX + path,
                            "oidcToken": {
                                "audience": PLATFORM_ORIGIN,
                                "serviceAccountEmail": PLATFORM_SA,
                            },
                        },
                        tries=1,
                    )
                except Exception:  # keep the loop alive; the reason may hold secrets
                    log.warning("LOCAL_MAIL_SCHEDULER_FAILED path=%s", path)

    inner = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(application):
        async with inner(application) as value:
            loop = asyncio.get_running_loop().create_task(scheduler())
            try:
                yield value
            finally:
                loop.cancel()
                await transport.client.aclose()

    app.router.lifespan_context = lifespan
