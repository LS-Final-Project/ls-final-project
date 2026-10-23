"""Structured JSON logs that carry work IDs and never carry secrets or message bodies (KAN-70).

Each line is one JSON object in the Cloud Logging format. Only the fields listed here are
written. Request and work IDs come from the current context, so callers bind an ID once
and every later line in the same request or job carries it.
"""

import json
import logging
import logging.config
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from fastapi import Request

# IDs that link a log line to a business record, a background job or an evaluation.
LINK_FIELDS = frozenset(
    {
        "ticket_id",
        "project_id",
        "improvement_request_id",
        "job_id",
        "experiment_id",
        "evaluation_run_id",
    }
)
# Context set for every request by the application middleware.
REQUEST_FIELDS = frozenset({"service", "request_id", "trace_id"})
# Per-line values a caller may pass with `extra=`. Anything else is dropped.
EXTRA_FIELDS = frozenset({"method", "path", "status", "duration_ms", "error_type"})

MAX_MESSAGE = 2000

_context: ContextVar[dict[str, str] | None] = ContextVar("team4_log_context", default=None)

_SECRET_PATTERNS = (
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*"), "[REDACTED]"),
    (
        re.compile(
            r"(?i)\b(access_token|refresh_token|id_token|token|code|code_verifier|client_secret"
            r"|secret|password|passwd|api_key|apikey|state|nonce|session|cookie|authorization"
            r"|set-cookie)(\s*[=:]\s*)([^&\s;,]+)"
        ),
        r"\1\2[REDACTED]",
    ),
    (re.compile(r"(__Host-team4_[A-Za-z_]+|team4_[A-Za-z_]*session)=([^;\s]+)"), r"\1=[REDACTED]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
)


def redact(text: str) -> str:
    """Masks tokens, passwords, cookies and email addresses, and caps the length."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    if len(text) > MAX_MESSAGE:
        text = text[:MAX_MESSAGE] + "…[truncated]"
    return text


def bind(**ids: object) -> None:
    """Adds work IDs to the current request or job context. Unknown keys are refused."""
    unknown = set(ids) - LINK_FIELDS
    if unknown:
        raise ValueError(f"Not a log link field: {sorted(unknown)}")
    context = _context.get()
    if context is None:
        # Outside a request or log_context there is nothing to attach the IDs to.
        return
    context.update({key: str(value) for key, value in ids.items() if value is not None})


@contextmanager
def log_context(**fields: object) -> Iterator[dict[str, str]]:
    """Opens a fresh context, for one request or one background job.

    The dict is shared with tasks started inside it, so IDs bound by a route are still
    visible to the middleware that writes the request line.
    """
    unknown = set(fields) - LINK_FIELDS - REQUEST_FIELDS
    if unknown:
        raise ValueError(f"Not a log context field: {sorted(unknown)}")
    context = {key: str(value) for key, value in fields.items() if value is not None}
    token = _context.set(context)
    try:
        yield context
    finally:
        _context.reset(token)


def bind_path_ids(request: Request) -> None:
    """Router dependency: links log lines to the record named in the path (ticket_id, ...)."""
    bind(**{k: v for k, v in request.path_params.items() if k in LINK_FIELDS})


def trace_id(header: str | None) -> str | None:
    """Trace ID from Cloud Run's X-Cloud-Trace-Context (`TRACE/SPAN;o=1`)."""
    if not header:
        return None
    value = header.split("/", 1)[0]
    return value if re.fullmatch(r"[0-9a-fA-F]{32}", value) else None


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "severity": record.levelname,
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        entry.update(_context.get() or {})
        for name in EXTRA_FIELDS:
            if name in record.__dict__:
                value = record.__dict__[name]
                entry[name] = redact(value) if isinstance(value, str) else value
        if record.exc_info and record.exc_info[0] is not None:
            # Exception text and tracebacks can contain provider responses or credentials.
            entry["error_type"] = record.exc_info[0].__name__
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if project and entry.get("trace_id"):
            entry["logging.googleapis.com/trace"] = f"projects/{project}/traces/{entry['trace_id']}"
        return json.dumps(entry, ensure_ascii=False, default=str)


LOGGING_CONFIG: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": JsonFormatter}},
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "stream": "ext://sys.stdout",
        }
    },
    "root": {"handlers": ["stdout"], "level": "INFO"},
    "loggers": {
        # Uvicorn's own access line would repeat the request line without work IDs and
        # with the raw query string, so request logging is done by the middleware only.
        "uvicorn.access": {"handlers": [], "level": "WARNING", "propagate": False},
        "uvicorn.error": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
        "uvicorn": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
    },
}


def configure_logging() -> None:
    logging.config.dictConfig(LOGGING_CONFIG)
