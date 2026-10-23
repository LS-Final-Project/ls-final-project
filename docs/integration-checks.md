# 외부 연동 점검

인증·메일·AI 연동 담당자가 필요할 때 사용한다. 일반 로컬 개발에는 필요 없다. 개인 Outlook의 로그인·Graph 읽기·실제 송수신과 Gemini API의 생성·임베딩 호출은 사전 검증됐다. 제품 안의 세션·토큰·메일 및 업무 AI 통합은 담당 티켓에서 구현한다.

## 로컬 설정

파일이 없을 때만 복사하고 로컬에서 편집한다. 기존 `.env*`는 보존하며 키·시크릿을 Git·채팅·프론트 VITE 변수에 넣지 않는다.

```bash
cp -n .env.integrations.example .env.integrations
```

실제 모델 호출은 `GEMINI_API_KEY`, Microsoft 로그인 점검은 앱의 유효한 `MS_CLIENT_SECRET`을 사용한다. 기존 `.env`의 키를 쓸 때는 `--env-file .env`를 지정한다. fake 개발은 둘 다 필요 없다.

## Gemini API

기본 제공자는 Gemini Developer API, 초기 생성 모델은 `gemini-3.6-flash`다. 최종 업무 모델·임베딩 모델과 차원은 평가 후 확정한다.

```bash
uv run --frozen python scripts/check_integrations.py gemini --env-file .env
uv run --frozen python -m eval.smoke --provider gemini --model gemini-3.6-flash --env-file .env
```

첫 명령은 지원 목록 조회, 두 번째는 영어·한국어 합성 JSON 생성이다. JSON·HTML 결과에는 스키마, 지연 및 입력·답변·추론·캐시·총 토큰을 기록한다. 가격표가 없으면 비용은 미산정이다. 자동 재시도·제공자 전환은 없으며 앱 시작·health·ready·CI에서는 실제 모델을 호출하지 않는다.

임베딩 합성 점검은 아래 명령을 사용한다. 관측 차원만으로 DB 인덱스를 바꾸지 않는다.

```bash
uv run --frozen python scripts/check_integrations.py gemini --env-file .env --embed-model gemini-embedding-001
```

호출 예시는 [AI 클라이언트](ai-client.md)를 따른다. 연결 점검에 최종 test 597건을 사용하지 않는다.

### 배포 키 관리

C/S와 TFT의 배포 설정은 Secret Manager의 `gemini-api-key:1`을 `GEMINI_API_KEY`로 전달한다. 두 실행 계정의 접근 권한은 준비돼 있다. 웹과 플랫폼에는 모델 키를 주지 않는다. dev에 준비된 설정의 실제 적용은 다음 main 배포 후 확인한다.

새 프로젝트를 준비하거나 접근 권한을 다시 구성할 때만 인프라 담당자의 터미널에서 실행한다. 기본은 미리보기이며 기존 값과 버전을 보존한다. 키 회전은 별도 절차다.

```bash
uv run --frozen python infra/register_gemini_secret.py
uv run --frozen python infra/register_gemini_secret.py --apply
```

## Microsoft 로그인과 메일

담당은 오새결(KAN-77·80·81)이다. 시연 연결은 개인 Microsoft 계정의 실제 Outlook 사서함으로 검증한다. 앱의 지원 계정 유형은 조직과 개인 계정을 포함하고, 서버의 시크릿 기반 토큰 교환을 사용한다.

1. Entra 앱 등록 → 인증 → **Web** 플랫폼의 리디렉션 URI에 `http://localhost:8765/callback`이 있어야 한다. 같은 URI를 SPA로 등록하지 않는다. 제품 production 콜백은 별도로 등록한다.
2. Graph 위임 권한 `Mail.Read`, `Mail.Send`, `offline_access`를 확인한다.
3. 유효한 시크릿은 `.env.integrations`에만 넣는다. 브라우저에서 실제 Outlook 받은편지함이 열리는 계정을 사용한다. 로그인 이름과 수신 주소는 다를 수 있으므로 실제 사서함 주소를 확인한다.

```bash
uv run --frozen python scripts/check_integrations.py microsoft --personal-account
uv run --frozen python scripts/check_integrations.py microsoft --personal-account --login
```

첫 명령은 OpenID 경로만 확인한다. `--login`은 계정 선택과 실제 Graph 읽기를 확인하며 발송하지 않는다. 개인 계정 옵션은 해당 실행에서 consumers authority를 사용하고 기존 테넌트 설정을 덮어쓰지 않는다. 토큰은 프로세스 메모리에만 유지한다. 조직 계정 점검은 `--personal-account`를 빼고 해당 조직의 동의 정책과 메일함 사용 권한을 확인한다.

WSL에서는 Windows 기본 브라우저를 사용한다. 자동으로 열리지 않으면 같은 프로세스가 기다리는 일회용 URL을 본인 브라우저에서 연다. 공유할 결과는 PASS 또는 민감값을 제외한 FAIL 코드·단계뿐이다.

### 필요한 경우에만 실제 송수신 확인

아래 명령은 지정한 본인 사서함으로 합성 메일 **1통을 실제 발송**한다. 주소를 실제 Outlook 수신 주소로 바꾼다. 이미 성공한 사전 검증을 온보딩 목적으로 반복하지 않는다.

```bash
uv run --frozen python scripts/check_integrations.py microsoft --personal-account --login \
  --mail-roundtrip-to '본인주소@outlook.com'
```

HTTP 202는 발송 접수다. 스크립트는 보낸편지함 저장과 받은편지함 또는 정크메일함의 동일 합성 제목 수신을 확인한다. 정크 수신을 받은편지함 도착으로 표시하지 않는다. 발송은 자동 재시도하지 않으며 PENDING이면 메일함에서 해당 제목을 먼저 확인한다. 명령을 반복할 때마다 새 메일이 발송된다.

서비스 사용자 키는 `tid + oid`이며 DB에 등록된 사용자·역할·부서·프로젝트 범위를 검사한다. 로그인 성공이나 메일 동의가 역할 자동 부여를 허용하지 않는다. 점검 스크립트는 제품의 토큰 보관·갱신·세션 및 승인 후 메일 발송 구현을 대신하지 않는다.

### KAN-77 제품 로그인 — 기존 공용 웹 검증

위 `--login`은 사전 점검용 `http://localhost:8765/callback`을 사용한다. 제품 로그인은
[인증 계약](../contracts/platform/auth.md#공개-http-계약-제안--기본-비활성-구현-초안)의
HTTP 경로·DB 세션·쿠키·CSRF를 실제 브라우저에서 확인한다. 사전 점검 성공과 제품
검증 성공을 구분한다. 세션 8시간 절대 만료·유휴 만료·앱 진입점·실패 UI는 KAN-57의
제안/미확정 표기를 유지하며, 검증 결과로 정책이 확정됐다고 보고하지 않는다.

기존 공용 웹을 사용하면 주소는 다음과 같다. Entra에는 **Web** 플랫폼으로 등록해야 한다.
사전 점검의 localhost URI는 유지하고 제품 URI 등록 여부를 별도로 확인한다.

```text
앱 origin: https://team4-web-gew6ci4pgq-du.a.run.app
Web callback: https://team4-web-gew6ci4pgq-du.a.run.app/api/v1/platform/auth/callback
로그인 시작: https://team4-web-gew6ci4pgq-du.a.run.app/api/v1/platform/auth/login
```

적용 순서는 다음과 같다. 공용 GCP 변경은 운영 담당자가 수행한다.

1. [배포 절차](service-delivery.md#배포-판단)에 따라 대상 커밋·포함 변경·서비스별 배포
   범위를 확인한다. dev 병합만으로는 배포되지 않는다. main 반영은 누적 변경을 포함하며,
   CD 활성 상태에서는 해당 main의 CI 성공 후 변경 서비스가 자동 배포된다.
2. 공용 DB의 `identity.users`, `identity.user_roles`, `identity.oauth_authorizations`,
   `identity.sessions`와 플랫폼 실행 권한을 확인한다. 누락된 마이그레이션은 별도로 적용한다.
   이미지 배포는 DB 마이그레이션을 자동 실행하지 않는다. KAN-78의 실제 검증 사용자
   `tid + oid`·활성 상태·역할·필요한 부서 등록을 확인한다. 시연 사용자로 대체하지 않는다.
3. 제품 인증 코드를 포함한 platform과 인증 중계를 포함한 web을 배포한 뒤, 인증 비활성
   상태의 로그인 503을 확인한다. web에서 404면 해당 경로의 배포·중계 준비부터 확인한다.
4. 기존 Entra 앱의 위 Web callback 등록을 확인한다. 플랫폼 서버에 인증 계약의
   `MICROSOFT_AUTH_CLIENT_ID`, `MICROSOFT_AUTH_CLIENT_SECRET`, `MICROSOFT_AUTH_APP_ORIGIN`,
   `MICROSOFT_AUTH_REDIRECT_URI`를 연결하고 `DEV_AUTH_ENABLED=false`를 유지한다.
   시크릿은 Secret Manager 참조로 주입한다. 사전 점검의 `MS_*` 설정은 제품 앱이
   자동으로 읽거나 변환하지 않는다. 준비가 끝난 뒤 `MICROSOFT_AUTH_ENABLED=true`로 활성화한다.

로그인 화면이 아직 연결되지 않았어도 브라우저에서 위 로그인 시작 주소를 직접 연다.
검증할 본인의 등록된 개인 Outlook 계정을 선택한다. 이 단계는 메일 권한을 요청하거나
메일을 발송하지 않는다. 성공하면 POST callback 후 고정 앱 루트로 이동하며,
`GET /api/v1/platform/me`가 200으로 등록 사용자와 DB 역할을 반환해야 한다.
다시 로그인해 세션 교체를 확인하고 로그아웃 전후의 상태도 확인한다.

화면 연결 전 로그아웃은 **같은 공용 웹의 개발자 도구 Console**에서 다음처럼 검사할 수 있다.
세션 쿠키는 HttpOnly이며 이 코드로 읽지 않는다. `/me`에서 받은 CSRF 값은 요청에만 사용한다.

```javascript
const beforeLogout = await fetch('/api/v1/platform/me', { credentials: 'same-origin', cache: 'no-store' });
const loginCsrf = beforeLogout.headers.get('X-CSRF-Token');
if (beforeLogout.status !== 200 || !loginCsrf) throw new Error('현재 로그인과 CSRF 응답을 먼저 확인하세요.');
const logoutResult = await fetch('/api/v1/platform/auth/logout', {
  method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': loginCsrf },
});
const afterLogout = await fetch('/api/v1/platform/me', { credentials: 'same-origin', cache: 'no-store' });
console.info({ before: beforeLogout.status, logout: logoutResult.status, after: afterLogout.status });
```

정상 기대값은 `{ before: 200, logout: 204, after: 401 }`이다. 브라우저의 재로그인 후
이전 세션 거부, 미등록 계정의 401과 자동 등록 없음도 확인한다. code·state·쿠키·토큰·
개인 계정 식별자 원문은 Jira에 붙이지 않고 대상 배포 SHA·단계·상태·검사 결과만 기록한다.
실패는 실제 단계와 원인을 구분하며 503을 로그인 성공으로, 404를 설정 비활성으로 처리하지 않는다.
Graph·첨부·토큰 갱신은 KAN-80, 토큰 영속 보관·연결 해제는 KAN-81에서 검증한다.

참고: [Gemini API 키](https://ai.google.dev/gemini-api/docs/api-key), [Microsoft 인증 코드 흐름](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow), [Graph sendMail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0).
