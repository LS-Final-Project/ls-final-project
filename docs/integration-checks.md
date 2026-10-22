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

참고: [Gemini API 키](https://ai.google.dev/gemini-api/docs/api-key), [Microsoft 인증 코드 흐름](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow), [Graph sendMail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0).
