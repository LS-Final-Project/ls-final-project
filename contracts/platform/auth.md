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

## KAN-77 callback 경계 초안

KAN-57의 화면·업무 상태·권한 행렬은 김종하·김나영과 확인한다.
아래 내부 경계는 합의 전 초안이며 공개 API 또는 통합 완료 계약이 아니다.

`backend/api/platform/auth/callback.py`의 `complete_callback()`은 서버가 보관한
인가 시도와 callback의 state, 목적, 브라우저 결속 및 만료를 대조한다. 중복 query
키, 공급자 오류, code 누락을 토큰 교환 전에 거부한다. 로그인과 사서함 연결은
서로의 callback으로 처리하지 않으며 사서함 연결은 시작한 기존 사용자 세션에
결속한다. 사서함의 Microsoft 식별자는 서비스 사용자와 별개로 다루며 이 함수가
서비스 로그인 계정을 바꾸거나 역할을 부여하지 않는다.

호출자는 브라우저 결속값을 쿠키 등으로 전달받고 서버의 인가 시도를 원자적으로
한 번만 소비해야 한다. MSAL flow의 nonce·PKCE verifier는 서버에만 보관한다.
`OAuthAdapter.redeem()`은 저장된 flow로 code를 교환하고 nonce·서명·issuer·audience·
유효기간·tid/oid를 검증한 뒤 `MicrosoftSubject(tid, oid)`만 반환하는 경계다.
MSAL의 flow 시작과 callback 교환은 [Microsoft 안내](https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens)를 따른다.
단순 JWT 디코딩이나 브라우저가 전달한 식별 결과를 인증으로 사용하지 않는다.

이번 초안에는 테스트 전용 fake 어댑터와 거부 회귀만 있다. 실제 MSAL 어댑터,
인가 시도 저장·원자적 소비 및 재사용 거부, 등록 사용자 조회와 기존 서버 세션 연결,
production 쿠키·CSRF·로그아웃, 토큰 보관은 후속 구현이다. 따라서 실제 토큰 검증이나
로그인 완료를 주장하지 않는다. 공개 라우터·OpenAPI·로컬 개발 세션은 변경하지 않는다.
