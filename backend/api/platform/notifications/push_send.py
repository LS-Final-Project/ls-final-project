"""FCM-only transport for pywebpush 2.5.0; no redirects, proxies or second DNS lookup.

Each attempt runs in a killable child. Socket timeouts alone are not a wall clock
limit (DNS and slowly arriving headers can exceed them). The CLI waits at most
10 seconds then kills/joins the child, while still holding the subscription lock.
Only status/Retry-After cross the pipe; endpoint/key/provider text never does.
"""

import ipaddress
import json
import logging
import multiprocessing
import socket
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import requests
from pywebpush import WebPushException, webpush  # type: ignore[import-untyped]
from requests.adapters import HTTPAdapter
from urllib3 import HTTPSConnectionPool
from urllib3.connection import HTTPSConnection

from backend.api.platform.notifications.push_settings import HOST, PushWorkerSettings, endpoint

DEADLINE_SECONDS = 10


def resolve() -> str:
    addresses = {str(item[4][0]) for item in socket.getaddrinfo(HOST, 443, type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("Unsafe push DNS")
    return sorted(addresses)[0]


class FcmAdapter(HTTPAdapter):
    """One fixed TLS pool; the original hostname remains the SNI/certificate name."""

    def __init__(self, address: str) -> None:
        if not ipaddress.ip_address(address).is_global:
            raise ValueError("Unsafe push IP")

        class PinnedConnection(HTTPSConnection):
            def _new_conn(self) -> socket.socket:
                return socket.create_connection((address, 443), timeout=5)

        class PinnedPool(HTTPSConnectionPool):
            ConnectionCls = PinnedConnection

        self.pinned = PinnedPool(HOST, 443, maxsize=1, block=True)
        super().__init__(max_retries=0)

    def get_connection_with_tls_context(
        self, request: Any, verify: Any, proxies: Any = None, cert: Any = None
    ) -> Any:
        endpoint(request.url)
        if verify is not True or proxies or cert:
            raise ValueError("Invalid push transport")
        return self.pinned

    def close(self) -> None:
        self.pinned.close()
        super().close()


class FcmSession(requests.Session):
    def __init__(self, address: str) -> None:
        super().__init__()
        self.trust_env = False
        self.mount("https://", FcmAdapter(address))

    def post(
        self, url: Any, data: Any = None, json: Any = None, **kwargs: Any
    ) -> requests.Response:
        endpoint(url)
        # pywebpush reads response.text even with verbose=False. Close the raw
        # stream immediately after headers and replace the body with an empty value.
        response = super().post(
            url,
            data=data,
            json=json,
            **{**kwargs, "timeout": (5, 5), "stream": True, "allow_redirects": False},
        )
        response.close()
        response._content = b""
        return response


def retry_delay(value: str | None) -> int | None:
    if not value:
        return 60
    try:
        seconds = (
            int(value)
            if value.isdecimal()
            else int((parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        )
    except (ValueError, TypeError, OverflowError):
        return 60
    # A Retry-After beyond the event TTL must not be shortened and retried early.
    return max(1, seconds) if seconds <= 3600 else None


def transmit(row: dict[str, Any], settings: PushWorkerSettings) -> tuple[str, int]:
    """Only the isolated child uses this real transport; tests inject a fake session."""
    endpoint(row["endpoint"])
    with FcmSession(resolve()) as session:
        try:
            result = webpush(
                subscription_info={
                    "endpoint": row["endpoint"],
                    "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
                },
                data=json.dumps(
                    {"id": str(row["id"]), "title": row["title"], "url": row["link_path"]}
                ),
                vapid_private_key=settings.private_key.get_secret_value(),
                vapid_claims={"sub": settings.subject},
                headers={"Urgency": "normal"},
                ttl=3600,
                timeout=5,
                requests_session=session,
            )
        except WebPushException as error:
            result = error.response
            if result is None:
                return "UNKNOWN", 0
        if 200 <= result.status_code <= 202:
            return "SENT", 0
        if result.status_code in (404, 410):
            return "GONE", 0
        if result.status_code in (429, 503):
            delay = retry_delay(result.headers.get("Retry-After"))
            return ("PENDING", delay) if delay is not None else ("FAILED", 0)
        return "FAILED", 0


def child_send(pipe: Any, row: dict[str, Any], settings: PushWorkerSettings) -> None:
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        try:
            outcome = transmit(row, settings)
        except BaseException:
            outcome = ("UNKNOWN", 0)
        pipe.send(outcome)
    except BaseException:
        # Broken pipes and cancellation must never escape into multiprocessing's
        # traceback/logger, which is outside the application's redaction boundary.
        pass
    finally:
        pipe.close()
        logging.disable(previous_logging)


def stop_child(process: Any) -> None:
    if process.pid is None:
        return
    warned = False
    while True:
        try:
            if not process.is_alive():
                break
            try:
                process.kill()
            except OSError:
                pass
            process.join(1)
            if process.is_alive() and not warned:
                logging.getLogger(__name__).error("NOTIFICATION_PUSH_CHILD_STOP_UNCONFIRMED")
                warned = True
        except BaseException:
            # Repeated parent cancellation must not bypass the stop confirmation.
            continue
        # Exceptional kernel/process-stop failure: fail closed while keeping the
        # caller's generation lock, rather than assume the child stopped sending.
    process.close()


def send(row: dict[str, Any], settings: PushWorkerSettings) -> tuple[str, int]:
    """Dedicated Job only. Keep the DB lock until child exit is confirmed."""
    process = reader = writer = None
    try:
        context = multiprocessing.get_context("spawn")
        reader, writer = context.Pipe(duplex=False)
        process = context.Process(target=child_send, args=(writer, row, settings), daemon=True)
        process.start()
        writer.close()
        process.join(DEADLINE_SECONDS)
        if process.is_alive() or process.exitcode != 0:
            return "UNKNOWN", 0
        return reader.recv() if reader.poll() else ("UNKNOWN", 0)
    except Exception:
        return "UNKNOWN", 0
    finally:
        # BaseException/cancellation also goes through child cleanup before a TX
        # can unwind. A failed spawn has no child; no-result/nonzero exit is UNKNOWN.
        if process is not None:
            stop_child(process)
        if reader is not None:
            reader.close()
        if writer is not None:
            writer.close()
