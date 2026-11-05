"""Local demo mailbox: no Microsoft app, nothing leaves the machine.

`mail-on --demo` swaps the two outside parties of the real mail path for local stand-ins:
the mailbox consent (a synthetic page like the local login) and Microsoft Graph (a JSON store
under the namespace). Everything between them is the product code as it runs with a real
mailbox: sync, triage, reply linking, intake, send intents and sent-copy checks.

Customer mail is put in with `mail-demo-receive`. A reply can point at the last answer the
agent sent to that address (In-Reply-To + same conversation), so the reply links by header the
way real mail does. These are synthetic messages for local demos only.
"""

import base64
import contextlib
import fcntl
import json
import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from hashlib import sha256
from pathlib import Path
from secrets import token_urlsafe
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from fastapi import HTTPException
from pydantic import SecretStr

from backend.api.platform.auth.callback import (
    MicrosoftSubject,
    OAuthPurpose,
    PendingAuthorization,
    reject_callback,
)
from backend.api.platform.auth.microsoft import (
    PERSONAL_TENANT,
    MailboxAuthorization,
    MailboxTokenUpdate,
)
from scripts.local_business.auth import local_origin, require_local_fixture

STORE = ".mail-demo.json"
MARKER = "mail-demo"  # state/auth/mail-demo: the web server shows the synthetic consent page
CODE = "local-demo-mailbox"
TOKEN = "local-demo-graph-token"
MAILBOX = "cs-desk@example.invalid"  # the agent's demo mailbox address
DOMAIN = "team4.demo.invalid"
GRAPH = "https://graph.microsoft.com/v1.0"
DELTA = re.compile(r"/v1\.0/me/mailFolders/(inbox|sentitems)/messages/delta")
MESSAGE = re.compile(r"/v1\.0/me/messages/([A-Za-z0-9_-]{1,128})(/\$value|/attachments)?")
INTENT = re.compile(r"ep/value eq '([0-9a-fA-F-]{36})'")


def store_path(state: Path) -> Path:
    return state / "files" / STORE


class DemoStore:
    """Messages of the demo mailbox in one JSON file, written under an exclusive file lock."""

    def __init__(self, path: Path):
        self.path = path

    @contextlib.contextmanager
    def open(self) -> Iterator[dict[str, Any]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_name(self.path.name + ".lock"), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = (
                json.loads(self.path.read_text(encoding="utf-8"))
                if self.path.exists()
                else {"next_seq": 0, "messages": []}
            )
            yield data
            temporary = self.path.with_name(self.path.name + ".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, self.path)

    def add(self, data: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
        message = {**message, "seq": data["next_seq"], "id": "demo-" + uuid4().hex}
        data["next_seq"] += 1
        data["messages"].append(message)
        return message


def mime(
    *,
    sender: str,
    to: list[str],
    subject: str,
    body: str,
    message_id: str,
    at: datetime,
    in_reply_to: str | None = None,
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(to)
    message["Subject"] = subject
    message["Date"] = format_datetime(at)
    message["Message-ID"] = message_id
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content(body)
    return message.as_bytes()


def iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def receive(
    store: DemoStore,
    *,
    sender: str,
    subject: str | None,
    body: str,
    reply: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Put one customer mail in the demo inbox.

    Args:
        store: The namespace's demo store.
        sender: Customer address (use a reserved test domain such as example.invalid).
        subject: Subject. With `reply` and none given, "Re: " + the answered mail's subject.
        body: Plain text body.
        reply: Answer the agent's latest sent mail to this address (else the latest sent mail).
        now: Received time (default now).

    Returns:
        dict: The stored message without its MIME.
    """
    sender = sender.strip()
    if sender.count("@") != 1:
        raise ValueError("LOCAL_MAIL_DEMO_SENDER_INVALID")
    at = now or datetime.now(UTC)
    with store.open() as data:
        parent = None
        if reply:
            sent = [m for m in data["messages"] if m["folder"] == "sentitems"]
            mine = [m for m in sent if sender.lower() in (a.lower() for a in m["to"])]
            parent = (mine or sent or [None])[-1]
            if parent is None:
                raise ValueError("LOCAL_MAIL_DEMO_NOTHING_TO_REPLY")
        if not subject:
            answered = (parent or {}).get("subject") or ""
            subject = (
                answered
                if re.match(r"re\s*:", answered, re.IGNORECASE)
                else ("Re: " + answered if answered else "(no subject)")
            )
        message_id = make_msgid(domain="customer.example.invalid")
        stored = store.add(
            data,
            {
                "folder": "inbox",
                "internetMessageId": message_id,
                "conversationId": parent["conversationId"] if parent else "conv-" + uuid4().hex,
                "subject": subject,
                "from": sender,
                "to": [MAILBOX],
                "receivedDateTime": iso(at),
                "sentDateTime": iso(at - timedelta(seconds=5)),
                "mime": base64.b64encode(
                    mime(
                        sender=sender,
                        to=[MAILBOX],
                        subject=subject,
                        body=body,
                        message_id=message_id,
                        at=at,
                        in_reply_to=parent["internetMessageId"] if parent else None,
                    )
                ).decode(),
            },
        )
    return {k: v for k, v in stored.items() if k != "mime"}


def meta(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": message["id"],
        "internetMessageId": message["internetMessageId"],
        "conversationId": message["conversationId"],
        "subject": message["subject"],
        "from": {"emailAddress": {"address": message["from"]}},
        "receivedDateTime": message["receivedDateTime"],
        "sentDateTime": message["sentDateTime"],
        "hasAttachments": False,
    }


def addresses(items: Any) -> list[str]:
    return [
        r["emailAddress"]["address"]
        for r in items or []
        if isinstance(r, dict) and isinstance(r.get("emailAddress"), dict)
    ]


class DemoGraph(httpx.AsyncBaseTransport):
    """Answers the Graph calls the mail workers make; everything else goes to `fallback`."""

    def __init__(self, store: DemoStore, fallback: httpx.AsyncBaseTransport | None = None):
        self.store = store
        self.fallback = fallback or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(403, json={"error": "local_demo_mailbox"})
        if request.url.host != "graph.microsoft.com":
            return await self.fallback.handle_async_request(request)
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})
        return self.graph(request, await request.aread())

    def graph(self, request: httpx.Request, body: bytes) -> httpx.Response:
        path, query = request.url.path, parse_qs(urlsplit(str(request.url)).query)
        if request.method == "POST" and path == "/v1.0/me/sendMail":
            return self.send(json.loads(body))
        if request.method != "GET":
            return httpx.Response(405, json={"error": {"code": "MethodNotAllowed"}})
        with self.store.open() as data:
            messages = list(data["messages"])
            next_seq = data["next_seq"]
        if found := DELTA.fullmatch(path):
            folder = found.group(1)
            start = int(query.get("$deltatoken", ["0"])[0])
            items = [meta(m) for m in messages if m["folder"] == folder and m["seq"] >= start]
            link = f"{GRAPH}/me/mailFolders/{folder}/messages/delta?" + urlencode(
                {"$deltatoken": next_seq}
            )
            return httpx.Response(200, json={"value": items, "@odata.deltaLink": link})
        if path == "/v1.0/me/mailFolders/sentitems/messages":
            wanted = INTENT.search(query.get("$filter", [""])[0])
            value = [
                {
                    "id": m["id"],
                    "internetMessageId": m["internetMessageId"],
                    "sentDateTime": m["sentDateTime"],
                }
                for m in messages
                if wanted and m["folder"] == "sentitems" and m.get("intent") == wanted.group(1)
            ]
            return httpx.Response(200, json={"value": value[:5]})
        if found := MESSAGE.fullmatch(path):
            message = next((m for m in messages if m["id"] == found.group(1)), None)
            if message is None:
                return httpx.Response(404, json={"error": {"code": "ErrorItemNotFound"}})
            if found.group(2) == "/$value":
                return httpx.Response(
                    200,
                    content=base64.b64decode(message["mime"]),
                    headers={"Content-Type": "message/rfc822"},
                )
            if found.group(2) == "/attachments":
                return httpx.Response(200, json={"value": []})
            return httpx.Response(200, json=meta(message))
        return httpx.Response(404, json={"error": {"code": "ErrorInvalidRequest"}})

    def send(self, payload: dict[str, Any]) -> httpx.Response:
        message = payload.get("message") if isinstance(payload, dict) else None
        if not isinstance(message, dict):
            return httpx.Response(400, json={"error": {"code": "ErrorInvalidRequest"}})
        to = addresses(message.get("toRecipients")) + addresses(message.get("ccRecipients"))
        subject = message.get("subject") or ""
        content = (message.get("body") or {}).get("content") or ""
        intent = next(
            (
                p.get("value")
                for p in message.get("singleValueExtendedProperties") or []
                if isinstance(p, dict)
            ),
            None,
        )
        at = datetime.now(UTC)
        message_id = make_msgid(domain=DOMAIN)
        with self.store.open() as data:
            # Outlook threads a reply with the customer's conversation; keep that for linking.
            plain = re.sub(r"^(re|fw|fwd)\s*:\s*", "", subject, flags=re.IGNORECASE).strip()
            earlier = [
                m
                for m in data["messages"]
                if m["folder"] == "inbox"
                and m["from"].lower() in (a.lower() for a in to)
                and re.sub(
                    r"^(re|fw|fwd)\s*:\s*", "", m["subject"] or "", flags=re.IGNORECASE
                ).strip()
                == plain
            ]
            self.store.add(
                data,
                {
                    "folder": "sentitems",
                    "internetMessageId": message_id,
                    "conversationId": earlier[-1]["conversationId"]
                    if earlier
                    else "conv-" + uuid4().hex,
                    "subject": subject,
                    "from": MAILBOX,
                    "to": to,
                    "receivedDateTime": iso(at),
                    "sentDateTime": iso(at),
                    "intent": intent,
                    "mime": base64.b64encode(
                        mime(
                            sender=MAILBOX,
                            to=to,
                            subject=subject,
                            body=content,
                            message_id=message_id,
                            at=at,
                        )
                    ).decode(),
                },
            )
        return httpx.Response(202)


class LocalDemoMailbox:
    """Mailbox consent without Microsoft: the existing routes still bind state and session."""

    def __init__(self, web_origin: str):
        require_local_fixture()
        self.web_origin = local_origin(web_origin)

    def begin(self, purpose, *, browser_binding, now, owner_user_id=None):
        require_local_fixture()
        if purpose is not OAuthPurpose.MAILBOX or owner_user_id is None:
            raise HTTPException(403)
        state = token_urlsafe(32)
        return self.web_origin + "/local/mailbox-authorize?" + urlencode({"state": state}), (
            PendingAuthorization(
                purpose=purpose,
                state=state,
                browser_binding_hash=sha256(browser_binding.encode()).hexdigest(),
                expires_at=now + timedelta(minutes=5),
                provider_flow={"local_demo_mailbox": True},
                owner_user_id=owner_user_id,
            )
        )

    def redeem_mailbox(self, pending, parameters, *, browser_binding, session_user_id, now):
        require_local_fixture()
        callback = dict(parameters)
        if (
            pending.purpose is not OAuthPurpose.MAILBOX
            or pending.provider_flow != {"local_demo_mailbox": True}
            or pending.owner_user_id != session_user_id
            or callback.get("code") != CODE
        ):
            raise reject_callback()
        subject = MicrosoftSubject(
            PERSONAL_TENANT, uuid5(NAMESPACE_URL, f"local-demo-mailbox:{session_user_id}")
        )
        return MailboxAuthorization(
            owner_user_id=session_user_id, subject=subject, serialized_cache=demo_cache(subject)
        )

    def acquire_mailbox_token(self, *, subject, serialized_cache, force_refresh=False):
        require_local_fixture()
        try:
            accounts = json.loads(serialized_cache.get_secret_value())["Account"].values()
            [account] = accounts
        except (ValueError, KeyError, TypeError, AttributeError):
            raise reject_callback() from None
        if subject.tid != PERSONAL_TENANT or account.get("local_account_id") != str(subject.oid):
            raise reject_callback()
        return MailboxTokenUpdate(
            access_token=SecretStr(TOKEN), serialized_cache=serialized_cache, cache_changed=False
        )


def demo_cache(subject: MicrosoftSubject) -> SecretStr:
    """A token cache in the MSAL serialized format with one account and no tokens.

    The vault checks that the cache holds exactly this mailbox's account; the demo Graph
    accepts the fixed demo token instead of anything from the cache.
    """
    home = f"{subject.oid}.{subject.tid}"
    account = {
        "home_account_id": home,
        "environment": "login.microsoftonline.com",
        "realm": "consumers",
        "local_account_id": str(subject.oid),
        "username": MAILBOX,
        "authority_type": "MSSTS",
    }
    return SecretStr(
        json.dumps(
            {
                "AccessToken": {},
                "RefreshToken": {},
                "IdToken": {},
                "Account": {f"{home}-login.microsoftonline.com-consumers": account},
                "AppMetadata": {},
            }
        )
    )
