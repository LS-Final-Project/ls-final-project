"""KAN-71 메일 수집을 전용 PostgreSQL과 가짜 Graph로 검증한다.

실제 Graph·토큰 제공자와는 연결하지 않는다. 토큰 함수는 KAN-80·81 계약의 호출 형태를 따른
합성 함수이며, 이 검사 통과가 실제 Outlook 연동 통과를 뜻하지 않는다.
"""

import asyncio
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

import httpx
import psycopg
import pytest
from pydantic import SecretStr

from backend.api.platform.mail import sync
from backend.api.platform.mail.graph import GRAPH_BASE, initial_delta_url
from backend.api.platform.mail.storage import LocalObjectStore
from backend.api.platform.mail.sync import sync_mailbox
from backend.shared.errors import ApplicationError

TOKEN = "synthetic-mail-token"
FRESH = "synthetic-fresh-token"
MIGRATION = Path("infra/migrations/platform/20261028_KAN-71_mail_sync.sql")
# Microsoft 개인 계정 테넌트. KAN-81 연결 표가 이 값만 허용한다.
CONSUMER_TID = "9188040d-6c67-4c5b-b112-36a304b66dad"
INBOX = initial_delta_url("inbox")
SENT = initial_delta_url("sentitems")
INBOX_PAGE_2 = f"{GRAPH_BASE}/me/mailFolders('inbox')/messages/delta?$skiptoken=p2"
INBOX_DELTA = f"{GRAPH_BASE}/me/mailFolders('inbox')/messages/delta?$deltatoken=d1"
INBOX_DELTA_2 = f"{GRAPH_BASE}/me/mailFolders('inbox')/messages/delta?$deltatoken=d2"
SENT_DELTA = f"{GRAPH_BASE}/me/mailFolders('sentitems')/messages/delta?$deltatoken=s1"


@pytest.fixture(scope="session")
def mail_database(mailbox_database):
    """KAN-81 연결 표가 있는 전용 검사 DB에 integration 스키마와 KAN-71 마이그레이션을 적용한다.

    Args:
        mailbox_database: KAN-81 보관 마이그레이션이 적용된 전용 DB.

    Returns:
        str: 관리자 연결 문자열.
    """
    # 스키마·권한은 마이그레이션이 만든다. 검사가 대신 만들어 결함을 가리지 않게 한다.
    with psycopg.connect(mailbox_database, autocommit=True) as conn:
        if conn.execute("SELECT to_regclass('integration.mail_sync_runs')").fetchone()[0] is None:
            sql = MIGRATION.read_text()
            conn.execute("\n".join(x for x in sql.splitlines() if not x.startswith("\\")))
    return mailbox_database


@pytest.fixture
def mailbox(mail_database, registered_user):
    """KAN-81 연결 표에 연결된 합성 사서함 하나를 만들고 검사 뒤 관련 행을 정리한다.

    캐시 칸은 형식만 맞춘 임의 바이트다. 토큰 함수가 가짜라서 복호화하지 않는다.

    Args:
        mail_database: 전용 검사 DB.
        registered_user: 합성 등록 사용자.

    Returns:
        dict: 사서함 ID, 소유자 ID, 관리자 연결 문자열.
    """
    mailbox_id, owner = uuid4(), registered_user[1]
    with psycopg.connect(mail_database) as conn:
        conn.execute(
            "INSERT INTO mailbox_auth.connections (id, owner_user_id, subject_tid, subject_oid, "
            "revision, state, key_version, cache_ciphertext) "
            "VALUES (%s, %s, %s, %s, 1, 'connected', 'test-v1', %s)",
            (mailbox_id, owner, CONSUMER_TID, uuid4(), os.urandom(64)),
        )
    yield {"id": mailbox_id, "owner": owner, "dsn": mail_database}
    with psycopg.connect(mail_database) as conn:
        conn.execute(
            "DELETE FROM integration.mail_attachments WHERE message_id IN "
            "(SELECT id FROM integration.mail_messages WHERE mailbox_id=%s)",
            (mailbox_id,),
        )
        for table in ("mail_messages", "mail_sync_cursors", "mail_sync_runs"):
            conn.execute(
                psycopg.sql.SQL("DELETE FROM integration.{} WHERE mailbox_id=%s").format(
                    psycopg.sql.Identifier(table)
                ),
                (mailbox_id,),
            )
        conn.execute("DELETE FROM mailbox_auth.connections WHERE id=%s", (mailbox_id,))


def message(graph_id, received, subject="Inquiry", attachments=False):
    """delta 항목 하나.

    Args:
        graph_id: 합성 ImmutableId.
        received: UTC 수신 시각 문자열.
        subject: 제목.
        attachments: hasAttachments 값.

    Returns:
        dict: Graph delta 항목.
    """
    return {
        "id": graph_id,
        "internetMessageId": f"<{graph_id}@example.invalid>",
        "conversationId": "conv-1",
        "subject": subject,
        "from": {"emailAddress": {"name": "Customer", "address": "customer@example.invalid"}},
        "receivedDateTime": received,
        "sentDateTime": received,
        "hasAttachments": attachments,
    }


class FakeGraph:
    """URL별로 정해 둔 응답만 돌려주는 가짜 Graph."""

    def __init__(self):
        """첫 동기화 자료를 준비한다.

        Args:
            없음.

        Returns:
            None.
        """
        self.pages = {
            INBOX: (
                200,
                {
                    "value": [message("AAMk/1=", "2026-10-27T01:00:00Z", attachments=True)],
                    "@odata.nextLink": INBOX_PAGE_2,
                },
            ),
            INBOX_PAGE_2: (
                200,
                {"value": [message("m2", "2026-10-27T02:00:00Z")], "@odata.deltaLink": INBOX_DELTA},
            ),
            SENT: (
                200,
                {
                    "value": [message("s1", "2026-10-27T03:00:00Z", "Re: Inquiry")],
                    "@odata.deltaLink": SENT_DELTA,
                },
            ),
            SENT_DELTA: (200, {"value": [], "@odata.deltaLink": SENT_DELTA}),
        }
        self.mime = {
            gid: f"Subject: {gid}\r\n\r\nbody {gid}".encode()
            for gid in ("AAMk/1=", "m2", "s1", "m3")
        }
        self.attachments = {
            "AAMk/1=": [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "id": "a1",
                    "name": "manual.pdf",
                    "contentType": "application/pdf",
                    "isInline": False,
                },
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "id": "a2",
                    "name": "logo.png",
                    "contentType": "image/png",
                    "isInline": True,
                },
                {
                    "@odata.type": "#microsoft.graph.referenceAttachment",
                    "id": "a3",
                    "name": "link",
                    "contentType": None,
                    "isInline": False,
                },
            ]
        }
        self.attachment_bytes = {("AAMk/1=", "a1"): b"%PDF-synthetic", ("AAMk/1=", "a2"): b"PNG"}
        self.requests = []
        # Graph가 받아 주는 토큰. 검사에서 빼면 그 토큰의 요청에 401을 돌려준다(만료 흉내).
        self.accepted = {TOKEN, FRESH}
        self.unauthorized = 0

    def handler(self, request):
        """요청 하나에 응답한다.

        Args:
            request: httpx 요청.

        Returns:
            httpx.Response: 정해 둔 응답 또는 404.
        """
        token = request.headers["authorization"].removeprefix("Bearer ")
        assert token in (TOKEN, FRESH)
        assert 'IdType="ImmutableId"' in request.headers["prefer"]
        url = str(request.url)
        self.requests.append(url)
        if token not in self.accepted:
            self.unauthorized += 1
            return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})
        for key, (status, body) in self.pages.items():
            if str(httpx.URL(key)) == url:
                return httpx.Response(status, json=body)
        parts = [unquote(p) for p in request.url.raw_path.decode().split("?")[0].split("/")]
        # ['', 'v1.0', 'me', 'messages', id, ...]
        if parts[3] == "messages" and parts[-1] == "$value" and len(parts) == 6:
            if parts[4] in self.mime:
                return httpx.Response(200, content=self.mime[parts[4]])
        if parts[3] == "messages" and parts[-1] == "attachments":
            return httpx.Response(200, json={"value": self.attachments.get(parts[4], [])})
        if parts[3] == "messages" and parts[5] == "attachments" and parts[-1] == "$value":
            data = self.attachment_bytes.get((parts[4], parts[6]))
            if data is not None:
                return httpx.Response(200, content=data)
        return httpx.Response(404, json={"error": {"code": "ErrorItemNotFound"}})

    def count(self, suffix):
        """특정 끝부분을 가진 요청 수.

        Args:
            suffix: URL 끝부분.

        Returns:
            int: 요청 수.
        """
        return sum(url.split("?")[0].endswith(suffix) for url in self.requests)


def token_getter(error=None, refresh_error=None, fresh=FRESH):
    """KAN-80·81 계약 형태의 합성 토큰 함수.

    Args:
        error: 처음 호출에서 낼 ApplicationError 인자 또는 없음.
        refresh_error: `force_refresh=True` 호출에서 낼 ApplicationError 인자 또는 없음.
        fresh: `force_refresh=True` 호출이 돌려줄 토큰.

    Returns:
        callable: 호출 기록(mailbox_id, owner_user_id, force_refresh)을 가진 비동기 함수.
    """
    calls = []

    async def get(*, mailbox_id, owner_user_id, force_refresh=False):
        calls.append((mailbox_id, owner_user_id, force_refresh))
        if force_refresh:
            if refresh_error:
                raise ApplicationError(*refresh_error)
            return SecretStr(fresh)
        if error:
            raise ApplicationError(*error)
        return SecretStr(TOKEN)

    get.calls = calls
    return get


def run(settings, mailbox, graph, store, get_token=None):
    """가짜 Graph로 수집을 한 번 실행한다.

    Args:
        settings: platform_app 설정.
        mailbox: 합성 사서함.
        graph: FakeGraph.
        store: 원문 저장소.
        get_token: 토큰 함수. 없으면 정상 합성 토큰.

    Returns:
        SyncResult: 실행 결과.
    """

    async def go():
        transport = httpx.MockTransport(graph.handler)
        async with httpx.AsyncClient(transport=transport) as client:
            return await sync_mailbox(
                settings,
                mailbox["id"],
                get_token=get_token or token_getter(),
                client=client,
                store=store,
            )

    return asyncio.run(go())


def rows(dsn, sql, *args):
    """관리자 연결로 조회한다.

    Args:
        dsn: 관리자 연결 문자열.
        sql: 조회문.
        *args: 매개변수.

    Returns:
        list[tuple]: 결과 행.
    """
    with psycopg.connect(dsn) as conn:
        return conn.execute(sql, args).fetchall()


def test_first_sync_stores_both_folders_with_raw_and_attachments(settings, mailbox, tmp_path):
    graph, store = FakeGraph(), LocalObjectStore(tmp_path)
    get_token = token_getter()
    result = run(settings, mailbox, graph, store, get_token)
    assert (result.status, result.error_code, result.messages_stored) == ("SUCCEEDED", None, 3)
    # 토큰 함수는 저장된 소유자로 호출된다.
    assert get_token.calls == [(mailbox["id"], mailbox["owner"], False)]
    stored = rows(
        mailbox["dsn"],
        "SELECT graph_id, folder, received_at, raw_storage_key, raw_sha256, raw_size, "
        "from_address FROM integration.mail_messages WHERE mailbox_id=%s ORDER BY received_at",
        mailbox["id"],
    )
    assert [(r[0], r[1]) for r in stored] == [
        ("AAMk/1=", "inbox"),
        ("m2", "inbox"),
        ("s1", "sentitems"),
    ]
    assert stored[0][2].isoformat() == "2026-10-27T01:00:00+00:00"
    for graph_id, _, _, key, digest, size, sender in stored:
        data = (tmp_path / key).read_bytes()
        assert data == graph.mime[graph_id]
        assert (hashlib.sha256(data).hexdigest(), len(data)) == (digest, size)
        assert sender == "customer@example.invalid"
    attachments = rows(
        mailbox["dsn"],
        "SELECT a.graph_attachment_id, a.kind, a.is_inline, a.storage_key, a.size "
        "FROM integration.mail_attachments a JOIN integration.mail_messages m "
        "ON m.id=a.message_id WHERE m.mailbox_id=%s ORDER BY 1",
        mailbox["id"],
    )
    assert [(a[0], a[1], a[2], a[4]) for a in attachments] == [
        ("a1", "file", False, 14),
        ("a2", "file", True, 3),
        ("a3", "reference", False, None),
    ]
    assert (tmp_path / attachments[0][3]).read_bytes() == b"%PDF-synthetic"
    assert attachments[2][3] is None
    cursors = rows(
        mailbox["dsn"],
        "SELECT folder, next_link, delta_link FROM integration.mail_sync_cursors "
        "WHERE mailbox_id=%s ORDER BY folder",
        mailbox["id"],
    )
    assert cursors == [("inbox", None, INBOX_DELTA), ("sentitems", None, SENT_DELTA)]
    # 토큰은 어떤 업무 기록에도 남지 않는다.
    with psycopg.connect(mailbox["dsn"]) as conn:
        for table in ("mail_messages", "mail_attachments", "mail_sync_runs", "mail_sync_cursors"):
            dump = conn.execute(
                psycopg.sql.SQL(
                    "SELECT coalesce(json_agg(t)::text, '') FROM integration.{} t"
                ).format(psycopg.sql.Identifier(table))
            ).fetchone()[0]
            assert TOKEN not in dump


def test_incremental_sync_uses_the_delta_link_and_keeps_removed_rows(settings, mailbox, tmp_path):
    graph, store = FakeGraph(), LocalObjectStore(tmp_path)
    run(settings, mailbox, graph, store)
    graph.pages[INBOX_DELTA] = (
        200,
        {
            "value": [
                message("m3", "2026-10-27T05:00:00Z"),
                message("m2", "2026-10-27T02:00:00Z", subject="Inquiry (read)"),
                {"id": "AAMk/1=", "@removed": {"reason": "deleted"}},
            ],
            "@odata.deltaLink": INBOX_DELTA_2,
        },
    )
    graph.requests.clear()
    result = run(settings, mailbox, graph, store)
    assert (result.status, result.messages_stored, result.messages_removed) == ("SUCCEEDED", 1, 1)
    assert graph.requests[0] == str(httpx.URL(INBOX_DELTA))
    # 이미 보관한 m2의 원문은 다시 받지 않는다.
    assert graph.count("/m3/$value") == 1 and graph.count("/m2/$value") == 0
    removed = rows(
        mailbox["dsn"],
        "SELECT graph_id, removed_at IS NOT NULL FROM integration.mail_messages "
        "WHERE mailbox_id=%s ORDER BY graph_id",
        mailbox["id"],
    )
    assert removed == [("AAMk/1=", True), ("m2", False), ("m3", False), ("s1", False)]
    assert rows(
        mailbox["dsn"],
        "SELECT delta_link FROM integration.mail_sync_cursors WHERE mailbox_id=%s "
        "AND folder='inbox'",
        mailbox["id"],
    ) == [(INBOX_DELTA_2,)]


def test_only_one_sync_runs_per_mailbox(settings, mailbox, tmp_path):
    live, stale = uuid4(), uuid4()
    with psycopg.connect(mailbox["dsn"]) as conn:
        conn.execute(
            "INSERT INTO integration.mail_sync_runs (id, mailbox_id, lease_expires_at) "
            "VALUES (%s, %s, now() + interval '1 minute')",
            (live, mailbox["id"]),
        )
    graph = FakeGraph()
    with pytest.raises(ApplicationError) as error:
        run(settings, mailbox, graph, LocalObjectStore(tmp_path))
    assert (error.value.status_code, error.value.code) == (409, "MAIL_SYNC_IN_PROGRESS")
    assert graph.requests == []
    # lease가 끝난 실행은 실패로 정리하고 새 실행이 맡는다.
    with psycopg.connect(mailbox["dsn"]) as conn:
        conn.execute(
            "UPDATE integration.mail_sync_runs SET id=%s, "
            "lease_expires_at=now() - interval '1 second' WHERE id=%s",
            (stale, live),
        )
    assert run(settings, mailbox, graph, LocalObjectStore(tmp_path)).status == "SUCCEEDED"
    assert rows(
        mailbox["dsn"],
        "SELECT status, error_code FROM integration.mail_sync_runs WHERE id=%s",
        stale,
    ) == [("FAILED", "LEASE_EXPIRED")]


def test_concurrent_runs_do_not_both_start(settings, mailbox, tmp_path):
    graph = FakeGraph()

    async def both():
        transport = httpx.MockTransport(graph.handler)
        async with httpx.AsyncClient(transport=transport) as client:
            calls = [
                sync_mailbox(
                    settings,
                    mailbox["id"],
                    get_token=token_getter(),
                    client=client,
                    store=LocalObjectStore(tmp_path),
                )
                for _ in range(2)
            ]
            return await asyncio.gather(*calls, return_exceptions=True)

    outcomes = asyncio.run(both())
    codes = sorted(o.code if isinstance(o, ApplicationError) else o.status for o in outcomes)
    assert codes == ["MAIL_SYNC_IN_PROGRESS", "SUCCEEDED"]
    assert rows(
        mailbox["dsn"],
        "SELECT count(*) FROM integration.mail_messages WHERE mailbox_id=%s",
        mailbox["id"],
    ) == [(3,)]


def disconnect(mailbox):
    """KAN-81의 연결 해제와 같은 상태로 바꾼다.

    Args:
        mailbox: 합성 사서함.

    Returns:
        None.
    """
    with psycopg.connect(mailbox["dsn"]) as conn:
        conn.execute(
            "UPDATE mailbox_auth.connections SET state='disconnected', cache_ciphertext=NULL "
            "WHERE id=%s",
            (mailbox["id"],),
        )


def test_disconnected_mailboxes_are_not_synced(settings, mailbox, tmp_path):
    disconnect(mailbox)
    get_token = token_getter()
    with pytest.raises(ApplicationError) as error:
        run(settings, mailbox, FakeGraph(), LocalObjectStore(tmp_path), get_token)
    assert (error.value.status_code, error.value.code) == (409, "MAILBOX_NOT_CONNECTED")
    assert get_token.calls == []


def test_unknown_mailbox_is_denied(settings, mailbox, tmp_path):
    unknown = dict(mailbox, id=uuid4())
    with pytest.raises(ApplicationError) as error:
        run(settings, unknown, FakeGraph(), LocalObjectStore(tmp_path))
    assert (error.value.status_code, error.value.code) == (403, "MAILBOX_ACCESS_DENIED")


@pytest.mark.parametrize(
    "error",
    [
        (409, "MAILBOX_RECONNECT_REQUIRED", "Mailbox reconnection is required."),
        (503, "MAILBOX_TOKEN_UNAVAILABLE", "Mailbox authentication is unavailable."),
        (403, "MAILBOX_ACCESS_DENIED", "Mailbox access is not allowed."),
    ],
)
def test_token_failures_are_recorded_not_hidden(settings, mailbox, tmp_path, error):
    graph = FakeGraph()
    result = run(settings, mailbox, graph, LocalObjectStore(tmp_path), token_getter(error))
    assert (result.status, result.error_code) == ("FAILED", error[1])
    assert graph.requests == []
    assert rows(
        mailbox["dsn"],
        "SELECT status, error_code, finished_at IS NOT NULL FROM integration.mail_sync_runs "
        "WHERE mailbox_id=%s",
        mailbox["id"],
    ) == [("FAILED", error[1], True)]


def test_unauthorized_page_keeps_progress_for_the_next_run(settings, mailbox, tmp_path):
    graph, store = FakeGraph(), LocalObjectStore(tmp_path)
    page_2 = graph.pages[INBOX_PAGE_2]
    graph.pages[INBOX_PAGE_2] = (401, {"error": {"code": "InvalidAuthenticationToken"}})
    get_token = token_getter()
    result = run(settings, mailbox, graph, store, get_token)
    # 새 토큰으로 한 번만 다시 보내고, 그래도 401이면 실패로 끝낸다.
    assert (result.status, result.error_code, result.messages_stored) == (
        "FAILED",
        "GRAPH_UNAUTHORIZED",
        1,
    )
    assert [force for *_, force in get_token.calls] == [False, True]
    assert graph.requests.count(str(httpx.URL(INBOX_PAGE_2))) == 2
    assert graph.count("/messages/delta") == 3
    assert rows(
        mailbox["dsn"],
        "SELECT next_link, delta_link FROM integration.mail_sync_cursors WHERE mailbox_id=%s",
        mailbox["id"],
    ) == [(INBOX_PAGE_2, None)]
    graph.pages[INBOX_PAGE_2] = page_2
    graph.requests.clear()
    result = run(settings, mailbox, graph, store)
    assert (result.status, result.messages_stored) == ("SUCCEEDED", 2)
    assert graph.requests[0] == str(httpx.URL(INBOX_PAGE_2))


def test_expired_sync_state_restarts_without_refetching(settings, mailbox, tmp_path):
    graph, store = FakeGraph(), LocalObjectStore(tmp_path)
    run(settings, mailbox, graph, store)
    graph.pages[INBOX_DELTA] = (410, {"error": {"code": "SyncStateNotFound"}})
    graph.requests.clear()
    result = run(settings, mailbox, graph, store)
    assert (result.status, result.messages_stored) == ("SUCCEEDED", 0)
    assert graph.requests[:2] == [str(httpx.URL(INBOX_DELTA)), str(httpx.URL(INBOX))]
    assert graph.count("/$value") == 0


def test_message_deleted_after_delta_is_skipped(settings, mailbox, tmp_path):
    graph = FakeGraph()
    del graph.mime["m2"]
    result = run(settings, mailbox, graph, LocalObjectStore(tmp_path))
    assert (result.status, result.messages_stored) == ("SUCCEEDED", 2)


def test_disconnect_during_sync_stops_writing(settings, mailbox, tmp_path):
    graph = FakeGraph()
    original = graph.handler

    def disconnect_after_first_page(request):
        response = original(request)
        if str(request.url) == str(httpx.URL(INBOX_PAGE_2)):
            disconnect(mailbox)
        return response

    graph.handler = disconnect_after_first_page
    result = run(settings, mailbox, graph, LocalObjectStore(tmp_path))
    assert (result.status, result.error_code, result.messages_stored) == (
        "FAILED",
        "MAILBOX_NOT_CONNECTED",
        1,
    )


def test_runtime_role_cannot_delete_mail(settings, mailbox, tmp_path):
    run(settings, mailbox, FakeGraph(), LocalObjectStore(tmp_path))
    with psycopg.connect(settings.database_url.get_secret_value()) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "DELETE FROM integration.mail_messages WHERE mailbox_id=%s", (mailbox["id"],)
            )


def test_storage_writes_each_content_once(tmp_path):
    store, mailbox_id = LocalObjectStore(tmp_path), uuid4()
    first = asyncio.run(store.put(b"same", mailbox_id=mailbox_id))
    second = asyncio.run(store.put(b"same", mailbox_id=mailbox_id))
    assert first == second
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == [first.sha256]
    assert json.dumps(first.key).startswith(f'"mail/{mailbox_id}/sha256/')


def test_same_content_in_two_mailboxes_is_stored_separately(tmp_path):
    # 한 사서함의 객체를 지우거나 권한을 좁혀도 다른 사서함 객체는 그대로여야 한다.
    store, a, b = LocalObjectStore(tmp_path), uuid4(), uuid4()
    first = asyncio.run(store.put(b"same", mailbox_id=a))
    second = asyncio.run(store.put(b"same", mailbox_id=b))
    assert first.sha256 == second.sha256
    assert first.key != second.key
    (tmp_path / first.key).unlink()
    assert (tmp_path / second.key).read_bytes() == b"same"


def test_sync_keys_objects_by_mailbox(settings, mailbox, tmp_path):
    run(settings, mailbox, FakeGraph(), LocalObjectStore(tmp_path))
    keys = rows(
        mailbox["dsn"],
        "SELECT m.raw_storage_key FROM integration.mail_messages m WHERE m.mailbox_id=%s "
        "UNION ALL SELECT a.storage_key FROM integration.mail_attachments a "
        "JOIN integration.mail_messages m ON m.id=a.message_id "
        "WHERE m.mailbox_id=%s AND a.storage_key IS NOT NULL",
        mailbox["id"],
        mailbox["id"],
    )
    assert keys
    assert all(key.startswith(f"mail/{mailbox['id']}/sha256/") for (key,) in keys)


def test_cancelled_sync_is_finished_not_left_running(settings, mailbox, tmp_path):
    graph = FakeGraph()

    async def cancel_on_first_download():
        task = None

        async def handler(request):
            if request.url.raw_path.endswith(b"/$value"):
                task.cancel()
                await asyncio.sleep(10)
            return graph.handler(request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            task = asyncio.create_task(
                sync_mailbox(
                    settings,
                    mailbox["id"],
                    get_token=token_getter(),
                    client=client,
                    store=LocalObjectStore(tmp_path),
                )
            )
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(cancel_on_first_download())
    assert rows(
        mailbox["dsn"],
        "SELECT status, error_code, finished_at IS NOT NULL FROM integration.mail_sync_runs "
        "WHERE mailbox_id=%s",
        mailbox["id"],
    ) == [("FAILED", "SYNC_CANCELLED", True)]
    # lease 만료(300초)를 기다리지 않고 바로 다음 실행이 시작된다.
    result = run(settings, mailbox, graph, LocalObjectStore(tmp_path))
    assert (result.status, result.messages_stored) == ("SUCCEEDED", 3)


def test_lease_is_extended_before_each_download(settings, mailbox, tmp_path):
    graph = FakeGraph()
    original = graph.handler
    seen = []

    def check_lease(request):
        if (
            request.url.raw_path.endswith(b"/$value")
            and b"/attachments/" not in request.url.raw_path
        ):
            with psycopg.connect(mailbox["dsn"]) as conn:
                seen.append(
                    conn.execute(
                        "SELECT lease_expires_at > started_at + interval '300 seconds' "
                        "FROM integration.mail_sync_runs WHERE mailbox_id=%s AND status='RUNNING'",
                        (mailbox["id"],),
                    ).fetchone()[0]
                )
        return original(request)

    graph.handler = check_lease
    assert run(settings, mailbox, graph, LocalObjectStore(tmp_path)).status == "SUCCEEDED"
    # 처음 lease(시작 + 300초)보다 늦게 끝나도록 메시지마다 늘어나 있다.
    assert seen == [True, True, True]


def test_missing_attachment_keeps_the_message(settings, mailbox, tmp_path):
    graph = FakeGraph()
    del graph.attachment_bytes[("AAMk/1=", "a1")]
    result = run(settings, mailbox, graph, LocalObjectStore(tmp_path))
    assert (result.status, result.messages_stored) == ("SUCCEEDED", 3)
    assert rows(
        mailbox["dsn"],
        "SELECT a.graph_attachment_id FROM integration.mail_attachments a "
        "JOIN integration.mail_messages m ON m.id=a.message_id WHERE m.mailbox_id=%s ORDER BY 1",
        mailbox["id"],
    ) == [("a2",), ("a3",)]


def test_lost_lease_is_not_reported_as_success(settings, mailbox, tmp_path, monkeypatch):
    finish = sync.finish_run

    async def expired_before_finish(settings_, result):
        # 마지막 페이지 기록 뒤, 종료 기록 전에 다른 실행이 이 실행을 정리한 상황.
        with psycopg.connect(mailbox["dsn"]) as conn:
            conn.execute(
                "UPDATE integration.mail_sync_runs SET status='FAILED', "
                "error_code='LEASE_EXPIRED', finished_at=now() WHERE id=%s",
                (result.run_id,),
            )
        await finish(settings_, result)

    monkeypatch.setattr(sync, "finish_run", expired_before_finish)
    result = run(settings, mailbox, FakeGraph(), LocalObjectStore(tmp_path))
    assert (result.status, result.error_code) == ("FAILED", "LEASE_EXPIRED")


def expire_on(graph, predicate, token):
    """조건에 맞는 요청이 처음 오면 그때부터 토큰 하나를 거부하게 한다.

    Args:
        graph: FakeGraph.
        predicate: 요청을 받아 만료 시점인지 판단하는 함수.
        token: 만료시킬 토큰.

    Returns:
        None.
    """
    original = graph.handler

    def handler(request):
        if predicate(request):
            graph.accepted.discard(token)
        return original(request)

    graph.handler = handler


def is_first_mime(request):
    path = request.url.raw_path
    return path.endswith(b"/$value") and b"/attachments/" not in path


def test_token_expiring_mid_run_is_refreshed_once_and_sync_completes(settings, mailbox, tmp_path):
    graph = FakeGraph()
    expire_on(graph, is_first_mime, TOKEN)
    get_token = token_getter()
    result = run(settings, mailbox, graph, LocalObjectStore(tmp_path), get_token)
    assert (result.status, result.error_code, result.messages_stored) == ("SUCCEEDED", None, 3)
    assert get_token.calls == [
        (mailbox["id"], mailbox["owner"], False),
        (mailbox["id"], mailbox["owner"], True),
    ]
    assert graph.unauthorized == 1


def test_refresh_budget_is_shared_across_the_whole_run(settings, mailbox, tmp_path):
    # 첫 토큰이 원문 요청에서, 새 토큰이 다음 페이지에서 또 만료된다. 두 번째 갱신은 없다.
    graph = FakeGraph()
    expire_on(graph, is_first_mime, TOKEN)
    expire_on(graph, lambda r: str(r.url) == str(httpx.URL(INBOX_PAGE_2)), FRESH)
    get_token = token_getter()
    result = run(settings, mailbox, graph, LocalObjectStore(tmp_path), get_token)
    assert (result.status, result.error_code, result.messages_stored) == (
        "FAILED",
        "GRAPH_UNAUTHORIZED",
        1,
    )
    assert [force for *_, force in get_token.calls] == [False, True]
    assert graph.unauthorized == 2
    assert rows(
        mailbox["dsn"],
        "SELECT next_link, delta_link FROM integration.mail_sync_cursors WHERE mailbox_id=%s",
        mailbox["id"],
    ) == [(INBOX_PAGE_2, None)]


@pytest.mark.parametrize(
    "refresh_error",
    [
        (409, "MAILBOX_RECONNECT_REQUIRED", "Mailbox reconnection is required."),
        (503, "MAILBOX_TOKEN_UNAVAILABLE", "Mailbox authentication is unavailable."),
    ],
)
def test_failed_refresh_is_recorded_and_graph_is_not_called_again(
    settings, mailbox, tmp_path, refresh_error
):
    graph = FakeGraph()
    graph.accepted.discard(TOKEN)
    get_token = token_getter(refresh_error=refresh_error)
    result = run(settings, mailbox, graph, LocalObjectStore(tmp_path), get_token)
    assert (result.status, result.error_code) == ("FAILED", refresh_error[1])
    assert graph.requests == [str(httpx.URL(INBOX))]
    assert [force for *_, force in get_token.calls] == [False, True]


def test_empty_refreshed_token_is_not_used(settings, mailbox, tmp_path):
    graph = FakeGraph()
    graph.accepted.discard(TOKEN)
    result = run(settings, mailbox, graph, LocalObjectStore(tmp_path), token_getter(fresh=""))
    assert (result.status, result.error_code) == ("FAILED", "MAILBOX_TOKEN_UNAVAILABLE")
    assert graph.requests == [str(httpx.URL(INBOX))]


@pytest.mark.parametrize(
    ("status", "code"),
    [(403, "GRAPH_FORBIDDEN"), (429, "GRAPH_THROTTLED"), (503, "GRAPH_UNAVAILABLE")],
)
def test_other_graph_failures_do_not_refresh(settings, mailbox, tmp_path, status, code):
    graph = FakeGraph()
    graph.pages[INBOX_PAGE_2] = (status, {"error": {"code": "Other"}})
    get_token = token_getter()
    result = run(settings, mailbox, graph, LocalObjectStore(tmp_path), get_token)
    assert (result.status, result.error_code) == ("FAILED", code)
    assert [force for *_, force in get_token.calls] == [False]
    assert graph.requests.count(str(httpx.URL(INBOX_PAGE_2))) == 1


def test_cancel_while_recording_the_end_still_finishes_the_run(
    settings, mailbox, tmp_path, monkeypatch
):
    finish = sync.finish_run

    async def slow_finish(settings_, result):
        await asyncio.sleep(0.2)
        await finish(settings_, result)

    monkeypatch.setattr(sync, "finish_run", slow_finish)

    async def cancel_during_finish():
        async with httpx.AsyncClient(transport=httpx.MockTransport(FakeGraph().handler)) as client:
            task = asyncio.create_task(
                sync_mailbox(
                    settings,
                    mailbox["id"],
                    get_token=token_getter(),
                    client=client,
                    store=LocalObjectStore(tmp_path),
                )
            )
            while not rows(
                mailbox["dsn"],
                "SELECT 1 FROM integration.mail_sync_cursors WHERE mailbox_id=%s "
                "AND folder='sentitems' AND delta_link IS NOT NULL",
                mailbox["id"],
            ):
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0.5)

    asyncio.run(cancel_during_finish())
    assert rows(
        mailbox["dsn"],
        "SELECT status, finished_at IS NOT NULL FROM integration.mail_sync_runs WHERE mailbox_id=%s",
        mailbox["id"],
    ) == [("SUCCEEDED", True)]
