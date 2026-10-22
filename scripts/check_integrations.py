#!/usr/bin/env python3
"""Read-only preflight plus an opt-in interactive Microsoft mail-read probe."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
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


def open_login_browser(url: str) -> None:
    """Pass the URL over stdin to Windows, avoiding shell expansion and argv logs."""
    executable = shutil.which("powershell.exe")
    fallback = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    if not executable and fallback.is_file():
        executable = str(fallback)
    if executable:
        result = subprocess.run(
            [
                executable,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$u=[Console]::In.ReadToEnd(); Start-Process -FilePath $u",
            ],
            input=url,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
        if result.returncode == 0:
            return
    if webbrowser.open(url):
        return
    # This URL contains state and a public PKCE challenge, never a token or secret.
    # The verifier stays in this process. Do not paste the URL into shared chat.
    print("Open this one-time login URL in your local browser; do not share it:", flush=True)
    print(url, flush=True)


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


def microsoft_error_summary(result: dict) -> str:
    """Extract only protocol enums and AADSTS numbers, never descriptions or identifiers."""
    allowed_errors = {
        "invalid_request",
        "invalid_grant",
        "invalid_client",
        "unauthorized_client",
        "access_denied",
        "interaction_required",
        "temporarily_unavailable",
        "server_error",
        "invalid_scope",
        "login_required",
        "consent_required",
    }
    error = result.get("error")
    code = error if isinstance(error, str) and error in allowed_errors else "oauth_failed"
    raw_codes = result.get("error_codes")
    numbers = {
        str(item)
        for item in (raw_codes if isinstance(raw_codes, list) else [])
        if type(item) is int and 100 <= item <= 9999999999
    }
    description = result.get("error_description", "")
    if isinstance(description, str):
        numbers.update(re.findall(r"\bAADSTS(\d{3,10})\b", description))
    aadsts = ",".join(f"AADSTS{number}" for number in sorted(numbers)[:8]) or "not_provided"
    return f"{code}; AADSTS={aadsts}"


def microsoft(values: dict[str, str], login: bool, personal_account: bool = False) -> int:
    try:
        tenant = (
            "9188040d-6c67-4c5b-b112-36a304b66dad"
            if personal_account
            else str(UUID(values.get("MS_TENANT_ID", "")))
        )
        client_id = str(UUID(values.get("MS_CLIENT_ID", "")))
    except ValueError:
        raise ConfigurationError(
            "Set valid MS_TENANT_ID and MS_CLIENT_ID in .env.integrations."
        ) from None
    authority = f"https://login.microsoftonline.com/{'consumers' if personal_account else tenant}"
    with urlopen(f"{authority}/v2.0/.well-known/openid-configuration", timeout=15) as response:
        discovery = json.load(response)
    if discovery.get("issuer") != f"https://login.microsoftonline.com/{tenant}/v2.0":
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
    flow = app.initiate_auth_code_flow(
        scopes=["Mail.Read", "Mail.Send"], redirect_uri=redirect, prompt="select_account"
    )
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
        open_login_browser(flow["auth_uri"])
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
        phase = "authorization" if callback.get("error") else "token_exchange"
        print(f"FAIL: Microsoft OAuth {microsoft_error_summary(result)}; phase={phase}.")
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


def vertex(args: argparse.Namespace) -> int:
    # Support direct `python scripts/check_integrations.py`, not only an installed package.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.shared.ai import (
        AISettings,
        create_client,
        embed,
        generate,
        refresh_runtime_credentials,
        runtime_credentials,
    )

    overrides = {"ai_provider": "vertex"}
    for field, value in (
        ("google_cloud_project", args.project),
        ("google_cloud_location", args.location),
        ("google_cloud_embedding_location", args.embedding_location),
        ("gemini_model", args.model),
    ):
        if value is not None:
            overrides[field] = value
    settings = AISettings(_env_file=args.env_file, **overrides)
    source_credentials = None
    if args.gcloud_account:
        from google.oauth2.credentials import Credentials

        result = subprocess.run(
            ["gcloud", "auth", "print-access-token", f"--account={args.gcloud_account}"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise ConfigurationError(
                "Reauthenticate the selected gcloud account before verification"
            )
        source_credentials = Credentials(token=result.stdout.strip())
    credentials = (
        runtime_credentials(
            settings.google_cloud_project,
            args.runtime_service,
            source_credentials=source_credentials,
        )
        if args.runtime_service
        else None
    )
    if credentials is not None:
        refresh_runtime_credentials(credentials, args.wait_runtime_iam)
    with create_client(
        settings, credentials=credentials, embedding=bool(args.embed_model)
    ) as client:
        if args.embed_model:
            result = embed(
                client,
                args.embed_model,
                "합성 연결 점검. Synthetic connection check.",
                dimensions=args.dimensions,
                task_type=args.embedding_task,
            )
            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "provider": "vertex",
                        "kind": "embedding",
                        "project": settings.google_cloud_project,
                        "location": settings.google_cloud_embedding_location
                        or settings.google_cloud_location,
                        "model": args.embed_model,
                        "runtime_service": args.runtime_service,
                        "observed_dimensions": len(result.values),
                        "input_tokens": result.input_tokens,
                        "billable_characters": result.billable_characters,
                        "vectors_saved": False,
                        "production_dimensions_selected": False,
                    },
                    ensure_ascii=False,
                )
            )
        else:
            answer = generate(client, settings, "Return exactly OK.")
            if answer.text.strip() != "OK":
                print("FAIL: expected synthetic OK response; response content was not logged.")
                return 1
            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "provider": "vertex",
                        "kind": "generation",
                        "project": settings.google_cloud_project,
                        "location": settings.google_cloud_location,
                        "model": settings.gemini_model,
                        "model_version": answer.model_version,
                        "runtime_service": args.runtime_service,
                        "finish_reasons": answer.finish_reasons,
                        "input_tokens": answer.usage.input_tokens,
                        "output_tokens": answer.usage.output_tokens,
                        "thought_tokens": answer.usage.thought_tokens,
                        "total_tokens": answer.usage.total_tokens,
                    },
                    ensure_ascii=False,
                )
            )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=("microsoft", "gemini", "vertex"))
    parser.add_argument("--env-file", type=Path, default=Path(".env.integrations"))
    parser.add_argument(
        "--login", action="store_true", help="Microsoft interactive login and read-only inbox check"
    )
    parser.add_argument(
        "--personal-account", action="store_true", help="Microsoft personal Outlook account probe"
    )
    parser.add_argument(
        "--embed-model", help="Check one synthetic embedding instead of listing models"
    )
    parser.add_argument("--project")
    parser.add_argument("--location")
    parser.add_argument("--embedding-location")
    parser.add_argument("--model")
    parser.add_argument("--dimensions", type=int)
    parser.add_argument(
        "--embedding-task", choices=("RETRIEVAL_QUERY", "RETRIEVAL_DOCUMENT", "SEMANTIC_SIMILARITY")
    )
    parser.add_argument("--runtime-service", choices=("cs", "tft"))
    parser.add_argument("--wait-runtime-iam", type=int, default=0)
    parser.add_argument(
        "--gcloud-account",
        help="Use this existing CLI account for opt-in runtime identity verification",
    )
    args = parser.parse_args()
    if args.personal_account and args.service != "microsoft":
        parser.error("--personal-account is only available for Microsoft")
    if not 0 <= args.wait_runtime_iam <= 240 or (
        args.wait_runtime_iam and (args.service != "vertex" or not args.runtime_service)
    ):
        parser.error("--wait-runtime-iam requires vertex runtime verification and 0..240 seconds")
    if args.gcloud_account and (args.service != "vertex" or not args.runtime_service):
        parser.error("--gcloud-account requires vertex and --runtime-service")
    if args.dimensions is not None and (not args.embed_model or not 1 <= args.dimensions <= 3072):
        parser.error("Use --dimensions between 1 and 3072 with --embed-model")
    if args.service != "vertex" and any(
        (
            args.project,
            args.location,
            args.embedding_location,
            args.model,
            args.runtime_service,
            args.dimensions,
            args.embedding_task,
        )
    ):
        parser.error("Project, location, model, runtime and dimension options require vertex mode")
    try:
        if args.service == "vertex":
            return vertex(args)
        values = configuration(args.env_file)
        return (
            microsoft(values, args.login, args.personal_account)
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
        code = getattr(error, "code", None)
        status = getattr(error, "status", "")
        if isinstance(code, int):
            safe_status = (
                status
                if status
                in {
                    "RESOURCE_EXHAUSTED",
                    "NOT_FOUND",
                    "PERMISSION_DENIED",
                    "UNAUTHENTICATED",
                    "INVALID_ARGUMENT",
                    "UNAVAILABLE",
                }
                else "provider_error"
            )
            guidance = (
                "Check Vertex project, IAM, model location and quota."
                if args.service == "vertex"
                else "Check the key's project and quota in AI Studio."
            )
            print(f"FAIL: HTTP {code} {safe_status}. {guidance}")
        else:
            print(
                f"FAIL: {type(error).__name__}. See docs/integration-checks.md and local configuration."
            )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
