# 외부 연동 사전 확인

로컬 개발 시작에는 필요하지 않다. 교육장 계정과 실제 앱 연동을 확인하는 담당자가 실행한다. 키와 시크릿을 채팅, Git 또는 프론트 VITE 변수에 넣지 않는다. 아래 절차는 메일을 발송하지 않는다.

## 로컬 설정

파일이 아직 없을 때만 복사하고 로컬에서 편집한다. 기존 파일이 있으면 보존한다.

```bash
cp -n .env.integrations.example .env.integrations
```

예시에 사용자 제공 테넌트와 애플리케이션 ID를 넣었다. MS_CLIENT_SECRET과 GEMINI_API_KEY는 실제 연동 확인 때만 로컬에서 입력한다. GCP용 기존 .env를 수정할 필요가 없다.

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

## Gemini

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

## 2026-10-22 확인 결과

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

기존 Developer API의 월간 지출 한도 오류와 이 경로의 생성 성공을 구분한다. 이번 성공은 교육장 사용자 인증으로 확인한 단건 연결 점검이다. 앱의 Cloud Run 실행 계정 권한과 Vertex용 SDK 및 평가 제공자 연결, 임베딩과 최종 업무 모델 선정은 아직 검증하지 않았다. 관리형 에이전트 실행 환경을 새로 채택하거나 서비스를 추가한 것은 아니다. [공식 global 엔드포인트 안내](https://docs.cloud.google.com/gemini-enterprise-agent-platform/resources/locations#global-endpoint).

## Microsoft 관리자 동의에서 막힐 때

WSL에서는 스크립트가 Windows 기본 브라우저를 호출한다. 실패하면 같은 프로세스가 기다리는 일회용 로그인 URL을 터미널에 표시한다. URL은 본인 브라우저에서만 사용하고 채팅에 붙이지 않는다. 토큰과 시크릿은 출력하지 않는다.

사용자는 로그인 화면에서 관리자 승인 필요 메시지를 확인했고 Entra의 관리자 동의 부여 버튼이 비활성화돼 있다고 보고했다. API 권한 표의 관리자 동의 필요 '아니요'는 권한 자체의 속성이다. 조직의 사용자 동의 및 미검증 앱 정책은 별개이므로 해당 표시만으로 조직 승인이 불필요하다고 판단하지 않는다.

관리자에게 전달할 요청 문구:

> 개발용 앱 ls-cs-rfp-agent의 Microsoft 로그인 및 메일 연동 검증을 위해 관리자 동의가 필요합니다. 앱 ID는 7160833b-2aa3-497f-bc6c-11c59851c254이고 테넌트 ID는 6c2ea5ee-418c-4e13-8eaa-5163cdb50c67입니다. 요청하는 Graph 위임 권한은 Mail.Read, Mail.Send, offline_access입니다. 시연용 사용자에 대해 사용할 수 있는 승인 방식과 메일함 사용 가능 여부를 확인 부탁드립니다.

관리자에게 메시지를 대신 보내지는 않았다. 승인 후 로그인 및 Mail.Read를 재검증하고, Mail.Send 동의와 실제 테스트 메일 발송 및 수신은 구분해 확인한다. 현재는 실제 로그인, 읽기 및 발송 성공을 확인하지 못했다. [Microsoft 관리자 동의 안내](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/grant-admin-consent)를 참고한다.
