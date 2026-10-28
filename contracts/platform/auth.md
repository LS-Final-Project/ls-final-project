# 플랫폼 인증 계약 시작점

원본: `backend/api/platform/auth/`의 schemas.py, routes.py, http.py 및 settings.py.

GET /api/v1/platform/me는 현재 세션의 Identity를 반환한다. id, tid, oid, 표시 이메일과 이름, 부서 및 역할을 DB에서 조회한다. 쿠키에는 무작위 세션 ID만 있고 DB에는 해당 값의 hash를 저장한다. 요청마다 사용자 활성 상태와 역할을 다시 읽는다. 부서 역할은 부서가 없으면 거부한다. manager 역할이 프로젝트 책임자나 원문 열람 범위를 자동 부여하지 않는다.

Microsoft 로그인 HTTP 경계는 기본 비활성 구현 초안이다. `MICROSOFT_AUTH_ENABLED=false`이면 로그인·callback·로그아웃은 503이며, `/me`는 기존 개발 인증이 활성화된 경우 외에는 401이다. Microsoft 인증과 개발 인증은 동시에 활성화할 수 없다. production에서는 개발 쿠키와 임의 역할 헤더를 인증으로 받지 않는다. 사서함 토큰 저장은 후속 기능이다. 업무 사용자 맥락의 JSON 중계는 아래 KAN-178 설정을 명시적으로 켰을 때만 사용한다.

로컬에서 DEV_AUTH_ENABLED=true일 때만 제공하는 경로:

| 경로 | 동작 |
| --- | --- |
| GET /api/v1/platform/dev/accounts | DB에 미리 생성한 활성 사용자 목록 |
| POST /api/v1/platform/dev/session | user_id 한 개로 선택. 등록되지 않은 ID 및 클라이언트 역할 지정 거부 |
| DELETE /api/v1/platform/dev/session | DB 세션 무효화 및 쿠키 삭제 |

브라우저의 변경 요청은 localhost:5173 및 127.0.0.1:5173 Origin만 허용한다. 쿠키는 HttpOnly, SameSite=Lax이며 HTTP 로컬 개발이므로 Secure는 사용하지 않는다. 개발 경로는 일반 계약 산출물에 포함하지 않으며 로컬 Swagger에서 확인한다.

수정 후 `python3 scripts/workspace.py contracts platform`을 실행한다.

## 운영 세션에서 업무 사용자 모델로 변환 (KAN-178 첫 단계)

`context.py`의 `load_session_principal()`은 기존 `load_session()` 결과를 KAN-78의
공통 `backend.shared.access.Principal`로 변환하는 서버 내부 함수다. 역할·부서·관리 범위는
기존 세션 SQL과 `Identity` 검증을 재사용해 요청마다 다시 조회하며 별도 SQL·캐시·DTO를
만들지 않는다. manager 역할 없이 관리 범위 행이 남는 등 등록 조건이 불일치하면 기존
검증에 따라 401로 거부한다. DB 조회 실패도 기존 503으로 전파한다.

반환 필드는 [권한 계약 1.4](permissions.md#14-업무-서비스가-받는-사용자-정보-kan-78-형식-제안)의
`user_id`, 다중 `roles`, nullable `dept`, `managed_departments`, `managed_projects`다.
공개 `Identity`의 이메일·표시 이름·Microsoft `tid`/`oid`와 원문 세션·Microsoft 토큰은
`Principal`에 포함하지 않는다. 조회만으로 세션 만료를 연장하거나 권한을 부여하지 않는다.

첫 단계는 내부 변환만 제공했으며, 다음 절의 명시 활성화 JSON 중계에서 이 함수를
호출한다. `Principal`의 필드 호환성만으로 헤더를 신뢰하지 않는다. TFT의 담당 sales·현재
책임자는 TFT 업무 관계로 판정하며 이 변환 함수가 대신하지 않는다.

실제 PostgreSQL 검사 `tests/platform/test_session_context.py`를 `auth-database` CI에
포함해 공통 모델 변환·권한 회수·등록/세션 거부·DB 장애를 확인한다. 기존 KAN-78의
관리 범위·부서 코드 마이그레이션을 사용하며 새 마이그레이션과 공개 API 변경은 없다.
이 내부 변환 검증을 실제 공용 로그인 성공으로 해석하지 않는다.

## 업무 JSON 중계와 호출자 검증 (KAN-178)

`BUSINESS_RELAY_ENABLED`는 웹·플랫폼·C/S·TFT 모두 기본 `false`다. 비활성 production의
업무 경로는 기존 404 잠금을 유지한다. 활성화는 실제 권한·호출자 검증을 확인하는 별도
통합·배포 판단이며, 이번 코드에서 GCP IAM·환경 변수·main 배포를 바꾸지 않는다.

흐름은 브라우저 → 웹 → 플랫폼 → 고정 C/S 또는 TFT다. 웹은 플랫폼용 IAM 토큰과
운영 세션 쿠키만 구분해서 전달하며, 플랫폼은 운영 세션·활성 사용자·현재 역할·부서·
명시 관리 범위를 DB에서 매번 읽어 공통 `Principal`을 만든다. 브라우저의 Authorization,
`X-Team4-Principal`, `X-Team4-Caller-Token` 및 다른 내부 인증 헤더를 전달하지 않는다.
플랫폼→업무 서비스 요청에는 원문 세션 쿠키·Microsoft 토큰·표시 이메일을 넣지 않는다.

| 내부 헤더 | 생성·검증 |
| --- | --- |
| `Authorization: Bearer <Google ID token>` | 플랫폼 실행 계정이 메타데이터 서버에서 대상 업무 URL을 audience로 발급. Cloud Run IAM용 |
| `X-Team4-Caller-Token` | 위 원본 Google ID 토큰. 업무 컨테이너가 고정 Google JWKS의 RS256 서명·Google issuer·정확한 수신 audience·필수 시간/sub·허용 플랫폼 이메일·email_verified=true 검사 |
| `X-Team4-Principal` | 플랫폼 세션에서 만든 5필드 JSON. 호출자 인증 뒤 공통 Principal 모델로 검증. 누락·중복·8KiB 초과·허용하지 않은 필드/역할/범위는 401 |

Google 키 조회 장애는 503, 잘못된 호출자 증명은 401이다. 호출자 헤더는 한 개·16KiB
이내여야 한다. 활성화한 local/test에서도 같은 호출자 검증을 강제한다. 중계가 비활성인
local/test의 기존 `principal_header()` fake는 독립 개발용으로만 유지한다. 호출자 검증
성공 뒤에도 C/S·TFT의 역할·객체·행동 검사를 그대로 수행하며 IAM 성공을 사용자 권한으로
해석하지 않는다. 이 ID 토큰은 Principal의 필드를 서명한 사용자 JWT가 아니라 플랫폼
실행 계정의 bearer 증명이다. 토큰/서비스 계정 impersonation 권한과 TLS 경계를 보호해야 한다.

Cloud Run의 `X-Serverless-Authorization`은 컨테이너 전달 시 서명이 제거될 수 있으므로
애플리케이션 증명으로 사용하지 않는다. 원본 토큰을 별도 내부 헤더로 검증하는 이유다.
[Cloud Run 서비스 간 인증](https://docs.cloud.google.com/run/docs/authenticating/service-to-service)과
[Google ID 토큰 검증](https://docs.cloud.google.com/compute/docs/instances/verifying-instance-identity)을 따른다.

허용 경로는 현재 dev에 병합된 다음 JSON API뿐이다. UUID는 표준 하이픈 표기만 허용한다.
새 업무 API를 추가하면 웹·플랫폼 허용 목록과 회귀 검사를 함께 확장해야 한다. 웹
`frontend/server.mjs`의 `businessRoutes`와 플랫폼 `relay.py`의 `BUSINESS_ROUTES`는 같은 표이며,
두 검사가 테스트 전용 `docs/openapi/business-relay-routes.json`과 각각 완전 일치를 확인한다.

| 경로 | 메서드 | 허용 query |
| --- | --- | --- |
| `/api/v1/cs/tickets` | GET, POST | GET만 `limit`·`cursor` |
| `/api/v1/cs/tickets/{ticket_id}` | GET, PATCH | 없음 |
| `/api/v1/cs/tickets/{ticket_id}/audit` | GET | 없음 |
| `/api/v1/cs/tickets/{ticket_id}/units` | GET | 없음 |
| `/api/v1/cs/tickets/{ticket_id}/unit-history` | GET | 없음 |
| `/api/v1/cs/stats/overview` | GET | `start`·`end`·`dept`·`customer` |
| `/api/v1/cs/stats/owners` | GET | `start`·`end`·`dept` |
| `/api/v1/cs/stats/customers/{customer_external_id}` | GET | `start`·`end` |
| `/api/v1/cs/improvement-requests` | GET | 없음 |
| `/api/v1/cs/improvement-requests/{request_id}` | GET | 없음 |
| `/api/v1/tft/projects` | GET | `limit`·`cursor` |
| `/api/v1/tft/projects/{project_id}` | GET | 없음 |

query는 표에 적힌 키만 중복 없이(값 각각 200자 이내) 허용하고, 표에 없는 키·중복·변경 요청의
query·query가 없는 경로의 query는 400이다. 세부 형식(시각·부서·페이지 범위)은 기존 업무 API가
검증한다. `customer_external_id` 경로 값은 웹·플랫폼 모두 디코드한 경로에서
`[A-Za-z0-9][A-Za-z0-9._-]{0,63}`(첫 글자 영숫자)만 허용하고 아니면 404다. 디코드된 `?`·`#`·`/`와
`.`·`..` 세그먼트가 upstream 경로나 query를 바꾸지 못하게 하기 위해서이며, 플랫폼은 이 때문에 `request.url.path` 대신 ASGI scope의
디코드된 경로로 검사·전달한다. 개선요청 생성·수정 등 변경 메서드와 처리 이력·업무 흐름
조회는 아직 중계하지 않는다. 임의 upstream·SSE·파일·메일·미병합 API는 허용하지 않는다. 플랫폼과 웹은 요청 1MiB·응답 8MiB로 제한하며 upstream 이동이나
자동 재시도 없이 고정 HTTPS 대상으로만 전달한다. 플랫폼 연결/읽기 제한은 3초/20초,
웹 전체 중계 제한은 45초다. JSON이 아닌 응답·리디렉션·네트워크 장애는 플랫폼 503 또는
웹 502로 처리하고 공급자 원문을 반환하지 않는다.

변경 요청은 정확한 `MICROSOFT_AUTH_APP_ORIGIN`과 운영 세션에 결속된 `X-CSRF-Token`,
`application/json`을 요구한다. 조회도 Origin이 있으면 고정 출처와 대조한다. `/me`의 CSRF
응답 헤더를 화면이 받아 보내는 연결은 KAN-130 범위다. 업무 상태·JSON 본문·요청 ID와
ETag를 보존하며 If-Match·Idempotency-Key는 기존 소비자 API로 전달한다. 업무 API의
판본/멱등성 규칙을 중계에서 새로 구현하지 않는다. 업무 응답은 성공·실패 모두 no-store이며
Set-Cookie·Location·내부 인증 헤더는 브라우저로 전달하지 않는다.

명시 활성화 시 백엔드 세 서비스에 필요한 값:

```text
BUSINESS_RELAY_ENABLED=false
BUSINESS_CS_URL=https://<정확한 CS Cloud Run 서비스 출처>
BUSINESS_TFT_URL=https://<정확한 TFT Cloud Run 서비스 출처>
BUSINESS_PLATFORM_EMAIL=<platform-runtime 실행 서비스 계정 이메일>
```

출처는 path·마지막 slash·자격증명·query·fragment가 없는 서로 다른 HTTPS 주소여야 한다.
플랫폼은 Microsoft 운영 인증도 켜져 있어야 하며 개발 인증과 동시에 사용할 수 없다.
웹은 같은 플래그와 기존 `PLATFORM_URL`을 사용한다. C/S는 추가로
`CS_MANUAL_DATA_ORIGIN=demo` 또는 `real`을 명시해야 활성화된다. 공용 시연은
production에서도 demo를 유지하며 실제 문의 배포만 real을 선택한다. 이 설정은 새 수동
문의에만 적용하고 브라우저가 지정할 수 없다. 출처가 다른 자료를 한 배포에서 혼합 접수하지 않는다. 수신 서비스별 run.invoker 부여와
실제 공용 계정 검증은 [배포 안내](../../docs/service-delivery.md#kan-178-업무-중계-활성화-전-확인)를 따른다.

검사는 `tests/test_service_auth.py`(합성 RSA의 실제 서명·클레임 검증),
`tests/platform/test_business_relay.py`(실제 PostgreSQL·세 서비스 앱·세션/역할/범위 회수·
위조/직접 호출/CSRF 거부), `test_relay_transport.py`(upstream 실패·응답 제한) 및 웹 서버
회귀다. Google JWKS/메타데이터와 서비스 간 HTTP 소켓은 fake이며 실제 Google 토큰·IAM·
Outlook 브라우저 로그인·공용 배포 성공으로 보고하지 않는다. 실제 DB 검사는
`auth-database` CI에도 포함한다. SSE·비동기 맥락과 업무 기능 전체 완료는 별도다.

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
`test_authorization_store.py`, `test_session_store.py`, `test_login_http.py`,
`test_session_context.py`, `test_business_relay.py`를 명시적으로 실행한다. 일반 검사에서는 `AUTH_TEST_DATABASE_URL`이 없으면 DB 검사를
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
인가 결과는 본문으로만 전달하며 인증 경로의 query를 거부한다. callback 본문 형식·8KiB
상한도 중계 전에 확인한다. 브라우저 Cookie 중 두 `__Host-team4_` 인증 쿠키만 전달하고,
브라우저 Authorization 대신 기존 Cloud Run 서비스 간 IAM 토큰을 사용한다.
Origin·CSRF 헤더를 전달하고 인증 응답의 Location·여러 Set-Cookie·CSRF 헤더를 보존한다.
Microsoft redirect를 서버에서 따라가지 않는다. 인증 중계 제한 시간은 45초이며 기존
health/ready는 15초다. 업무 JSON은 위 KAN-178 설정과 허용 목록에 한정하며 임의 API·개발 로그인·SSE는 중계하지 않는다.

현재 프론트의 공통 JSON 요청 함수는 응답 헤더를 반환하지 않고 기존 logout 함수는
개발 세션용이다. KAN-130 화면 구현 시 `/me`의 CSRF 응답 헤더를 읽어 로그아웃에
전달하는 연결이 필요하다. 이번 변경에는 화면·버튼이나 공통 요청 함수 변경을 포함하지 않는다.

HTTP·쿠키·CSRF·인증 전용 중계는 비활성 초안으로 구현했으며 KAN-57 합의, 운영 활성화,
실제 Outlook 브라우저 흐름, 로그인 화면 및 공용 업무 중계/SSE 검증은 남아 있다.
사서함 연결·Graph·토큰 보관은 KAN-80·81 범위다. 이 초안으로 KAN-57·77을 완료 처리하지 않는다.

## 사서함 토큰 소비자 계약 초안 (KAN-80·81 → KAN-71)

**상태: 소비자 합의 전 제안이다. 인증 측 제공자와 앱 내부 주입은 구현했으며,
KAN-71의 실제 소비자 연결·합의 및 Microsoft/Graph 검증은 아직 하지 않았다.**
[KAN-71 문의 답변](https://lsteam4.atlassian.net/browse/KAN-71?focusedCommentId=10267)의
함수 형태와 실패 구분을 구체화한다. 아래 함수의 import 경로, 오류 매핑과 재시도 책임은
KAN-71·80·81에서 확인할 항목이다. 이 문서만으로 실제 사서함 연결이나 토큰 보관이
가능해진 것은 아니다. 이 소비자 인터페이스 때문에 공개 토큰 HTTP API·OpenAPI·운영 설정·
DB 스키마를 추가하지 않는다. 기존 초기 연결 HTTP와 보관 구현은 아래 절에서 구분한다.

### 서버 내부 호출 형태

```python
from uuid import UUID

from pydantic import SecretStr


async def get_mailbox_access_token(
    *, mailbox_id: UUID, owner_user_id: UUID
) -> SecretStr:
    """소유 관계를 확인하고 유효한 Graph access token을 반환한다.

    Args:
        mailbox_id: 서버에 등록된 사서함 연결의 내부 ID.
        owner_user_id: 인증된 요청 또는 서버의 수집 작업에서 확인한 소유 사용자 ID.

    Returns:
        SecretStr: 서버의 Graph 호출에만 사용할 유효한 access token.

    Raises:
        ApplicationError: 아래 실패 코드에 따라 토큰을 제공할 수 없는 경우.
    """
    ...
```

`mailbox_id`는 사서함 연결의 내부 UUID이며 Microsoft `oid`나 이메일로 대체하지 않는다.
`owner_user_id`는 `identity.users.id`다. 요청 경계는 유효한 사용자와 해당 작업의 권한을
확인하고, 비동기 작업은 서버에 저장된 연결·수집 작업에서 소유자를 복원한다. 브라우저나
검증되지 않은 작업 본문이 지정한 두 ID를 그대로 넘겨 권한 확인을 대신하지 않는다.
메일 수집이 실행될 때마다 현재 연결·소유자·활성 상태를 확인하며, ID를 안다는 이유로
토큰 접근을 허용하지 않는다. 사서함 연결과 로그인 사용자의 결속은 위 callback 계약을 따른다.
사서함의 Microsoft 식별자는 별도 연결 정보이며, 이메일을 기준으로 서비스 로그인 사용자나
연결 소유자를 자동 변경하지 않는다.

반환값은 조회 시점에 유효한 access token 하나다. 만료됐거나 갱신에 실패해 사용할 수 없는 토큰,
빈 문자열·`None`을 성공으로 반환하지 않는다. 소비자는 Graph 호출의 인증 헤더를 구성할
때만 서버 메모리에서 원문을 사용하고, 브라우저·URL·업무 데이터·로그에 기록하지 않는다.
refresh token, MSAL 캐시와 암호화 키는 이 반환값에 포함하지 않는다. `SecretStr`은 표현을
마스킹하는 자료형이며 저장 암호화나 접근제한의 구현을 뜻하지 않는다.

### 실패 구분 제안

호출 경계와 토큰 제공자는 각자의 권한·연결 검사를 수행한다. 실패를 전달할 때는 새 오류
기반 클래스를 만들기보다 기존 `backend.shared.errors.ApplicationError`의
`status_code`, `code`, `message`를 재사용하는 안이다. 아래 값은 소비자와 합의할 제안이며,
내부 함수에 대한 값이다. 이를 위한 공개 오류 API나 공용 업무 오류 목록을 추가하지 않는다.

| code | status_code | 조건 | 안전한 message 제안 | 소비자 처리 |
| --- | --- | --- | --- | --- |
| `MAILBOX_ACCESS_DENIED` | 403 | 알 수 없는 연결 ID, 다른 소유자, 비활성 사용자 또는 허용되지 않은 호출 작업 | `Mailbox access is not allowed.` | 수집 중단. 연결 존재 여부나 타인의 정보를 노출하지 않음 |
| `MAILBOX_NOT_CONNECTED` | 409 | 소유 관계는 확인됐지만 연결이 완료되지 않았거나 명시적으로 해제됨 | `Mailbox is not connected.` | 연결 전까지 수집을 실행하지 않음 |
| `MAILBOX_RECONNECT_REQUIRED` | 409 | 자격증명 철회·동의 만료 등 사용자 재연결이 필요한 것으로 분류됨 | `Mailbox reconnection is required.` | 자동 재시도 대신 재연결 필요 상태 전달 |
| `MAILBOX_TOKEN_UNAVAILABLE` | 503 | DB·시크릿·복호화·공급자 통신 등으로 유효한 토큰을 제공할 수 없음 | `Mailbox authentication is unavailable.` | 실패를 기록하고 작업 정책에 따른 처리. 자동 재연결로 단정하지 않음 |

연결 존재 여부·소유 관계를 확인할 수 없는 DB 장애는 접근 거부나 미연결로 위장하지 않고
`MAILBOX_TOKEN_UNAVAILABLE`로 실패한다. 알 수 없는 연결과 다른 소유자의 연결은 같은
접근 거부로 처리하고, 소유 관계 확인 전에 공급자 토큰을 교환하거나 복호화하지 않는다.
공급자의 원시 오류 설명, 토큰·캐시·계정 식별자는 오류 메시지와 로그에 넣지 않는다.
다른 예외까지 일괄 잡아 빈 토큰으로 바꾸는 폴백도 두지 않는다.

### 구현 책임과 KAN-71의 fake 경계

| 담당 범위 | 책임 |
| --- | --- |
| KAN-80 인증·Graph 경계 | 기존 MSAL·인가 시도 검증 재사용, 연결 소유자 검증, 유효 토큰 획득·갱신과 공급자 실패 분류, 실제 메시지·첨부 접근 확인 |
| KAN-81 보관·연결 해제 | 토큰/캐시의 암호화·접근제한, 갱신 결과의 안전한 저장과 경합 처리, 해제 후 토큰 조회 차단·저장 자격증명 제거/비활성화, 재연결 시 이전 캐시 재사용 차단 |
| KAN-71 메일 수집 | 서버의 사서함/작업 소유자를 사용한 호출, 사서함당 단일 처리, 페이지네이션·증분·원문/첨부 보존, 작업 실패 기록 |

메일 수집 코드에서 OAuth·refresh token 처리나 별도 토큰 캐시를 중복 구현하지 않는다.
MSAL은 캐시의 유효 토큰을 사용하거나 갱신할 수 있으며 오류를 구분하는 획득 방법도
제공하므로, 기존 SDK 위에 공급자 경계를 연결한다
([MSAL 토큰 획득](https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens)).
refresh token은 만료 전에 철회될 수 있으므로 재연결 필요 상태를 일시 장애와 구분한다
([Microsoft refresh token 안내](https://learn.microsoft.com/en-us/entra/identity-platform/refresh-tokens)).
연결 해제 후 차단은 이후 getter 호출을 대상으로 검증한다. 이미 반환된 토큰의 외부 즉시
무효화까지 이번 함수 계약으로 보장하지 않으며, 그 보장이 필요하면 별도 검증 항목으로 둔다.

KAN-71의 fake는 위 두 keyword 인자를 받는 비동기 callable과 `SecretStr` 반환 형태를
사용하는 안이다. 실제 Graph와 연결하지 않는 합성 토큰만 반환하고, 실패 시나리오는 위
`ApplicationError` 코드로 구분한다. 별도 인터페이스 계층이나 운영용 fake를 새로 등록하지
않는다. 합의 후 최소한 다음을 fake 소비자와 실제 제공자 양쪽에서 대조한다.

- 정상 소유자·연결은 토큰을 받고, 다른 소유자와 알 수 없는 연결은 같은 접근 거부가 된다.
- 미연결·재연결 필요·일시 장애를 구분하며, 실패를 빈 목록·동기화 성공으로 기록하지 않는다.
- 소비자가 refresh token을 받지 않고, 토큰 값이 오류·로그·업무 저장 payload에 남지 않는다.

### 인증 측 제공자와 주입 경로 (소비자 합의 전)

구현 import 경로는
`backend.api.platform.auth.mailbox_tokens.MailboxTokenProvider`다. 서버 구성 단계에서
`Settings`, 기존 `PersonalMicrosoftOAuth`, `MailboxCipher`를 주입하고,
인스턴스의 `get_mailbox_access_token` 메서드만 소비자의 비동기 callable로 넘긴다.
결속된 메서드는 위 예시처럼 두 keyword 인자만 받고 `SecretStr` 하나를 반환한다.
소비자에게 암호기·설정·MSAL 캐시를 인자로 넘기도록 요구하지 않는다.

```python
from backend.api.platform.auth.mailbox_tokens import MailboxTokenProvider

# 플랫폼/작업의 신뢰할 서버 구성 단계에서만 준비한다.
provider = MailboxTokenProvider(settings=settings, adapter=adapter, cipher=cipher)
get_mailbox_access_token = provider.get_mailbox_access_token
# 소비자에게는 위 callable을 주입한다. ID는 인증된 요청/저장 작업에서 복원한다.
token = await get_mailbox_access_token(mailbox_id=mailbox_id, owner_user_id=owner_user_id)
```

플랫폼 앱은 기존 `MAILBOX_AUTH_ENABLED`가 true이고 전용 설정을 검증한 경우에만
같은 사서함 어댑터·암호기로 `app.state.mailbox_tokens`를 구성한다. 기본 false에서는
None이며 키·DB·공급자 호출 없이 기존 로그인과 분리된다. 메일 실행 경계는 제공자가
없으면 작업을 시작하지 않아야 하며 임의 기본 키·개발 토큰·다른 캐시로 대체하지 않는다.
이 앱 내부 상태를 공개 API로 반환하지 않는다. KAN-71의 요청/워커에 주입하는 코드는
소비자 합의 후 해당 담당 범위에서 연결한다. 별도 전역 singleton이나 자동 등록 fake는 없다.

제공자는 아래 기존 조회·갱신 저장 경계를 재사용한다. 네 가지 `ApplicationError`를
그대로 전달하고, 내부 저장 개정 충돌은 기존처럼 `MAILBOX_TOKEN_UNAVAILABLE`/503이다.
작업 인증·업무 권한 확인을 대신하거나 실패를 빈 토큰/동기화 성공으로 바꾸지 않는다.

**Graph 401 책임은 아직 합의 전이다.** 현재 getter는 Graph 응답이나 거부된 토큰을
받지 않고, 강제 갱신 옵션·Graph 호출·자동 재시도를 제공하지 않는다. 캐시가 아직
유효하면 getter를 다시 불러도 같은 토큰이 나올 수 있으므로 401 뒤 getter 호출/Graph
재요청 루프를 만들지 않는다. 현재 제안은 해당 작업을 실패로 남기고, 401 하나로 연결을
영구 철회/재연결 필요로 단정하지 않는 것이다. 이후 강제 갱신 신호·제한된 재시도·실패
기록 책임은 KAN-71과 함께 정한 뒤 양쪽에 검증한다. 인증 제공자는 기존 MSAL의 실제
갱신 오류를 분류하며, 소비자 fake도 미합의 강제 갱신을 구현했다고 가정하지 않는다.
KAN-71 [메일 수집 계약](mail.md#실패)도 현재 Graph 401을 `GRAPH_UNAUTHORIZED`로 실패
기록하고 강제 갱신·재시도 없이 다음 실행에 맡긴다.

### Graph 401 처리 제안 (KAN-71 확인 전, 미구현)

**상태: 합의 전 제안·미구현이다. 인증 측과 KAN-71 모두 아래 내용을 구현하지 않았다.**
위 현재 동작에서는 만료 전에 거부된 캐시 토큰이 다음 실행에서도 다시 401이 될 수 있다.
아래는 이 간격을 메우는 최소안이며 KAN-71 확인 후 양쪽에 구현·검증한다.

KAN-71이 답할 질문:

1. 401 뒤 `force_refresh=True` 호출은 수집 코드가 직접 내는가, 아니면 다음 실행을 부르는 쪽(KAN-72)에 넘기는가?
2. 실행 한 번의 강제 갱신 1회·같은 Graph 요청 재요청 1회 상한이 수집 흐름(페이지·메시지 내려받기 단위)에 맞는가?
3. 재요청도 401이면 실행을 기존 `GRAPH_UNAUTHORIZED`/`FAILED`로 끝내고 커서를 유지하는 것으로 충분한가?

| 항목 | 제안 |
| --- | --- |
| 신호 | getter에 선택 키워드 `force_refresh: bool = False`를 추가한다. 기존 두 ID 호출과 KAN-71의 토큰 함수 Protocol은 그대로 맞는다. 거부된 토큰·Graph 응답은 넘기지 않는다 |
| 제공자 동작 | `force_refresh=True`여도 소유자·활성·연결 재검사는 같다. 그 뒤 기존 MSAL `acquire_token_silent_with_error`의 `force_refresh=True`로 캐시 토큰을 건너뛰고 refresh token으로 새 access token을 받아, 기존과 같이 암호화 커밋 후에만 반환한다. 제공자는 Graph를 호출하지 않는다 |
| 사용 조건 | Graph가 401을 반환했을 때만 쓴다. 403·429·5xx·`syncStateNotFound`에는 쓰지 않는다. 사전 예방용 강제 갱신도 하지 않는다 |
| 재시도 상한 | 수집 실행 한 번에 강제 갱신 1회, 그 토큰으로 같은 Graph 요청 1회 재요청까지. 실행 간 재시도 간격은 실행을 부르는 쪽(KAN-72)이 정한다 |
| 두 번째 401 | 기존 `GRAPH_UNAUTHORIZED`로 실행을 실패 기록하고 더 부르지 않는다. 401만으로 연결을 재연결 필요나 해제 상태로 바꾸지 않는다 |
| 강제 갱신 실패 | 새 코드 없이 기존 네 코드를 그대로 쓴다. 갱신 거부는 `MAILBOX_RECONNECT_REQUIRED`/409, DB·복호화·공급자 장애와 저장 개정 충돌은 `MAILBOX_TOKEN_UNAVAILABLE`/503이다. 이 실패에는 자동 재시도하지 않는다 |
| 기록 책임 | 실행 실패·오류 코드 기록은 KAN-71, 갱신 결과 저장·경합 처리와 오류 분류는 KAN-81이 맡는다. 토큰·공급자 원문 설명은 어느 쪽 기록에도 남기지 않는다 |

구현 시 확인할 검사는 다음과 같다. 합성 Microsoft 응답과 가짜 Graph로 확인하며 실제
Outlook·Graph 통과를 뜻하지 않는다.

- 제공자: 유효 캐시에서도 `force_refresh=True`면 갱신 POST가 정확히 1회 일어나고 새 캐시가 커밋된 뒤에만 토큰을 반환한다. 기본값 호출은 지금처럼 캐시 적중 시 POST가 없다.
- 소비자: 401 → 강제 갱신 1회 → 재요청 성공이면 실행이 이어진다. 401이 두 번이면 `GRAPH_UNAUTHORIZED`이고 getter 호출은 실행당 2회를 넘지 않는다.
- 강제 갱신의 `MAILBOX_RECONNECT_REQUIRED`·`MAILBOX_TOKEN_UNAVAILABLE`이 실행 기록에 그대로 남는다.

### MSAL 사서함 캐시 어댑터 (부분 구현)

`backend/api/platform/auth/microsoft.py`의 동기 메서드
`PersonalMicrosoftOAuth.acquire_mailbox_token(subject=..., serialized_cache=...)`는
검증된 Microsoft 사서함 식별자와 해당 연결의 복호화된 `SecretStr` 캐시를 받는다.
호출자는 현재 연결의 소유자·활성·작업 권한을 먼저 확인하고 무결성이 보호된 캐시를
복호화해야 한다. 이 메서드는 DB 소유자 검사나 서비스 사용자 인증을 대신하지 않는다.
비동기 서비스에서 호출할 때는 기존 MSAL 경계처럼 스레드에서 실행해야 한다.

- 고정 개인 계정 authority·앱 설정을 재사용하고 호출마다 별도 `SerializableTokenCache`를
  사용한다. 이메일 대신 단일 캐시 계정의 `local_account_id`와 검증된 `oid`, 개인 계정
  realm·고정 환경을 대조한다. 다중 계정이나 다른 계정의 캐시는 접근 거부로 실패한다.
- MSAL의 `acquire_token_silent_with_error()`로 캐시 적중 또는 갱신을 처리한다. 반환된
  opaque access token을 JWT로 해석하지 않고 현재 앱·계정·realm의 캐시 및 기존
  `Mail.Read`·`Mail.Send` 권한과 대조한다. 캐시 적중 응답에는 `scope`가 없을 수 있어
  그때도 해당 토큰의 캐시 권한을 확인한다. 만료/빈 토큰이나 잘못된 토큰 형식은 반환하지 않는다.
  SDK의 선제 갱신 실패 시 아직 유효한 캐시가 반환될 수 있으며 동일한 유효성·계정·권한
  검증을 적용한다. 유효 토큰 조회 성공을 갱신 성공이나 공급자 철회 즉시 탐지로 해석하지 않는다.
- 갱신 응답에 ID 토큰이 있으면 기존 RS256/JWKS·issuer/audience·시간·개인 `tid`/`oid`
  검증을 재사용하고 저장된 사서함 식별자와 같은지 확인한다. 갱신에서는 새 nonce를
  요청하지 않으므로 그 검증에 새 nonce를 요구하지 않는다. 초기 인가의 nonce 검증은 유지한다.
- 내부 결과 `MailboxTokenUpdate`는 access token, 직렬화된 캐시, 변경 여부를 담는다.
  토큰·캐시는 `SecretStr`이며 결과의 repr에도 내용을 표시하지 않는다. 변경 여부는
  MSAL 직렬화가 플래그를 초기화하기 전에 읽는다. 캐시는 KAN-81 보관 경계 전용이고
  KAN-71 소비자에게 전달하지 않는다. 이 메서드가 결과를 저장하지는 않는다.
- 계정 불일치는 `MAILBOX_ACCESS_DENIED`, 갱신 자격증명 부재/MSAL이 반환한 재인가 필요는
  `MAILBOX_RECONNECT_REQUIRED`, 손상된 캐시·공급자/필요한 서명키 조회 실패는
  `MAILBOX_TOKEN_UNAVAILABLE`로 구분한다. 제공자 오류 설명이나 AADSTS 숫자로 분기하지
  않고 OAuth `error`로 분류한다
  ([Microsoft 오류 처리](https://learn.microsoft.com/en-us/entra/identity-platform/reference-error-codes)).
  `MAILBOX_NOT_CONNECTED`는 연결 상태를 아는 상위 계층의 책임이며 이 어댑터가 생성하지 않는다.
- 고정 MSAL 버전의 갱신 실패 DEBUG 기록은 `error_description`, 캐시 이벤트는 식별자를
  인코딩한 `client_info`를 포함하므로 이 두 기록만 안전한 고정 문장으로 변환한다.
  로그 수준이나 성공·실패 구분은 유지하고, 다른 SDK 기록은 변경하지 않는다.
  실제 SDK의 DEBUG 기록까지 합성 성공·오류 회귀로 확인한다.

위 내부 어댑터의 합성 HTTP·실제 MSAL/RSA 회귀는 실제 Microsoft·Graph 연결 검증과
구분한다. MSAL SDK의 기본 캐시·갱신 판정을 재사용하며 별도 재시도 루프·새 설정·공용
DB 변경은 추가하지 않는다.

### 초기 사서함 캐시 전달 경계 (부분 구현)

`backend/api/platform/auth/mailbox.py`의 서버 내부 함수는 기존 세션·인가 시도 테이블만
사용한다. 아래 기본 비활성 HTTP 경계가 초기 인가와 보관을 연결한다.

- `start_mailbox_link()`는 원문 세션 쿠키를 기존 `load_session()`으로 확인한 뒤 현재
  활성 등록 사용자·유효 역할의 ID에 인가 시도를 결속해 저장한다. URL만 반환한다.
  HTTP 호출자는 Origin·CSRF와 브라우저 결속 쿠키를 검사해야 하며 임의 사용자 ID를 받지 않는다.
- `complete_mailbox_link()`는 현재 세션을 확인하고 기존 원자적 인가 소비를 재사용한다.
  시작 브라우저·목적·소유자·만료가 맞아야 삭제가 커밋되며, 외부 교환 실패 후에도 소비를
  복구하지 않는다. 다른 사용자나 브라우저의 요청은 정상 시도를 소비하지 않는다.
- `PersonalMicrosoftOAuth.redeem_mailbox()`는 기존 callback 검증을 재사용하고 초기 ID
  토큰의 nonce·서명·발급자/대상·시간·개인 계정 식별자를 검증한다. 호출 전용 캐시의
  계정·앱·권한과 유효 access token, 후속 갱신 자격증명을 확인한다. 보관 전용
  `MailboxAuthorization`에는 세션에서 확인한 `owner_user_id`, 별도 사서함 `subject`,
  `SecretStr` 캐시만 담으며 repr로 표시하지 않는다. 초기 캐시를 기존 조회·갱신 어댑터로
  전달할 수 있다. 사서함의 Microsoft 식별자로 서비스 사용자·역할·세션을 변경하지 않는다.
- 교환 이후에도 같은 세션의 현재 활성·역할·만료/철회 상태를 다시 조회한다. 교환 도중
  로그아웃·비활성화·유효 역할 상실은 캐시 인계 전에 거부한다. 이 확인과 후속 저장 사이의
  경합까지 해결한 것은 아니므로 보관 트랜잭션도 현재 소유자·활성·연결 상태를 검사해야 한다.
- `complete_and_store_mailbox_link()`는 위 초기 인계와 아래 `create_mailbox()`를 명시적으로
  연결한다. 저장 트랜잭션이 커밋된 뒤 `MailboxConnectionReceipt(mailbox_id, revision)`만
  반환하며 토큰·캐시·사서함 `tid+oid`·서비스 소유자 ID를 결과에 포함하지 않는다.
  저장 실패를 연결 성공으로 바꾸지 않으며 이미 소비한 인가를 복구하지 않는다. 같은 callback을
  재전송해 다시 교환·저장하지 않고, 실패 후 새 연결을 시도하려면 새 인가를 시작해야 한다.

저수준 `complete_mailbox_link()`의 결과는 평문 메모리 캐시이며 `SecretStr`은 암호화가 아니다.
아래 보관 계층의 내부 함수에 명시적으로 전달해야 암호화 저장한다. 초기 HTTP 연결은
아래 명시 활성화 경계가 담당하며, 소비자용 getter의 최종 주입과 실제 Microsoft 동의/정책·
Graph 메시지/첨부·실제 외부 갱신은
미검증이다. 합성 MSAL/RSA와 전용 PostgreSQL 검사를 실제 연결·배포 완료로 해석하지 않는다.

### 사서함 보관 계층 (KAN-81 부분 구현, 운영 미적용)

`backend/api/platform/auth/mailbox_vault.py`는 서버 내부에서만 호출한다. 기본 비활성에서
HTTP callback이 보관을 호출하지 않으며 아래 사서함 전용 활성화가 필요하다. 기본 로그인과
기존 인증 점검의 선행 조건을 추가하지 않는다. 소비자 getter와 별개로 인증·보관 담당자만 평문 캐시를 취급한다.

- `MailboxCipher`는 서버에서 주입한 표준 base64 형식의 32바이트 키와 고정 버전 이름으로
  AES-256-GCM을 사용한다. 매 암호화마다 임의 12바이트 nonce를 생성하고, 형식 버전·키
  버전·연결 ID·서비스 소유자·사서함 `tid+oid`·개정 번호를 인증 부가 데이터로 결속한다.
  변조·다른 문맥·잘못된 키/버전은 `MAILBOX_TOKEN_UNAVAILABLE`로 실패한다. 키를 캐시나
  DB에 저장하거나 자동 생성·폴백하지 않는다. 현재 단일 키 버전만 지원하며 자동 키 회전은 없다
  ([AEAD 공식 문서](https://cryptography.io/en/latest/hazmat/primitives/aead/)).
- `create_mailbox()`는 검증된 `MailboxAuthorization`과 현재 세션으로 연결을 저장한다. 현재
  사용자·유효 역할·세션을 DB에서 다시 확인하고, 계정이 결속된 캐시의 암호문만 플랫폼 전용
  `mailbox_auth.connections`에 저장한다. 같은 소유자에 서명 검증된 같은 Microsoft 계정
  (`subject_tid`·`subject_oid`)의 행이 있으면 새 행 대신 그 행을 `connected`·새 암호문·개정
  +1로 갱신해 같은 `mailbox_id`(KAN-71 수집 이력·커서)를 유지하고, 없으면 새 행을 만든다.
  다른 소유자의 같은 계정 행과 다른 계정은 재사용하지 않는다. 잠금 순서는 다른 보관 함수와
  같이 사용자 행 → 연결 행이다. `(owner_user_id, subject)` 유일 제약은 없으므로 같은 계정
  행이 여럿이면 `updated_at DESC, id` 순 첫 행 하나만 갱신한다(중복 방지는 유일 인덱스
  마이그레이션이 필요한 후속 항목). 이메일을 인증 키로 쓰지 않는다.
- `load_mailbox()`는 신뢰할 서버 세션/저장된 작업에서 확인한 소유자 ID를 받는다.
  알 수 없는 연결과 다른 소유자는 같은 `MAILBOX_ACCESS_DENIED`이며 복호화 전에 거부한다.
  `manager`도 타인의 개인 사서함 접근을 허용하지 않는다. 작업용 조회·갱신은 작업 인증과
  현재 사용자의 활성·유효 역할이 필요하며, 브라우저 로그아웃을 사서함 연결 해제로 간주하지 않는다.
- 사용자 행은 배타 잠금으로 보호해 FK를 통한 새 역할·세션 삽입도 직렬화하고, 기존 역할과
  필요한 세션 행에는 공유 잠금을 잡아 현재 Identity 조건을 확인한다. 연결 행 잠금과 개정
  비교를 함께 사용하며, OAuth/Graph 네트워크 호출은 이 트랜잭션 밖에서
  수행한다. 사용자 비활성화·역할 변경·로그아웃과 저장이 교차하는 것을 막고 세션 쓰기 SQL도
  DB 시각의 만료를 검사한다. 잠금/문장 제한시간은 각각3초/5초이며 DB 장애는 고정503으로
  변환한다([PostgreSQL 행 잠금](https://www.postgresql.org/docs/17/explicit-locking.html)).
- `save_mailbox_update()`는 기존 MSAL 결과를 현재 권한·연결·사서함 식별자·개정 재검사 후
  저장한다. 변경 없는 캐시는 다시 쓰지 않는다. 오래된 개정은 내부 보관 전용
  `MAILBOX_VERSION_CONFLICT`/409이며 자동 재시도하지 않는다. 소비자 getter의 오류/401 후
  재시도 계약을 확정한 것은 아니다.
- `disconnect_mailbox()`는 현재 세션 소유자를 확인해 암호문을 NULL로 지우고 개정을
  증가시킨다. 이후 조회·갱신은 `MAILBOX_NOT_CONNECTED`다. `reconnect_mailbox()`는 새 OAuth
  인계와 현재 세션·기대 개정을 검사해 새 암호문과 개정을 저장한다. 인계의 Microsoft 계정
  (`subject_tid`·`subject_oid`)이 기존 행과 다르면 저장 없이 `MAILBOX_ACCESS_DENIED`로 거부해
  같은 `mailbox_id`를 다른 계정으로 바꾸지 않는다(KAN-71 수집 커서 보호). 이전 갱신 결과가 새 연결을
  덮어쓸 수 없다. 명시적 해제/재연결을 구현한 것이며 외부 철회 자동 탐지나 이미 반환한 access
  token의 즉시 무효화, 메모리·백업에서의 물리적 제거까지 보장하지 않는다. 해제·재연결 모두
  연결 행은 삭제하지 않고 같은 ID에서 `state='disconnected'`/`'connected'`로만 바꾼다(KAN-71
  수집 표의 `mailbox_id` FK 보장).

`StoredMailbox`와 캐시는 repr에 내용을 표시하지 않는 보관 전용 메모리 값이다. 캐시로
기존 MSAL 조회·갱신을 수행할 수 있지만 이 계층은 Graph를 호출하지 않는다. KAN-71에
캐시나 refresh token을 반환하지 않으며, 현재 보관 함수를 공개 HTTP API로 노출하지 않는다.
앱 런타임은 DDL을 수행하지 않는다. 운영자는 별도 승인 후 새 마이그레이션·별도 읽기 전용
점검·전용 Secret/IAM/서버 주입을 준비해야 한다. 현재 공용 DB·Secret·IAM·Cloud Run 설정과
배포에는 적용하지 않았다. 운영 순서는 [SQL 안내](../../infra/sql/README.md#사서함-보관-준비-kan-81)를 따른다.

### 보관 캐시의 조회·갱신 연결 (KAN-81 부분 구현)

`backend/api/platform/auth/mailbox.py`의 `acquire_stored_mailbox_token()`은 인증·보관 내부의
연결 함수다. `Settings`, 기존 `PersonalMicrosoftOAuth`, `MailboxCipher`를 서버가 명시적으로
주입하고 연결 ID와 신뢰할 소유자 ID를 전달한다. 위 제공자가 이 내부 함수를 재사용한다.
제공자 import 및 앱 내부 구성은 위 절에 명시했으며, KAN-71의
실제 주입·소비자 합의를 완료한 것은 아니다. HTTP 요청·수집 작업의 인증과 업무 권한
확인을 대신하지 않는다. `owner_user_id`를 브라우저 입력에서 그대로 가져오지 않는다.

- `load_mailbox()`로 현재 소유자·활성·유효 역할·연결을 검사한 뒤 캐시를 복호화한다.
  거부된 연결에 대해 SDK를 호출하지 않는다.
- MSAL 조회·갱신은 DB 트랜잭션 밖의 스레드에서 수행한다. 네트워크를 기다리는 동안
  사용자·역할·연결 행 잠금을 유지하지 않는다.
- SDK 성공 후 항상 `save_mailbox_update()`를 호출한다. 캐시가 바뀌지 않은 적중도 현재
  권한·연결·사서함 식별자·개정을 재검사한다. 변경 캐시는 암호화 저장이 커밋된 뒤에만
  `SecretStr` access token 하나를 반환하고, 변경 없는 캐시는 DB를 다시 쓰지 않는다.
- 제공 중 비활성·유효 역할 상실·연결 해제는 새 토큰을 반환하지 않는다. 재연결이나 다른
  갱신으로 개정이 바뀐 경우 내부 `MAILBOX_VERSION_CONFLICT`를
  `MAILBOX_TOKEN_UNAVAILABLE`/503으로 변환하고 자동 재시도하지 않는다. 동시에 같은 캐시를
  갱신한 두 호출은 커밋한 결과의 토큰만 반환할 수 있다. 회전된 공급자 자격증명을 사용한
  패자의 결과를 다시 저장하거나 성공 토큰으로 반환하지 않는다.
- DB 저장 실패도 토큰 제공 실패다. 기존 SDK의 재연결 필요/일시 장애 분류는 그대로 전달하며
  실패 캐시를 저장하거나 연결 상태를 임의 변경하지 않는다. 이미 반환한 토큰의 즉시 외부
  무효화, 재연결 필요 상태의 영속 표시·자동 복구는 이번 연결 함수가 보장하지 않는다.

실제 MSAL·RSA·PostgreSQL·AES-GCM과 합성 Microsoft HTTP를 연결하는 회귀 범위다.
실제 Microsoft 동의·정책·Graph 요청·실제 외부 갱신, 소비자 fake 교체·HTTP 운영 적용·전용 키
주입은 별도이며, 합성 검사 성공을 실제 사서함 수집 가능 상태로 표시하지 않는다.

### 사서함 초기 연결 HTTP (기본 비활성, 운영 미적용)

기존 로그인과 별도로 웹·플랫폼의 `MAILBOX_AUTH_ENABLED` 기본값은 false다. 플랫폼은
비활성 경로를 DB/공급자 호출 전404로 닫으며 키나 사서함 어댑터를 구성하지 않는다.
기본 로그인은 사서함 키·스키마 없이 유지한다. 웹은 이 플래그가 켜졌을 때만 아래 다섯
경로(목록 GET과 POST 넷)를 고정 플랫폼 upstream으로 중계하고, 브라우저 Authorization은 전달하지 않는다.

| 경로 | 보호 및 결과 |
| --- | --- |
| `GET /api/v1/platform/auth/mailbox` | 읽기라 CSRF 헤더 없이 `/me`와 같은 Origin(있으면 앱 출처와 일치)·현재 운영 세션·활성 사용자를 검사하고 세션 소유자의 연결만 `data.items[]`의 `mailbox_id`·`state`·`revision`·`updated_at`으로 최근 변경 순200 반환. Microsoft 식별자·키 버전·암호문·토큰은 반환하지 않음. DB 장애는 `MAILBOX_TOKEN_UNAVAILABLE`/503. 웹은 GET만 쿼리 없이 중계하고 인증 쿠키·Origin만 전달 |
| `POST /api/v1/platform/auth/mailbox/start` | 현재 운영 세션·동일 Origin·`X-CSRF-Token` 검사 후 일회 인가를 저장하고 `data.authorization_url`만200 반환 |
| `POST /api/v1/platform/auth/mailbox/callback` | Microsoft의 제한된 form_post를 같은 출처 완료 요청으로 넘기는 무상태 HTML200. 이 단계는 DB 소비·교환·저장을 하지 않음. 오류(비활성404·잘못된 입력400 등)는 HTML이 아닌 공통 JSON `ErrorResponse` |
| `POST /api/v1/platform/auth/mailbox/complete` | 동일 Origin·현재 운영 세션·별도 브라우저 결속·사서함 목적·소유자·일회 state를 검증하고 암호화 커밋 후 고정 앱 루트 `/`로303. 같은 소유자의 같은 Microsoft 계정 연결이 있으면 그 행을 재연결(같은 `mailbox_id`, 개정 +1)하고 없으면 새 연결 |
| `POST /api/v1/platform/auth/mailbox/{mailbox_id}/disconnect` | 동일 Origin·현재 세션 결속 CSRF·현재 소유자를 검사하고 암호문 제거 커밋 후204. 외부 Microsoft 즉시 철회가 아님. 연결 행은 삭제하지 않고 `state='disconnected'`로 유지(KAN-71 FK 보장) |

시작은 별도 `__Host-team4_mailbox_oauth` 쿠키(5분·Secure·HttpOnly·SameSite=Lax·Path=/)를
설정한다. 로그인 결속 쿠키와 구분하며 원문 세션·해시·캐시·토큰을 복사하지 않는다. 성공
완료는 이 쿠키만 삭제하고 기존 서비스 로그인·역할·세션 쿠키를 변경하지 않는다.

기존 Lax 세션은 Microsoft의 교차 사이트 POST에 실리지 않는다. 첫 callback 문서는
code/state 두 hidden 입력만 HTML escape해 같은 출처 완료 경로로 전달한다. URL·스크립트에
code/state를 넣거나 다른 공급자 필드를 반사하지 않는다. `no-referrer`에서 일반 form 제출은
Origin:null이 될 수 있으므로 nonce CSP가 허용한 고정 스크립트가 같은 출처 fetch를 사용한다.
완료 요청의 Origin 검사를 완화하지 않으며 state와 별도 결속으로 OAuth CSRF를 검사한다.
시작/해제의 일반 변경 요청과 달리 이 완료 POST는 별도 CSRF 헤더를 요구하지 않는다.

문서는 no-store·no-referrer, nonce script·connect-src self·form-action none·base-uri none·
frame-ancestors none CSP를 적용하고 웹도 CSP를 보존한다. 완료의303 응답을 받은 뒤에만
고정 루트로 이동한다. 오류/통신 실패는 연결 성공으로 바꾸지 않으며 고정 안내만 표시한다.
JavaScript가 꺼져 있으면 활성화 후 새 연결을 안내한다. 두 form 입력 경로는 query를
거부하고 기존8KiB/20필드·중복 보존 검사를 재사용한다. 소비한 인가는 실패 후 복구하지 않는다.

플랫폼 활성화에는 기존 Microsoft 로그인 설정 외에 아래 전용 설정이 모두 필요하다.

- `MAILBOX_AUTH_REDIRECT_URI`: 기존 Web 앱에 추가 등록할 동일 출처의 정확한
  `/api/v1/platform/auth/mailbox/callback`. 로그인 callback을 교체하지 않는다.
- `MAILBOX_AUTH_ENCRYPTION_KEY`: 플랫폼만 읽는 고정 Secret 버전의 표준 base64 32바이트 키.
- `MAILBOX_AUTH_KEY_VERSION`: 보관 암호문의 키 버전에 결속할 비밀이 아닌 고정 이름.

불완전한 활성 설정은 서버 시작 시 원문 없는 오류로 거부한다. 키는 웹·브라우저에 주입하지
않으며 앱이 Secret/IAM/DB를 생성하지 않는다. 공용에서는 최신 스키마·권한을 재조회하고
필요한 보관 마이그레이션을 한 번 적용/004 읽기 전용 점검한 뒤 운영자가 키·IAM·설정을 준비한다.
기존 identity7테이블 점검이나 계정 등록을 반복하지 않는다. main 배포와 활성화는 별도 승인이다.

이번은 초기 연결/해제 HTTP까지다. 연결 목록·재연결 UI/HTTP·소비자 getter의 최종 주입,
실제 Microsoft 동의/Graph/외부 refresh·운영 설정 검증과 재연결 필요 상태 영속 표시는 남아 있다.
합성 HTTP/MSAL·자체 DB·독립 Chrome 검사를 실제 Outlook/공용 환경 완료로 확대하지 않는다.

**남은 합의·검증:** 위 제공자의 import·주입 방식과 오류 코드/상태 매핑에 대한 소비자 확인,
갱신 여유시간·외부 요청
제한시간, 위 내부 경합 처리를 소비자에게 연결하는 방식, 일시 장애의 제한된 재시도 책임을
확인해야 한다. getter의
성공이 뒤따르는 Graph 요청 성공까지 보장하지는 않는다. Graph의 401 이후 강제 갱신 신호와
재시도 방식은 [Graph 401 처리 제안](#graph-401-처리-제안-kan-71-확인-전-미구현)으로 KAN-71
확인을 기다리며, KAN-71이 임의로 갱신하거나 무한 재시도하지 않는다. 이 초안의
구문·링크 확인은 fake 소비자 연동·실제 Graph·토큰 암호화·공용 배포 검증의 증거가 아니다.
