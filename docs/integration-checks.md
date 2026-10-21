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
