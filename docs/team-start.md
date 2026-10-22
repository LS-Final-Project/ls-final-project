# 팀원 개발 시작 안내

설치부터 첫 PR까지의 공통 온보딩 원본은 이 문서다. README와 노션의 이름별 안내는 이 문서를 연결하며 설치 절차를 따로 복제하지 않는다. 업무별 설계 문서는 필요한 단계에서만 참고한다.

처음에는 이 문서 전체를 읽지 않고 아래 세 단계만 진행한다.

1. **1~5 실행:** 서비스 연결 완료와 시연 계정 4개가 보이면 로컬 준비 완료다. 지금 보이는 화면은 개발 환경 확인용이다.
2. **[자신의 작업 시작](#자신의-작업-시작):** 아래 첫 티켓 표에서 자기 작업과 첫 결과를 확인한다. 이름별 노션은 그 작업의 상세 경계만 참고한다.
3. **Jira 확인 후 작은 PR:** 선행 조건과 완료 범위를 확인하고 자기 기능의 계약·합성 예시 또는 작은 화면부터 구현한다. 첫 PR은 dev 대상으로 올린다.

이 로컬 실행에는 GCP 권한, Gemini 키와 Microsoft 시크릿이 필요하지 않다. data 디렉터리를 내려받거나 교육장 IAM 및 배포 스크립트를 실행할 필요도 없다. 명령은 모두 저장소 루트에서 실행한다. 한 블록 안에서도 앞 명령이 실패하면 다음 단계로 넘어가지 말고 맨 아래 문제 해결을 확인한다. 코드 블록의 명령만 복사하며 화면의 프롬프트나 실행 결과는 함께 붙이지 않는다.

## 1. 준비할 프로그램

Windows는 Docker Desktop과 WSL2의 Ubuntu 터미널을 사용한다. Ubuntu가 아직 없으면 [Microsoft WSL 설치 안내](https://learn.microsoft.com/windows/wsl/install)부터 완료한다.

1. Docker Desktop을 실행하고 Settings → Resources → WSL Integration에서 사용하는 Ubuntu를 연결한다.
2. 이후 명령은 PowerShell 대신 Ubuntu 터미널에서 실행한다. Python, uv, Node.js와 npm, GitHub CLI도 그 터미널에서 확인할 수 있어야 한다. Windows에만 설치된 Node.js를 그대로 쓰는 구성과 섞지 않는다.
3. `cd ~`로 WSL 홈 디렉터리로 이동한 뒤 저장소를 받는다. VS Code를 사용하면 WSL에서 해당 폴더를 연다.

macOS 및 Linux에서도 아래 명령은 같으며 Docker 엔진이 실행 중이어야 한다.

| 프로그램 | 확인 명령 | 정상 기준 |
| --- | --- | --- |
| Git | git --version | 버전 출력 및 비공개 저장소 접근 권한 |
| GitHub CLI | gh --version | 아래 브라우저 인증과 비공개 저장소 접근에 사용 |
| Docker와 Compose | docker compose version | Compose 버전 출력 |
| 실행 중인 Docker 엔진 | docker info | 엔진 연결 성공 |
| Python | python3 --version | 3.11 이상. 로컬 실행용 3.12는 uv가 준비 |
| uv | uv --version | 버전 출력 |
| Node.js와 npm | node --version, npm --version | Node 22.12 이상. Node 24 LTS 권장 |

설치 안내: [Docker Desktop](https://docs.docker.com/desktop/), [uv 설치](https://docs.astral.sh/uv/getting-started/installation/), [Node.js](https://nodejs.org/en/download), [GitHub CLI](https://cli.github.com/). Windows에서는 아래 명령을 WSL Ubuntu의 python3로 실행한다. 저장소 접근이 거부되면 본인의 GitHub 계정으로 조직 초대를 수락하고 저장소 권한을 확인한다. 공용 GitHub 계정이나 다른 팀원의 토큰을 복사하지 않는다.

## 2. 저장소 받기

GitHub에 본인 계정으로 로그인해 조직 초대를 수락하고 [저장소](https://github.com/LS-Final-Project/ls-final-project)가 보이는지 확인한다. 비공개 저장소가 보이지 않으면 인프라 담당자에게 본인 계정의 접근 권한을 확인한다.

처음 한 번 터미널의 Git 인증을 연결한다. 기존 Git 인증으로 정상 clone/pull이 된다면 이 단계는 생략한다.

```bash
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
```

안내된 코드를 본인 브라우저에 입력하고 같은 GitHub 계정으로 승인한다. 브라우저가 자동으로 열리지 않으면 터미널에 표시된 주소를 직접 연다. [GitHub CLI 로그인 안내](https://cli.github.com/manual/gh_auth_login)를 참고한다. 인증 코드와 토큰을 채팅이나 문서에 붙이지 않는다. 일반 GitHub 로그인 비밀번호를 git의 비밀번호로 입력하는 방식은 사용하지 않는다.

새 폴더를 받는다.

```bash
cd ~
git clone https://github.com/LS-Final-Project/ls-final-project.git
cd ls-final-project
git switch dev
git pull --ff-only
```

이미 받은 저장소에서는 git status로 수정 중인 파일을 확인한다. 변경이 있으면 자신의 작업 브랜치에 보존한 뒤 전환한다. 기존 작업을 지우거나 reset --hard로 정리하지 않는다. 아래 git switch dev는 로컬 dev가 없으면 origin/dev를 추적하는 브랜치를 만든다.

```bash
git status
git fetch origin
git remote set-head origin -a
git switch dev
git pull --ff-only
```

## 3. 최초 설치

저장소 루트에서 실행한다.

```bash
python3 scripts/workspace.py setup
```

정상 결과의 마지막에 READY가 표시된다. 이 명령은 Python 및 프론트 의존성을 잠금 파일 기준으로 설치하고 타입을 생성한다. .env.local에 서로 다른 로컬 DB 비밀번호를 생성하며 기존 .env와 기존 .env.local은 덮어쓰지 않는다. frontend/.env.local에는 로컬 API 주소만 들어간다.

설치 중 한 명령이 실패했으면 원인을 해결한 뒤 같은 setup 명령을 다시 실행해도 된다. 기존 로컬 비밀번호는 유지한다.

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

두 번째 터미널을 열어 같은 저장소 폴더로 이동한 뒤 실행한다. 이 터미널은 프론트 서버가 실행되는 동안 열린 채로 둔다. 첫 번째 터미널에서 Git과 검사 명령을 실행하면 된다.

```bash
npm run dev --prefix frontend
```

터미널에 Vite의 Local 주소가 나오면 브라우저에서 http://127.0.0.1:5173 을 연다. 서비스 연결 완료 문구와 시연 계정 4개가 보여야 한다. 계정을 선택하면 현재 계정에 역할이 표시된다. 선택 해제를 누르면 세션이 무효화된다.

| 계정 | 역할 | 부서 |
| --- | --- | --- |
| 시연 C/S 담당자 | cs_agent | 없음 |
| 시연 기술부서 담당자 | dept_staff | technical |
| 시연 수주 담당자 | sales | 없음 |
| 시연 관리자 | manager | 없음 |

이 계정은 합성한 로컬 개발 사용자이며 실제 팀원의 Microsoft 계정과 연결되지 않는다. 업무 범위와 최종 승인권은 후속 API에서 별도로 검사해야 한다.

기본 모드는 모의 응답이다. C/S 문의와 TFT 목록은 개발용 합성 예시다. 계정 선택 및 준비 상태는 실제 로컬 DB와 API를 사용한다. 실제 API 모드로 바꾸면 아직 구현하지 않은 목록은 501을 반환한다. 이 실패를 숨기기 위해 자동으로 모의 응답으로 전환하지 않는다.

## 자신의 작업 시작

| 담당 | 첫 개발 티켓 | 첫 PR에서 확인할 결과 | 착수 전 확인 |
| --- | --- | --- | --- |
| 김나영 | [KAN-77 인증 연결](https://lsteam4.atlassian.net/browse/KAN-77) | 기존 세션을 재사용할 인증 경계와 fake 어댑터, 잘못된 callback/state를 거부하는 회귀 | 선행 KAN-57의 화면·상태·권한 합의 |
| 오새결 | [KAN-84 과제별 모델 인터페이스](https://lsteam4.atlassian.net/browse/KAN-84) | 기존 SDK 위의 입력·결과 타입과 fake 호출 예시, API 담당자에게 전달할 형식 | 선행 KAN-82의 평가 기준 |
| 김종하 | [KAN-64 화면 라우팅](https://lsteam4.atlassian.net/browse/KAN-64) | 기존 앱에 C/S·TFT 진입과 화면 이동을 추가하고 빌드 및 합성 예시 연결 확인 | 기존 실행 기반을 재설치하지 않고 라우팅의 남은 범위 확인 |

첫 티켓은 기존 담당 범위 안에서 좁힌 개발 시작점이며 티켓 전체 완료를 뜻하지 않는다. 2026-10-22 조회에서 KAN-57과 KAN-82는 미완료다. 시작할 때 담당자와 선행 조건을 확인하고, 미합의 필드는 초안으로 구분한다. 합의 전에는 통합·완료 처리하지 않는다. 이후 상태는 Jira를 따른다.

첫 커밋 전에 현재 Git 작성자 이름과 이메일을 확인한다.

```bash
git config --get user.name
git config --get user.email
```

값이 없거나 다른 사람 정보이면 이 저장소에 사용할 본인 정보로 설정한다. 아래 예시는 본인 이름과 GitHub 등록 이메일 또는 GitHub의 비공개 noreply 이메일로 바꾼다. 기존에 올바른 값이 있으면 다시 설정하지 않는다.

```bash
git config --local user.name "본인 이름"
git config --local user.email "본인 GitHub 등록 이메일"
```

저장소를 받은 뒤 Git 훅을 처음 한 번 설치한다.

```bash
python3 scripts/dev.py setup
```

설정은 이 저장소 폴더에 유지된다. 컴퓨터나 터미널을 껐다 켜거나 pull 및 브랜치 전환을 해도 setup을 반복할 필요는 없다. 다른 컴퓨터나 새 폴더에 clone했거나 Python 실행 경로가 바뀌었을 때 다시 실행한다.

새 Jira 작업을 시작할 때마다 start로 최신 origin/dev에서 작업 브랜치를 만든다. 아래는 김종하의 KAN-64 예시이며 자신이 맡은 실제 티켓과 작업명으로 바꾼다. 초기 셋업의 main 직접 push 예외를 새 기능 개발에 적용하지 않는다.

```bash
python3 scripts/dev.py start KAN-64 frontend-routes
```

같은 작업을 다음 날 이어갈 때는 기존 작업 브랜치에서 계속한다. setup이나 start를 다시 실행하지 않는다. 현재 브랜치는 git branch --show-current로 확인한다.

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

### 티켓 하나를 개발하는 순서

1. Jira의 담당자, 선행 작업과 완료 조건을 확인한다. 아래 이름별 문서에서 첫 작업과 결과 전달 대상을 확인한다.
2. 자기 기능 폴더에 입력 및 응답 모델과 합성 예시를 먼저 정한다. API 제공자와 프론트 담당자가 필드, 오류 및 작업 상태를 확인한다.
3. DB가 필요하면 자기 서비스 마이그레이션 파일을 추가한다. 업무 로직, 저장 및 외부 연동을 나눠 구현하고 계약에 합의한 fixture로 독립 테스트한다.
4. 자기 서비스 계약을 재생성하고 아래 검사 명령을 실행한다. 실제 API 실패를 모의 성공으로 바꾸지 않는다.
5. 자기 변경 파일만 커밋하고 브랜치를 push한다. PR에 Jira, 변경 내용, 검증 결과와 남은 제약을 적고 검토자 1명을 요청한다.
6. PR 대상이 dev인지 확인하고 검토 및 CI 통과 후 Squash merge한다. 결과를 Jira에 남긴다. 티켓 전체 완료 조건을 충족한 최종 PR에만 제목에 `[완료 KAN-64]`처럼 자기 번호를 표시하면 dev 병합 시 Jira가 자동 완료된다. 중간 PR은 표시하지 않고 진행 중을 유지한다. dev 반영만으로 공용 배포는 실행되지 않으며 배포는 인프라 담당자가 별도로 진행한다.

예시는 실제 자기 Jira 번호와 경로로 바꿔 실행한다. 공통 수정이 아니면 다른 서비스의 생성물을 함께 커밋하지 않는다.

```bash
git status
git add backend/api/cs/tickets docs/openapi/cs.json frontend/src/mocks/generated/cs.json
git commit -m "문의 API 구현"
git push -u origin HEAD
```

Git 훅이 브랜치의 Jira 번호를 커밋 제목에 추가한다. GitHub에서 push한 브랜치의 Compare & pull request를 누른다. **base는 dev, compare는 본인의 KAN 작업 브랜치**인지 확인한다. 변경 이유와 실제 검증 결과를 작성하고 Create draft pull request로 먼저 공유할 수 있다. 검토 준비가 되면 Ready for review로 바꾸고 검토자가 지정됐는지 확인한다. Draft에는 자동 검토 요청을 보내지 않는다. CI가 실패하면 고친 커밋을 같은 브랜치에 push한다. 승인과 검사 성공을 확인한 뒤 Squash and merge한다.

기본 브랜치 보호는 현재 요금제에서 서버 강제가 되지 않는다. 버튼이 활성화돼 있어도 CI와 검토를 직접 확인해야 한다. 최종 PR 제목 예시는 `[KAN-64] [완료 KAN-64] 화면 라우팅 완료`다. 병합 후 Jira가 실제 완료로 바뀌었는지 확인한다. 자동화 설정은 활성화했으며 첫 실제 병합 이벤트 검증은 남아 있다. 일반 기능 작업의 기본 검토 배정은 김나영 작업을 김종하, 오새결 작업을 김나영, 김종하 작업을 오새결이 검토하는 순서이며 티켓에 별도 배정이 있으면 그 배정을 따른다.

### 담당 경계와 공통 작업

| 담당 | 개발 범위 | 다른 담당자와 맞출 지점 |
| --- | --- | --- |
| [김나영](https://www.notion.so/401910042c3781ecb002d9786de41392) | 플랫폼 인증 및 메일, C/S API와 결정론적 규칙, 통합 회귀 | 오새결의 AI 결과를 API 및 작업 상태로 연결하고 김종하에게 계약과 예시 제공 |
| [오새결](https://www.notion.so/401910042c3781fcad57d0ed3aee8fdb) | 데이터 및 검색, C/S와 TFT의 AI, 평가 실행과 지표 | 공통 AI SDK를 재사용해 구조화 결과를 반환하고 API 담당자와 실행 및 저장 계약 합의 |
| [김종하](https://www.notion.so/401910042c3781408cedf75121232986) | 프론트, TFT API, 플랫폼 평가 관리 API | 두 API 제공자의 생성 타입을 소비하고 오새결의 평가 실행 및 결과를 화면과 연결 |

자기 첫 티켓의 상세 경계와 후속 순서는 노션의 [개발 온보딩](https://www.notion.so/401910042c3781ed9768f13ef422a4f8)에서 확인한다. 이름별 문서 하단의 전체 WBS는 필요할 때 보는 참고 목록이다. WBS와 Jira의 기존 일정은 유지한다. 초기 셋업 9건만 기존 합의에 따라 Jira 실행 담당이 오새결이며 WBS에는 김나영으로 남아 있다. 이미 준비한 환경을 새 업무로 중복 구축하지 않는다.

플랫폼을 함께 수정할 때 김나영은 auth 및 mail, 김종하는 evaluations 등 자기 기능 폴더와 마이그레이션을 사용한다. 이 중 아직 없는 폴더는 기능 구현 시 만든다. 공통 라우터 등록 변경은 작게 유지하고 서로의 DTO를 직접 import하지 않는다. 공유 엔진도 규칙, 입력 어댑터, 검색을 별도 파일로 나눠 수정한다.

작업 시작 시 AI 도구에는 AGENTS.md, 이 문서, 자기 Jira 티켓과 소유 경계를 함께 전달한다. 다른 사람의 변경을 덮어쓰거나 data 및 OpenDesign 원본을 수정하도록 지시하지 않는다.

```bash
python3 scripts/workspace.py contracts cs
```

C/S를 수정한 예시다. 자기 서비스의 schemas.py와 routes.py 및 examples.py를 수정한 뒤 생성한다. TFT는 tft, 플랫폼은 platform을 사용한다. 생성된 TypeScript는 직접 편집하지 않는다.

## 매일 개발 시작과 종료

첫 번째 터미널에서 저장소 루트로 이동해 API와 DB를 실행한다. 이 명령은 확인을 마치면 프롬프트로 돌아온다.

```bash
python3 scripts/workspace.py up
```

두 번째 터미널에서 프론트를 실행한다. 프론트 코드 수정은 Vite가 반영하며 백엔드 수정 후에는 첫 번째 터미널에서 up을 다시 실행한다.

```bash
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

## 필요할 때 참고: DB 테이블 추가

자기 서비스의 infra/migrations/cs, tft 또는 platform 디렉터리에 날짜와 Jira 번호가 있는 새 SQL 파일을 추가한다. 예시는 `20261022_KAN-78_identity.sql`이다. 기존에 적용한 파일을 고치는 대신 새 파일을 추가한다. SQL은 BEGIN, SET LOCAL ROLE 해당서비스_migrator, 실제 DDL 및 COMMIT으로 작성한다. 앱 계정으로 테이블을 만들지 않는다.

이미 초기화한 DB에는 추가 파일을 명시적으로 적용한다. 다음은 제공한 플랫폼 초기 파일을 다시 확인하는 예시이며 실제 작업에서는 자신의 새 파일명으로 바꾼다. 이 명령은 로컬 Compose DB만 대상으로 한다.

```bash
python3 scripts/workspace.py migrate platform 20261022_KAN-78_identity.sql
```

up은 기존 볼륨을 지우거나 모든 마이그레이션을 자동 재실행하지 않는다. 잘못된 SQL은 중단하고 원인을 확인한다. 이 명시 실행 도구는 production 마이그레이션 자동화가 아니다.

## 자기 변경 검증

코드를 작성하는 AI도 아래 검사를 실제 실행한다. 담당자 이름이 아니라 이번에 바꾼 경로와 영향을 기준으로 선택한다. 검사 결과와 코드 작성 원칙은 [AGENTS](../AGENTS.md#필요한-만큼-구현하고-실제로-검증하기), 사람의 검토 기준은 [CONTRIBUTING](../CONTRIBUTING.md#pr-작성과-검토)를 따른다.

| 변경 범위 | 실행할 검사 |
| --- | --- |
| C/S API·규칙 | `uv run --frozen python scripts/check_backend.py cs` |
| TFT API | `uv run --frozen python scripts/check_backend.py tft` |
| 인증·메일·평가 관리 API | `uv run --frozen python scripts/check_backend.py platform` |
| 공통 backend·AI·Python 의존성 | `uv run --frozen python scripts/check_backend.py all`. 영향을 받는 프론트와 인프라 검사는 아래에서 추가 |
| 평가 코드·평가 테스트 | `uv run --frozen python scripts/check_backend.py eval` |
| 프론트 | `npm test --prefix frontend`와 `npm run build --prefix frontend`. 변경한 화면 흐름도 실제 확인 |
| API 계약 | 해당 백엔드 검사에 `python3 scripts/workspace.py contracts cs --check` 추가. cs는 tft 또는 platform으로 교체하며 공통 계약은 all 사용 |
| 인프라·스크립트 Python | 아래 Ruff·포맷 및 unittest 명령 |
| 문서만 변경 | 링크·명령·범위·결정의 일관성 확인. 무관한 pytest 실행이나 테스트 신설은 필요 없음 |

백엔드 검사 한 번으로 Ruff 검사·포맷 확인·mypy·pytest·OpenAPI 및 합성 예시 일치를 확인한다. `all`은 백엔드 전체와 평가 테스트를 포함하며 저장소 전체 검사를 뜻하지 않는다. `eval`은 Ruff·포맷·pytest를 실행하며 별도 타입 검사는 아직 없다. 공통 코드 변경 시 영향을 받는 소비자 검사도 실행한다.

테스트는 `tests/cs`, `tests/tft`, `tests/platform`, `tests/eval`에 기능별로 둔다. 루트 테스트는 공통 회귀로 실행한다. 그 밖의 테스트 폴더는 우선 공유 범위로 취급해 백엔드와 평가 검사에서 재귀 수집한다. 새 테스트 폴더를 추가할 때도 해당 PR의 CI에서 실제 실행되는지 확인한다.

인프라·스크립트 검사:

```bash
uv run --frozen ruff check infra scripts
uv run --frozen ruff format --check infra scripts
python3 -m unittest discover -s infra/tests -q
python3 -m unittest discover -s scripts/tests -q
```

PR 자동화 JavaScript를 바꾸면 `node .github/tests/pr_metadata.test.cjs`도 실행한다. CI도 같은 검사를 실행한다. 위 unittest는 실제 GCP 변경을 수행하지 않는다.

검사 스크립트는 첫 실패에서 중단한다. 예를 들어 Ruff가 실패하면 이후 pytest는 미실행이다. 실패를 고친 뒤 명령을 다시 실행한다. PR에는 검증한 커밋, 실제 명령·결과, 미실행 이유와 CI 링크를 남긴다. 커밋 전 검사는 작업 트리 기준으로 표시하고 마지막 코드 수정에 영향받은 검사를 다시 실행한다. 필수 검증이 남으면 최종 완료 표시와 병합을 보류한다. 변경하지 않은 서비스의 정상 skipped와 검사 선택 오류를 구분한다.

프론트의 현재 `npm test`는 정적 웹 서버·프록시 테스트다. `build`는 TypeScript와 번들 생성 검사이며 React 화면의 클릭·입력·로딩·오류 동작을 보장하지 않는다. 화면 변경은 확인한 흐름과 방법을 PR에 기록한다. DB·세션 연결을 바꾸면 이미 실행한 로컬 환경에서 `python3 scripts/workspace.py check`도 사용한다. 이 연결 점검은 업무 기능 전체 검사나 실제 모델 품질 평가를 대신하지 않는다.

키 없이 평가 실행과 기록 경로를 확인한다.

```bash
uv run --frozen python -m eval.smoke
```

출력된 artifacts/eval 아래 JSON과 HTML 파일을 연다. fake 제공자는 외부 모델을 호출하지 않는다. 스키마 확인과 지연 및 사용량 필드 기록을 검증하며 업무 정확도나 실제 모델 성능을 측정한 결과는 아니다. 가격표가 없으면 비용은 미산정이다. 실제 Gemini 및 Microsoft 확인은 [외부 연동 점검](integration-checks.md)을 따른다. 최종 test 597건은 이 절차에서 사용하지 않는다.

## 필요할 때 참고: 실제 모델 호출

화면과 API 골격 개발은 위 fake 방식으로 시작한다. 실제 모델이 필요한 시점에 [외부 연동 점검](integration-checks.md)에서 로컬 GEMINI_API_KEY를 준비하고 아래 명령을 실행한다.

```bash
uv run --frozen python -m eval.smoke --provider gemini --env-file .env
```

기본 호출은 API 키를 사용하는 Gemini API이며 초기 모델은 gemini-3.6-flash다. 초기 연결 기준이며 최종 품질 평가로 선택한 모델은 아니다. 생성 결과의 스키마, 지연 및 토큰은 출력된 HTML에서 확인한다. 키가 다른 파일에 있으면 --env-file을 해당 파일로 바꾼다. fake 개발에서는 이 준비를 하지 않으며 Vertex ADC와 IAM 설정도 필요하지 않다. 업무 코드에서 재사용할 연결 및 임베딩 예제는 [공통 AI 클라이언트](ai-client.md)를 따른다.

## 문제가 생기면

| 증상 | 확인 및 해결 |
| --- | --- |
| Docker 명령을 못 찾거나 엔진 연결 실패 | Docker Desktop 또는 Docker Engine을 실행하고 Windows는 Ubuntu의 WSL Integration을 확인한 뒤 같은 터미널에서 docker info 재확인 |
| gh/uv/node/npm 명령을 못 찾음 | 현재 사용하는 WSL 또는 macOS/Linux 환경에 설치됐는지 확인하고 터미널을 다시 연다. Windows용 실행 파일과 WSL 설치를 섞지 않는다 |
| git clone/pull 인증 실패 또는 Repository not found | 브라우저에서 저장소 접근과 조직 초대를 확인하고 gh auth login 및 gh auth setup-git 수행 |
| Author identity unknown | 자신의 Git 이름과 이메일을 이 문서의 작업 시작 절차대로 설정 |
| 작업 시작 명령이 변경 파일 때문에 중단 | git status로 확인하고 본인 작업을 먼저 보존한다. 훅이나 변경 검사를 끄거나 자동 삭제하지 않는다 |
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

## 인프라 담당자 참고: 공용 환경과 배포

일반 기능 개발의 첫 PR에는 이 절을 먼저 수행할 필요가 없다.

[공용 웹](https://team4-web-gew6ci4pgq-du.a.run.app/)을 열면 플랫폼 DB와 pgvector 및 FTS 연결 상태를 확인할 수 있다. 이 화면은 배포 연결 확인용이다. 로컬 시연 계정 선택과 합성 업무 목록은 로컬 실행에서 사용한다. 실제 Microsoft 로그인과 업무 화면은 후속 구현이다.

공용 백엔드는 비공개이므로 Cloud Run API 주소에 직접 접속했을 때 403이 나오는 것은 정상이다. 플랫폼 health/ready는 웹 서버의 서비스 계정으로 연결한다. 개발 중인 업무 API를 연결하려고 백엔드를 임의로 공개하지 않는다.

서비스별 CI와 배포 범위 및 실행 방법은 [서비스별 CI와 CD](service-delivery.md)를 따른다. 기능 코드는 PR에서 해당 서비스와 계약 소비자 검사를 통과시킨다. 자동 CD 활성화 후에는 main 반영과 CI 성공 시 변경된 서비스만 배포된다.


배포 시 dev → main PR은 Create a merge commit으로 병합하고 main의 동일 SHA CI와 CD 성공을 확인한다. main → dev 동기화도 merge commit을 사용한다. 상세 실행은 배포 안내를 따른다.
