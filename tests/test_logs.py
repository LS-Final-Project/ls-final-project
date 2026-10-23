"""KAN-70: request lines carry request and work IDs; secrets and bodies never reach a log."""

import io
import json
import logging
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.cs.main import create_app as cs_app
from backend.api.cs.tickets import repository, routes
from backend.shared.access import Principal, principal_header
from backend.shared.logs import JsonFormatter, bind, log_context, redact, trace_id
from backend.shared.settings import Settings

CS_AGENT = principal_header(Principal(user_id=uuid4(), roles={"cs_agent"}))
TRACE = "0af7651916cd43dd8448eb211c80319c"


@pytest.fixture
def lines(monkeypatch):
    """JSON lines written by the request and application loggers during the test."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    restore = []
    for name in ("backend.request", "backend.api.common.application"):
        log = logging.getLogger(name)
        monkeypatch.setattr(log, "handlers", [handler])
        monkeypatch.setattr(log, "propagate", False)
        # Another test may have run a logging dictConfig that disabled existing loggers.
        monkeypatch.setattr(log, "disabled", False)
        # setLevel also clears the logger's cached level decisions.
        restore.append((log, log.level))
        log.setLevel(logging.INFO)
    yield lambda: [json.loads(line) for line in stream.getvalue().splitlines()]
    for log, level in restore:
        log.setLevel(level)


@pytest.fixture
def client(monkeypatch):
    @asynccontextmanager
    async def fake_connection(settings):
        yield object()

    async def missing(conn, ticket_id):
        return None

    monkeypatch.setattr(routes, "connection", fake_connection)
    monkeypatch.setattr(repository, "get_ticket", missing)
    with TestClient(cs_app(Settings(_env_file=None, environment="test"))) as test_client:
        yield test_client


def test_request_line_carries_request_trace_and_work_ids(client, lines):
    ticket = uuid4()
    response = client.get(
        f"/api/v1/cs/tickets/{ticket}?cursor=secret-value",
        headers={**CS_AGENT, "X-Cloud-Trace-Context": f"{TRACE}/1;o=1"},
    )
    [line] = [entry for entry in lines() if entry["message"] == "request completed"]
    assert line["request_id"] == response.headers["X-Request-ID"]
    assert line["service"] == "cs"
    assert line["trace_id"] == TRACE
    assert line["ticket_id"] == str(ticket)
    assert (line["method"], line["status"]) == ("GET", 404)
    # The query string never reaches the log.
    assert line["path"] == f"/api/v1/cs/tickets/{ticket}"
    assert "secret-value" not in json.dumps(line)
    assert line["severity"] == "INFO" and line["duration_ms"] >= 0


def test_unhandled_error_logs_the_type_but_not_the_message(client, lines, monkeypatch):
    async def explode(conn, ticket_id):
        raise RuntimeError("password=hunter2 for kim@example.com")

    monkeypatch.setattr(repository, "get_ticket", explode)
    response = client.get(f"/api/v1/cs/tickets/{uuid4()}", headers=CS_AGENT)
    assert response.status_code == 500
    text = "\n".join(json.dumps(line) for line in lines())
    assert "hunter2" not in text and "kim@example.com" not in text
    [error] = [line for line in lines() if line["message"] == "Unhandled request error"]
    assert error["error_type"] == "RuntimeError" and error["severity"] == "ERROR"


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Authorization: Bearer abc.def-123", "abc.def-123"),
        ("access_token=ya29.a0AfH6SMB", "ya29.a0AfH6SMB"),
        ("callback?code=0.AXoA-secret&state=s123", "0.AXoA-secret"),
        ("callback?code=0.AXoA-secret&state=s123", "s123"),
        ("client_secret: s3cr3t", "s3cr3t"),
        ("Cookie: __Host-team4_session=opaque-value", "opaque-value"),
        ("id eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl", "c2lnbmF0dXJl"),
        ("customer kim.nayoung@example.com wrote", "kim.nayoung@example.com"),
    ],
)
def test_messages_are_redacted(text, secret):
    assert secret not in redact(text)


def test_only_known_fields_are_written():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "mail received", None, None)
    record.body = "Dear team, the gasket leaks"
    record.token = "abc"
    record.status = 200
    with log_context(service="cs", request_id="r1"):
        bind(job_id="j1", experiment_id="e1")
        entry = json.loads(JsonFormatter().format(record))
    assert "body" not in entry and "token" not in entry
    assert entry["status"] == 200
    assert (entry["job_id"], entry["experiment_id"], entry["service"]) == ("j1", "e1", "cs")


def test_unknown_link_fields_are_refused():
    with pytest.raises(ValueError):
        bind(body="text")
    with pytest.raises(ValueError):
        with log_context(email="kim@example.com"):
            pass


def test_long_messages_are_truncated():
    assert len(redact("x" * 5000)) < 2100


@pytest.mark.parametrize("header", [None, "", "not-a-trace/1", "1234/1;o=1"])
def test_invalid_trace_headers_are_ignored(header):
    assert trace_id(header) is None
