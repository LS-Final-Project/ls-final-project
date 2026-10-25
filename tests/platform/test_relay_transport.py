"""고정 업무 upstream의 HTTP 실패·응답 제한과 안전한 상태 전달을 확인한다."""

from unittest.mock import MagicMock, Mock

import pytest
import requests
from fastapi import HTTPException

from backend.api.platform.relay import forward


def response(status=200, content_type="application/json", body=b"{}"):
    """requests 스트리밍 응답의 외부 HTTP 경계만 대체한다.

    Args:
        status: 재현할 HTTP 상태.
        content_type: 응답 본문 형식.
        body: 응답 본문 바이트.

    Returns:
        MagicMock: 크기·형식·전송 검증에 사용할 응답 컨텍스트.
    """
    upstream = MagicMock(
        status_code=status,
        headers={
            "Content-Type": content_type,
            "ETag": '"4"',
            "X-Request-ID": "synthetic",
            "Set-Cookie": "secret",
            "Authorization": "secret",
        },
    )
    upstream.__enter__.return_value = upstream
    upstream.iter_content.return_value = iter([body])
    return upstream


@pytest.mark.parametrize("status", [200, 201, 401, 403, 404, 409, 412, 422, 503])
def test_business_status_json_and_allowed_headers_are_preserved(monkeypatch, status):
    """업무 실패를 성공으로 바꾸지 않고 상태·본문·판본과 요청 ID만 전달한다.

    Args:
        monkeypatch: HTTP 통신 대체 도구.
        status: 유지해야 할 업무 또는 의존성 상태.

    Returns:
        None: 재시도·이동 금지, 타임아웃과 민감 응답 헤더 제거를 확인한다.
    """
    sender = Mock(return_value=response(status))
    monkeypatch.setattr(requests, "request", sender)
    result = forward(
        "GET",
        "https://cs.example.invalid/api/v1/cs/tickets",
        {"Authorization": "Bearer synthetic"},
        b"",
    )
    assert result.status_code == status and result.body == b"{}"
    assert result.headers["etag"] == '"4"' and result.headers["x-request-id"] == "synthetic"
    assert result.headers["cache-control"] == "no-store"
    assert "set-cookie" not in result.headers and "authorization" not in result.headers
    assert sender.call_count == 1
    assert sender.call_args.kwargs["allow_redirects"] is False
    assert sender.call_args.kwargs["timeout"] == (3, 20)
    assert sender.return_value.__exit__.called


@pytest.mark.parametrize("failure", ["redirect", "html", "oversize", "timeout"])
def test_downstream_failure_is_safe_and_not_retried(monkeypatch, failure):
    """리디렉션·형식·크기·통신 실패를 원문 비노출 503으로 처리한다.

    Args:
        monkeypatch: 외부 통신 대체 도구.
        failure: 업무 upstream에서 재현할 실패.

    Returns:
        None: 공급자 원문과 인증 정보 비노출·재시도 없음·응답 정리를 확인한다.
    """
    upstream = response(body=b"private-provider-secret")
    if failure == "redirect":
        upstream.status_code = 302
    elif failure == "html":
        upstream.headers["Content-Type"] = "text/html"
    elif failure == "oversize":
        upstream.iter_content.return_value = iter([b"x" * (8 * 1024 * 1024 + 1)])
    sender = (
        Mock(side_effect=requests.Timeout("private-provider-secret"))
        if failure == "timeout"
        else Mock(return_value=upstream)
    )
    monkeypatch.setattr(requests, "request", sender)
    with pytest.raises(HTTPException) as result:
        forward("POST", "https://cs.example.invalid/api/v1/cs/tickets", {}, b"{}")
    assert result.value.status_code == 503 and "private-provider-secret" not in str(result.value)
    assert sender.call_count == 1
    if failure != "timeout":
        assert upstream.__exit__.called
