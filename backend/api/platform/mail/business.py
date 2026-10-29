"""플랫폼 → C/S 내부 호출(KAN-110): 회신 통지와 메일 접수.

중계(`relay.py`)와 같은 신뢰 경계를 쓴다. 플랫폼 실행 계정의 ID 토큰(audience = C/S 서비스 URL)을
`Authorization`과 별도 호출자 헤더에 넣고, 사용자가 있는 호출에만 Principal 헤더를 더한다.
업무 중계가 꺼져 있으면(C/S 주소·토큰 없음) 호출하지 않는다.
"""

import asyncio
from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx
from fastapi import Request

from backend.shared.access import PRINCIPAL_HEADER, Principal
from backend.shared.service_auth import CALLER_HEADER

REPLY_PATH = "/internal/v1/cs/tickets/{ticket_id}/customer-reply"
FROM_MAIL_PATH = "/internal/v1/cs/tickets/from-mail"


class CsCaller:
    """C/S 내부 경로를 부르는 클라이언트. 리디렉션을 따르지 않고 응답 JSON만 돌려준다."""

    def __init__(
        self,
        base_url: str,
        token_provider: Callable[[str], str],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url
        self.token_provider = token_provider
        self.transport = transport

    async def post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        principal: Principal | None = None,
        request_id: UUID | None = None,
    ) -> httpx.Response:
        """JSON을 보내고 응답을 그대로 돌려준다. 연결·형식 오류는 httpx 예외로 올라간다.

        Args:
            path: C/S 내부 경로.
            body: JSON 본문.
            principal: 사용자가 있는 호출의 Principal(접수). 없으면 시스템 호출(회신 통지).
            request_id: 요청 ID 전달.

        Returns:
            httpx.Response: C/S 응답.
        """
        token = await asyncio.to_thread(self.token_provider, self.base_url)
        headers = {
            "Authorization": f"Bearer {token}",
            CALLER_HEADER: token,
            "Accept": "application/json",
        }
        if principal is not None:
            headers[PRINCIPAL_HEADER] = principal.model_dump_json()
        if request_id is not None:
            headers["X-Request-ID"] = str(request_id)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20, connect=5), follow_redirects=False, transport=self.transport
        ) as http:
            return await http.post(self.base_url + path, json=body, headers=headers)


def cs_caller(request: Request) -> CsCaller | None:
    """검사 주입이 있으면 그것, 업무 중계가 켜져 있으면 실제 클라이언트, 아니면 None."""
    override = getattr(request.app.state, "cs_caller", None)
    if override is not None:
        return override
    settings = request.app.state.settings
    if not settings.business_relay_enabled:
        return None
    return CsCaller(settings.business_cs_url, request.app.state.business_token_provider)
