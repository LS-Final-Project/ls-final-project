"""KAN-71 메일 수집을 전용 PostgreSQL과 가짜 Graph로 검증한다.

실제 Graph·토큰 제공자와는 연결하지 않는다. 토큰 함수는 KAN-80·81 계약의 호출 형태를 따른
합성 함수이며, 이 검사 통과가 실제 Outlook 연동 통과를 뜻하지 않는다.
"""

import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

import httpx
import psycopg
import pytest
from pydantic import SecretStr

from backend.api.platform.mail.graph import GRAPH_BASE, initial_delta_url
from backend.api.platform.mail.storage import LocalObjectStore
from backend.api.platform.mail.sync import sync_mailbox
from backend.shared.errors import ApplicationError

TOKEN = "synthetic-mail-token"
MIGRATION = Path("infra/migrations/platform/20261027_KAN-71_mail_sync.sql")
INBOX = initial_delta_url("inbox")
SENT = initial_delta_url("sentitems")
INBOX_PAGE_2 = f"{GRAPH_BASE}/me/mailFolders('inbox')/messages/delta?$skiptoken=p2"
INBOX_DELTA = f"{GRAPH_BASE}/me/mailFolders('inbox')/messages/delta?$deltatoken=d1"
INBOX_DELTA_2 = f"{GRAPH_BASE}/me/mailFolders('inbox')/messages/delta?$deltatoken=d2"
SENT_DELTA = f"{GRAPH_BASE}/me/mailFolders('sentitems')/messages/delta?$deltatoken=s1"


@pytest.fixture(scope="session")
def mail_database(auth_database):
    """전용 검사 DB에 integration 스키마와 KAN-71 마이그레이션을 적용한다.

    Args:
        auth_database: 인증 마이그레이션이 적용된 전용 DB.

    Returns:
        str: 관리자 연결 문자열.
    """
    with psycopg.connect(auth_database, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS integration AUTHORIZATION platform_migrator")
        conn.execute("GRANT USAGE ON SCHEMA integration TO platform_app")
        conn.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE platform_migrator IN SCHEMA integration "
            "GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO platform_app"
        )
        if conn.execute("SELECT to_regclass('integration.mailboxes')").fetchone()[0] is None:
            sql = MIGRATION.read_text()
            conn.execute("\n".join(x for x in sql.splitlines() if not x.startswith("\\")))
    return auth_database


@pytest.fixture
def mailbox(mail_database, registered_user):
    """연결된 합성 사서함 하나를 만들고 검사 뒤 관련 행을 정리한다.

    Args:
        mail_database: 전용 검사 DB.
        registered_user: 합성 등록 사용자.

    Returns:
        dict: 사서함 ID, 소유자 ID, 관리자 연결 문자열.
    """
    mailbox_id, owner = uuid4(), registered_user[1]
    with psycopg.connect(mail_database) as conn:
        conn.execute(
            "INSERT INTO integration.mailboxes (id, owner_user_id, provider, data_origin, "
            "connection_status) VALUES (%s, %s, 'microsoft_graph', 'synthetic', 'CONNECTED')",
            (mailbox_id, owner),
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
        conn.execute("DELETE FROM integration.mailboxes WHERE id=%s", (mailbox_id,))


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

    def handler(self, request):
        """요청 하나에 응답한다.

        Args:
            request: httpx 요청.

        Returns:
            httpx.Response: 정해 둔 응답 또는 404.
        """
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert 'IdType="ImmutableId"' in request.headers["prefer"]
        url = str(request.url)
        self.requests.append(url)
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


def token_getter(error=None):
    """KAN-80·81 계약 형태의 합성 토큰 함수.

    Args:
        error: 낼 ApplicationError 인자 또는 없음.

    Returns:
        callable: 호출 기록을 가진 비동기 함수.
    """
    calls = []

    async def get(*, mailbox_id, owner_user_id):
        calls.append((mailbox_id, owner_user_id))
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
    assert get_token.calls == [(mailbox["id"], mailbox["owner"])]
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


@pytest.mark.parametrize(
    ("status", "code"),
    [
        ("PENDING", "MAILBOX_NOT_CONNECTED"),
        ("DISCONNECTED", "MAILBOX_NOT_CONNECTED"),
        ("RECONNECT_REQUIRED", "MAILBOX_RECONNECT_REQUIRED"),
    ],
)
def test_unconnected_mailboxes_are_not_synced(settings, mailbox, tmp_path, status, code):
    with psycopg.connect(mailbox["dsn"]) as conn:
        conn.execute(
            "UPDATE integration.mailboxes SET connection_status=%s WHERE id=%s",
            (status, mailbox["id"]),
        )
    get_token = token_getter()
    with pytest.raises(ApplicationError) as error:
        run(settings, mailbox, FakeGraph(), LocalObjectStore(tmp_path), get_token)
    assert (error.value.status_code, error.value.code) == (409, code)
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
    result = run(settings, mailbox, graph, store)
    # 401 뒤에 토큰을 갱신하거나 다시 시도하지 않는다.
    assert (result.status, result.error_code, result.messages_stored) == (
        "FAILED",
        "GRAPH_UNAUTHORIZED",
        1,
    )
    assert graph.count("/messages/delta") == 2
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
            with psycopg.connect(mailbox["dsn"]) as conn:
                conn.execute(
                    "UPDATE integration.mailboxes SET connection_status='DISCONNECTED' WHERE id=%s",
                    (mailbox["id"],),
                )
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
    store = LocalObjectStore(tmp_path)
    first = asyncio.run(store.put(b"same"))
    second = asyncio.run(store.put(b"same"))
    assert first == second
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == [first.sha256]
    assert json.dumps(first.key).startswith('"mail/sha256/')
