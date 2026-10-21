# 플랫폼 인증 계약 시작점

원본: backend/api/platform/auth/schemas.py 및 routes.py.

GET /api/v1/platform/me는 현재 세션의 Identity를 반환한다. id, tid, oid, 표시 이메일과 이름, 부서 및 역할을 DB에서 조회한다. 쿠키에는 무작위 세션 ID만 있고 DB에는 해당 값의 hash를 저장한다. 요청마다 사용자 활성 상태와 역할을 다시 읽는다. 부서 역할은 부서가 없으면 거부한다. manager 역할이 프로젝트 책임자나 원문 열람 범위를 자동 부여하지 않는다.

현재 세션 구현은 로컬 개발용이다. production에서는 개발 쿠키와 임의 역할 헤더를 인증으로 받지 않으며 Microsoft 서버 로그인 구현 전에는 401을 반환한다. production MSAL, 토큰 저장, CSRF와 사용자 맥락 중계는 후속 기능이다.

로컬에서 DEV_AUTH_ENABLED=true일 때만 제공하는 경로:

| 경로 | 동작 |
| --- | --- |
| GET /api/v1/platform/dev/accounts | DB에 미리 생성한 활성 사용자 목록 |
| POST /api/v1/platform/dev/session | user_id 한 개로 선택. 등록되지 않은 ID 및 클라이언트 역할 지정 거부 |
| DELETE /api/v1/platform/dev/session | DB 세션 무효화 및 쿠키 삭제 |

브라우저의 변경 요청은 localhost:5173 및 127.0.0.1:5173 Origin만 허용한다. 쿠키는 HttpOnly, SameSite=Lax이며 HTTP 로컬 개발이므로 Secure는 사용하지 않는다. 개발 경로는 일반 계약 산출물에 포함하지 않으며 로컬 Swagger에서 확인한다.

수정 후 `python3 scripts/workspace.py contracts platform`을 실행한다.
