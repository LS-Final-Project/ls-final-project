"""Microsoft Graph 메일 읽기. 토큰은 요청 헤더에만 쓰고 기록하지 않는다.

delta 조회로 받은편지함·보낸편지함의 변경을 페이지 단위로 받고, 원문은 MIME(`$value`)으로,
첨부는 목록과 각 첨부의 `$value`로 받는다. 모든 요청에 ImmutableId를 요청해 폴더 이동에도
메시지 ID가 바뀌지 않게 한다.
"""

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from urllib.parse import quote, urlsplit

import httpx
from pydantic import SecretStr

GRAPH_HOST = "graph.microsoft.com"
GRAPH_BASE = f"https://{GRAPH_HOST}/v1.0"
Folder = Literal["inbox", "sentitems"]
FOLDERS: tuple[Folder, ...] = ("inbox", "sentitems")
PAGE_SIZE = 50
# Outlook.com 메시지 크기 상한(약 35MB)보다 크게 두고, 넘는 응답은 끝까지 읽지 않는다.
MAX_OBJECT_BYTES = 50 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024
ERROR_BODY_BYTES = 64 * 1024
MESSAGE_FIELDS = (
    "id,internetMessageId,conversationId,subject,from,receivedDateTime,sentDateTime,hasAttachments"
)
ATTACHMENT_KINDS = {
    "#microsoft.graph.fileAttachment": "file",
    "#microsoft.graph.itemAttachment": "item",
    "#microsoft.graph.referenceAttachment": "reference",
}
SYNC_RESET_CODES = {"syncStateNotFound", "resyncRequired", "SyncStateNotFound"}
TokenRefresher = Callable[[], Awaitable[SecretStr]]


class GraphError(Exception):
    """원시 응답 대신 고정 코드만 전달하는 Graph 실패."""

    def __init__(self, code: str) -> None:
        """실패 코드를 지정한다.

        Args:
            code: 실행 기록에 남길 고정 코드.

        Returns:
            None.
        """
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class MessageMeta:
    """delta 항목에서 읽은 메시지 속성."""

    graph_id: str
    internet_message_id: str | None
    conversation_id: str | None
    subject: str | None
    from_address: str | None
    received_at: datetime
    sent_at: datetime | None
    has_attachments: bool


@dataclass(frozen=True)
class DeltaPage:
    """delta 한 페이지. 마지막 페이지에만 delta_link가 있다."""

    messages: list[MessageMeta]
    removed: list[str]
    next_link: str | None
    delta_link: str | None


@dataclass(frozen=True)
class AttachmentMeta:
    """첨부 목록의 한 항목."""

    graph_id: str
    kind: Literal["file", "item", "reference"]
    name: str | None
    content_type: str | None
    is_inline: bool


def initial_delta_url(folder: Folder) -> str:
    """커서가 없을 때 처음부터 받는 delta 주소.

    Args:
        folder: 잘 알려진 폴더 이름.

    Returns:
        str: 필요한 속성만 고른 delta 주소.
    """
    return f"{GRAPH_BASE}/me/mailFolders/{folder}/messages/delta?$select={MESSAGE_FIELDS}"


def checked_link(value: Any) -> str | None:
    """Graph가 준 다음 주소가 같은 Graph 출처인지 확인한다.

    Args:
        value: 응답의 `@odata.nextLink` 또는 `@odata.deltaLink`.

    Returns:
        str | None: 확인한 주소. 없으면 None.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise GraphError("GRAPH_BAD_RESPONSE")
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or parts.netloc != GRAPH_HOST
        or not parts.path.startswith("/v1.0/me/")
        or parts.fragment
    ):
        # 다른 호스트로 토큰을 보내지 않는다.
        raise GraphError("GRAPH_LINK_REJECTED")
    return value


def parse_time(value: Any, required: bool) -> datetime | None:
    """시간대가 있는 ISO 시각을 읽는다.

    Args:
        value: Graph 응답 값.
        required: 값이 꼭 있어야 하는지.

    Returns:
        datetime | None: 시간대가 있는 시각.
    """
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise GraphError("GRAPH_BAD_RESPONSE")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise GraphError("GRAPH_BAD_RESPONSE") from None
    if parsed.tzinfo is None:
        raise GraphError("GRAPH_BAD_RESPONSE")
    return parsed


def optional_text(value: Any) -> str | None:
    """선택 문자열 속성을 읽는다.

    Args:
        value: Graph 응답 값.

    Returns:
        str | None: 문자열 또는 없음.
    """
    if value is None or isinstance(value, str):
        return value
    raise GraphError("GRAPH_BAD_RESPONSE")


def parse_message(item: Mapping[str, Any]) -> MessageMeta:
    """delta 항목을 메시지 속성으로 바꾼다.

    Args:
        item: `@removed`가 없는 delta 항목.

    Returns:
        MessageMeta: 수신 시각이 필수인 메시지 속성.
    """
    graph_id = item.get("id")
    if not isinstance(graph_id, str) or not graph_id:
        raise GraphError("GRAPH_BAD_RESPONSE")
    sender = item.get("from")
    address = None
    if sender is not None:
        if not isinstance(sender, dict) or not isinstance(sender.get("emailAddress"), dict):
            raise GraphError("GRAPH_BAD_RESPONSE")
        address = optional_text(sender["emailAddress"].get("address"))
    has_attachments = item.get("hasAttachments", False)
    if not isinstance(has_attachments, bool):
        raise GraphError("GRAPH_BAD_RESPONSE")
    received_at = parse_time(item.get("receivedDateTime"), required=True)
    assert received_at is not None
    return MessageMeta(
        graph_id=graph_id,
        internet_message_id=optional_text(item.get("internetMessageId")),
        conversation_id=optional_text(item.get("conversationId")),
        subject=optional_text(item.get("subject")),
        from_address=address,
        received_at=received_at,
        sent_at=parse_time(item.get("sentDateTime"), required=False),
        has_attachments=has_attachments,
    )


def failure_code(response: httpx.Response, body: bytes) -> str:
    """응답 상태를 고정 실패 코드로 분류한다.

    Args:
        response: 성공하지 않은 응답.
        body: 이미 읽은 본문 일부.

    Returns:
        str: 원시 오류 설명을 담지 않는 코드.
    """
    status = response.status_code
    if status == 410:
        return "GRAPH_SYNC_RESET"
    if status == 400:
        try:
            error = json.loads(body).get("error", {})
        except (ValueError, AttributeError):
            error = {}
        if isinstance(error, dict) and error.get("code") in SYNC_RESET_CODES:
            return "GRAPH_SYNC_RESET"
        return "GRAPH_BAD_REQUEST"
    return {
        401: "GRAPH_UNAUTHORIZED",
        403: "GRAPH_FORBIDDEN",
        404: "GRAPH_NOT_FOUND",
        429: "GRAPH_THROTTLED",
    }.get(status, "GRAPH_UNAVAILABLE" if status >= 500 else "GRAPH_BAD_RESPONSE")


class GraphMail:
    """한 사서함의 access token으로 메일을 읽는다.

    Graph가 401을 돌려주면 이 객체가 사는 동안(수집 실행 한 번) 단 한 번만 토큰을 새로 받아
    같은 요청을 다시 보낸다. 페이지·메시지마다 기회를 새로 주지 않아 401이 반복돼도 요청이
    늘어나지 않는다. 토큰 갱신 자체는 토큰 제공자가 한다.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        token: SecretStr,
        refresh: TokenRefresher | None = None,
    ) -> None:
        """HTTP 클라이언트와 토큰을 받는다.

        Args:
            client: 제한 시간이 설정된 클라이언트. 리디렉션은 따르지 않아야 한다.
            token: 토큰 제공자가 돌려준 유효한 access token.
            refresh: 캐시를 건너뛰고 새 토큰을 받는 함수. 없으면 401에서 바로 실패한다.

        Returns:
            None.
        """
        self.client = client
        self.token = token
        self.refresh = refresh

    async def get(self, url: str, limit: int = MAX_OBJECT_BYTES) -> bytes:
        """Graph 주소 하나를 받는다. 401이면 남은 갱신 기회로 한 번 다시 시도한다.

        Args:
            url: 확인한 Graph 주소.
            limit: 본문 바이트 상한.

        Returns:
            bytes: 성공 응답의 본문.
        """
        try:
            return await self.get_once(url, limit)
        except GraphError as error:
            if error.code != "GRAPH_UNAUTHORIZED" or self.refresh is None:
                raise
        # 기회를 먼저 없앤다. 갱신이 실패해도 이 실행에서 다시 갱신하지 않는다.
        refresh, self.refresh = self.refresh, None
        self.token = await refresh()
        return await self.get_once(url, limit)

    async def get_once(self, url: str, limit: int) -> bytes:
        """현재 토큰으로 Graph 주소 하나를 한 번 요청한다.

        Args:
            url: 확인한 Graph 주소.
            limit: 본문 바이트 상한.

        Returns:
            bytes: 성공 응답의 본문.
        """
        headers = {
            "Authorization": f"Bearer {self.token.get_secret_value()}",
            "Prefer": f'IdType="ImmutableId", odata.maxpagesize={PAGE_SIZE}',
        }
        try:
            async with self.client.stream(
                "GET", url, headers=headers, follow_redirects=False
            ) as response:
                ok = response.status_code == 200
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if ok and len(body) > limit:
                        raise GraphError("MAIL_OBJECT_TOO_LARGE")
                    if not ok and len(body) > ERROR_BODY_BYTES:
                        break
        except httpx.HTTPError:
            raise GraphError("GRAPH_UNAVAILABLE") from None
        if not ok:
            raise GraphError(failure_code(response, bytes(body[:ERROR_BODY_BYTES])))
        return bytes(body)

    async def get_json(self, url: str) -> dict[str, Any]:
        """JSON 객체 응답을 받는다.

        Args:
            url: 확인한 Graph 주소.

        Returns:
            dict[str, Any]: 응답 객체.
        """
        body = await self.get(url, limit=MAX_JSON_BYTES)
        try:
            value = json.loads(body)
        except ValueError:
            raise GraphError("GRAPH_BAD_RESPONSE") from None
        if not isinstance(value, dict):
            raise GraphError("GRAPH_BAD_RESPONSE")
        return value

    async def delta_page(self, url: str) -> DeltaPage:
        """delta 한 페이지를 받는다.

        Args:
            url: 처음 주소, 저장한 next_link 또는 delta_link.

        Returns:
            DeltaPage: 메시지·삭제 항목과 다음 위치.
        """
        page = await self.get_json(checked_link(url) or "")
        items = page.get("value")
        if not isinstance(items, list):
            raise GraphError("GRAPH_BAD_RESPONSE")
        messages, removed = [], []
        for item in items:
            if not isinstance(item, dict):
                raise GraphError("GRAPH_BAD_RESPONSE")
            if "@removed" in item:
                graph_id = item.get("id")
                if not isinstance(graph_id, str) or not graph_id:
                    raise GraphError("GRAPH_BAD_RESPONSE")
                removed.append(graph_id)
            else:
                messages.append(parse_message(item))
        next_link = checked_link(page.get("@odata.nextLink"))
        delta_link = checked_link(page.get("@odata.deltaLink"))
        if (next_link is None) == (delta_link is None):
            raise GraphError("GRAPH_BAD_RESPONSE")
        return DeltaPage(messages, removed, next_link, delta_link)

    def message_url(self, graph_id: str) -> str:
        """메시지 주소. ID는 경로 한 칸으로 인코딩한다.

        Args:
            graph_id: ImmutableId.

        Returns:
            str: 메시지 주소.
        """
        return f"{GRAPH_BASE}/me/messages/{quote(graph_id, safe='')}"

    async def message_mime(self, graph_id: str) -> bytes:
        """메시지 원문 MIME을 받는다.

        Args:
            graph_id: ImmutableId.

        Returns:
            bytes: 첨부를 포함한 원문.
        """
        return await self.get(self.message_url(graph_id) + "/$value")

    async def attachments(self, graph_id: str) -> list[AttachmentMeta]:
        """첨부 목록을 모든 페이지에 걸쳐 받는다.

        Args:
            graph_id: ImmutableId.

        Returns:
            list[AttachmentMeta]: 인라인 첨부를 포함한 목록.
        """
        url: str | None = (
            self.message_url(graph_id) + "/attachments?$select=id,name,contentType,isInline"
        )
        found = []
        while url:
            page = await self.get_json(url)
            items = page.get("value")
            if not isinstance(items, list):
                raise GraphError("GRAPH_BAD_RESPONSE")
            for item in items:
                if not isinstance(item, dict):
                    raise GraphError("GRAPH_BAD_RESPONSE")
                kind = ATTACHMENT_KINDS.get(item.get("@odata.type", ""))
                attachment_id = item.get("id")
                is_inline = item.get("isInline", False)
                if (
                    kind is None
                    or not isinstance(attachment_id, str)
                    or not attachment_id
                    or not isinstance(is_inline, bool)
                ):
                    raise GraphError("GRAPH_BAD_RESPONSE")
                found.append(
                    AttachmentMeta(
                        graph_id=attachment_id,
                        kind=kind,  # type: ignore[arg-type]
                        name=optional_text(item.get("name")),
                        content_type=optional_text(item.get("contentType")),
                        is_inline=is_inline,
                    )
                )
            url = checked_link(page.get("@odata.nextLink"))
        return found

    async def attachment_bytes(self, graph_id: str, attachment_id: str) -> bytes:
        """파일·항목 첨부의 원본 바이트를 받는다.

        Args:
            graph_id: 메시지 ImmutableId.
            attachment_id: 첨부 ID.

        Returns:
            bytes: 파일 바이트 또는 항목 첨부의 MIME.
        """
        return await self.get(
            f"{self.message_url(graph_id)}/attachments/{quote(attachment_id, safe='')}/$value"
        )
