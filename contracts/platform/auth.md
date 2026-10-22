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

### 개인 Outlook용 MSAL 어댑터

`backend/api/platform/auth/microsoft.py`의 `PersonalMicrosoftOAuth`는 KAN-76에서
성공한 개인 계정 `consumers` authority와 시크릿 기반 서버 앱을 사용한다.
학교 테넌트 설정을 덮어쓰지 않으며 조직 계정의 자동 대체 경로는 제공하지 않는다.
생성자에는 기존 앱 ID·서버 시크릿·정확한 Web callback 주소를 명시적으로 전달한다.
환경 파일을 자동으로 읽거나 앱 등록을 변경하지 않는다. 이전 SPA/Web 혼용 문제를
피하려면 해당 서버 callback을 Entra의 Web 플랫폼에 등록해야 한다.

`begin()`은 MSAL의 state·nonce·PKCE 생성을 사용해 로그인 URL과 서버 전용 인가
시도를 반환한다. 서비스 로그인은 메일 권한을 추가하지 않고 사서함 연결만
Mail.Read·Mail.Send를 요청한다. openid·profile·offline_access는 MSAL이 추가한다.
브라우저에 반환할 값은 로그인 URL뿐이며 flow·시크릿·토큰을 응답에 포함하지 않는다.

`redeem()`은 저장된 callback 주소·state·nonce·verifier를 확인하고 MSAL로 code를
교환한다. 원시 ID 토큰은 개인 계정 전용 Microsoft JWKS의 공개키로 RS256 서명,
정확한 issuer·앱 audience·필수 클레임·유효기간·nonce·개인 계정 tid와 UUID oid를
검증한다. MSAL이 디코딩한 클레임만으로 인증하지 않는다. 공급자 연결 장애는 503,
잘못된 토큰은 401로 구분하며 오류 응답에는 원시 공급자 설명이나 토큰을 넣지 않는다.
ID 토큰의 발급·사용 시작·만료 시각에는 서버 간 시계 오차를 고려해 60초의 여유를
적용하며 이 범위를 벗어나면 거부한다. 이 여유는 인가 시도 만료나 일회 소비에는 적용하지 않는다.
토큰 캐시는 호출마다 새로 만들고 영속 저장하지 않는다. 반환값은 tid·oid뿐이며
사서함 토큰 연결과 업무 권한 부여를 완료한 결과가 아니다.

기존 개발 의존성인 MSAL을 실행 의존성으로 옮겼다. 직접 사용하는 PyJWT와 requests도
실행 의존성으로 명시했다. MSAL 1.39의 토큰 획득은 nonce를 확인하지만 ID 토큰의
서명·issuer·audience·유효기간 검증을 대신하지 않아 별도의 검증이 필요하다.
잠금 파일의 기존 패키지 버전은 유지한다.

합성 HTTP 응답과 실제 MSAL·RSA 서명 검증으로 회귀를 확인한다. 실제 Outlook을
사용한 이번 제품 어댑터 검증은 아직 수행하지 않았다. 등록 사용자 조회와 기존
서버 세션 연결, production 쿠키·CSRF·로그아웃,
토큰 보관은 후속 구현이다. 공개 라우터·OpenAPI·로컬 개발 세션은 변경하지 않는다.

### 서버 인가 시도 저장과 일회 소비

`attempts.py`의 `save_authorization()`은 인가 시도를 플랫폼 전용
`identity.oauth_authorizations`에 저장한다. state와 브라우저 결속값은 SHA-256으로
조회하며, MSAL flow의 nonce·PKCE verifier는 서버 DB에만 보관한다. 로그인 시도는
사용자 ID를 갖지 않고 사서함 연결 시도는 기존 서비스 사용자 ID에 결속한다.
업무 서비스인 cs_app·tft_app에는 해당 테이블 접근 권한이 없다.

`complete_stored_callback()`은 중복 키·공급자 오류·누락된 입력을 거부한 뒤
state·목적·브라우저·사용자·만료를 조건으로 `DELETE ... RETURNING`을 실행한다.
삭제를 커밋한 다음 기존 callback 검증과 MSAL 교환을 스레드에서 실행한다.
동시 요청에서는 한 건만 교환하며, 공급자 장애가 나도 이미 소비한 시도를 복구하지
않는다. 사용자는 새로운 인가 시도를 시작해야 한다. 잘못된 결속 요청은 정상 시도를
소비하지 않는다. 만료는 전달된 현재 시각과 DB의 실제 시각을 모두 검사한다.

마이그레이션 `20261023_KAN-77_oauth_authorizations.sql`은 기존 KAN-78의 users 테이블
생성 다음에 적용한다. 날짜 접두사는 부트스트랩의 파일 정렬 순서를 유지한다.
기존 로컬 DB에는 다음 명령으로 명시적으로 적용하며 공용 DB 변경·배포는 별도다.

```bash
python3 scripts/workspace.py migrate platform 20261023_KAN-77_oauth_authorizations.sql
```

전용 `kan77_auth_test` DB를 사용하는 통합 검사는 `AUTH_TEST_DATABASE_URL`을 명시한다.
일반 단위 검사에서는 해당 DB 검사를 건너뛰고, 플랫폼 변경 CI의 `auth-database` 작업에서
PostgreSQL 17로 실제 실행한다. 플랫폼 마이그레이션만 변경해도 플랫폼 검사 대상으로
선택해 해당 DB 검사가 빠지지 않게 한다. 공개 로그인 경로와 세션 연결은 여전히 미구현이다.
