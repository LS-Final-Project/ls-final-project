# 외부 연동 사전 확인

로컬 개발 시작에는 필요하지 않다. 교육장 계정과 실제 앱 연동을 확인하는 담당자가 실행한다. 키와 시크릿을 채팅, Git 또는 프론트 VITE 변수에 넣지 않는다. 아래 절차는 메일을 발송하지 않는다.

## 로컬 설정

파일이 아직 없을 때만 복사하고 로컬에서 편집한다. 기존 파일이 있으면 보존한다.

```bash
cp -n .env.integrations.example .env.integrations
```

예시에 사용자 제공 테넌트와 애플리케이션 ID를 넣었다. MS_CLIENT_SECRET은 Microsoft 연동 확인 때만 입력한다. 현재 기본 모델 경로인 Vertex에는 GEMINI_API_KEY가 필요하지 않다. GCP용 기존 .env를 수정할 필요가 없다.

## 현재 기본 모델 경로: Vertex AI

교육장 프로젝트의 Vertex AI global과 gemini-3.6-flash를 초기 연결 기준으로 사용한다. 기존 Developer API의 청구 계정 월간 지출 한도 오류가 발생한 뒤 이 경로에서 실제 생성과 스키마 기록을 확인했다. 최종 업무 모델은 품질, 지연과 비용을 비교한 뒤 선정한다. Cloud Run과 DB는 서울이며 global 모델 호출은 서울 내 처리만을 보장하지 않는다.

로컬 호출에는 ADC가 필요하다. CLI의 gcloud auth login과 ADC는 별도 자격증명이다. 이미 작동하는 ADC가 있으면 재설정하지 않는다. 처음 사용하는 팀원만 본인 교육장 계정으로 다음 명령을 실행한다.

```bash
gcloud auth application-default login
uv run --frozen python scripts/check_integrations.py vertex
uv run --frozen python -m eval.smoke --provider vertex
```

첫 점검은 합성 OK 응답 1건, 평가는 영어와 한국어 합성 JSON 2건이다. 기존 .env.integrations에 AI 설정이 없더라도 기본값은 교육장 프로젝트, global, gemini-3.6-flash, 최대 출력 1024다. 다른 설정을 비교하려면 --project, --location, --model 또는 평가의 --max-output-tokens를 지정한다. 실패해도 Developer API 키로 자동 전환하거나 요청을 자동 재시도하지 않는다.

평가의 JSON과 HTML에는 제공자, 프로젝트, 위치, 모델 및 실제 모델 판본, 지연, 스키마 결과와 입력, 답변, 추론, 캐시 입력 및 총 토큰을 기록한다. 원문 오류, 토큰과 키는 저장하지 않는다. 제공되지 않은 사용량과 미등록 가격표의 비용은 미산정으로 남긴다. 이 합성 점검은 실제 업무 합격선 평가가 아니다.

임베딩 위치는 생성 위치와 별도로 지정한다. 다음 조합을 합성 1건으로 확인했으며 반환 벡터를 출력하거나 저장하지 않는다.

```bash
uv run --frozen python scripts/check_integrations.py vertex \
  --embed-model gemini-embedding-001 --embedding-location us-central1 \
  --embedding-task RETRIEVAL_DOCUMENT
```

관측 기본 차원은 3072다. production DB의 벡터 차원을 이 결과로 자동 변경하지 않는다. 모델, 차원과 실제 검색 품질을 결정한 뒤 인덱스와 수집 처리를 함께 맞춘다. --dimensions로 요청한 차원과 실제 결과가 다르거나 입력이 잘리면 점검은 실패한다.

GCP 권한 준비는 인프라 담당자가 실행한다. 일반 팀원에게 프로젝트 IAM 변경 권한을 요구하지 않는다.

```bash
bash infra/setup_vertex_access.sh --verify-runtime
```

이 스크립트는 cs-runtime과 tft-runtime에 aiplatform.endpoints.predict와 serviceusage.services.use만 포함한 사용자 정의 역할을 부여한다. platform-runtime과 web-runtime에는 모델 권한을 주지 않는다. 선택한 교육장 CLI 계정에만 각 실행 계정의 임시 토큰 발급 권한을 10분 만료 조건으로 부여하고, 5분 수명 토큰으로 합성 요청을 1건씩 확인한 뒤 정확히 해당 임시 바인딩을 제거한다. 호출 실패 때도 제거하며 터미널 강제 종료 시에는 만료 조건이 남는다. API 키와 서비스 계정 키를 만들지 않는다. IAM 준비만 하려면 --verify-runtime을 생략한다.

이 검증은 실제 서비스 계정 신원으로 로컬 SDK를 호출하는 점검이다. Cloud Run 업무 API 내부에서 모델을 호출하는 기능의 통합 검증은 해당 기능 구현 후 수행한다. Cloud Run에서는 연결된 실행 계정의 ADC를 그대로 사용하며 사용자 계정의 인증 파일을 배포하지 않는다. 앱 기동, health, ready와 CI에서는 모델을 호출하지 않는다. 팀원 코드 사용법은 [공통 AI 클라이언트](ai-client.md)를 따른다.

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

이 옵션은 해당 실행에만 consumers authority와 개인 계정의 고정 테넌트를 사용한다. 기존 MS_TENANT_ID와 시크릿을 덮어쓰지 않고 임의 조직 테넌트를 허용하지 않는다. 실제 로그인은 개인 계정을 선택해 수행한다. 메일 읽기만 확인하며 발송하지 않는다. 공개 OpenID discovery는 성공했고 실제 사용자 로그인과 Mail.Read는 아직 확인 전이다.

개인 계정에서는 Entra 앱 역할이 실행 시 지원되지 않으므로 앱 역할 claim에 의존하지 않는다. 확정된 DB 등록 사용자와 역할 정책을 유지하고 등록되지 않은 사용자에게 자동 권한을 주지 않는다. 이 사전 점검은 production 사용자 등록이나 전체 인증 기능 구현을 대신하지 않는다. [지원 계정 유형과 앱 역할 제한](https://learn.microsoft.com/en-us/entra/identity-platform/supported-accounts-validation), [Graph 위임 권한](https://learn.microsoft.com/en-us/graph/permissions-reference#mailread)을 참고한다.

Gmail 주소를 로그인 이름으로 쓰는 Microsoft 개인 계정도 있다. Microsoft 계정 보유와 Outlook 사서함 보유는 따로 확인한다. Outlook 웹에서 실제 받은편지함이 열리는지 확인하며 Gmail 주소라는 이유만으로 OAuth 실패 원인이나 사서함 부재를 단정하지 않는다. [Microsoft 계정의 외부 이메일 주소 안내](https://support.microsoft.com/en-us/accounts-billing/subscriptions/connecting-a-microsoft-subscription-to-outlook).

사용자는 지원 계정 유형을 조직 및 개인 포함으로 설정했다고 보고했고 Gmail 주소로 로그인한 결과 OAuth invalid_request가 발생했다. 이는 Graph 호출 이전의 실패이며 구체 원인은 아직 미확정이다. 진단 출력은 OAuth 오류 종류와 AADSTS 숫자 및 authorization 또는 token_exchange 단계만 남긴다. 오류 설명 원문, 이메일, 시크릿, 인증 코드와 state 및 콜백 URL은 출력하지 않는다. 같은 명령으로 다시 확인한 FAIL 행만 공유한다. AADSTS가 제공되지 않으면 not_provided로 표시한다. [공식 오류 코드 안내](https://learn.microsoft.com/en-us/entra/identity-platform/reference-error-codes).

## 기존 Gemini Developer API 진단

아래는 기존 API 키 경로를 별도로 진단하는 명령이다. 현재 생성은 월간 지출 한도로 막혀 있으므로 재시도를 반복하지 않는다. 위 Vertex 경로를 기본으로 사용한다.

지원 모델 이름과 기능만 조회한다.

```bash
uv run --frozen python scripts/check_integrations.py gemini
```

기존 .env에 키가 있다면 파일을 복사하지 않고 해당 파일을 지정할 수 있다.

```bash
uv run --frozen python scripts/check_integrations.py gemini --env-file .env
```

출력에서 generateContent를 지원하는 실제 모델 ID를 골라 합성 2건의 smoke 호출을 실행한다. 아래 MODEL_ID를 목록의 값으로 바꾼다. 이 선택은 연결 점검용이며 최종 업무 모델 선정은 평가 결과를 따른다.

```bash
uv run --frozen python -m eval.smoke --provider gemini --model MODEL_ID --env-file .env
```

JSON 및 HTML에 모델, 합성 사례 판본, 스키마 검증, 지연과 제공자가 반환한 토큰 사용량을 기록한다. 가격표는 아직 등록하지 않아 비용을 0으로 표시하지 않고 미산정으로 남긴다. 실패 또는 토큰 미제공도 기록하며 업무 합격선은 평가하지 않는다.

목록에 있는 임베딩 모델도 합성 문장 1개로 별도 확인할 수 있다. EMBEDDING_MODEL_ID를 실제 목록의 값으로 바꾼다. 반환 벡터는 출력하거나 저장하지 않으며 확인한 기본 차원을 production 인덱스 차원으로 자동 채택하지 않는다.

```bash
uv run --frozen python scripts/check_integrations.py gemini --env-file .env --embed-model EMBEDDING_MODEL_ID
```

근거: [Microsoft MSAL 인증 코드 흐름](https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens), [Graph 메시지 조회](https://learn.microsoft.com/en-us/graph/api/user-list-messages), [Google Gen AI SDK](https://github.com/googleapis/python-genai).

## 2026-10-22 기존 Developer API 확인 이력

Microsoft OpenID discovery와 Gemini 모델 목록 조회는 성공했다. gemini-embedding-001의 합성 임베딩 1건도 성공했고 기본 차원 3072를 확인했다. 실제 Microsoft 로그인 및 Graph 호출과 메일 발송은 미실행이다.

생성 smoke는 gemini-2.5-flash-lite 및 gemini-2.5-flash에서 HTTP 404, gemini-3.8-flash에서 HTTP 429가 나왔다. 모델 목록 노출만으로 현재 키의 생성 호출 가능 여부가 보장되지 않았다. 실제 사용 가능한 생성 모델 및 할당량 확인 후 재검증해야 하며 아직 모델과 production 임베딩 차원을 확정하지 않았다. 실패 기록에도 원문 응답과 키는 포함하지 않는다.

추가 점검에서 gemini-3.8-flash 및 gemini-3.1-flash-lite 모두 HTTP 429 RESOURCE_EXHAUSTED였다. 응답의 안전한 구조화 필드에는 구체적인 quota 위반 항목이 없어 한도 0, 일일 소진 또는 다른 제한 중 무엇인지 확정하지 않았다. 같은 키가 속한 프로젝트를 AI Studio의 사용량과 한도에서 확인하도록 사용자에게 요청했다. 생성 성공이나 토큰 실측 완료로 표시하지 않는다.

### 후속 확인: 청구 계정 월간 지출 한도 도달

사용자는 같은 키로 Python 호출이 이전에 성공했고 현재 AI Studio가 Tier 2라고 보고했다. 키 소속을 읽기 전용으로 조회해 교육장 프로젝트 proj-skkuls0106-183255377938임을 확인했고 GCP billingEnabled=true도 확인했다. 키와 인증 토큰은 출력하지 않았다.

짧은 합성 생성 요청의 HTTP 429 응답 설명에서 다음 문구를 확인했다. 키와 URL 등을 제거한 오류 설명만 확인했으며 업무 데이터는 사용하지 않았다.

> Your billing account has exceeded its monthly spending cap.

따라서 현재 확인된 생성 차단 원인은 청구 계정의 월간 지출 한도다. 앞선 원인 미확정 기록보다 이 결과가 우선한다. Tier 2 등급과 생성 API의 현재 사용 가능 여부는 구분한다. Google 문서상 청구 계정 한도는 연결된 프로젝트의 Gemini API 지출을 합산한다. 교육장 내 다른 프로젝트와 같은 청구 계정을 공유하는지는 담당자 확인이 필요하다.

교육장 담당자에게 위 오류와 프로젝트 ID를 전달해 Gemini API 청구 계정의 월간 지출 및 한도 상향 가능 여부를 확인한다. 개인 결제 연결과 청구 계정 변경 및 한도 조정은 실행하지 않았다. 추가 생성 재시도도 중단한다. [공식 월간 지출 한도 안내](https://ai.google.dev/gemini-api/docs/billing#billing-account-tier-spend-caps).

## 후속 확인: Vertex AI global 생성 성공

사용자 요청으로 기존 Developer API 키 경로와 별도로 교육장 프로젝트의 Vertex AI global 엔드포인트를 확인했다. aiplatform.googleapis.com은 이미 활성화돼 있었으며 API 활성화와 IAM 및 배포 설정은 변경하지 않았다.

- 프로젝트: proj-skkuls0106-183255377938
- 인증: 기존 교육장 GCP 사용자 계정의 OAuth 액세스 토큰. 키와 토큰은 출력하거나 저장하지 않았다.
- 위치 및 모델: global, gemini-3.6-flash
- 호출: aiplatform.googleapis.com의 v1/projects/PROJECT/locations/global/publishers/google/models/MODEL:generateContent
- 합성 입력: Return exactly OK.
- 결과: HTTP 200, 실제 OK 응답, finishReason=STOP. 입력 4, 답변 1, thinking 82, 총 87토큰을 확인했다.

처음 maxOutputTokens=64 요청은 HTTP 200이었지만 본문이 비어 있었다. 1024로 늘린 후 위 실제 텍스트 결과를 확인했다. 짧은 연결 점검에도 출력 한도가 답변 생성을 막지 않는지 확인한다. 내부 추론 내용은 출력하지 않았다.

기존 Developer API의 월간 지출 한도 오류와 이 경로의 생성 성공을 구분한다. 위 REST 단건 점검 뒤 공통 SDK와 평가 제공자를 연결했다. 실제 영어 및 한국어 합성 평가 2건이 모두 스키마를 통과했고, Vertex us-central1의 gemini-embedding-001도 합성 1건 성공했다. 실행 계정 검증은 별도 절차로 진행하며 최종 업무 모델은 아직 미확정이다. 관리형 에이전트 실행 환경을 새로 채택하거나 서비스를 추가한 것은 아니다. [공식 global 엔드포인트 안내](https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/locations#global-endpoint).

## Microsoft 관리자 동의에서 막힐 때

WSL에서는 스크립트가 Windows 기본 브라우저를 호출한다. 실패하면 같은 프로세스가 기다리는 일회용 로그인 URL을 터미널에 표시한다. URL은 본인 브라우저에서만 사용하고 채팅에 붙이지 않는다. 토큰과 시크릿은 출력하지 않는다.

사용자는 로그인 화면에서 관리자 승인 필요 메시지를 확인했고 Entra의 관리자 동의 부여 버튼이 비활성화돼 있다고 보고했다. API 권한 표의 관리자 동의 필요 '아니요'는 권한 자체의 속성이다. 조직의 사용자 동의 및 미검증 앱 정책은 별개이므로 해당 표시만으로 조직 승인이 불필요하다고 판단하지 않는다.

관리자에게 전달할 요청 문구:

> 개발용 앱 ls-cs-rfp-agent의 Microsoft 로그인 및 메일 연동 검증을 위해 관리자 동의가 필요합니다. 앱 ID는 7160833b-2aa3-497f-bc6c-11c59851c254이고 테넌트 ID는 6c2ea5ee-418c-4e13-8eaa-5163cdb50c67입니다. 요청하는 Graph 위임 권한은 Mail.Read, Mail.Send, offline_access입니다. 시연용 사용자에 대해 사용할 수 있는 승인 방식과 메일함 사용 가능 여부를 확인 부탁드립니다.

관리자에게 메시지를 대신 보내지는 않았다. 승인 후 로그인 및 Mail.Read를 재검증하고, Mail.Send 동의와 실제 테스트 메일 발송 및 수신은 구분해 확인한다. 현재는 실제 로그인, 읽기 및 발송 성공을 확인하지 못했다. [Microsoft 관리자 동의 안내](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/grant-admin-consent)를 참고한다.

### 실행 계정 권한 전파 확인

첫 실행 계정 점검은 임시 권한 추가 직후 토큰 발급에서 RefreshError가 발생했고 임시 바인딩을 회수했다. 모델 요청 이전의 인증 단계다. 이후 IAM_PERMISSION_DENIED인 토큰 발급만 최대 180초 기다리도록 보완했다. 모델 생성은 재시도하지 않는다. IAM 정책 변경의 반영에는 수분이 걸릴 수 있다. [공식 IAM 전파 시간 안내](https://docs.cloud.google.com/iam/docs/access-change-propagation).

사용자가 보완한 스크립트를 실행한 결과 약 70초 전파 대기 후 두 실행 신원 모두 gemini-3.6-flash, global의 실제 생성에서 PASS와 STOP을 확인했다. C/S는 입력 4, 답변 1, 추론 60, 총 65토큰이고 TFT는 입력 4, 답변 1, 추론 67, 총 72토큰이다. 이후 IAM을 읽기 전용 조회해 호출 역할의 대상이 두 실행 계정뿐이고 검증용 임시 바인딩은 양쪽 모두 0건임을 확인했다. 업무 API 내부의 생성 기능은 아직 구현 전이다.

공통 SDK의 실제 스키마 점검 결과는 artifacts/eval/c8af4a52-737e-41e7-beb1-8959ecf56dec.json 및 .html에 있다. 영어는 3200.51ms와 총 196토큰, 한국어는 1602.21ms와 총 201토큰이고 스키마가 모두 유효했다. 이는 두 합성 요청의 관측값이며 성능 보장이나 업무 품질 평가 결과가 아니다.

공통 모듈과 설정은 main의 e557c8b에 반영했고 CI 39706740083 및 자동 CD 39706861091가 성공했다. Cloud Run 백엔드 3개의 새 이미지와 SDK import, 실행 설정 및 DB 준비 상태를 확인했다. 웹은 재배포하지 않았다. 상세 빌드 기록은 [서비스별 CI와 CD](service-delivery.md)에 있다.
