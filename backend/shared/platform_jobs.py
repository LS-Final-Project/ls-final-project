"""서비스 → 플랫폼 내부 API 호출(Google ID 토큰, 오류 분류). tft 작업 접수(KAN-199·KAN-188)가 쓴다.

C/S의 `backend/api/cs/workflow/mail.PlatformMail`과 같은 동작이다. tft 이미지에는 `backend/api/cs`가 없어 그 모듈을
import할 수 없으므로 여기 둔다. C/S를 이 모듈로 옮기는 것은 후속이다(그때까지 두 곳이 같은 규칙을 가진다).
"""

import asyncio
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException


class PlatformUnreachable(Exception):
    """연결 실패: 플랫폼이 요청을 받지 못했다."""


class PlatformUnknown(Exception):
    """요청이 닿았을 수 있지만 쓸 수 있는 답이 없다."""


class PlatformRejected(Exception):
    """플랫폼이 오류 코드로 거절했다. 아무것도 저장하지 않았다."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def is_https_origin(url: str) -> bool:
    """경로·질의·사용자 정보 없는 HTTPS origin인지(ID 토큰 audience로 쓴다).

    Args:
        url: 설정 값.

    Returns:
        bool: 맞으면 True.
    """
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
        and parsed.username is None
        and not any(c.isspace() for c in url)
    )


class PlatformClient:
    """실행 계정 ID 토큰으로 플랫폼 내부 API를 부른다."""

    # 저장하지 않고 거절했음을 뜻하는 5xx 오류 코드. 그 밖의 5xx는 결과를 모르는 것으로 본다.
    known_server_refusals: frozenset[str] = frozenset()

    def __init__(
        self,
        base_url: str,
        token_provider: Callable[[str], str],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url
        self.token_provider = token_provider
        self.transport = transport

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST하고 응답 `data`를 돌려준다.

        Args:
            path: 플랫폼 내부 경로.
            body: JSON 본문.

        Returns:
            dict: 응답 data. 연결 실패는 PlatformUnreachable, 답을 모르면 PlatformUnknown, 거절은 PlatformRejected.
        """
        try:
            token = await asyncio.to_thread(self.token_provider, self.base_url)
        except HTTPException:
            raise PlatformUnreachable() from None
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(20, connect=5),
                follow_redirects=False,
                transport=self.transport,
            ) as http:
                response = await http.post(self.base_url + path, json=body, headers=headers)
        except httpx.ConnectError:
            raise PlatformUnreachable() from None
        except httpx.HTTPError:
            raise PlatformUnknown() from None
        try:
            payload = response.json()
        except ValueError:
            raise PlatformUnknown() from None
        error = payload.get("error") if isinstance(payload, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        if response.status_code >= 500:
            if code in self.known_server_refusals:
                raise PlatformRejected(response.status_code, code)
            raise PlatformUnknown()
        if response.status_code >= 400:
            raise PlatformRejected(response.status_code, str(code or "PLATFORM_ERROR")[:64])
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise PlatformUnknown()
        return payload["data"]
