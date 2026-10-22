# 외부 연동 사전 확인

로컬 개발 시작에는 필요하지 않다. 교육장 계정과 실제 앱 연동을 확인하는 담당자가 실행한다. 키와 시크릿을 채팅, Git 또는 프론트 VITE 변수에 넣지 않는다. 기본 절차는 읽기 전용이며, --mail-roundtrip-to를 명시했을 때만 합성 메일 1통을 실제 발송한다.

## 로컬 설정

파일이 아직 없을 때만 복사하고 로컬에서 편집한다. 기존 파일이 있으면 보존한다.

```bash
cp -n .env.integrations.example .env.integrations
```

예시에 사용자 제공 테넌트와 애플리케이션 ID를 넣었다. MS_CLIENT_SECRET은 Microsoft 연동 확인 때만 입력한다. 실제 모델 호출에는 GEMINI_API_KEY가 필요하다. 기존 .env에 키가 있다면 --env-file .env로 사용하며 파일을 복사하거나 덮어쓰지 않는다. 로컬 fake 개발에는 키나 gcloud ADC가 필요 없다.

## 기본 모델 경로: Gemini API

Google Gen AI SDK의 Gemini Developer API를 API 키로 호출한다. 초기 모델은 gemini-3.6-flash이며 최종 업무 모델과 임베딩 차원은 평가 후 결정한다. 일시적인 생성 차단을 영구 제약으로 보지 않기로 사용자와 결정했고, 기존 키로 합성 생성 2건의 성공을 다시 확인했다.

```bash
uv run --frozen python scripts/check_integrations.py gemini --env-file .env
uv run --frozen python -m eval.smoke --provider gemini --model gemini-3.6-flash --env-file .env
```

첫 명령은 지원 모델 목록만 조회하고 두 번째는 영어 및 한국어 합성 JSON을 한 번씩 생성한다. 키가 .env.integrations에 있으면 해당 파일을 --env-file에 지정한다. JSON 및 HTML에는 스키마와 지연, 입력·답변·추론·캐시·총 토큰을 기록한다. 키, 원문 오류와 내부 추론 내용은 저장하지 않는다. 가격표가 없으면 비용은 미산정이다. 앱 시작, health, ready 및 CI에서는 모델을 호출하지 않는다. 자동 재시도나 다른 제공자로의 폴백은 없다.

2026-10-22 실제 결과: gemini-3.6-flash로 영어 2677.99ms / 총 184토큰, 한국어 2090.67ms / 총 208토큰이며 두 건 모두 JSON 스키마와 STOP을 확인했다. 결과는 artifacts/eval/bb247d49-a5b6-48f9-a2b5-e84233f8e69e.json 및 .html이다. 합성 연결 점검 결과이며 업무 성능 합격 판정은 아니다. 최종 test 597건은 사용하지 않았다.

같은 공통 SDK의 Gemini API 경로로 gemini-embedding-001 합성 문장 1건도 실제 성공했고 3072차원을 관측했다. 벡터와 키는 출력하지 않았다. 이 모델과 차원은 최종 검색 평가로 확정한 값이 아니다.

임베딩 모델 목록과 지원 차원은 별도로 확인한다. 합성 문장 한 건을 호출하려면 다음 명령을 사용한다. 관측 차원만으로 production DB 차원을 바꾸지 않는다.

```bash
uv run --frozen python scripts/check_integrations.py gemini --env-file .env --embed-model gemini-embedding-001
```

### Cloud Run의 키 준비

인프라 담당자가 기존 키를 Secret Manager에 준비한다. 키를 이미지, Git, 프론트 VITE 변수나 GitHub Secret으로 복제하지 않는다. 이 스크립트는 기존 Secret 값과 버전은 보존하고 C/S·TFT 실행 계정에 해당 Secret의 읽기 권한을 추가한다. 기존의 다른 IAM 바인딩은 제거하지 않는다.

```bash
uv run --frozen python infra/register_gemini_secret.py
uv run --frozen python infra/register_gemini_secret.py --apply
```

기본 실행은 미리보기만 수행한다. --apply는 인프라 담당자의 터미널에서 실행한다. 배포 설정은 두 앱에 AI_PROVIDER=gemini와 GEMINI_API_KEY=gemini-api-key:1을 전달하며 웹과 플랫폼에는 키를 주지 않는다. 버전 1을 고정하며 키 회전은 별도 작업이다. Cloud Run의 인바운드 IAM과 GitHub OIDC는 계속 필요하지만 Gemini 호출을 위해 Vertex ADC나 추론 IAM을 준비할 필요는 없다.

사용자가 --apply를 실행해 기존 버전 보존과 두 실행 계정의 권한 추가 성공을 보고했다. 새 설정의 실제 Cloud Run 반영과 실행 신원에서의 Gemini API 호출은 다음 main 배포 후 확인한다. 기존 Vertex IAM은 이번 작업에서 변경하지 않았다. 공통 클라이언트의 선택형 Vertex 어댑터와 과거 진단 도구는 호환용이며 온보딩 필수 단계가 아니다.

사용법은 [공통 AI 클라이언트](ai-client.md), 배포는 [서비스별 CI와 CD](service-delivery.md)를 따른다. 근거: [Gemini API 키](https://ai.google.dev/gemini-api/docs/api-key), [Cloud Run Secret 설정](https://docs.cloud.google.com/run/docs/configuring/services/secrets).

## Microsoft

먼저 테넌트의 OpenID 경로만 확인한다. 이 성공은 앱 권한이나 메일함 사용 가능성을 뜻하지 않는다.

```bash
uv run --frozen python scripts/check_integrations.py microsoft
```

실제 로그인 전 Entra 앱 등록에서 다음을 설정한다.

1. 인증의 Web 플랫폼에 http://localhost:8765/callback 리디렉션 URI를 추가한다. 실제 서비스의 production 콜백은 별도로 설정한다.
2. Graph 위임 권한 Mail.Read와 Mail.Send를 확인한다. 이미 추가했다면 반복 추가하지 않는다. 테넌트의 사용자 동의 정책에 따라 관리자 동의가 필요할 수 있다.
3. 인증서 및 비밀에서 클라이언트 시크릿을 만들거나 기존 유효한 값을 사용하고 .env.integrations의 MS_CLIENT_SECRET에만 입력한다. 시크릿 값은 채팅에 보내지 않는다.

브라우저를 사용할 수 있는 본인 PC 터미널에서 실행한다.

```bash
uv run --frozen python scripts/check_integrations.py microsoft --login
```

MSAL이 PKCE와 state가 포함된 로그인 흐름을 처리한다. 로그인 후 Graph에서 받은 편지함 메시지 ID 최대 1개만 요청하며 ID와 본문을 출력하지 않는다. 로그인, Mail.Send 동의 여부와 Mail.Read 호출 결과만 출력한다. 토큰은 메모리에만 있고 종료 시 저장하지 않는다. 이 스크립트의 성공이 앱의 production 로그인 및 토큰 보관 구현을 대신하지 않는다.

메일함 라이선스 또는 테넌트 정책으로 Graph가 실패하면 표시된 HTTP 코드와 Azure 설정을 확인한다. Mail.Send 동의 확인은 실제 메일 발송 테스트와 구분한다. 원문 메일과 토큰이 있는 HTTP 응답을 그대로 공유하지 않는다.

### 개인 Outlook 계정으로 사전 검증

조직 관리자 승인이 어려워 개인 Outlook.com 계정의 별도 검증을 준비했다. Entra 앱의 지원 계정 유형을 조직과 개인 계정 포함으로 설정하고 기존 Web 콜백 및 유효한 시크릿을 유지한다. 매니페스트 값은 AzureADandPersonalMicrosoftAccount다. 앱 설정을 바꾸기 어려우면 그 원인을 확인하고 별도 앱 사용 여부를 결정한다.

```bash
uv run --frozen python scripts/check_integrations.py microsoft --personal-account
uv run --frozen python scripts/check_integrations.py microsoft --personal-account --login
```

이 옵션은 해당 실행에만 consumers authority와 개인 계정의 고정 테넌트를 사용한다. 기존 MS_TENANT_ID와 시크릿을 덮어쓰지 않고 임의 조직 테넌트를 허용하지 않는다. 실제 로그인은 개인 계정을 선택해 수행한다. --mail-roundtrip-to가 없으면 메일 읽기만 확인하며 발송하지 않는다. 사용자 터미널에서 개인 계정의 실제 로그인, Mail.Send 동의 및 Graph Mail.Read 호출 성공을 확인했다. 받은편지함 조회는 0건이었으며 실제 발송과 수신은 별도 검증한다.

개인 계정에서는 Entra 앱 역할이 실행 시 지원되지 않으므로 앱 역할 claim에 의존하지 않는다. 확정된 DB 등록 사용자와 역할 정책을 유지하고 등록되지 않은 사용자에게 자동 권한을 주지 않는다. 이 사전 점검은 production 사용자 등록이나 전체 인증 기능 구현을 대신하지 않는다. [지원 계정 유형과 앱 역할 제한](https://learn.microsoft.com/en-us/entra/identity-platform/supported-accounts-validation), [Graph 위임 권한](https://learn.microsoft.com/en-us/graph/permissions-reference#mailread)을 참고한다.

Gmail 주소를 로그인 이름으로 쓰는 Microsoft 개인 계정도 있다. Microsoft 계정 보유와 Outlook 사서함 보유는 따로 확인한다. Outlook 웹에서 실제 받은편지함이 열리는지 확인하며 Gmail 주소라는 이유만으로 OAuth 실패 원인이나 사서함 부재를 단정하지 않는다. [Microsoft 계정의 외부 이메일 주소 안내](https://support.microsoft.com/en-us/accounts-billing/subscriptions/connecting-a-microsoft-subscription-to-outlook).

사용자는 지원 계정 유형을 조직 및 개인 포함으로 설정했다고 보고했고 Gmail 주소로 로그인한 결과 OAuth invalid_request가 발생했다. 후속 진단에서 AADSTS90023과 token_exchange 단계를 확인했고 사용자 화면에서 콜백이 SPA로 등록된 것을 확인했다. Web 플랫폼으로 옮기도록 안내한 뒤 실제 로그인과 Mail.Read가 성공했다. Python의 시크릿 기반 서버 토큰 교환은 Web 콜백으로 구성한다. 진단 출력은 OAuth 오류 종류와 AADSTS 숫자 및 authorization 또는 token_exchange 단계만 남긴다. 오류 설명 원문, 이메일, 시크릿, 인증 코드와 state 및 콜백 URL은 출력하지 않는다. 같은 명령으로 다시 확인한 FAIL 행만 공유한다. AADSTS가 제공되지 않으면 not_provided로 표시한다. [공식 오류 코드 안내](https://learn.microsoft.com/en-us/entra/identity-platform/reference-error-codes).

로그인 요청은 select_account를 지정해 기존 브라우저 세션이 자동 선택되지 않도록 한다. 계정 선택 전에 앱 요청이 거절되면 여전히 오류가 날 수 있으므로 이 설정만으로 원인이 해결됐다고 판단하지 않는다. 사용자는 Outlook 계정을 만든 뒤 실제 받은편지함이 보인다고 확인했고, 이후 OAuth와 Graph Mail.Read 성공을 보고했다. 이 성공은 로컬 사전 검증이며 production 로그인, 토큰 보관과 실제 메일 발송까지 완료했다는 의미는 아니다.

### 합성 메일 발송 및 수신 검증

기존 --login은 발송하지 않는다. 실제 발송을 확인하려면 본인 Outlook 주소로 바꿔 아래 명령을 직접 실행한다. 이 명령은 지정한 주소로 합성 메일 1통을 실제 발송한다. 수신 주소가 로그인한 본인 메일함의 주소여야 같은 세션에서 수신도 확인할 수 있다. 주소를 채팅이나 Git에 넣지 않는다.

```bash
uv run --frozen python scripts/check_integrations.py microsoft --personal-account --login \
  --mail-roundtrip-to '본인주소@outlook.com'
```

제목은 Team4 mail integration check와 고유한 합성 probe 값이며 본문에는 합성 연결 점검 문장만 들어간다. 실제 업무 데이터, 첨부와 추가 수신자는 없다. 발송 POST는 한 번만 실행하고 자동 재시도하지 않는다. 토큰은 프로세스 메모리에만 유지한다.

Graph의 HTTP 202는 접수이며 배달 완료를 보장하지 않는다. 스크립트는 약 60초 동안 보낸편지함과 로그인한 받은편지함 및 정크메일함에서 해당 고유 제목을 조회한다. 보낸편지함 저장과 받은편지함 또는 정크메일함 수신 중 하나를 확인해야 PASS를 출력한다. signed_in_delivery_confirmed는 수신 여부이며 signed_in_inbox_confirmed 및 signed_in_junk_confirmed로 위치를 구분한다. 정크 수신을 받은편지함 도착으로 표시하지 않는다. 주소와 원문 및 메시지 ID는 출력하지 않는다. PENDING이면 접수 후 아직 확인되지 않은 상태이므로 같은 메일이 미발송됐다고 단정하거나 자동 재발송하지 않는다. 출력된 합성 probe로 메일함에서 확인한다. 실행마다 새 합성 메일이 생성되므로 불필요하게 명령을 반복하지 않는다. [공식 sendMail 응답 의미](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0).

이 옵션의 로컬 검증은 단일 발송, 정확한 합성 제목 조회, 접수와 수신 구분, 실패 시 무재시도와 민감값 비노출을 확인했다. 사용자 실제 실행에서 HTTP 202 접수와 보낸편지함의 합성 메일을 확인했다. probe 71d7a8e27a35472e9ed13ac32bed74c2는 60초 내 받은편지함에서 찾지 못해 PENDING이었다. 자동 재발송은 0회이며 사용자에게 보낸 메일의 수신 주소와 정크메일함 및 받은편지함 위치 확인을 요청했다. 배달 완료로 표시하지 않는다. 관련 코드의 원격 CI 39711845319도 성공했다.

후속 확인에서 사용자가 입력한 수신 도메인 .com과 계정에 등록된 Outlook 별칭의 .kr이 달랐다. 사용자 화면에서 Gmail과 Outlook 주소가 같은 Microsoft 개인 계정의 별칭임을 확인했다. Outlook 설정에는 outlook_문자숫자@outlook.com 형태도 보였으며 사용자가 Outlook 별칭을 기본으로 지정했다. 이 변경만으로 배달 성공이나 404 원인을 확정하지 않는다.

기본 별칭 변경 후 올바른 수신 주소로 실행한 probe 8ee0827413e24ea2a0df5a3143858f67은 로그인과 Mail.Send 동의 및 Mail.Read를 통과했지만 POST /me/sendMail에서 HTTP 404가 발생했다. 이 시도는 HTTP 202 접수를 확인하지 못했고 수신도 미검증이다. 기존 출력에는 Graph 세부 코드가 없어 원인은 아직 미확정이다. 사서함 초기화나 별칭 전파 및 관리자 동의 문제로 단정하지 않는다.

Graph 요청 오류는 HTTP 상태, 실패 단계(mail_read, send_mail, sent_items, inbox, junkemail)와 허용 목록의 code만 출력한다. 오류 본문은 메모리에서 크기를 제한해 읽고 message, 요청 주소, 이메일과 토큰 및 요청 ID를 출력하지 않는다. 인식하지 못한 코드는 not_provided_or_unrecognized로 표시한다. 발송과 조회 오류를 구분하고 자동 재발송은 하지 않는다. Graph 오류의 code와 message 구분은 [공식 오류 응답 안내](https://learn.microsoft.com/en-us/graph/errors)를 따른다. 로컬 scripts 테스트 31건과 Ruff 및 포맷 검사를 통과했다. 후속 실제 결과는 아래와 같다. 추가 발송은 사용자가 직접 실행했다.

최신 실제 검증: 사용자가 웹 발신 주소를 본인 Outlook 별칭으로 선택한 뒤 발송했고 Gmail에서 해당 별칭으로 보낸 메일의 수신도 확인했다. 이어 사용자 CLI의 probe 0e977472cfe7449a8f46a0e8526d7062가 로그인, Mail.Send 동의, Mail.Read, HTTP 202 접수 및 보낸편지함 저장을 통과했다. 사용자 화면에서 같은 probe의 합성 메일이 정크메일함에 도착한 것을 확인했다. 따라서 로컬 Microsoft OAuth 및 Graph 읽기와 실제 발송 및 수신의 연결 가능성은 확인됐다. 이전 HTTP 404는 이번 실행에서 재현되지 않았지만 이전 오류의 근본 원인은 미확정이다.

기존 CLI는 받은편지함만 조회해 이 실제 수신을 PENDING으로 표시했다. 이를 보완해 Graph의 표준 폴더 이름 junkemail도 읽기 전용으로 확인한다. 메일 이동, 정크 해제, 주소록 및 허용 목록 변경이나 권한 추가는 하지 않는다. 사용자에게 메일 화면의 정크 메일이 아닙니다 버튼으로 직접 분류를 정정할 수 있음을 안내한다. [Graph 표준 폴더 이름](https://learn.microsoft.com/en-us/graph/api/resources/mailfolder?view=graph-rest-1.0)을 따른다. 정크 수신과 받은편지함 수신 구분 및 발송 저장 미확인 시 PENDING을 포함한 로컬 scripts 테스트 33건과 Ruff 및 포맷 검사를 통과했다. 수정 후 정크메일함의 실제 API 조회는 아직 실행하지 않았다. 이미 화면으로 수신을 확인했으므로 이 수정만을 위해 합성 메일을 재발송할 필요는 없다. 제품의 서버 세션, 토큰 보관 및 갱신, 역할과 동기화 및 승인 후 발송 구현은 여전히 별도다.

## 호출 경로 변경 이력

이전 점검에서 Gemini Developer API에 일시적인 HTTP 429와 월간 지출 한도 메시지가 발생해 대체 경로를 검증한 이력이 있다. 이후 사용자 확인과 실제 Gemini API 생성 재검증을 근거로 기본 경로를 API 키 방식으로 되돌렸다. 과거 오류를 현재의 지속적인 차단 상태로 표시하지 않는다.

## Microsoft 관리자 동의에서 막힐 때

WSL에서는 스크립트가 Windows 기본 브라우저를 호출한다. 실패하면 같은 프로세스가 기다리는 일회용 로그인 URL을 터미널에 표시한다. URL은 본인 브라우저에서만 사용하고 채팅에 붙이지 않는다. 토큰과 시크릿은 출력하지 않는다.

사용자는 로그인 화면에서 관리자 승인 필요 메시지를 확인했고 Entra의 관리자 동의 부여 버튼이 비활성화돼 있다고 보고했다. API 권한 표의 관리자 동의 필요 '아니요'는 권한 자체의 속성이다. 조직의 사용자 동의 및 미검증 앱 정책은 별개이므로 해당 표시만으로 조직 승인이 불필요하다고 판단하지 않는다.

관리자에게 전달할 요청 문구:

> 개발용 앱 ls-cs-rfp-agent의 Microsoft 로그인 및 메일 연동 검증을 위해 관리자 동의가 필요합니다. 앱 ID는 7160833b-2aa3-497f-bc6c-11c59851c254이고 테넌트 ID는 6c2ea5ee-418c-4e13-8eaa-5163cdb50c67입니다. 요청하는 Graph 위임 권한은 Mail.Read, Mail.Send, offline_access입니다. 시연용 사용자에 대해 사용할 수 있는 승인 방식과 메일함 사용 가능 여부를 확인 부탁드립니다.

관리자에게 메시지를 대신 보내지는 않았다. 승인 후 로그인 및 Mail.Read를 재검증하고, Mail.Send 동의와 실제 테스트 메일 발송 및 수신은 구분해 확인한다. 현재는 실제 로그인, 읽기 및 발송 성공을 확인하지 못했다. [Microsoft 관리자 동의 안내](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/grant-admin-consent)를 참고한다.
