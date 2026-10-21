#!/usr/bin/env python3
"""Read-only preflight plus an opt-in interactive Microsoft mail-read probe."""

import argparse
import json
import os
import re
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from uuid import UUID

from dotenv import dotenv_values


class ConfigurationError(ValueError):
    """Only fixed messages with setting names, never user-supplied values."""


def configuration(path: Path) -> dict[str, str]:
    values = dotenv_values(path) if path.exists() else {}
    return {key: os.environ.get(key) or value or "" for key, value in values.items()} | {
        key: os.environ[key]
        for key in (
            "MS_TENANT_ID",
            "MS_CLIENT_ID",
            "MS_CLIENT_SECRET",
            "MS_REDIRECT_URI",
            "GEMINI_API_KEY",
        )
        if os.environ.get(key)
    }


def microsoft(values: dict[str, str], login: bool) -> int:
    try:
        tenant = str(UUID(values.get("MS_TENANT_ID", "")))
        client_id = str(UUID(values.get("MS_CLIENT_ID", "")))
    except ValueError:
        raise ConfigurationError(
            "Set valid MS_TENANT_ID and MS_CLIENT_ID in .env.integrations."
        ) from None
    authority = f"https://login.microsoftonline.com/{tenant}"
    with urlopen(f"{authority}/v2.0/.well-known/openid-configuration", timeout=15) as response:
        discovery = json.load(response)
    if discovery.get("issuer") != f"{authority}/v2.0":
        raise ValueError("Unexpected tenant issuer")
    print("PASS: tenant OpenID discovery. App consent and mailbox access are not yet verified.")
    if not login:
        return 0
    secret = values.get("MS_CLIENT_SECRET")
    redirect = values.get("MS_REDIRECT_URI", "http://localhost:8765/callback")
    parsed = urlparse(redirect)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "localhost"
        or parsed.path != "/callback"
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError(
            "Use the registered local Web redirect http://localhost:8765/callback"
        )
    if not secret:
        raise ConfigurationError(
            "Set MS_CLIENT_SECRET only in .env.integrations before --login; see docs/integration-checks.md"
        )
    import msal

    app = msal.ConfidentialClientApplication(
        client_id, authority=authority, client_credential=secret
    )
    flow = app.initiate_auth_code_flow(scopes=["Mail.Read", "Mail.Send"], redirect_uri=redirect)
    callback = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Query strings contain one-time authorization codes.

        def do_GET(self):
            if urlparse(self.path).path != "/callback":
                self.send_error(404)
                return
            callback.update(
                {key: value[0] for key, value in parse_qs(urlparse(self.path).query).items()}
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write("로그인 결과를 터미널에서 확인하세요. 이 창은 닫아도 됩니다.".encode())

    with HTTPServer(("127.0.0.1", parsed.port or 8765), Callback) as server:
        server.timeout = 1
        print("Opening Microsoft login. Codes and tokens will not be printed or saved.", flush=True)
        webbrowser.open(flow["auth_uri"])
        print(
            "If no browser opens, run this command in a desktop terminal with browser access.",
            flush=True,
        )
        deadline = time.monotonic() + 180
        while not callback and time.monotonic() < deadline:
            server.handle_request()
    if not callback:
        raise ValueError("Login timed out; no token was stored")
    result = app.acquire_token_by_auth_code_flow(flow, callback)
    if "access_token" not in result:
        code = re.sub(r"[^A-Za-z0-9_]", "", str(result.get("error", "oauth_failed")))[:80]
        print(f"FAIL: Microsoft OAuth {code}. Check consent and redirect settings.")
        return 1
    if result.get("id_token_claims", {}).get("tid") != tenant:
        raise ValueError("Authenticated tenant did not match configured tenant")
    scopes = set(str(result.get("scope", "")).split())
    print("PASS: Microsoft login. Mail.Send consent:", "Mail.Send" in scopes)
    request = Request(
        "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$top=1&$select=id",
        headers={"Authorization": f"Bearer {result['access_token']}"},
    )
    with urlopen(request, timeout=20) as response:
        mailbox = json.load(response)
    print(
        f"PASS: Graph Mail.Read; returned {len(mailbox.get('value', []))} message ID(s). Content and IDs were not logged."
    )
    print("Mail sending was not executed. Tokens remain only in process memory.")
    return 0


def gemini(values: dict[str, str], embed_model: str | None) -> int:
    if not values.get("GEMINI_API_KEY"):
        raise ValueError(
            "Set GEMINI_API_KEY locally or use --env-file .env. Never paste the key into chat."
        )
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=values["GEMINI_API_KEY"],
        http_options=types.HttpOptions(
            timeout=20000, retry_options=types.HttpRetryOptions(attempts=1)
        ),
    )
    if embed_model:
        response = client.models.embed_content(
            model=embed_model, contents="Synthetic setup connection check."
        )
        embeddings = response.embeddings or []
        if len(embeddings) != 1 or not embeddings[0].values:
            raise ValueError("No embedding returned")
        print(
            f"PASS: {embed_model}; observed default dimensions={len(embeddings[0].values)}. Vectors were not printed or stored; production dimensions are not selected."
        )
    else:
        for model in client.models.list():
            print(model.name, ",".join(model.supported_actions or []))
        print("Listed models only; generation quality and final model selection are not evaluated.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=("microsoft", "gemini"))
    parser.add_argument("--env-file", type=Path, default=Path(".env.integrations"))
    parser.add_argument(
        "--login", action="store_true", help="Microsoft interactive login and read-only inbox check"
    )
    parser.add_argument(
        "--embed-model", help="Check one synthetic embedding instead of listing models"
    )
    args = parser.parse_args()
    try:
        values = configuration(args.env_file)
        return (
            microsoft(values, args.login)
            if args.service == "microsoft"
            else gemini(values, args.embed_model)
        )
    except HTTPError as error:
        print(
            f"FAIL: HTTP {error.code}; check tenant, consent, key restrictions or mailbox licensing."
        )
    except ConfigurationError as error:
        print(f"FAIL: {error}")
    except Exception as error:
        # Do not echo HTTP bodies, requests, tokens or SDK exception strings.
        print(
            f"FAIL: {type(error).__name__}. See docs/integration-checks.md and local configuration."
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
