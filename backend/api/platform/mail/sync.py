"""사서함 하나의 메일 수집 실행. 사서함마다 실행 중인 수집은 하나뿐이다.

1. 사서함 연결을 잠그고 만료된 실행을 실패로 정리한 뒤 새 실행을 만든다.
2. 저장된 소유자로 토큰 제공자를 호출한다. 실패는 그 코드로 실행에 기록한다.
3. 폴더마다 저장된 커서에서 delta 페이지를 받는다. 새 메시지의 원문·첨부를 먼저 저장소에
   쓰고, 한 트랜잭션에서 실행 소유를 확인한 뒤 메시지·첨부·커서를 함께 기록한다.
   중간에 멈춰도 다음 실행이 마지막으로 기록한 페이지부터 이어 받는다.
"""

import contextlib
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID, uuid4

import httpx
import psycopg
from pydantic import SecretStr

from backend.api.platform.mail.graph import (
    FOLDERS,
    AttachmentMeta,
    DeltaPage,
    Folder,
    GraphError,
    GraphMail,
    MessageMeta,
    initial_delta_url,
)
from backend.api.platform.mail.storage import ObjectStore, StoredObject
from backend.shared.database import connection
from backend.shared.errors import ApplicationError
from backend.shared.settings import Settings

if TYPE_CHECKING:
    from backend.api.platform.auth.mailbox_tokens import MailboxTokenProvider

Row = dict[str, Any]
Conn = psycopg.AsyncConnection[Row]
LEASE_SECONDS = 300


class MailboxTokenGetter(Protocol):
    """KAN-80·81 계약의 `get_mailbox_access_token`과 같은 호출 형태."""

    def __call__(self, *, mailbox_id: UUID, owner_user_id: UUID) -> Awaitable[SecretStr]:
        """유효한 access token을 돌려주거나 ApplicationError를 낸다.

        Args:
            mailbox_id: 사서함 연결 ID.
            owner_user_id: 서버에 저장된 연결 소유자.

        Returns:
            Awaitable[SecretStr]: Graph 호출에만 쓰는 토큰.
        """
        ...


@dataclass
class SyncResult:
    """수집 실행 한 번의 결과. 실패도 실행 기록에 남는다."""

    run_id: UUID
    status: str
    error_code: str | None = None
    messages_stored: int = 0
    messages_removed: int = 0


@dataclass
class FetchedMessage:
    """저장소에 쓴 새 메시지와 첨부."""

    meta: MessageMeta
    raw: StoredObject
    attachments: list[tuple[AttachmentMeta, StoredObject | None]] = field(default_factory=list)


class LeaseLost(Exception):
    """다른 실행이 만료된 실행을 정리해 이 실행이 더 기록할 수 없다."""


class MailboxDisconnected(Exception):
    """실행 중 연결이 해제됐다. 재연결 필요 여부는 토큰 제공자가 판단한다."""


async def begin_run(conn: Conn, mailbox_id: UUID) -> tuple[UUID, UUID]:
    """연결 상태를 확인하고 이 사서함의 유일한 실행을 만든다.

    Args:
        conn: 플랫폼 실행 사용자 연결.
        mailbox_id: 사서함 연결 ID.

    Returns:
        tuple[UUID, UUID]: 새 실행 ID와 저장된 소유자 ID.
    """
    async with conn.transaction():
        # 연결 표는 KAN-81 소유다. 수집은 소유자·연결 상태만 읽고 토큰 열은 읽지 않는다.
        cursor = await conn.execute(
            "SELECT owner_user_id, state FROM mailbox_auth.connections "
            "WHERE id=%s FOR NO KEY UPDATE",
            (mailbox_id,),
        )
        mailbox = await cursor.fetchone()
        if mailbox is None:
            raise ApplicationError(403, "MAILBOX_ACCESS_DENIED", "Mailbox access is not allowed.")
        if mailbox["state"] != "connected":
            raise ApplicationError(409, "MAILBOX_NOT_CONNECTED", "Mailbox is not connected.")
        await conn.execute(
            "UPDATE integration.mail_sync_runs SET status='FAILED', error_code='LEASE_EXPIRED', "
            "finished_at=now() WHERE mailbox_id=%s AND status='RUNNING' AND lease_expires_at<=now()",
            (mailbox_id,),
        )
        cursor = await conn.execute(
            "INSERT INTO integration.mail_sync_runs (id, mailbox_id, lease_expires_at) "
            "VALUES (%s, %s, now() + make_interval(secs => %s)) "
            "ON CONFLICT (mailbox_id) WHERE status='RUNNING' DO NOTHING RETURNING id",
            (uuid4(), mailbox_id, LEASE_SECONDS),
        )
        run = await cursor.fetchone()
        if run is None:
            raise ApplicationError(409, "MAIL_SYNC_IN_PROGRESS", "Mailbox sync is in progress.")
        return run["id"], mailbox["owner_user_id"]


async def hold_run(conn: Conn, run_id: UUID) -> None:
    """현재 트랜잭션에서 실행 소유를 확인하고 lease를 늘린다.

    Args:
        conn: 트랜잭션 중인 연결.
        run_id: 이 실행의 ID.

    Returns:
        None. 소유를 잃었거나 연결이 끊겼으면 예외를 낸다.
    """
    cursor = await conn.execute(
        "UPDATE integration.mail_sync_runs r "
        "SET lease_expires_at=now() + make_interval(secs => %s) "
        "FROM mailbox_auth.connections m WHERE r.id=%s AND r.status='RUNNING' "
        "AND m.id=r.mailbox_id RETURNING m.state",
        (LEASE_SECONDS, run_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise LeaseLost
    if row["state"] != "connected":
        raise MailboxDisconnected


async def finish_run(settings: Settings, result: SyncResult) -> None:
    """아직 이 실행이 소유한 경우에만 종료 상태를 기록한다.

    Args:
        settings: DB 설정.
        result: 종료 상태와 고정 오류 코드.

    Returns:
        None.
    """
    async with connection(settings) as conn, conn.transaction():
        await conn.execute(
            "UPDATE integration.mail_sync_runs SET status=%s, error_code=%s, finished_at=now() "
            "WHERE id=%s AND status='RUNNING'",
            (result.status, result.error_code, result.run_id),
        )


async def read_cursor(settings: Settings, mailbox_id: UUID, folder: Folder) -> str:
    """이어 받을 위치를 정한다. 진행 중 페이지가 있으면 그것부터 받는다.

    Args:
        settings: DB 설정.
        mailbox_id: 사서함 연결 ID.
        folder: 폴더 이름.

    Returns:
        str: next_link, delta_link 또는 처음 주소.
    """
    async with connection(settings) as conn:
        cursor = await conn.execute(
            "SELECT next_link, delta_link FROM integration.mail_sync_cursors "
            "WHERE mailbox_id=%s AND folder=%s",
            (mailbox_id, folder),
        )
        row = await cursor.fetchone()
    if row and (row["next_link"] or row["delta_link"]):
        return str(row["next_link"] or row["delta_link"])
    return initial_delta_url(folder)


async def stored_ids(settings: Settings, mailbox_id: UUID, graph_ids: list[str]) -> set[str]:
    """이미 원문을 보관한 메시지 ID. 다시 받지 않는다.

    Args:
        settings: DB 설정.
        mailbox_id: 사서함 연결 ID.
        graph_ids: 이번 페이지의 메시지 ID.

    Returns:
        set[str]: 이미 저장한 ID.
    """
    if not graph_ids:
        return set()
    async with connection(settings) as conn:
        cursor = await conn.execute(
            "SELECT graph_id FROM integration.mail_messages "
            "WHERE mailbox_id=%s AND graph_id = ANY(%s)",
            (mailbox_id, graph_ids),
        )
        return {row["graph_id"] for row in await cursor.fetchall()}


async def fetch_message(graph: GraphMail, store: ObjectStore, meta: MessageMeta) -> FetchedMessage:
    """새 메시지의 원문과 첨부를 저장소에 쓴다.

    Args:
        graph: 사서함 Graph 클라이언트.
        store: 원문 저장소.
        meta: delta 항목의 속성.

    Returns:
        FetchedMessage: 저장 위치를 담은 메시지.
    """
    fetched = FetchedMessage(meta, await store.put(await graph.message_mime(meta.graph_id)))
    for attachment in await graph.attachments(meta.graph_id):
        stored = None
        if attachment.kind != "reference":
            data = await graph.attachment_bytes(meta.graph_id, attachment.graph_id)
            stored = await store.put(data)
        fetched.attachments.append((attachment, stored))
    return fetched


async def record_page(
    conn: Conn,
    run_id: UUID,
    mailbox_id: UUID,
    folder: Folder,
    page: DeltaPage,
    fetched: list[FetchedMessage],
    known: set[str],
) -> tuple[int, int]:
    """한 페이지의 결과와 다음 커서를 한 트랜잭션에 기록한다.

    Args:
        conn: 트랜잭션 중인 연결. hold_run을 먼저 통과해야 한다.
        run_id: 이 실행의 ID.
        mailbox_id: 사서함 연결 ID.
        folder: 폴더 이름.
        page: delta 페이지.
        fetched: 새로 보관한 메시지.
        known: 이미 보관해 원문을 다시 받지 않은 메시지 ID.

    Returns:
        tuple[int, int]: 새로 기록한 메시지 수와 제거 표시한 수.
    """
    stored = 0
    for item in fetched:
        meta, raw = item.meta, item.raw
        cursor = await conn.execute(
            "INSERT INTO integration.mail_messages (id, mailbox_id, graph_id, folder, "
            "internet_message_id, conversation_id, subject, from_address, received_at, sent_at, "
            "has_attachments, raw_storage_key, raw_sha256, raw_size, first_sync_run_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (mailbox_id, graph_id) DO UPDATE SET folder=EXCLUDED.folder, "
            "removed_at=NULL RETURNING id, (xmax = 0) AS inserted",
            (
                uuid4(),
                mailbox_id,
                meta.graph_id,
                folder,
                meta.internet_message_id,
                meta.conversation_id,
                meta.subject,
                meta.from_address,
                meta.received_at,
                meta.sent_at,
                meta.has_attachments,
                raw.key,
                raw.sha256,
                raw.size,
                run_id,
            ),
        )
        row = await cursor.fetchone()
        assert row is not None
        stored += int(row["inserted"])
        for attachment, data in item.attachments:
            await conn.execute(
                "INSERT INTO integration.mail_attachments (id, message_id, graph_attachment_id, "
                "kind, name, content_type, is_inline, storage_key, sha256, size) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (message_id, graph_attachment_id) DO NOTHING",
                (
                    uuid4(),
                    row["id"],
                    attachment.graph_id,
                    attachment.kind,
                    attachment.name,
                    attachment.content_type,
                    attachment.is_inline,
                    data.key if data else None,
                    data.sha256 if data else None,
                    data.size if data else None,
                ),
            )
    if known:
        # 폴더 이동이나 다시 나타난 메시지. 원문은 처음 보관한 것을 유지한다.
        await conn.execute(
            "UPDATE integration.mail_messages SET folder=%s, removed_at=NULL "
            "WHERE mailbox_id=%s AND graph_id = ANY(%s)",
            (folder, mailbox_id, sorted(known)),
        )
    removed = 0
    if page.removed:
        # 다른 폴더로 옮겨 간 메시지에는 이 폴더의 제거를 적용하지 않는다.
        cursor = await conn.execute(
            "UPDATE integration.mail_messages SET removed_at=now() WHERE mailbox_id=%s "
            "AND folder=%s AND graph_id = ANY(%s) AND removed_at IS NULL",
            (mailbox_id, folder, page.removed),
        )
        removed = cursor.rowcount
    await conn.execute(
        "INSERT INTO integration.mail_sync_cursors (mailbox_id, folder, next_link, delta_link) "
        "VALUES (%s, %s, %s, %s) ON CONFLICT (mailbox_id, folder) DO UPDATE SET "
        "next_link=EXCLUDED.next_link, "
        "delta_link=COALESCE(EXCLUDED.delta_link, integration.mail_sync_cursors.delta_link), "
        "updated_at=now()",
        (mailbox_id, folder, page.next_link, page.delta_link),
    )
    await conn.execute(
        "UPDATE integration.mail_sync_runs SET messages_stored=messages_stored+%s, "
        "messages_removed=messages_removed+%s WHERE id=%s",
        (stored, removed, run_id),
    )
    return stored, removed


async def reset_cursor(settings: Settings, run_id: UUID, mailbox_id: UUID, folder: Folder) -> None:
    """Graph가 동기화 상태를 버렸을 때 폴더를 처음부터 다시 받게 한다.

    Args:
        settings: DB 설정.
        run_id: 이 실행의 ID.
        mailbox_id: 사서함 연결 ID.
        folder: 폴더 이름.

    Returns:
        None. 이미 보관한 메시지는 ID로 다시 찾아 원문을 받지 않는다.
    """
    async with connection(settings) as conn, conn.transaction():
        await hold_run(conn, run_id)
        await conn.execute(
            "UPDATE integration.mail_sync_cursors SET next_link=NULL, delta_link=NULL, "
            "updated_at=now() WHERE mailbox_id=%s AND folder=%s",
            (mailbox_id, folder),
        )


async def sync_folder(
    settings: Settings,
    graph: GraphMail,
    store: ObjectStore,
    result: SyncResult,
    mailbox_id: UUID,
    folder: Folder,
) -> None:
    """폴더 하나를 delta 마지막 페이지까지 받는다.

    Args:
        settings: DB 설정.
        graph: 사서함 Graph 클라이언트.
        store: 원문 저장소.
        result: 누적할 실행 결과.
        mailbox_id: 사서함 연결 ID.
        folder: 폴더 이름.

    Returns:
        None. Graph 실패는 GraphError로 전달한다.
    """
    url: str | None = await read_cursor(settings, mailbox_id, folder)
    reset = False
    while url:
        try:
            page = await graph.delta_page(url)
        except GraphError as error:
            if error.code != "GRAPH_SYNC_RESET" or reset:
                raise
            reset = True
            await reset_cursor(settings, result.run_id, mailbox_id, folder)
            url = initial_delta_url(folder)
            continue
        known = await stored_ids(settings, mailbox_id, [m.graph_id for m in page.messages])
        fetched = []
        for meta in page.messages:
            if meta.graph_id in known:
                continue
            try:
                fetched.append(await fetch_message(graph, store, meta))
            except GraphError as error:
                # delta 이후 지워진 메시지. 다음 delta가 제거를 알려 준다.
                if error.code != "GRAPH_NOT_FOUND":
                    raise
        async with connection(settings) as conn, conn.transaction():
            await hold_run(conn, result.run_id)
            stored, removed = await record_page(
                conn, result.run_id, mailbox_id, folder, page, fetched, known
            )
        result.messages_stored += stored
        result.messages_removed += removed
        url = page.next_link


async def sync_mailbox(
    settings: Settings,
    mailbox_id: UUID,
    *,
    get_token: MailboxTokenGetter,
    client: httpx.AsyncClient,
    store: ObjectStore,
) -> SyncResult:
    """사서함 하나의 받은편지함·보낸편지함을 수집한다.

    Args:
        settings: DB 설정.
        mailbox_id: 서버에 저장된 사서함 연결 ID.
        get_token: 토큰 제공자. 수집 코드는 토큰을 갱신하지 않는다.
        client: Graph 호출용 클라이언트.
        store: 원문·첨부 저장소.

    Returns:
        SyncResult: 성공 또는 기록된 실패. 연결 전·진행 중이면 ApplicationError를 낸다.
    """
    async with connection(settings) as conn:
        run_id, owner_user_id = await begin_run(conn, mailbox_id)
    result = SyncResult(run_id=run_id, status="RUNNING")
    try:
        token = await get_token(mailbox_id=mailbox_id, owner_user_id=owner_user_id)
        if not token.get_secret_value():
            raise ApplicationError(
                503, "MAILBOX_TOKEN_UNAVAILABLE", "Mailbox authentication is unavailable."
            )
        graph = GraphMail(client, token)
        for folder in FOLDERS:
            await sync_folder(settings, graph, store, result, mailbox_id, folder)
        result.status = "SUCCEEDED"
    except ApplicationError as error:
        result.status, result.error_code = "FAILED", error.code
    except GraphError as error:
        result.status, result.error_code = "FAILED", error.code
    except MailboxDisconnected:
        result.status, result.error_code = "FAILED", "MAILBOX_NOT_CONNECTED"
    except LeaseLost:
        # 다른 실행이 이미 LEASE_EXPIRED로 기록했다.
        result.status, result.error_code = "FAILED", "LEASE_EXPIRED"
        return result
    except Exception:
        # 기록에 실패해도 원래 오류를 전달한다. 남은 RUNNING은 lease 만료로 정리된다.
        result.status, result.error_code = "FAILED", "INTERNAL_ERROR"
        with contextlib.suppress(Exception):
            await finish_run(settings, result)
        raise
    await finish_run(settings, result)
    return result


if TYPE_CHECKING:

    def _provider_fits(provider: MailboxTokenProvider) -> MailboxTokenGetter:
        """KAN-81 토큰 제공자가 수집이 기대하는 호출 형태와 맞는지 mypy가 확인한다."""
        return provider.get_mailbox_access_token
