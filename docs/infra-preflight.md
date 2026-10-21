# 교육장 GCP 사전 점검

2026-10-21 CLI 및 Storage API 조회 결과. KAN-62의 확인 기록이며 앱 배포 완료를 뜻하지 않는다. 기존 RFI 리소스 삭제 후 상태와 신규 DB 구성, 12:45 UTC 실행 계정 및 서울 저장소 확인 결과를 기록했다.

## 서울 신규 DB 생성 확인

사용자가 안내한 명령을 터미널에서 실행한 뒤 2026-10-21 12:18 UTC에 읽기 전용으로 재조회했다. 아래는 제안이 아니라 실제 리소스 설정이다.

| 항목 | 조회 결과 |
| --- | --- |
| 인스턴스 | `team4-sql`, `RUNNABLE` |
| 리전 및 버전 | `asia-northeast3`, `POSTGRES_17`, `ENTERPRISE` |
| 사양 | `db-custom-2-8192`, 2 vCPU 및 메모리 8GiB |
| 가용성 | `ZONAL` |
| 저장소 | SSD 20GB, 자동 확장 활성, 상한 50GB |
| 연결 | 공인 IPv4 활성, 허용 네트워크 목록 없음, `connectorEnforcement=REQUIRED`, `sslMode=ENCRYPTED_ONLY` |
| 백업 및 삭제 보호 | 일일 백업 활성, 서울 보관, 19:00 UTC 시작, 7개 보관, 삭제 보호 활성 |
| 연결 이름 | `proj-skkuls0106-183255377938:asia-northeast3:team4-sql` |
| DB 및 사용자 | 12:24 UTC 재조회에서 `postgres`와 업무 DB `team4` 존재 확인. 사용자는 `postgres`만 존재하며 서비스별 스키마, 접속 계정 및 권한 분리는 아직 없음 |

관리자 접속과 `current_database()=team4`, `current_user=postgres`는 사용자가 터미널에서 확인했다고 응답했다. 실행 계정 3개의 DB 연결 IAM 권한과 실제 앱 로그인은 아직 검증하지 않았다. 비밀번호는 조회하거나 출력하지 않았다.

KAN-65의 초기 권한 SQL을 `infra/sql/001_service_roles.sql`, 검증 SQL을 `infra/sql/002_verify_service_roles.sql`에 준비했다. 앱 로그인 3개와 NOLOGIN 마이그레이션 역할 3개 및 서비스별 8개 스키마를 구성하는 제안이다. 아직 Cloud SQL에 적용하지 않았으며 공유 search 및 runtime 접근 정책과 업무 테이블 마이그레이션은 후속 작업이다. 로컬 PostgreSQL 16.15에서 비슈퍼유저 관리자로 적용 및 권한 검증을 통과했고 테스트 서버는 종료했다. 실제 Cloud SQL PostgreSQL 17에서 사용자 실행 및 PASS 확인이 남아 있다. 자세한 실행법과 검증 범위는 `infra/sql/README.md`를 따른다.

## 대상과 결과

| 항목 | 확인 결과 |
| --- | --- |
| 계정 | `skkuls0106@skkukdp.re.kr` |
| 프로젝트 | `proj-skkuls0106-183255377938`, 이름 `skkuls0106`, 번호 `1015616094444` |
| 프로젝트 상태 | ACTIVE |
| 결제 연결 | 활성화 |
| 프로젝트에 직접 부여된 역할 | `roles/owner`, `roles/editor` |
| 배포 리전 | 확정 설계는 서울 `asia-northeast3` |
| 리전 조직 정책 | 조회한 `gcp.resourceLocations` 유효 정책은 모든 값 허용 |
| IAM 도메인 조직 정책 | 조회한 `iam.allowedPolicyMemberDomains` 유효 정책은 모든 값 허용 |
| 서울 Cloud Run 서비스 | 없음 |
| 서울 Artifact Registry 저장소 | team4, Docker, STANDARD_REPOSITORY. 12:45 UTC 재조회 |

프로젝트 소유자 역할과 위 두 조직 정책의 조회만으로 모든 생성 권한과 조직 제한을 검증한 것은 아니다. 할당량과 나머지 적용 정책, API 활성화 및 실제 배포 가능 여부를 후속 작업에서 확인한다.

## API 상태

| API | 상태 |
| --- | --- |
| Cloud Run | 활성 |
| Cloud SQL Admin | 활성 |
| Cloud Tasks | 최초 점검에서 비활성. 사용자 설정 후 재조회에서 활성 확인 |
| Cloud Scheduler | 활성 |
| Cloud Build | 최초 점검에서 비활성. 사용자 설정 후 재조회에서 활성 확인 |
| Artifact Registry | 활성 |
| Secret Manager | 활성 |
| Cloud Storage | 활성 |
| IAM 및 IAM Credentials | 활성 |
| Logging 및 Monitoring | 활성 |
| Compute Engine | 활성 |
| Service Networking | 비활성. DB 연결 방식에 따라 필요 여부 결정 |

Cloud Run Jobs는 Cloud Run API를 사용한다. API 활성화는 사용자가 직접 진행했고 후속 CLI 조회로 Cloud Tasks와 Cloud Build의 활성 상태를 확인했다.

실행 서비스 계정은 `cs-runtime`, `tft-runtime`, `platform-runtime`의 세 이름으로 생성했다. 11:52 UTC 재조회에서 세 계정의 존재와 disabled=false를 확인했다. 프로젝트 IAM 정책에 세 계정의 직접 역할 부여는 없었으며 리소스별 IAM, 상속 권한과 Cloud Run 연결은 미검증이다. 기존 기본 계정과 `gh-deploy` 계정은 유지한다.

## 기존 리소스

| 종류 | 이름 | 리전 및 상태 |
| --- | --- | --- |
| Cloud SQL | `rfi-db` | 사용자 승인으로 최종 백업 없이 삭제 완료. 프로젝트 인스턴스 및 백업 목록 비어 있음 |
| Cloud Storage | `proj-skkuls0106-183255377938-rfi-data` | soft delete를 0초로 조회 후 객체 전체 버전과 버킷 삭제 명령 성공. 일반 목록은 비어 있으나 복구 보존 버킷 1개가 남아 2026-10-28 20:51:25 KST 영구 삭제 예정 |
| Cloud Run 및 Scheduler | `rfi-web`, `rfi-collect`, `rfi-collect-daily` | 삭제 완료 |
| Artifact Registry | `us-central1/rfi` | 삭제 완료. 전 리전 저장소 목록 비어 있음 |
| Secret Manager | `cloud-database-url` | 삭제 완료. `gemini-api-key`만 보존 |

사용자는 DB 최종 백업 생성을 생략하고 버킷의 복구 보존을 해제하는 범위에 대해 **백업 없이 완전 삭제**를 명시적으로 승인했다. GCS에는 보존 기간 종료를 기다리는 잔여 버킷이 있으며 기간 중 강제 영구 삭제는 지원하지 않는다. 실행 계정 3개는 유지했다. 11:55 UTC 당시 서울 리소스는 미생성이었으며 이후 위 `team4-sql` 생성만 12:18 UTC에 확인했다. 서비스, 버킷, 이미지 저장소, Tasks 큐, Scheduler와 Jobs 및 Cloud Build 트리거는 앞선 점검에서 없었고 이번 DB 점검에서는 재조회하지 않았다.

## 다음 작업

1. 활성화한 Cloud Tasks 및 Cloud Build와 실행 계정 3개의 필요한 권한을 검증하고 이미지 저장소, 시연 파일 저장소, Secret Manager 연결을 설정한다.
2. 생성한 서울 Cloud SQL에 업무 DB 및 서비스별 스키마와 접속 계정을 구성하고 권한 격리를 검증한다.
3. 앱 실행 골격과 함께 C/S, TFT, 플랫폼을 독립 배포하고 health 및 내부 호출 인증 확인.
4. Cloud Build 검증 및 배포 흐름 연결.
5. 실제 Microsoft 로그인 및 메일 연동, Gemini 키 지원 목록과 호출 제약, 서비스별 할당량 확인.

운영 환경 정책은 production이며 실제 로그인과 권한 검사를 적용한다. 기존 WBS 담당자는 유지하고 실제 인프라 세팅은 오새결이 진행한다.

## 서비스별 DB 계정 적용 후 확인

2026-10-21 12:36 UTC, 사용자가 초기 권한 SQL과 검증 SQL 및 비밀번호 설정을 완료했다고 응답한 뒤 API를 재조회했다. `cs_app`, `tft_app`, `platform_app`, `postgres` 사용자 존재를 확인했다. 세부 ACL과 PASS는 사용자 실행 보고이며 에이전트가 Cloud SQL에 SQL을 직접 실행해 확인한 결과와 구분한다. 계정별 실제 로그인 검증, Cloud Run 실행 계정의 IAM 연결과 Secret Manager 배포 설정은 남아 있다. 로컬 `.env`에는 DB명과 앱 계정명, 비밀번호 입력용 빈 항목을 추가했고 기존 값은 보존했다. 비밀번호는 읽거나 출력하지 않았다.

## 앱 로그인 이후 배포 준비 점검

2026-10-21 12:40 UTC, 사용자가 앱 계정 3개의 실제 로그인 및 스키마 접근 행렬 확인과 로컬 .env 입력을 완료했다고 답했다. 로컬 비밀번호 3개는 비어 있지 않음을 확인했고 값은 출력하지 않았다. 실제 로그인 결과는 사용자 보고와 에이전트 직접 검증을 구분한다. 프로젝트 IAM 정책에서 실행 계정 3개의 직접 역할 부여는 여전히 없었고 서울 Artifact Registry와 일반 GCS 버킷 목록은 비어 있었다. 다음은 Cloud SQL Client 권한 부여와 서울 이미지 저장소, 비공개 파일 버킷 및 Secret 연결이다. 안내할 명령의 실행은 아직 하지 않았다.

## 실행 계정 및 서울 저장소 생성 확인

2026-10-21 12:45 UTC 재조회 결과다. 아래는 사용자 명령 실행 후 API로 확인한 실제 설정이며 제안과 구분한다.

| 항목 | 확인 결과 |
| --- | --- |
| 실행 계정 3개 | cs-runtime, tft-runtime, platform-runtime 각각 프로젝트 수준 roles/cloudsql.client |
| Artifact Registry | asia-northeast3/team4, DOCKER, STANDARD_REPOSITORY, 생성 12:44:38 UTC |
| 파일 버킷 | proj-skkuls0106-183255377938-team4-files, ASIA-NORTHEAST3 |
| 버킷 보호 | uniform_bucket_level_access=true, public_access_prevention=enforced, soft delete 604800초 |
| 버킷 직접 권한 | platform-runtime에 roles/storage.objectUser. 프로젝트 기본 역할에서 파생된 버킷 권한도 유지됨 |
| Secret 목록 | gemini-api-key만 존재. 새 DB Secret은 아직 없음 |

다음 단계용 infra/register_db_secrets.py와 infra/README.md를 준비했다. 새 DB 비밀번호 3개를 서울 복제 위치의 Secret으로 만들고 각각 해당 실행 계정에 Secret 수준의 접근 권한을 부여한다. 실제 프로젝트에는 아직 실행하지 않았다. 가짜 비밀번호로 표준 입력 전달, 대상 프로젝트 검증, 재실행 시 기존 값 보존 및 오류 출력의 값 비노출 테스트 4건을 통과했다. Cloud Run 서비스의 Secret 버전 연결 및 실제 DB 접근 검증은 후속 작업이다.

## DB Secret 등록 확인

2026-10-21 12:51 UTC 실조회에서 cs-db-password, tft-db-password, platform-db-password가 각각 서울 복제 위치에 생성됐고 버전 1이 ENABLED임을 확인했다. 각 Secret 정책의 직접 바인딩에는 해당 cs-runtime, tft-runtime, platform-runtime의 roles/secretmanager.secretAccessor만 있다. 상위 정책의 상속 권한 전체를 분석하거나 비밀번호 값을 조회한 것은 아니다.

infra/verify_cloud_db.sh를 다음 사용자 실행용으로 준비했다. 각 실행 계정과 DB 사용자, Secret 버전 1로 실행되는 Cloud Run Jobs 3개를 생성 또는 갱신해 SELECT current_user만 수행한다. 문법과 모의 gcloud 인자 및 대상 프로젝트 검증을 통과했으며 실제 Cloud Run 실행은 아직 하지 않았다. 이후 사용자 완료 응답 시 실제 실행 상태와 로그를 확인한다.

## Cloud Run 실제 DB 연결 검증 완료

2026-10-21 12:57 UTC 조회에서 verify-cs-db-m6f9v, verify-tft-db-zhsgk, verify-platform-db-6w6zv가 모두 Completed=True 및 succeededCount=1이었다. 완료 시각은 각각 12:56:12, 12:56:33, 12:56:55 UTC다. Job의 실행 계정은 대응하는 서비스 계정이었고 로그에서 cs_app, tft_app, platform_app을 각각 확인했다. Secret 값은 조회하지 않았다. 업무 CRUD와 실제 FastAPI 서비스 배포 완료를 뜻하지 않는다.

12:58 UTC 서울 Cloud Tasks 큐는 없었다. infra/README.md에 rfp-document-processing 큐 생성과 TFT 계정의 enqueuer 권한 안내를 작성했으며 아직 실행하지 않았다. 초기 제안 값은 동시 2건, 초당 전송 2건, 최대 시도 5회, backoff 10~300초다. 처리 API URL 및 OIDC와 서비스 호출 권한은 앱 배포 후 연결한다.

## Cloud Tasks 큐와 작업 등록 권한 확인

2026-10-21 13:00 UTC 사용자 실행 후 재조회에서 rfp-document-processing 큐 RUNNING, maxConcurrentDispatches=2, maxDispatchesPerSecond=2, maxBurstSize=10, maxAttempts=5, minBackoff=10s 및 maxBackoff=300s를 확인했다. 큐 수준 IAM에는 tft-runtime의 roles/cloudtasks.enqueuer가 있었다. 처리 URL과 OIDC 연결 및 실제 작업 처리 검증은 남아 있다. 같은 시점 Cloud Run 서비스 목록과 서울 Cloud Build 트리거 목록은 비어 있었다. 다음은 앱 실행 골격과 Docker 빌드 구성 및 Cloud Build 배포 연결이다.

## KAN-63 로컬 앱 및 빌드 구성 검증

세 FastAPI 실행 골격과 Dockerfile 및 빌드 전 테스트를 포함한 cloudbuild.yaml을 작성했다. 로컬 Python 3.12와 Docker에서 테스트 18건 및 하위 사례 2건을 통과했고 실제 runtime 컨테이너 3개의 health, 서비스 선택과 production 및 비루트 실행을 검증했다. 임시 컨테이너는 제거했다. 소스 업로드 목록에서 .env와 data 등의 제외 및 Cloud Build SDK 설정 파싱도 확인했다. 이 단계에서는 GCP 리소스를 변경하거나 Cloud Build를 실행하지 않았다. 실제 빌드 계정 권한과 소스 저장 경로 설정, 이미지 업로드와 비공개 서비스 배포는 다음 단계다. 자세한 검증은 backend/README.md를 따른다.

## 2026-10-22 Cloud Build 준비 확인

사용자가 setup_cloud_build.sh 실행 완료를 알린 뒤 읽기 전용 조회로 cloudbuild-runner 서비스 계정과 proj-skkuls0106-183255377938-team4-build-source 버킷을 확인했다. 버킷은 ASIA-NORTHEAST3이며 uniform bucket access와 public access prevention이 켜져 있다. 빌드 계정의 소스 버킷 objectViewer, Artifact Registry writer와 프로젝트 logging.logWriter, 교육장 사용자의 해당 빌드 계정 serviceAccountUser 권한을 각각 확인했다. 실제 원격 빌드와 서비스 배포는 아직 실행하지 않았다. 다음은 최신 main의 submit_cloud_build.sh를 사용자 터미널에서 실행한 뒤 BUILD_ID 및 결과 digest를 조회하는 단계다.

로컬 Compose에서는 pgvector 및 FTS와 세 앱의 DB 연결을 실제 확인했다. 이 결과를 Cloud SQL의 vector 확장 또는 신규 identity 테이블 적용 완료로 보고하지 않는다.

## 2026-10-22 실제 Cloud Build 성공

사용자가 submit_cloud_build.sh를 실행했고 빌드 401a8afb-f9b9-4a51-878e-2a96fc373e99의 SUCCESS와 main 커밋 f04298161ea5b234d6e31521d186a6dd29f893f4를 확인했다. 서울 team4 저장소에 C/S, TFT, 플랫폼 이미지 3개의 SHA 및 BUILD_ID 태그와 결과 digest가 기록됐다. deploy_private.py의 기본 미리보기가 성공했고 production 및 익명 차단과 서비스별 DB Secret 연결을 확인했다. 실제 --apply 실행 및 호출 검증은 후속 기록을 따른다.

## 2026-10-22 첫 비공개 배포 확인

사용자가 성공 빌드의 digest로 team4-cs, team4-tft, team4-platform을 배포했다. 세 서비스의 Ready=True와 이미지 일치, 대응 runtime 서비스 계정 및 DB 사용자, 해당 Secret 버전 1, Cloud SQL Connector, ENVIRONMENT=production, DEV_AUTH_ENABLED=false와 minScale=1을 확인했다. IAM의 공개 주체는 없으며 invoker 검사도 활성 상태다. 실제 요청 결과 세 서비스 모두 익명 health 403, 교육장 사용자로 인증한 health 200이다. 플랫폼의 개발 계정 경로는 인증 요청에도 404다. 토큰은 프로세스 메모리에서만 사용하고 출력하지 않았다.

첫 `/api/v1/<service>/ready` 결과는 세 서비스 모두 503이었다. 아직 클라우드 적용을 확인하지 못한 vector 확장부터 점검하도록 Cloud SQL Studio의 team4 DB에서 CREATE EXTENSION IF NOT EXISTS 실행 및 버전 조회를 요청했다. 503만으로 원인을 확장 누락으로 확정하지 않으며, 실행 후 실제 ready를 다시 확인한다. 신규 identity 테이블과 OAuth, 업무 기능 및 프론트 호스팅은 이 배포에 포함되지 않는다.

## 2026-10-22 Cloud SQL 확장 적용 후 준비 검사 통과

사용자가 Cloud SQL Studio에서 vector 확장 생성 및 버전 조회를 실행했다고 응답한 뒤 세 서비스의 인증 ready를 다시 호출했다. C/S, TFT와 플랫폼 모두 HTTP 200이고 data.database, data.vector, data.fts가 모두 true였다. 실제 FastAPI가 각 서비스 DB 계정으로 자기 스키마 및 pgvector와 FTS 준비를 확인한 결과다. 첫 503 이후 확장 적용과 재검증 과정을 완료했으며 identity 업무 테이블과 실제 OAuth 구현 완료를 의미하지 않는다.
