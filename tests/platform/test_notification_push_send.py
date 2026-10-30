"""Real encryption with a fake wire, and fake process deadlines; never connect externally."""

import base64
from unittest.mock import Mock
from uuid import uuid4

import pytest
import requests
from pydantic import SecretStr

from backend.api.platform.notifications import push_send, push_worker
from backend.api.platform.notifications.push_settings import PushWorkerSettings
from tests.platform.test_notification_push import AUTH, ENDPOINT, PUBLIC


def config():
    return PushWorkerSettings(
        enabled=True,
        private_key=SecretStr(
            base64.urlsafe_b64encode((17).to_bytes(32, "big")).rstrip(b"=").decode()
        ),
        public_key=PUBLIC,
        subject="mailto:ops@example.invalid",
        batch_size=1,
    )


def row():
    return {
        "id": uuid4(),
        "endpoint": ENDPOINT,
        "p256dh": PUBLIC,
        "auth": AUTH,
        "title": "C/S 답변 확인 대기",
        "link_path": f"/cs/tickets/{uuid4()}",
    }


@pytest.mark.parametrize(
    "status,retry,expected",
    [
        (201, None, ("SENT", 0)),
        (404, None, ("GONE", 0)),
        (410, None, ("GONE", 0)),
        (429, "120", ("PENDING", 120)),
        (503, "60", ("PENDING", 60)),
        (503, "7200", ("FAILED", 0)),
        (403, None, ("FAILED", 0)),
        (302, None, ("FAILED", 0)),
    ],
)
def test_pywebpush_actual_encryption_fake_transport(monkeypatch, status, retry, expected):
    monkeypatch.setattr(push_send, "resolve", lambda: "8.8.8.8")
    fake = Mock(spec=requests.Response)
    fake.status_code = status
    fake.headers = {"Retry-After": retry} if retry else {}
    fake.text = ""
    fake.reason = "fake"
    seen = []

    def post(self, url, **kwargs):
        seen.append(kwargs)
        assert url == ENDPOINT
        assert kwargs["headers"]["ttl"] == "3600"
        assert kwargs["headers"]["Urgency"] == "normal"
        assert kwargs["headers"]["Authorization"].startswith("vapid ")
        assert isinstance(kwargs["data"], bytes)
        assert b"C/S" not in kwargs["data"]
        return fake

    monkeypatch.setattr(push_send.FcmSession, "post", post)
    assert push_send.transmit(row(), config()) == expected
    assert len(seen) == 1


def test_response_is_closed_without_reading_body_and_redirects_disabled(monkeypatch):
    response = Mock(spec=requests.Response)
    captured = {}

    def post(self, url, **kwargs):
        captured.update(kwargs)
        return response

    monkeypatch.setattr(requests.Session, "post", post)
    with push_send.FcmSession("8.8.8.8") as session:
        assert session.trust_env is False
        assert session.post(ENDPOINT, timeout=99) is response
    assert captured["allow_redirects"] is False
    assert captured["stream"] is True and captured["timeout"] == (5, 5)
    response.close.assert_called_once()
    assert response._content == b""


def test_dns_rejects_any_private_result_and_adapter_connects_pinned_ip(monkeypatch):
    monkeypatch.setattr(
        push_send.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("127.0.0.1", 443))]
    )
    with pytest.raises(ValueError):
        push_send.resolve()
    captured = []
    monkeypatch.setattr(
        push_send.socket, "create_connection", lambda address, **k: captured.append((address, k))
    )
    adapter = push_send.FcmAdapter("8.8.8.8")
    pool = adapter.get_connection_with_tls_context(Mock(url=ENDPOINT), True)
    conn = pool.ConnectionCls(host=pool.host, port=pool.port)
    conn._new_conn()
    assert captured == [(("8.8.8.8", 443), {"timeout": 5})]
    assert pool.host == "fcm.googleapis.com" and conn.host == pool.host
    assert adapter.max_retries.total == 0
    adapter.close()


def test_hung_dns_or_send_is_killed_before_generation_lock_can_release(monkeypatch):
    process = Mock()
    process.pid = 123
    process.is_alive.side_effect = [True, True, False, False]
    reader, writer = Mock(), Mock()
    context = Mock()
    context.Pipe.return_value = (reader, writer)
    context.Process.return_value = process
    monkeypatch.setattr(push_send.multiprocessing, "get_context", lambda mode: context)
    assert push_send.send(row(), config()) == ("UNKNOWN", 0)
    assert process.join.call_args_list[0].args == (10,)
    process.kill.assert_called_once()
    assert process.join.call_args_list[1].args == (1,)
    reader.recv.assert_not_called()
    reader.close.assert_called_once()
    writer.close.assert_called()


def test_disabled_worker_never_claims_or_sends():
    import asyncio

    from backend.shared.settings import Settings

    sender = Mock()
    assert (
        asyncio.run(
            push_worker.run(
                Settings(_env_file=None, environment="test"),
                PushWorkerSettings(enabled=False),
                sender=sender,
            )
        )
        == 0
    )
    sender.assert_not_called()


@pytest.mark.parametrize(
    "result,attempts,expected",
    [
        ("SENT", 1, "SENT"),
        ("PENDING", 1, "PENDING"),
        ("PENDING", 3, "FAILED"),
        ("UNKNOWN", 1, "UNKNOWN"),
        ("FAILED", 1, "FAILED"),
        ("GONE", 1, "DELETE"),
        ("SKIP", 1, "SKIPPED"),
    ],
)
def test_worker_commits_claim_before_send_and_keeps_generation_boundary(
    monkeypatch, result, attempts, expected
):
    import asyncio
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    from backend.shared.settings import Settings

    events = []
    conn = AsyncMock()

    @asynccontextmanager
    async def connection(_):
        events.append("BEGIN")
        yield conn
        events.append("COMMIT")

    monkeypatch.setattr(push_worker, "connection", connection)
    monkeypatch.setattr(push_worker.notifications, "expand_pending", AsyncMock())
    monkeypatch.setattr(push_worker.repository, "prepare", AsyncMock())
    item = {"intent_id": uuid4(), "subscription_id": uuid4(), "attempts": attempts}
    monkeypatch.setattr(push_worker.repository, "claim", AsyncMock(return_value=item))
    monkeypatch.setattr(
        push_worker.repository,
        "lock_attempt",
        AsyncMock(return_value=None if result == "SKIP" else row()),
    )
    finish = AsyncMock()
    monkeypatch.setattr(push_worker.repository, "finish", finish)

    def sender(data, push):
        assert events == ["BEGIN", "COMMIT", "BEGIN", "COMMIT", "BEGIN"]
        return result, 120

    asyncio.run(
        push_worker.run(Settings(_env_file=None, environment="test"), config(), sender=sender)
    )
    if expected == "DELETE":
        conn.execute.assert_awaited_once_with(
            "DELETE FROM notify.push_subscriptions WHERE id=%s", (item["subscription_id"],)
        )
        finish.assert_not_awaited()
    else:
        assert finish.await_args.args[2] == expected
    assert events == ["BEGIN", "COMMIT", "BEGIN", "COMMIT", "BEGIN", "COMMIT"]


def test_unconfirmed_child_stop_holds_cleanup_until_exit(monkeypatch, caplog):
    process = Mock(pid=123)
    process.is_alive.side_effect = [True, True, True, False, False]
    push_send.stop_child(process)
    assert process.kill.call_count == 2 and process.join.call_count == 2
    process.close.assert_called_once()
    assert "NOTIFICATION_PUSH_CHILD_STOP_UNCONFIRMED" in caplog.text
    assert ENDPOINT not in caplog.text and AUTH not in caplog.text


@pytest.mark.parametrize("failure", ["spawn", "empty", "exit", "cancel"])
def test_process_failures_and_parent_cancellation_cleanup(monkeypatch, failure):
    process = Mock(pid=123, exitcode=1 if failure == "exit" else 0)
    reader, writer = Mock(), Mock()
    reader.poll.return_value = False
    if failure == "spawn":
        process.start.side_effect = RuntimeError("sensitive provider value")
        process.pid = None
    elif failure == "cancel":
        process.join.side_effect = [KeyboardInterrupt(), None]
        process.is_alive.side_effect = [True, False, False]
    else:
        process.is_alive.return_value = False
    context = Mock()
    context.Pipe.return_value = (reader, writer)
    context.Process.return_value = process
    monkeypatch.setattr(push_send.multiprocessing, "get_context", lambda mode: context)
    if failure == "cancel":
        with pytest.raises(KeyboardInterrupt):
            push_send.send(row(), config())
        process.kill.assert_called_once()
    else:
        assert push_send.send(row(), config()) == ("UNKNOWN", 0)
    reader.recv.assert_not_called()
    reader.close.assert_called_once()
    writer.close.assert_called()


def test_child_provider_exception_never_leaves_pipe_as_text(monkeypatch, capsys, caplog):
    def explode(*args):
        import logging

        logging.error("%s %s", ENDPOINT, AUTH)
        raise RuntimeError(f"{ENDPOINT} {AUTH} private-key")

    monkeypatch.setattr(push_send, "transmit", explode)
    pipe = Mock()
    push_send.child_send(pipe, row(), config())
    pipe.send.assert_called_once_with(("UNKNOWN", 0))
    assert capsys.readouterr() == ("", "")
    assert ENDPOINT not in caplog.text and AUTH not in caplog.text


def test_unknown_transport_has_no_retry_or_error_payload(monkeypatch):
    monkeypatch.setattr(push_send, "resolve", lambda: "8.8.8.8")

    def post(*args, **kwargs):
        raise requests.Timeout("sensitive endpoint")

    monkeypatch.setattr(push_send.FcmSession, "post", post)
    pipe = Mock()
    push_send.child_send(pipe, row(), config())
    pipe.send.assert_called_once_with(("UNKNOWN", 0))
