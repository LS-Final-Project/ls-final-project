"""KAN-71 Graph 읽기: 다음 주소 출처 확인, 실패 분류, 크기 상한. 가짜 HTTP만 쓴다."""

import asyncio

import httpx
import pytest
from pydantic import SecretStr

from backend.api.platform.mail.graph import (
    MAX_OBJECT_BYTES,
    GraphError,
    GraphMail,
    checked_link,
    initial_delta_url,
)

TOKEN = "synthetic-token-value"


def call(handler, action):
    """가짜 전송으로 GraphMail 동작 하나를 실행한다.

    Args:
        handler: httpx MockTransport 처리 함수.
        action: GraphMail을 받아 코루틴을 돌려주는 함수.

    Returns:
        Any: 동작 결과.
    """

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await action(GraphMail(client, SecretStr(TOKEN)))

    return asyncio.run(run())


@pytest.mark.parametrize(
    "link",
    [
        "http://graph.microsoft.com/v1.0/me/messages",
        "https://graph.microsoft.com.example.invalid/v1.0/me/messages",
        "https://example.invalid/v1.0/me/messages",
        "https://graph.microsoft.com/beta/me/messages",
        "https://user@graph.microsoft.com/v1.0/me/messages",
    ],
)
def test_links_outside_graph_are_rejected(link):
    with pytest.raises(GraphError) as error:
        checked_link(link)
    assert error.value.code == "GRAPH_LINK_REJECTED"


def test_next_link_to_another_host_is_not_followed():
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(
            200, json={"value": [], "@odata.nextLink": "https://example.invalid/v1.0/me/x"}
        )

    with pytest.raises(GraphError) as error:
        call(handler, lambda graph: graph.delta_page(initial_delta_url("inbox")))
    assert error.value.code == "GRAPH_LINK_REJECTED"
    assert seen == ["graph.microsoft.com"]


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (401, {}, "GRAPH_UNAUTHORIZED"),
        (403, {}, "GRAPH_FORBIDDEN"),
        (404, {}, "GRAPH_NOT_FOUND"),
        (410, {}, "GRAPH_SYNC_RESET"),
        (400, {"error": {"code": "syncStateNotFound"}}, "GRAPH_SYNC_RESET"),
        (400, {"error": {"code": "BadRequest", "message": TOKEN}}, "GRAPH_BAD_REQUEST"),
        (429, {}, "GRAPH_THROTTLED"),
        (503, {}, "GRAPH_UNAVAILABLE"),
    ],
)
def test_failures_become_fixed_codes(status, body, code):
    with pytest.raises(GraphError) as error:
        call(
            lambda request: httpx.Response(status, json=body),
            lambda graph: graph.delta_page(initial_delta_url("inbox")),
        )
    assert error.value.code == code
    assert TOKEN not in str(error.value) and TOKEN not in repr(error.value)


def test_network_failure_hides_the_request():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(GraphError) as error:
        call(handler, lambda graph: graph.message_mime("id-1"))
    assert error.value.code == "GRAPH_UNAVAILABLE"
    assert error.value.__cause__ is None


def test_requests_carry_the_token_and_immutable_ids():
    headers = {}

    def handler(request):
        headers.update(request.headers)
        assert request.url.raw_path == b"/v1.0/me/messages/AAMk%2Fx%3D/$value"
        return httpx.Response(200, content=b"MIME")

    assert call(handler, lambda graph: graph.message_mime("AAMk/x=")) == b"MIME"
    assert headers["authorization"] == f"Bearer {TOKEN}"
    assert 'IdType="ImmutableId"' in headers["prefer"]


def test_oversized_objects_stop_reading():
    def handler(request):
        return httpx.Response(200, content=b"x" * (MAX_OBJECT_BYTES + 1))

    with pytest.raises(GraphError) as error:
        call(handler, lambda graph: graph.message_mime("id-1"))
    assert error.value.code == "MAIL_OBJECT_TOO_LARGE"


@pytest.mark.parametrize(
    "item",
    [
        {"id": "m1"},
        {"id": "m1", "receivedDateTime": "2026-10-27T01:00:00"},
        {"id": "", "receivedDateTime": "2026-10-27T01:00:00Z"},
        {"id": "m1", "receivedDateTime": "2026-10-27T01:00:00Z", "hasAttachments": "yes"},
    ],
)
def test_messages_need_an_id_and_a_zoned_receipt_time(item):
    def handler(request):
        return httpx.Response(200, json={"value": [item], "@odata.deltaLink": str(request.url)})

    with pytest.raises(GraphError) as error:
        call(handler, lambda graph: graph.delta_page(initial_delta_url("inbox")))
    assert error.value.code == "GRAPH_BAD_RESPONSE"


def test_page_needs_exactly_one_next_position():
    def handler(request):
        return httpx.Response(200, json={"value": []})

    with pytest.raises(GraphError) as error:
        call(handler, lambda graph: graph.delta_page(initial_delta_url("inbox")))
    assert error.value.code == "GRAPH_BAD_RESPONSE"
