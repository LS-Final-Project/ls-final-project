"""Local-only synchronous summary bridge; model keys never enter an API container.

The same bridge also serves the improvement-request AI draft (KAN-115), the ticket Korean
translation and the customer reply analysis (KAN-230) on their own paths with their own prompt
check and output schema; all share the driver's lock, limits and usage accounting.

Only the explicit CS driver serves this authenticated Docker-internal endpoint. Calls
share its worker lock, job/call limits, reservations and fail-closed usage accounting.
No prompt or response is persisted here; the regular summary route owns that record.
"""

import hmac
import json
import re
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from secrets import token_urlsafe
from uuid import uuid4

import httpx
from pydantic import SecretStr

from backend.engine.cs.classification import ClassificationInput
from backend.engine.cs.improvement_drafting import ImprovementDraft, ImprovementDraftInput
from backend.engine.cs.improvement_drafting import instruction as draft_instruction
from backend.engine.cs.improvement_drafting import render_prompt as render_draft_prompt
from backend.engine.cs.providers import GenerationKnobs, gemini_text_provider
from backend.engine.cs.reply import ReplyInterpretation, ReplyInterpretationInput
from backend.engine.cs.reply import instruction as reply_instruction
from backend.engine.cs.reply import render_prompt as render_reply_prompt
from backend.engine.cs.summary import INSTRUCTION, SummaryOutput, render_prompt
from backend.engine.cs.translation import INSTRUCTION as TRANSLATION_INSTRUCTION
from backend.engine.cs.translation import TranslationOutput
from backend.engine.cs.translation import render_prompt as render_translation_prompt
from backend.shared import ai
from backend.shared.errors import ApplicationError
from scripts.local_business.permit import Denied
from scripts.local_business.worker import (
    ADMISSION_LIMIT_CODES,
    Client,
    Configuration,
    LocalStop,
    Meter,
)

PORT = 38084
MAX_REQUEST = 256 * 1024


def unavailable():
    return ApplicationError(
        503,
        "LOCAL_SUMMARY_UNAVAILABLE",
        "Start a CS enable-real driver or wait for its current call to finish.",
    )


def bridge_provider(state: Path, endpoint: str = "/summary"):
    """Read the current driver capability per call; cached summaries need no driver.

    endpoint is "/summary", "/improvement-draft" (KAN-115), "/translation" or "/reply-analysis"
    (KAN-230).
    """

    def provide(prompt):
        try:
            from scripts.local_business.runtime import gate

            if not gate(state, "cs"):
                raise ValueError
            marker = json.loads((state / "control/enabled.json").read_text())
            bridge = marker["summary_bridge"]
            namespace = json.loads((state / "settings.json").read_text())["namespace"]
            if marker.get("owner") != "cs" or marker.get("mode") != "real":
                raise ValueError
            host = bridge["host"]
            if not re.fullmatch(re.escape(namespace) + r"-driver-[0-9a-f]{8}", host):
                raise ValueError
            with httpx.Client(trust_env=False, timeout=120) as client:
                response = client.post(
                    f"http://{host}:{PORT}{endpoint}",
                    headers={"Authorization": "Bearer " + bridge["capability"]},
                    json={"prompt": prompt},
                )
            response.raise_for_status()
            row = response.json()
            return ai.Generation(
                text=row["text"],
                usage=ai.Usage(**row["usage"]),
                model_version=row["model_version"],
                finish_reasons=row["finish_reasons"],
                model=row["model"],
                elapsed_ms=row["elapsed_ms"],
            )
        except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError):
            raise unavailable() from None

    return provide


def metered_provider(
    permit, token, control, model, *, response_schema=SummaryOutput, max_output_tokens=4096
):
    """Only invoked in the key-bearing driver, once per authorized summary or draft."""
    from backend.shared.settings import Settings
    from scripts.local_business.runtime import local_only

    local_only()
    # Match every paid entrypoint: tracing policy is checked before keys or clients.
    Settings(_env_file=None, environment="local", service_name="cs")
    job = uuid4()
    permit.grant(token, str(job))
    config = Configuration(
        "cs", job, token, control, Path("/run/secrets/gemini-api-key"), Path("/local/prices.json")
    )
    prices = ai.load_price_table(config.prices_file)
    meter = Meter(permit, config, prices)
    settings = ai.AISettings(
        _env_file=None,
        gemini_model=model,
        gemini_transient_retries=0,
        gemini_api_key=SecretStr(config.key_file.read_text().strip()),
    )
    meter.priced(model)
    return gemini_text_provider(
        Client(ai.create_client(settings), meter),
        settings,
        model=model,
        response_schema=response_schema,
        generation=GenerationKnobs(max_output_tokens=max_output_tokens),
    )


def metered_draft_provider(permit, token, control, model):
    """The improvement-request AI draft (KAN-115): its schema and a larger output limit."""
    return metered_provider(
        permit, token, control, model, response_schema=ImprovementDraft, max_output_tokens=8192
    )


def metered_translation_provider(permit, token, control, model):
    """The ticket Korean translation: its schema and a larger output limit (whole bodies)."""
    return metered_provider(
        permit, token, control, model, response_schema=TranslationOutput, max_output_tokens=16384
    )


def metered_reply_provider(permit, token, control, model):
    """The customer reply analysis (KAN-230): its schema and the route's output limit."""
    return metered_provider(
        permit, token, control, model, response_schema=ReplyInterpretation, max_output_tokens=8192
    )


def checked_prompt(path, prompt):
    """Accept only the exact prompt the C/S route renders for that path; else ValueError."""
    if path == "/summary":
        prefix = INSTRUCTION + "\n\n입력:\n"
        if not prompt.startswith(prefix):
            raise ValueError
        payload = ClassificationInput.model_validate_json(prompt[len(prefix) :])
        rendered = render_prompt(payload)
    elif path == "/improvement-draft":
        prefix = draft_instruction() + "\n\n입력:\n"
        if not prompt.startswith(prefix):
            raise ValueError
        draft = ImprovementDraftInput.model_validate_json(prompt[len(prefix) :])
        rendered = render_draft_prompt(draft)
    elif path == "/translation":
        prefix = TRANSLATION_INSTRUCTION + "\n\n입력:\n"
        if not prompt.startswith(prefix):
            raise ValueError
        payload = ClassificationInput.model_validate_json(prompt[len(prefix) :])
        rendered = render_translation_prompt(payload)
    elif path == "/reply-analysis":
        prefix = reply_instruction() + "\n\n입력:\n"
        if not prompt.startswith(prefix):
            raise ValueError
        reply = ReplyInterpretationInput.model_validate_json(prompt[len(prefix) :])
        rendered = render_reply_prompt(reply)
    else:
        raise ValueError
    if rendered != prompt:
        raise ValueError


class SummaryBridge:
    def __init__(
        self,
        permit,
        token,
        control,
        model,
        lock,
        *,
        address=("0.0.0.0", PORT),
        factory=metered_provider,
        draft_factory=metered_draft_provider,
        translation_factory=metered_translation_provider,
        reply_factory=metered_reply_provider,
    ):
        from scripts.local_business.runtime import local_only

        local_only()
        self.capability = token_urlsafe(32)
        self.failed = False
        self.accepting = True
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.connection.settimeout(5)
                expected = "Bearer " + bridge.capability
                supplied = self.headers.get("Authorization", "")
                if not supplied.isascii() or not hmac.compare_digest(supplied, expected):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= MAX_REQUEST:
                        raise ValueError
                    body = json.loads(self.rfile.read(length))
                    if set(body) != {"prompt"}:
                        raise ValueError
                    prompt = body["prompt"]
                    checked_prompt(self.path, prompt)
                except (ValueError, TypeError, AttributeError, OSError):
                    self.send_error(400)
                    return
                if not bridge.accepting or bridge.failed or not lock.acquire(blocking=False):
                    self.send_error(503)
                    return
                try:
                    make = {
                        "/summary": factory,
                        "/improvement-draft": draft_factory,
                        "/translation": translation_factory,
                        "/reply-analysis": reply_factory,
                    }[self.path]
                    result = make(permit, token, control, model)(prompt)
                    usage = asdict(result.usage)
                    usage.pop("billable_input_tokens", None)
                    usage.pop("raw", None)
                    data = json.dumps(
                        {
                            "text": result.text,
                            "usage": usage,
                            "model_version": result.model_version,
                            "finish_reasons": result.finish_reasons,
                            "model": result.model,
                            "elapsed_ms": result.elapsed_ms,
                        }
                    ).encode()
                except (Denied, LocalStop) as error:
                    reason = str(error) if isinstance(error, Denied) else error.admission_reason
                    # Reaching an explicit limit incurred no new provider cost and needs
                    # no cost review. Unknown spend and authorization failures still halt.
                    bridge.failed = reason not in ADMISSION_LIMIT_CODES
                    self.send_error(503)
                    return
                except Exception:
                    bridge.failed = True
                    self.send_error(503)
                    return
                finally:
                    lock.release()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:
                    pass  # Known usage already settled; a lost HTTP response never triggers retry.

        self.server = HTTPServer(address, Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.accepting = False
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
