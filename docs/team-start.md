# 팀원 개발 시작 안내

처음 저장소를 받은 팀원은 1~5를 순서대로 실행한다. 이 로컬 실행에는 GCP 권한, Gemini 키와 Microsoft 시크릿이 필요하지 않다. data 디렉터리는 내려받지 않아도 시작할 수 있으며 이 절차는 해당 디렉터리를 수정하지 않는다.

## 1. 준비할 프로그램

Windows는 Docker Desktop과 WSL2를 사용하고 같은 WSL 터미널에서 모든 명령을 실행하는 구성을 권장한다. 저장소는 WSL의 홈 디렉터리 아래에 둔다. macOS 및 Linux에서도 같은 명령을 사용한다.

| 프로그램 | 확인 명령 | 정상 기준 |
| --- | --- | --- |
| Git | git --version | 버전 출력 및 비공개 저장소 접근 권한 |
| Docker와 Compose | docker compose version | Compose 버전 출력 |
| 실행 중인 Docker 엔진 | docker info | 엔진 연결 성공 |
| Python | python3 --version | 3.11 이상. 로컬 실행용 3.12는 uv가 준비 |
| uv | uv --version | 버전 출력 |
| Node.js와 npm | node --version, npm --version | Node 22.12 이상. Node 24 LTS 권장 |

설치 안내: [Docker Desktop](https://docs.docker.com/desktop/), [uv 설치](https://docs.astral.sh/uv/getting-started/installation/), [Node.js](https://nodejs.org/en/download). Windows에서 Python 명령이 python이면 아래 python3 대신 python을 사용한다. 저장소 접근이 거부되면 본인의 GitHub 계정으로 조직 초대를 수락하고 저장소 권한을 확인한다. 공용 GitHub 계정이나 다른 팀원의 토큰을 복사하지 않는다.

## 2. 저장소 받기

처음 한 번 실행한다.

```bash
git clone https://github.com/LS-Final-Project/ls-final-project.git
cd ls-final-project
git switch main
git pull --ff-only
```

이미 받은 저장소라면 자신의 변경을 확인한 뒤 업데이트한다. 작업 파일을 지우거나 reset --hard하지 않는다.

```bash
git status
git switch main
git pull --ff-only
```

## 3. 최초 설치

저장소 루트에서 실행한다.

```bash
python3 scripts/workspace.py setup
```

정상 결과의 마지막에 READY가 표시된다. 이 명령은 Python 및 프론트 의존성을 잠금 파일 기준으로 설치하고 타입을 생성한다. .env.local에 서로 다른 로컬 DB 비밀번호를 생성하며 기존 .env와 기존 .env.local은 덮어쓰지 않는다. frontend/.env.local에는 로컬 API 주소만 들어간다.

로컬 파일을 팀원에게 보내지 않는다. 각 팀원은 자기 컴퓨터에서 setup을 실행한다. 처음 실행에는 패키지와 Docker 이미지 다운로드를 위한 인터넷 연결이 필요하다.

## 4. DB와 백엔드 실행

```bash
python3 scripts/workspace.py up
```

PostgreSQL 17과 pgvector, 서비스별 계정과 스키마, 합성 사용자 4개, API 3개를 실행한다. 마지막에 각 서비스의 DB 및 vector, FTS PASS와 계정 선택 및 권한 격리 PASS가 출력되면 정상이다.

| 대상 | 기본 주소 | 확인 |
| --- | --- | --- |
| C/S API | http://127.0.0.1:8081/docs | 문의 API와 로컬 문서 |
| TFT API | http://127.0.0.1:8082/docs | 프로젝트 API와 로컬 문서 |
| 플랫폼 API | http://127.0.0.1:8083/docs | 사용자 및 개발 계정 API |
| PostgreSQL | 127.0.0.1:5433, DB team4 | DB 도구 접속은 .env.local의 자기 서비스 비밀번호 사용 |

health는 프로세스만 확인한다. `/api/v1/cs/ready`, `/api/v1/tft/ready`, `/api/v1/platform/ready`는 실제 DB 계정, 스키마, pgvector와 FTS를 확인한다. 백엔드 코드를 수정했다면 up을 다시 실행해 이미지를 갱신한다.

## 5. 프론트 실행

같은 저장소의 별도 터미널에서 실행한다.

```bash
npm run dev --prefix frontend
```

브라우저에서 http://127.0.0.1:5173 을 연다. 서비스 연결 완료 문구와 시연 계정 4개가 보여야 한다. 계정을 선택하면 현재 계정에 역할이 표시된다. 선택 해제를 누르면 세션이 무효화된다.

| 계정 | 역할 | 부서 |
| --- | --- | --- |
| 시연 C/S 담당자 | cs_agent | 없음 |
| 시연 기술부서 담당자 | dept_staff | technical |
| 시연 수주 담당자 | sales | 없음 |
| 시연 관리자 | manager | 없음 |

이 계정은 합성한 로컬 개발 사용자이며 실제 팀원의 Microsoft 계정과 연결되지 않는다. 업무 범위와 최종 승인권은 후속 API에서 별도로 검사해야 한다.

기본 모드는 모의 응답이다. C/S 문의와 TFT 목록은 개발용 합성 예시다. 계정 선택 및 준비 상태는 실제 로컬 DB와 API를 사용한다. 실제 API 모드로 바꾸면 아직 구현하지 않은 목록은 501을 반환한다. 이 실패를 숨기기 위해 자동으로 모의 응답으로 전환하지 않는다.

## 공용 배포 환경 확인

[공용 웹](https://team4-web-gew6ci4pgq-du.a.run.app/)을 열면 플랫폼 DB와 pgvector 및 FTS 연결 상태를 확인할 수 있다. 이 화면은 배포 연결 확인용이다. 로컬 시연 계정 선택과 합성 업무 목록은 로컬 실행에서 사용한다. 실제 Microsoft 로그인과 업무 화면은 후속 구현이다.

공용 백엔드는 비공개이므로 Cloud Run API 주소에 직접 접속했을 때 403이 나오는 것은 정상이다. 플랫폼 health/ready는 웹 서버의 서비스 계정으로 연결한다. 개발 중인 업무 API를 연결하려고 백엔드를 임의로 공개하지 않는다.

서비스별 CI와 배포 범위 및 실행 방법은 [서비스별 CI와 CD](service-delivery.md)를 따른다. 기능 코드는 PR에서 해당 서비스와 계약 소비자 검사를 통과시킨다. 자동 CD 활성화 후에는 main 반영과 CI 성공 시 변경된 서비스만 배포된다.

## 매일 개발 시작과 종료

```bash
python3 scripts/workspace.py up
npm run dev --prefix frontend
```

점검만 다시 실행하려면 아래 명령을 사용한다.

```bash
python3 scripts/workspace.py check
```

종료할 때 프론트 터미널에서 Ctrl+C를 누르고 아래 명령을 실행한다. 로컬 DB 볼륨은 보존된다.

```bash
python3 scripts/workspace.py down
```

## 자신의 작업 시작

저장소의 Git 훅을 한 번 설치하고 실제 Jira 번호로 브랜치를 만든다. 아래 KAN-64는 프론트 실행 골격 티켓의 예시다. 자신이 맡은 실제 티켓으로 바꾼다. setup의 main 직접 push 예외는 현재 인프라 초기 작업에만 적용한다.

```bash
python3 scripts/dev.py setup
python3 scripts/dev.py start KAN-64 frontend-work
```

| 작업 | 주로 수정할 경로 |
| --- | --- |
| C/S API | backend/api/cs/ |
| TFT API | backend/api/tft/ |
| 인증, 메일과 평가 관리 API | backend/api/platform/ 아래 해당 기능 |
| 룰과 입력 어댑터 | backend/engine/ |
| LLM 호출과 프롬프트 | backend/agent/ |
| 평가 실행과 리포트 | eval/ |
| 화면과 API 소비자 | frontend/src/ |

담당 인원은 Jira를 따른다. API를 수정할 때는 [계약 수정 안내](../contracts/README.md)를 먼저 읽는다. 전체 DTO를 한 파일에 모으거나 다른 서비스의 모델을 직접 import하지 않는다.

```bash
python3 scripts/workspace.py contracts cs
```

C/S를 수정한 예시다. 자기 서비스의 schemas.py와 routes.py 및 examples.py를 수정한 뒤 생성한다. TFT는 tft, 플랫폼은 platform을 사용한다. 생성된 TypeScript는 직접 편집하지 않는다.

## DB 테이블을 추가할 때

자기 서비스의 infra/migrations/cs, tft 또는 platform 디렉터리에 날짜와 Jira 번호가 있는 새 SQL 파일을 추가한다. 예시는 `20261022_KAN-78_identity.sql`이다. 기존에 적용한 파일을 고치는 대신 새 파일을 추가한다. SQL은 BEGIN, SET LOCAL ROLE 해당서비스_migrator, 실제 DDL 및 COMMIT으로 작성한다. 앱 계정으로 테이블을 만들지 않는다.

이미 초기화한 DB에는 추가 파일을 명시적으로 적용한다. 다음은 제공한 플랫폼 초기 파일을 다시 확인하는 예시이며 실제 작업에서는 자신의 새 파일명으로 바꾼다. 이 명령은 로컬 Compose DB만 대상으로 한다.

```bash
python3 scripts/workspace.py migrate platform 20261022_KAN-78_identity.sql
```

up은 기존 볼륨을 지우거나 모든 마이그레이션을 자동 재실행하지 않는다. 잘못된 SQL은 중단하고 원인을 확인한다. 이 명시 실행 도구는 production 마이그레이션 자동화가 아니다.

## 검증과 평가 연결 점검

```bash
uv run --frozen ruff check backend tests eval scripts/workspace.py scripts/check_integrations.py
uv run --frozen mypy
uv run --frozen pytest -q
python3 scripts/workspace.py contracts all --check
npm run build --prefix frontend
```

키 없이 평가 실행과 기록 경로를 확인한다.

```bash
uv run --frozen python -m eval.smoke
```

출력된 artifacts/eval 아래 JSON과 HTML 파일을 연다. fake 제공자는 외부 모델을 호출하지 않는다. 스키마 확인과 지연 및 사용량 필드 기록을 검증하며 업무 정확도나 실제 모델 성능을 측정한 결과는 아니다. 가격표가 없으면 비용은 미산정이다. 실제 Gemini 및 Microsoft 확인은 [외부 연동 점검](integration-checks.md)을 따른다. 최종 test 597건은 이 절차에서 사용하지 않는다.

## 실제 모델 호출이 필요한 팀원

화면과 API 골격 개발은 위 fake 방식으로 시작한다. 실제 모델이 필요한 시점에 [외부 연동 점검](integration-checks.md)의 Vertex ADC 준비를 진행하고 아래 명령을 실행한다.

```bash
uv run --frozen python -m eval.smoke --provider vertex
```

현재 기본 조합은 교육장 프로젝트의 global, gemini-3.6-flash다. 초기 연결 기준이며 최종 품질 평가로 선택한 모델은 아니다. 생성 결과의 스키마, 지연 및 토큰은 출력된 HTML에서 확인한다. 팀원은 IAM 준비 스크립트를 반복 실행하지 않는다. 업무 코드에서 재사용할 연결 및 임베딩 예제는 [공통 AI 클라이언트](ai-client.md)를 따른다.

## 문제가 생기면

| 증상 | 확인 및 해결 |
| --- | --- |
| Docker 엔진 연결 실패 | Docker Desktop 또는 Docker Engine을 실행하고 docker info 재확인 |
| 포트 사용 중 | 해당 프로그램을 확인한다. .env.local의 LOCAL_*_PORT를 변경했다면 frontend/.env.local의 대응 API 주소도 수정하고 Vite 재시작 |
| 비밀번호 인증 실패 | 기존 DB 볼륨과 .env.local 비밀번호가 다를 수 있다. 기존 파일을 되찾거나 관리자와 확인한다. 볼륨을 자동 삭제하거나 비밀번호를 임의 재생성하지 않는다 |
| DB 초기화 실패 | 아래 logs 명령으로 SQL 실패 위치 확인. 실패를 숨기기 위해 반복 초기화 SQL을 실행하거나 볼륨을 삭제하지 않는다 |
| 브라우저 계정 목록 없음 | 플랫폼 및 DB ready 확인. 로컬 platform의 DEV_AUTH_ENABLED=true 여부 확인 |
| 실제 API 모드에서 501 | 계약 stub의 정상 결과. 기능을 구현하거나 화면 개발 중에는 모의 응답 모드 사용 |
| 타입에 새 필드가 없음 | 자기 서비스 contracts 명령 실행 후 TypeScript 컴파일 확인 |
| setup 재실행 뒤 비밀번호 유지 | 정상이다. 기존 .env.local과 DB 볼륨을 보존한다 |

```bash
python3 scripts/workspace.py logs
```

도움을 요청할 때 서비스 이름, HTTP 상태, request_id와 실행 명령을 공유한다. .env 파일과 토큰, 비밀번호가 포함된 출력은 공유하지 않는다.
