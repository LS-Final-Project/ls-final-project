"""명시적으로 활성화한 기존 C/S·TFT JSON API만 운영 세션으로 중계한다."""

import asyncio
import hmac
import re
from collections.abc import Awaitable, Callable
from urllib.parse import urlencode, urlsplit

import requests
from fastapi import APIRouter, FastAPI, HTTPException, Request
from requests.exceptions import RequestException
from starlette.responses import Response

from backend.api.platform.assignees.routes import validate_owner
from backend.api.platform.auth.context import load_session_principal
from backend.api.platform.auth.http import CSRF_HEADER, SESSION_COOKIE, configuration, csrf_token
from backend.shared.access import PRINCIPAL_HEADER
from backend.shared.models import CUSTOMER_ID, EVIDENCE_ID
from backend.shared.service_auth import CALLER_HEADER, metadata_token

REQUEST_LIMIT = 1024 * 1024
RESPONSE_LIMIT = 8 * 1024 * 1024
UUID_PATH = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
# 파일이 지나가는 두 경로만 위 한도와 JSON 규칙의 예외다. 인증·CSRF·Origin 검사는 같다.
# 웹 frontend/server.mjs의 uploadPath·downloadPath와 같아야 한다.
FILE_LIMIT = 26 * 1024 * 1024
FILE_TYPES = frozenset(
    (
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
)
UPLOAD_PATH = re.compile(rf"/api/v1/tft/projects/{UUID_PATH}/documents")
DOWNLOAD_PATH = re.compile(
    rf"(?:/api/v1/tft/documents/{UUID_PATH}/file|/api/v1/tft/projects/{UUID_PATH}/draft/exports/{UUID_PATH}/file)"
)
# 디코드한 경로에서 검사한다. C/S 입력과 같은 고객 ID 규약이라 ?·#·/와 .·.. 세그먼트가
# upstream URL을 바꾸지 못한다.
CUSTOMER_PATH = CUSTOMER_ID
# 조건 비교 근거 ID. 같은 이유로 첫 글자는 영숫자다(.·.. 세그먼트 차단).
EVIDENCE_PATH = EVIDENCE_ID
# Department codes (backend/shared/departments.py): upper-case code, no path separators.
DEPT_PATH = r"[A-Z][A-Z0-9_]{0,63}"
# (경로 틀, 허용 메서드, 허용 query 키). 웹 frontend/server.mjs의 businessRoutes와 같아야 하며
# 두 검사가 docs/openapi/business-relay-routes.json과 각각 대조한다. 허용 query 키는 GET에만
# 적용한다. 변경 메서드의 query는 경로와 상관없이 400이다(relay()의 query 검사).
BUSINESS_ROUTES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("/api/v1/cs/tickets", ("GET", "POST"), ("limit", "cursor")),
    ("/api/v1/cs/tickets/{uuid}", ("GET", "PATCH"), ()),
    ("/api/v1/cs/tickets/{uuid}/audit", ("GET",), ()),
    ("/api/v1/cs/tickets/{uuid}/analysis", ("GET", "POST"), ()),
    ("/api/v1/cs/tickets/{uuid}/analysis/corrections", ("POST",), ()),
    ("/api/v1/cs/tickets/{uuid}/units", ("GET",), ()),
    ("/api/v1/cs/tickets/{uuid}/unit-history", ("GET",), ()),
    ("/api/v1/cs/tickets/{uuid}/units/{uuid}", ("PUT",), ()),
    ("/api/v1/cs/tickets/{uuid}/resolution-case", ("POST",), ()),
    ("/api/v1/cs/tickets/{uuid}/workflow", ("GET",), ()),
    ("/api/v1/cs/tickets/{uuid}/workflow/start-review", ("POST",), ()),
    ("/api/v1/cs/tickets/{uuid}/workflow/transfer", ("POST",), ()),
    ("/api/v1/cs/tickets/{uuid}/workflow/recall", ("POST",), ()),
    ("/api/v1/cs/tickets/{uuid}/workflow/return", ("POST",), ()),
    ("/api/v1/cs/tickets/{uuid}/workflow/dept-result", ("POST",), ()),
    ("/api/v1/cs/tickets/{uuid}/workflow/dept-results/{uuid}/review", ("POST",), ()),
    ("/api/v1/cs/tickets/{uuid}/workflow/close", ("POST",), ()),
    ("/api/v1/cs/tickets/{uuid}/workflow/owner", ("PUT",), ()),
    ("/api/v1/cs/tickets/{uuid}/workflow/return-subtype", ("POST",), ()),
    ("/api/v1/cs/tickets/{uuid}/workflow/send", ("POST",), ()),
    ("/api/v1/cs/tickets/{uuid}/workflow/sends/{uuid}/refresh", ("POST",), ()),
    ("/api/v1/cs/tickets/{uuid}/workflow/sends/{uuid}/retry", ("POST",), ()),
    ("/api/v1/cs/stats/overview", ("GET",), ("start", "end", "dept", "customer")),
    ("/api/v1/cs/stats/owners", ("GET",), ("start", "end", "dept")),
    ("/api/v1/cs/stats/customers/{customer}", ("GET",), ("start", "end")),
    ("/api/v1/cs/improvement-requests", ("GET", "POST"), ()),
    ("/api/v1/cs/improvement-requests/{uuid}", ("GET", "PATCH"), ()),
    ("/api/v1/cs/improvement-requests/{uuid}/versions", ("GET",), ()),
    ("/api/v1/cs/improvement-requests/{uuid}/evidence", ("POST",), ()),
    ("/api/v1/cs/improvement-requests/{uuid}/evidence/{uuid}/remove", ("POST",), ()),
    ("/api/v1/cs/improvement-requests/{uuid}/owner", ("PUT",), ()),
    ("/api/v1/cs/improvement-requests/{uuid}/review", ("POST",), ()),
    ("/api/v1/cs/improvement-requests/{uuid}/send", ("POST",), ()),
    ("/api/v1/cs/improvement-requests/{uuid}/departments/{dept}/acknowledge", ("POST",), ()),
    ("/api/v1/cs/improvement-requests/{uuid}/departments/{dept}/reply", ("POST",), ()),
    ("/api/v1/cs/improvement-requests/{uuid}/departments/{dept}/action", ("POST",), ()),
    ("/api/v1/cs/improvement-requests/{uuid}/departments/{dept}/resend", ("POST",), ()),
    ("/api/v1/cs/improvement-requests/{uuid}/departments/{dept}/review-rejection", ("POST",), ()),
    ("/api/v1/cs/improvement-requests/{uuid}/followup-plan", ("POST",), ()),
    ("/api/v1/cs/improvement-requests/{uuid}/complete", ("POST",), ()),
    ("/api/v1/cs/improvement-requests/{uuid}/followup-signals", ("GET",), ("as_of",)),
    ("/api/v1/cs/improvement-requests/{uuid}/rounds", ("POST",), ()),
    ("/api/v1/cs/improvement-requests/{uuid}/condition-comparison", ("GET", "POST"), ("version",)),
    ("/api/v1/cs/improvement-requests/{uuid}/condition-comparison/judgements", ("POST",), ()),
    (
        "/api/v1/cs/improvement-requests/{uuid}/condition-comparison/evidence/{evidence}/remove",
        ("POST",),
        (),
    ),
    ("/api/v1/cs/condition-comparisons", ("GET",), ("product_code", "fault_id", "version")),
    (
        "/api/v1/tft/projects",
        ("GET", "POST"),
        ("limit", "cursor", "output_status", "judgement_status", "q"),
    ),
    ("/api/v1/tft/projects/{uuid}", ("GET", "PATCH"), ()),
    ("/api/v1/tft/projects/{uuid}/assignment", ("PATCH",), ()),
    ("/api/v1/tft/projects/{uuid}/documents", ("GET", "POST"), ()),
    ("/api/v1/tft/documents/{uuid}", ("GET",), ()),
    ("/api/v1/tft/documents/{uuid}/file", ("GET",), ()),
    ("/api/v1/tft/projects/{uuid}/draft", ("GET", "PATCH"), ("version",)),
    ("/api/v1/tft/projects/{uuid}/draft/rewrite", ("POST",), ()),
    ("/api/v1/tft/projects/{uuid}/draft/review-versions", ("POST",), ()),
    ("/api/v1/tft/projects/{uuid}/draft/exports", ("POST",), ()),
    ("/api/v1/tft/projects/{uuid}/draft/exports/{uuid}/file", ("GET",), ()),
    ("/api/v1/tft/documents/{uuid}/clauses", ("GET",), ("parent", "limit", "cursor")),
    ("/api/v1/tft/projects/{uuid}/process", ("POST",), ()),
    (
        "/api/v1/tft/projects/{uuid}/requirements",
        ("GET",),
        (
            "limit",
            "cursor",
            "document_version_id",
            "equipment",
            "task",
            "necessity",
            "status",
            "needs_rereview",
            "has_open_question",
            "next_owner_field",
            "unresolved_mandatory",
        ),
    ),
    ("/api/v1/tft/requirements/{uuid}", ("GET",), ()),
    ("/api/v1/tft/requirements/{uuid}/comparison", ("GET",), ()),
    ("/api/v1/tft/requirements/{uuid}/supplement-options", ("GET",), ()),
    ("/api/v1/tft/requirements/{uuid}/supplement-selection", ("POST",), ()),
    ("/api/v1/tft/projects/{uuid}/partner-candidates", ("GET",), ("requirement_id",)),
    (
        "/api/v1/tft/projects/{uuid}/questions",
        ("GET", "POST"),
        ("status", "requirement_id", "target", "target_dept"),
    ),
    ("/api/v1/tft/projects/{uuid}/questions/{uuid}/confirm", ("POST",), ()),
    ("/api/v1/tft/questions/{uuid}/reply", ("POST",), ()),
    (
        "/api/v1/tft/projects/{uuid}/proposals",
        ("GET", "POST"),
        ("status", "origin", "requirement_id"),
    ),
    ("/api/v1/tft/proposals/{uuid}/approve", ("POST",), ()),
    ("/api/v1/tft/proposals/{uuid}/reject", ("POST",), ()),
)
_PLACEHOLDERS = {
    "{uuid}": UUID_PATH,
    "{customer}": CUSTOMER_PATH,
    "{evidence}": EVIDENCE_PATH,
    "{dept}": DEPT_PATH,
}
# 분리 정규식은 _PLACEHOLDERS 키에서 만든다. 손으로 쓴 목록에 {dept}가 빠져 부서 경로 5개가 늘 404였다(KAN-116).
_SPLIT = re.compile("(" + "|".join(re.escape(key) for key in _PLACEHOLDERS) + ")")
_COMPILED = [
    (
        re.compile(
            "".join(_PLACEHOLDERS.get(part, re.escape(part)) for part in _SPLIT.split(template))
        ),
        set(methods),
        set(query),
    )
    for template, methods, query in BUSINESS_ROUTES
]
router = APIRouter()


def _route(path: str) -> tuple[set[str], set[str]]:
    """허용표에서 경로와 정확히 일치하는 항목의 메서드·query 키를 찾는다.

    Args:
        path: 디코딩된 절대 요청 경로.

    Returns:
        tuple[set[str], set[str]]: 허용 메서드와 query 키. 없으면 빈 집합 두 개.
    """
    for pattern, methods, query in _COMPILED:
        if pattern.fullmatch(path):
            return methods, query
    return set(), set()


def allowed_methods(path: str) -> set[str]:
    """이미 존재하는 업무 경로의 허용 메서드만 반환한다.

    Args:
        path: 디코딩된 절대 요청 경로.

    Returns:
        set[str]: 허용 메서드. 업무 경로가 아니면 빈 집합.
    """
    return _route(path)[0]


def allowed_query(path: str) -> set[str]:
    """허용된 업무 경로가 받을 수 있는 query 키만 반환한다.

    Args:
        path: 디코딩된 절대 요청 경로.

    Returns:
        set[str]: 허용 query 키. 빈 집합이면 query를 받지 않는다.
    """
    return _route(path)[1]


def forward(method: str, url: str, headers: dict[str, str], body: bytes) -> Response:
    """재시도·리디렉션 없이 고정 업무 서비스의 제한된 JSON 응답을 전달한다.

    Args:
        method: 허용 목록 검사를 마친 HTTP 메서드.
        url: 고정 upstream과 검증된 경로·query를 결합한 주소.
        headers: 플랫폼이 새로 만든 인증·사용자 정보와 허용된 요청 헤더.
        body: 크기 검사를 마친 요청 본문.

    Returns:
        Response: 업무 서비스의 상태·JSON·판본·요청 ID를 담은 캐시 금지 응답.

    Raises:
        HTTPException: 네트워크·리디렉션·응답 형식/크기 위반이면 503.
    """
    try:
        with requests.request(
            method,
            url,
            headers=headers,
            data=body,
            timeout=(3, 20),
            allow_redirects=False,
            stream=True,
        ) as upstream:
            content_type = upstream.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            is_file = (
                method == "GET"
                and upstream.status_code == 200
                and content_type in FILE_TYPES
                and DOWNLOAD_PATH.fullmatch(urlsplit(url).path) is not None
            )
            if 300 <= upstream.status_code < 400 or (
                upstream.status_code != 204 and content_type != "application/json" and not is_file
            ):
                raise HTTPException(503)
            chunks = []
            size = 0
            for chunk in upstream.iter_content(65536):
                size += len(chunk)
                if size > (FILE_LIMIT if is_file else RESPONSE_LIMIT):
                    raise HTTPException(503)
                chunks.append(chunk)
            outgoing = {"Cache-Control": "no-store", "Content-Type": "application/json"}
            if is_file:
                disposition = upstream.headers.get("Content-Disposition", "")
                outgoing |= {
                    "Content-Type": content_type,
                    "Content-Disposition": disposition
                    if disposition.startswith("attachment;")
                    else "attachment",
                    "X-Content-Type-Options": "nosniff",
                }
            for name in ("ETag", "X-Request-ID"):
                if name in upstream.headers:
                    outgoing[name] = upstream.headers[name]
            return Response(b"".join(chunks), status_code=upstream.status_code, headers=outgoing)
    except RequestException:
        raise HTTPException(503) from None


def configure_relay(app: FastAPI) -> None:
    """업무 중계의 실제 네트워크 경계를 연결하고 활성화 전 인증 설정을 확인한다.

    Args:
        app: Microsoft 로그인 설정을 이미 준비한 플랫폼 앱.

    Returns:
        None: 실제 구현 또는 테스트용 fake로 교체할 수 있는 전송 경계를 등록한다.
    """
    if app.state.settings.business_relay_enabled and not app.state.microsoft_auth.enabled:
        raise ValueError("Business relay requires Microsoft operational authentication")
    app.state.business_token_provider = metadata_token
    app.state.business_forwarder = forward

    @app.middleware("http")
    async def protect_business_response(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """업무 중계의 성공·오류 응답을 모두 캐시에서 제외한다.

        Args:
            request: 플랫폼에 도착한 요청.
            call_next: 기존 인증·오류 처리와 라우터를 실행할 다음 처리기.

        Returns:
            Response: 업무 응답에 캐시 금지를 추가한 결과.
        """
        response = await call_next(request)
        if request.url.path.startswith(("/api/v1/cs/", "/api/v1/tft/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(router)


@router.api_route(
    "/api/v1/cs/{path:path}", methods=["GET", "POST", "PATCH", "PUT"], include_in_schema=False
)
@router.api_route(
    "/api/v1/tft/{path:path}", methods=["GET", "POST", "PATCH"], include_in_schema=False
)
async def relay(request: Request, path: str) -> Response:
    """운영 세션에서 만든 사용자 정보와 플랫폼 토큰만 고정 업무 서비스로 전달한다.

    Args:
        request: 웹 중계를 통해 도착한 세션 쿠키·Origin·CSRF와 업무 요청.
        path: 경로 매개변수. 임의 URL이나 upstream 선택으로 사용하지 않는다.

    Returns:
        Response: 호출한 업무 API의 응답. 세션·내부 토큰·Set-Cookie는 전달하지 않는다.
    """
    settings = request.app.state.settings
    if not settings.business_relay_enabled:
        raise HTTPException(404)
    # request.url.path는 디코드된 ?를 query로 다시 잘라 낼 수 있어 scope의 경로를 쓴다.
    path = request.scope["path"]
    methods = allowed_methods(path)
    if not methods:
        raise HTTPException(404)
    if request.method not in methods:
        raise HTTPException(405)
    pairs = request.query_params.multi_items()
    keys = allowed_query(path)
    if pairs and (
        request.method != "GET"
        or not keys
        or len(pairs) != len(dict(pairs))
        or any(k not in keys or len(v) > 200 for k, v in pairs)
    ):
        raise HTTPException(400)
    auth = configuration(request)
    origin = request.headers.get("Origin")
    if origin and origin != auth.app_origin.rstrip("/"):
        raise HTTPException(403)
    token = request.cookies.get(SESSION_COOKIE)
    upload = request.method == "POST" and UPLOAD_PATH.fullmatch(path) is not None
    if request.method != "GET":
        if origin != auth.app_origin.rstrip("/"):
            raise HTTPException(403)
        if token and not hmac.compare_digest(
            request.headers.get(CSRF_HEADER, "").encode(), csrf_token(token).encode()
        ):
            raise HTTPException(403)
        if request.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != (
            "multipart/form-data" if upload else "application/json"
        ):
            raise HTTPException(415)
    principal = await load_session_principal(settings, token)
    raw_principal = principal.model_dump_json()
    if len(raw_principal.encode()) > 8192:
        raise HTTPException(503)
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > (FILE_LIMIT if upload else REQUEST_LIMIT):
            raise HTTPException(413)
        chunks.append(chunk)
    body = b"".join(chunks)
    if request.method == "PUT" and re.fullmatch(
        rf"/api/v1/cs/(?:tickets/{UUID_PATH}/workflow|improvement-requests/{UUID_PATH})/owner",
        path,
    ):
        await validate_owner(settings, principal, body)
    target = (
        settings.business_cs_url if path.startswith("/api/v1/cs/") else settings.business_tft_url
    )
    caller = await asyncio.to_thread(request.app.state.business_token_provider, target)
    outgoing = {
        "Authorization": "Bearer " + caller,
        CALLER_HEADER: caller,
        PRINCIPAL_HEADER: raw_principal,
        "X-Request-ID": str(request.state.request_id),
        "Accept": "application/json",
    }
    for name in ("If-Match", "Idempotency-Key"):
        if name in request.headers:
            outgoing[name] = request.headers[name]
    if request.method != "GET":
        # 경계값이 든 원래 형식을 그대로 넘긴다. 위에서 multipart/form-data임을 확인했다.
        outgoing["Content-Type"] = request.headers["Content-Type"] if upload else "application/json"
    query = "?" + urlencode(pairs) if pairs else ""
    return await asyncio.to_thread(
        request.app.state.business_forwarder,
        request.method,
        target + path + query,
        outgoing,
        body,
    )
