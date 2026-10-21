# KAN-63 API 실행 골격

C/S, TFT와 공통 플랫폼은 같은 저장소와 공통 패키지를 사용하되 각자 별도 프로세스 및 이미지로 실행한다. 공통 설정과 HTTP 응답 모델은 `backend/shared`, 업무 DTO는 각 서비스의 기능 폴더, 공통 HTTP 처리는 `backend/api/common`, 앱의 진입점은 `backend/api/{cs,tft,platform}/main.py`다. 각 앱은 다른 앱의 진입점을 import하지 않는다.

현재 구현은 프로세스 health와 DB 준비 상태, 공통 응답과 오류, 요청 ID, production 보호 및 로컬 DB 기반 계정 선택이다. 문의와 프로젝트 목록은 계약 stub으로 실제 API는 501이다. Microsoft 로그인, 업무 권한과 실제 메일 및 LLM, 작업 상태, SSE와 플랫폼 중계는 후속 작업이다. health의 성공은 DB나 외부 연동의 준비 상태를 뜻하지 않는다. 팀원 최초 실행은 [개발 시작 안내](../docs/team-start.md)를 따른다.

## 로컬 실행

저장소 루트에서 Python 3.12와 uv를 사용한다. 잠금 파일에는 Python 3.11 이상 호환 범위를 기록하며 Docker는 Python 3.12로 실행한다.

```bash
uv sync --frozen --python 3.12
```

아래 수동 실행은 프로세스 확인용이다. DB와 개발 계정 선택까지 실행하려면 팀원 안내의 Compose를 사용한다. 앱 설정은 루트 `.env`를 자동으로 읽으며 같은 이름의 환경 변수가 우선한다. DB 요청은 DATABASE_URL 또는 libpq의 PG 환경 변수를 사용하므로 수동 실행 시 자신이 지정한 연결 대상부터 확인한다. Compose에는 기존 `.env`를 주입하지 않는다.

```bash
SERVICE_NAME=cs PORT=8081 uv run --frozen python -m backend
```

```bash
SERVICE_NAME=tft PORT=8082 uv run --frozen python -m backend
```

```bash
SERVICE_NAME=platform PORT=8083 uv run --frozen python -m backend
```

| 서비스 | 프로세스 health | 서비스 health | 로컬 문서 |
| --- | --- | --- | --- |
| C/S | http://localhost:8081/health | /api/v1/cs/health | http://localhost:8081/docs |
| TFT | http://localhost:8082/health | /api/v1/tft/health | http://localhost:8082/docs |
| 플랫폼 | http://localhost:8083/health | /api/v1/platform/health | http://localhost:8083/docs |

성공 응답은 `data`와 `meta.request_id`, 오류는 `error.code`, `error.message`, `error.retryable`, `error.details` 및 `meta.request_id`를 사용한다. UUID 형식의 X-Request-ID만 이어받고 나머지는 새로 만든다. 요청 ID는 인증이나 멱등성 키로 사용하지 않는다.

production에서 DEV_AUTH_ENABLED=true면 시작을 거부한다. Cloud Run의 K_SERVICE가 있는 프로세스 역시 ENVIRONMENT=production을 요구한다. production HTTP 문서와 OpenAPI 경로는 숨기고, 앱별 JSON 파일을 빌드 산출물로 제공한다. 로컬 Compose 플랫폼에서만 DB에 등록한 시연 계정을 선택할 수 있으며 임의 사용자 헤더로 역할을 부여하지 않는다.

## 테스트 및 OpenAPI

```bash
uv run --frozen pytest -q
uv run --frozen ruff check backend tests
uv run --frozen ruff format --check backend tests
uv run --frozen mypy
uv run --frozen python -m backend.openapi
uv run --frozen python -m backend.openapi --check
uv run --frozen python -m backend.openapi --service cs
```

`docs/openapi/cs.json`, `tft.json`, `platform.json`은 실제 앱에서 생성한다. health, readiness와 대표 목록 stub 및 사용자 계약을 포함하며 업무 API 전체 계약은 아니다. 로컬 전용 계정 선택 경로는 일반 산출물에서 제외한다. 서비스별 생성 및 예시와 프론트 타입 연결은 [계약 안내](../contracts/README.md)를 따른다. 테스트는 합성 fixture만 사용하며 data 디렉터리를 읽거나 수정하지 않는다.

## Docker 이미지

```bash
docker build --target test -t team4-api-tests .
docker build --build-arg SERVICE_NAME=cs -t team4-cs .
docker build --build-arg SERVICE_NAME=tft -t team4-tft .
docker build --build-arg SERVICE_NAME=platform -t team4-platform .
docker run --rm -p 127.0.0.1:8081:8080 team4-cs
```

이미지는 UID/GID 10001로 실행하고 0.0.0.0의 PORT에 연결한다. 컨테이너 기본 설정은 production이다. .dockerignore와 .gcloudignore는 빌드에 필요한 소스만 허용하고 .env, data, Git 메타데이터와 가상 환경을 제외한다. 비밀번호를 build argument나 이미지 환경 변수로 넣지 않는다.

## Cloud Build 준비

루트 cloudbuild.yaml은 lint, 포맷과 타입 검사, 단위 테스트와 OpenAPI 일치 검사를 통과한 뒤 세 이미지를 만든다. 서울 team4 저장소에 전체 커밋 SHA 및 BUILD_ID 태그로 업로드하고 OCI revision label을 기록한다. 2026-10-22 사용자 실행의 실제 Cloud Build 및 이미지 업로드 성공을 확인했다. 빌드 자체에 서비스 배포 단계는 포함하지 않는다. 구체적인 실행 계정 설정과 소스 제출 및 비공개 배포 절차는 [빌드 안내](../infra/cloud-build.md)를 따른다.

Cloud Build 실행 계정과 이미지 쓰기 및 로그 권한을 설정한 뒤 사용자가 빌드를 실행한다. 이후 런타임 서비스 계정, ENVIRONMENT=production, Secret 버전과 Cloud SQL 연결을 포함해 서비스를 비공개로 배포하고 내부 호출 권한을 연결한다. 실제 Microsoft 로그인과 업무 권한이 준비되기 전에는 업무 API를 공개하지 않는다. .gcloudignore 업로드 목록은 `gcloud meta list-files-for-upload`로 확인할 수 있다.

참고: [FastAPI 컨테이너 배포](https://fastapi.tiangolo.com/deployment/docker/), [Cloud Run 런타임 계약](https://docs.cloud.google.com/run/docs/container-contract), [uv Docker 구성](https://docs.astral.sh/uv/guides/integration/docker/).

## 이번 구현의 검증 결과

2026-10-21, 로컬 Python 3.12에서 테스트 18건 및 하위 사례 2건, Ruff 검사와 포맷 검사, 앱별 OpenAPI 일치 검사를 통과했다. Docker 테스트 단계에서도 같은 검증을 통과했다. 테스트용 httpx와 Starlette 조합에서 deprecation 경고 1건이 있었으며 실패는 없었다.

서비스별 runtime 이미지 3개를 실제로 빌드하고 각각 localhost 임의 포트로 실행했다. 프로세스 및 서비스별 health 응답과 요청 ID, production 문서 경로 404, UID/GID 10001 실행 및 .env와 data 부재를 확인했다. 임시 컨테이너는 모두 제거했다.

gcloud의 실제 업로드 목록으로 .env, data, Git과 가상 환경 제외를 확인했다. Cloud Build SDK 파서로 cloudbuild.yaml의 테스트 선행 단계와 이미지 3개 구성을 검증했다. GCP의 Cloud Build 실행, 이미지 업로드, 앱 서비스 배포 및 실제 Microsoft 로그인과 업무 권한 검증은 아직 하지 않았다.
