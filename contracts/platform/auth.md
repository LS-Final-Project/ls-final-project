# 플랫폼 인증 계약 시작점

원본: `backend/api/platform/auth/`의 schemas.py, routes.py, http.py 및 settings.py.

GET /api/v1/platform/me는 현재 세션의 Identity를 반환한다. id, tid, oid, 표시 이메일과 이름, 부서 및 역할을 DB에서 조회한다. 쿠키에는 무작위 세션 ID만 있고 DB에는 해당 값의 hash를 저장한다. 요청마다 사용자 활성 상태와 역할을 다시 읽는다. 부서 역할은 부서가 없으면 거부한다. manager 역할이 프로젝트 책임자나 원문 열람 범위를 자동 부여하지 않는다.

Microsoft 로그인 HTTP 경계는 기본 비활성 구현 초안이다. `MICROSOFT_AUTH_ENABLED=false`이면 로그인·callback·로그아웃은 503이며, `/me`는 기존 개발 인증이 활성화된 경우 외에는 401이다. Microsoft 인증과 개발 인증은 동시에 활성화할 수 없다. production에서는 개발 쿠키와 임의 역할 헤더를 인증으로 받지 않는다. 사서함 토큰 저장과 업무 서비스로의 사용자 맥락 중계는 후속 기능이다.

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
아래 경계는 합의 전 초안이다. OpenAPI에 등록한 HTTP 경로도 기본 비활성이며 통합 완료 계약이 아니다.

`backend/api/platform/auth/callback.py`의 `complete_callback()`은 서버가 보관한
인가 시도와 callback의 state, 목적, 브라우저 결속 및 만료를 대조한다. 중복 입력
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
`response_mode=form_post`를 사용해 인가 code와 state를 callback URL 대신 POST 본문으로 받는다.

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
사용한 이번 제품 어댑터 검증은 아직 수행하지 않았다. HTTP 로그인·서버 세션 연결과
쿠키·CSRF·로그아웃은 기본 비활성 초안으로 구현했다. 토큰 보관은 후속 구현이며
기존 로컬 개발 세션 동작은 유지한다.

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
선택해 해당 DB 검사가 빠지지 않게 한다. 로그인 HTTP 경로와 세션 연결 검사도 같은 DB를 사용한다.

### 로그인 흐름과 등록 사용자 확인

`login.py`의 `start_login()`은 로그인 목적으로 고정한 MSAL 인가 시도를 만들고
DB 저장이 성공한 뒤 URL만 반환한다. MSAL 호출은 스레드에서 수행한다. 결속값의
생성과 쿠키 설정은 `http.py`의 HTTP 경계에서 담당하며, flow나 토큰을 응답하지 않는다.

`complete_login()`은 기존 `complete_stored_callback()`으로 로그인 목적의 시도를
한 번 소비하고 검증된 `tid + oid`로 활성 등록 사용자를 조회한다. 이메일이나
callback의 역할·사용자 필드로 계정을 선택하지 않는다. DB에 등록된 역할만 사용하며
미등록·비활성·무역할 사용자, 부서 없는 `dept_staff`는 401로 거부한다. 사용자와
역할을 자동 생성하지 않는다. 개발 세션과 동일한 조회 필드 및 Identity 검증을
`identity.py`에서 공유하며 기존 개발 로그인 동작은 유지한다.

`complete_login()` 자체는 서버 내부의 사용자 식별 결과만 반환한다. HTTP callback은
`complete_stored_callback()` 다음 `issue_session()`을 호출해 검증된 MicrosoftSubject의
등록·활성·역할을 DB에서 확인하고 세션을 발급한다. 사서함 연결과 토큰 보관은 KAN-80·81 범위다.

### 운영 서버 세션 저장 경계

`sessions.py`는 기존 `identity.dev_sessions`와 별개인 `identity.sessions`를 사용한다.
`http.py`가 이 저장 경계를 로그인 callback·쿠키·현재 사용자 조회·로그아웃에 연결한다.

- `issue_session()`은 **서버의 OAuth 검증 경계를 통과한 MicrosoftSubject만** 받는다.
  HTTP 요청의 tid·oid로 직접 호출하면 안 된다. 발급 시 DB 등록·활성·역할·부서를
  다시 확인하고, 매번 새로운 32바이트 난수 기반 세션 ID를 생성한다. DB에는 SHA-256
  hash·사용자 ID·생성 및 만료 시각만 저장하며 역할·MS 토큰은 세션에 저장하지 않는다.
- 발급 결과의 원문 토큰은 `SecretStr`로 마스킹하며 HTTP 응답 DTO가 아닌 서버 내부
  결과로 취급한다. HTTP 경계가 쿠키에 설정할 때만 원문을 꺼내 사용한다.
- 검증된 `previous_token_hash`를 지정하면 기존 운영 세션 삭제와 신규 발급을 같은 트랜잭션으로
  처리한다. 신규 삽입 실패 시 삭제도 롤백되며, 다른 브라우저의 세션은 유지한다.
  토큰을 재사용하거나 기존 세션을 연장하는 방식으로 로그인을 처리하지 않는다.
- `load_session()`은 DB 실제 시각으로 만료를 확인하고 현재 사용자 활성 상태·역할·
  부서를 재조회한다. 누락·미등록·만료·철회·무역할·부서 누락은 401이며 조회로
  만료를 연장하지 않는다. 개발 세션으로 폴백하지 않는다.
- `revoke_session()`은 해당 운영 세션을 삭제한다. 중복 철회와 토큰 누락은 허용하되
  DB 장애를 성공으로 숨기지 않는다. HTTP 경계는 Origin·CSRF 확인 뒤 철회를 실행하고
  성공했을 때 쿠키를 삭제한다. cs_app·tft_app에는 세션 테이블 조회 권한이 없다.

세션 수명은 현재 HTTP 초안과 같은 **8시간 절대 만료 제안**을 내부 구현에 적용했다.
공개 계약 확정 시 재확인하며 유휴 만료·전체 기기 로그아웃·감사 로그 API는 이번 범위가 아니다.
세션 교체·서버 만료·철회 원칙은 [OWASP 세션 관리 안내](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)를 참고한다.

마이그레이션 `20261023_KAN-77_sessions.sql`은 기존 users 생성 이후 적용한다.
기존 로컬 DB에는 아래 명령으로 명시적으로 적용하며 공용 DB 변경·배포는 별도다.

```bash
python3 scripts/workspace.py migrate platform 20261023_KAN-77_sessions.sql
```

실제 DB 검사는 `tests/platform/test_session_store.py`이며 OAuth 검사와 같은 전용 DB
준비를 `tests/platform/conftest.py`에서 공유한다. `auth-database` CI는
`test_authorization_store.py`, `test_session_store.py`, `test_login_http.py`를 명시적으로
실행한다. 일반 검사에서는 `AUTH_TEST_DATABASE_URL`이 없으면 DB 검사를
건너뛰며 이를 실제 DB 검증 성공으로 보고하지 않는다.

### 공개 HTTP 계약 제안 — 기본 비활성 구현 초안

KAN-57에 기록된 Microsoft 로그인·서버 세션·HttpOnly/Secure 쿠키 방식을 따른다.
아래 URL·쿠키 이름·세션 수명·화면 이동은 김종하와 확인할 제안이다. OpenAPI에는
`x-implementation-status: draft`와 활성화 설정 이름을 기록했다. 활성화 전에는 사용할 수
없으며, 이 등록으로 화면 계약 합의나 통합 완료를 의미하지 않는다.
KAN-78 사용자·역할 등록은 김나영의 담당으로 유지한다.

| 제안 경로 | 성공 시 동작 | 실패 시 동작 |
| --- | --- | --- |
| `GET /api/v1/platform/auth/login` | 새 브라우저 결속값·5분 인가 시도를 저장한 뒤 Microsoft URL로 302 | 공급자·DB 장애는 503, 저장 실패 시 이동·결속 쿠키 발급 없음 |
| `POST /api/v1/platform/auth/callback` | form_post의 결속·일회 소비·토큰·등록 사용자 검증 후 새 서버 세션을 발급하고 고정된 앱 루트 `/`로 303 | 잘못된 callback은 400, 토큰 또는 등록 사용자 검증 실패는 401, 공급자·DB 장애는 503. 실패 시 새 세션 없음 |
| `POST /api/v1/platform/auth/logout` | Origin·CSRF 확인 후 DB 세션 철회와 쿠키 삭제, 204. 쿠키가 없으면 유효 Origin만 확인하여 멱등 처리 | Origin·CSRF 실패는 403. GET으로 로그아웃하지 않음 |
| `GET /api/v1/platform/me` | 서버 세션으로 현재 활성 사용자와 DB 역할 조회. 기존 Identity 본문 및 `X-CSRF-Token` 응답 헤더 | 만료·철회·미등록·비활성 또는 유효 역할 없음은 401 |

- 결속 쿠키 제안: `__Host-team4_oauth`, 5분, HttpOnly·Secure·SameSite=None,
  Path=/, Domain 미지정. nonce·PKCE verifier·state 원문은 쿠키에 넣지 않는다.
  Microsoft에서 오는 교차 사이트 POST callback에도 결속 쿠키가 필요하므로 None을 사용한다.
  로그인 시작 시 기존 세션이 있으면 난수 결속값에 해당 세션의 hash만 덧붙인다.
  전체 결속값을 서버 인가 시도에 hash로 저장하고 callback에서 검증한 뒤에만 기존
  세션 철회에 사용한다. 기존 세션 원문은 이 쿠키에 넣지 않는다. 이 방식으로 POST에
  Lax 세션 쿠키가 빠져도 이전 세션을 교체하며, hash를 바꾼 callback은 거부한다.
- 세션 쿠키 제안: `__Host-team4_session`, 최대 8시간, HttpOnly·Secure·SameSite=Lax,
  Path=/, Domain 미지정. 브라우저에는 새 무작위 ID만, DB에는 hash·사용자·만료를 저장한다.
  로그인 성공 시 기존 서비스 세션을 교체하고 매 요청 사용자 활성 상태·역할을 재조회한다.
- callback은 URL 인자가 없는 `application/x-www-form-urlencoded` POST만 받는다.
  본문 8KiB·필드 20개로 제한하고 중복 키를 보존해 거부한다. GET callback은 405이며
  요청이 지정한 `return_url`로 이동하지 않는다. 앱 진입점과 실패 시 UI 표시는 합의가 필요하다.
  응답은 공통 오류 형식을 사용하고 code·state·토큰·공급자 원문 오류를 응답·화면 이동
  URL에 넣지 않는다. 인증 경로의 Uvicorn 접근 로그는 잘못 보낸 GET도 query를 제거한다.
  인증 응답은 Cache-Control: no-store 및 Referrer-Policy: no-referrer를 적용한다.
- 로그아웃은 정확한 앱 Origin과 `X-CSRF-Token` 요청 헤더를 함께 확인한다. 토큰은
  무작위 세션 ID를 키로 한 HMAC이며 `/me` 응답 헤더로만 전달하고 세션 쿠키 원문을
  노출하지 않는다. DB 철회가 실패하면 503을 반환하고 쿠키도 삭제하지 않는다.
- Microsoft HTTP 경계는 HTTPS만 허용하며 로컬 HTTP용 Secure 예외를 만들지 않는다.
  기존 개발 세션은 별도 모드이며 운영에서 수용하지 않는다.

쿠키의 교차 사이트 POST 제약은 [MDN Set-Cookie 안내](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie),
Origin·세션 결속 토큰은 [OWASP CSRF 안내](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)를 참고한다.

활성화에 필요한 설정 이름은 다음과 같다. 실제 시크릿은 서버에서 주입하며 저장소나 브라우저에 넣지 않는다.

```text
MICROSOFT_AUTH_ENABLED=false
MICROSOFT_AUTH_CLIENT_ID=<기존 앱 UUID>
MICROSOFT_AUTH_CLIENT_SECRET=<서버 시크릿 주입>
MICROSOFT_AUTH_APP_ORIGIN=https://app.example.invalid
MICROSOFT_AUTH_REDIRECT_URI=https://app.example.invalid/api/v1/platform/auth/callback
```

활성화 시 누락된 설정·HTTP·서로 다른 origin·개발 인증 동시 활성화는 시작 단계에서
거부한다. callback은 앱 origin과 위 고정 경로가 정확히 같아야 한다. 실제 활성화에는
KAN-57 화면 계약 확인, 기존 Entra 앱의 Web callback 등록 확인과 실제 계정의
`tid + oid` 사용자 등록이 필요하다. 시연 계정이 실제 Outlook 등록 사용자를 대신하지 않는다.
이번 변경은 앱 등록·실제 환경 설정·배포를 수행하지 않는다.

### 운영 웹의 인증 전용 중계

`frontend/server.mjs`는 위 네 인증 경로만 정확한 메서드로 플랫폼에 중계한다.
인가 결과는 본문으로만 전달하며 모든 API query를 거부한다. callback 본문 형식·8KiB
상한도 중계 전에 확인한다. 브라우저 Cookie 중 두 `__Host-team4_` 인증 쿠키만 전달하고,
브라우저 Authorization 대신 기존 Cloud Run 서비스 간 IAM 토큰을 사용한다.
Origin·CSRF 헤더를 전달하고 인증 응답의 Location·여러 Set-Cookie·CSRF 헤더를 보존한다.
Microsoft redirect를 서버에서 따라가지 않는다. 인증 중계 제한 시간은 45초이며 기존
health/ready는 15초다. 임의 API·개발 로그인·C/S·TFT·SSE 중계는 추가하지 않는다.

현재 프론트의 공통 JSON 요청 함수는 응답 헤더를 반환하지 않고 기존 logout 함수는
개발 세션용이다. KAN-130 화면 구현 시 `/me`의 CSRF 응답 헤더를 읽어 로그아웃에
전달하는 연결이 필요하다. 이번 변경에는 화면·버튼이나 공통 요청 함수 변경을 포함하지 않는다.

HTTP·쿠키·CSRF·인증 전용 중계는 비활성 초안으로 구현했으며 KAN-57 합의, 운영 활성화,
실제 Outlook 브라우저 흐름, 로그인 화면 및 업무 중계/SSE 검증은 남아 있다.
사서함 연결·Graph·토큰 보관은 KAN-80·81 범위다. 이 초안으로 KAN-57·77을 완료 처리하지 않는다.
