# KAN-62 배포 기반 설정

대상 프로젝트는 `proj-skkuls0106-183255377938`, 리전은 `asia-northeast3`다. DB 권한 초기 설정은 [sql/README.md](sql/README.md)를 따른다.

## DB 비밀번호 등록

저장소 루트의 `.env`에 GCP 대상 설정과 `CS_DB_PASSWORD`, `TFT_DB_PASSWORD`, `PLATFORM_DB_PASSWORD`를 준비한 뒤 일반 터미널에서 실행한다. `.env`는 신뢰하는 로컬 설정이며 shell에서 source 가능한 형식이어야 한다. 비밀번호에 `$`, 공백이나 `#` 등이 있으면 적절히 인용한다.

```bash
set +x
set -a
source ./.env
set +a
python3 infra/register_db_secrets.py --dry-run
python3 infra/register_db_secrets.py
```

| Secret | 값의 환경 변수 | 접근 권한을 받는 실행 계정 |
| --- | --- | --- |
| cs-db-password | CS_DB_PASSWORD | cs-runtime |
| tft-db-password | TFT_DB_PASSWORD | tft-runtime |
| platform-db-password | PLATFORM_DB_PASSWORD | platform-runtime |

새 Secret의 복제 위치는 서울 한 곳이다. 비밀번호는 gcloud의 표준 입력으로만 전달하고 명령 인자, 임시 파일과 출력에 넣지 않는다. 사용자 입력 전체를 검증한 뒤 생성하며 각 Secret에 해당 서비스의 `roles/secretmanager.secretAccessor`만 추가한다. 상위 IAM에서 상속된 권한을 제거하는 동작은 하지 않는다.

재실행 시 기존 Secret의 값을 읽거나 바꾸거나 새 버전을 추가하지 않는다. 활성 버전이 있는지 확인하고 해당 실행 계정의 접근 권한을 연결한다. 따라서 로컬 비밀번호와 기존 Secret 값이 같은지 비교한 결과가 아니며 비밀번호 교체는 별도 절차다. 실패 시 그전까지 생성한 Secret은 남겨 두며 원본을 삭제하지 않는다. 오류 출력은 값 노출을 피하기 위해 명령 종류와 종료 코드만 제공한다.

이 스크립트는 Secret 및 접근 권한 등록용이다. Cloud Run 배포, 실제 실행 계정의 Secret 접근과 DB 연결 및 Secret 버전 지정은 후속 배포에서 검증한다. 기존 gemini-api-key는 변경하지 않는다.

검증 명령은 실제 gcloud 호출 없이 가짜 비밀번호를 사용한다.

```bash
python3 -m unittest discover -s infra/tests -v
```

## Cloud Run 실행 계정의 실제 DB 연결 검증

2026-10-21 12:51 UTC 실조회에서 DB Secret 3개 모두 서울 복제 위치, 활성 버전 1 및 각 실행 계정의 직접 접근 권한을 확인했다. Secret 값은 조회하지 않았다.

같은 환경 변수를 불러온 일반 터미널에서 실행한다.

```bash
bash infra/verify_cloud_db.sh
```

이 명령은 서울에 `verify-cs-db`, `verify-tft-db`, `verify-platform-db` Cloud Run Jobs를 생성 또는 갱신하고 순서대로 실행한다. PostgreSQL 17 공식 이미지의 psql로 `SELECT current_user;`를 실행한다. 각 Job은 자기 실행 계정과 DB 사용자 및 Secret 버전 1을 사용하며 로컬 비밀번호는 읽지 않는다. Cloud SQL 연결과 Secret의 실제 전달, DB 비밀번호 인증을 함께 확인한다. Job당 태스크 1개, 최대 120초, 재시도 0이다.

세 실행이 모두 성공하면 PASS를 출력한다. 실패하면 다음 서비스로 진행하지 않으며 생성한 Job은 진단을 위해 남겨 둔다. 완료한 Job 정의도 남아 있으므로 실제 앱 및 평가 Job과 구분한다. 이 검증은 업무 테이블 CRUD와 FastAPI 서비스 배포를 확인하는 단계는 아니다.

스크립트는 shell 문법과 모의 gcloud 호출로 대상 및 인자를 검증했다. 사용자가 실행한 뒤 2026-10-21 12:57 UTC 실조회에서 아래 세 실행의 성공을 확인했고 로그의 DB 사용자도 각각 일치했다. Secret 값은 조회하지 않았다.

| Job | 실행 | 접속 사용자 | 결과 |
| --- | --- | --- | --- |
| verify-cs-db | verify-cs-db-m6f9v | cs_app | 태스크 1개 성공, 12:56:12 UTC 완료 |
| verify-tft-db | verify-tft-db-zhsgk | tft_app | 태스크 1개 성공, 12:56:33 UTC 완료 |
| verify-platform-db | verify-platform-db-6w6zv | platform_app | 태스크 1개 성공, 12:56:55 UTC 완료 |

참고: [Cloud Run Jobs 배포 옵션](https://docs.cloud.google.com/sdk/gcloud/reference/run/jobs/deploy), [Cloud SQL 연결](https://docs.cloud.google.com/sql/docs/postgres/connect-run).

## RFP 문서 처리 큐

12:58 UTC 서울 큐 목록은 비어 있었고 사용자가 다음 명령을 실행했다. 13:00 UTC 실조회로 큐 생성 및 권한을 확인했다. 큐 이름은 `rfp-document-processing`, 초기 동시 처리 상한 2건, 초당 전송 2건, 최초 실행 포함 최대 시도 5회로 시작한다. 성능 및 모델 할당량을 검증한 최종 값은 아니며 실제 처리 시간과 오류율을 보고 조정한다.

```bash
gcloud tasks queues create rfp-document-processing \
  --location="$LS_REGION" \
  --project="$LS_PROJECT_ID" \
  --account="$LS_GCP_ACCOUNT" \
  --max-concurrent-dispatches=2 \
  --max-dispatches-per-second=2 \
  --max-attempts=5 \
  --max-retry-duration=0s \
  --min-backoff=10s \
  --max-backoff=300s

gcloud tasks queues add-iam-policy-binding rfp-document-processing \
  --location="$LS_REGION" \
  --project="$LS_PROJECT_ID" \
  --account="$LS_GCP_ACCOUNT" \
  --member="serviceAccount:tft-runtime@${LS_PROJECT_ID}.iam.gserviceaccount.com" \
  --role=roles/cloudtasks.enqueuer
```

이 단계는 큐와 작업 등록 권한만 구성한다. 처리 API URL, OIDC 호출 계정 및 Cloud Run 호출 권한, 작업 상태 저장과 중복 방지는 TFT 서비스 배포 및 구현과 연결해 검증한다. 이 명령에서는 실제 문서 처리 작업을 등록하지 않는다. 참고: [큐 생성](https://docs.cloud.google.com/tasks/docs/creating-queues).

2026-10-21 13:00 UTC 실조회: 큐 상태 RUNNING, maxConcurrentDispatches=2, maxDispatchesPerSecond=2, 자동 계산된 maxBurstSize=10, maxAttempts=5, minBackoff=10s, maxBackoff=300s다. 큐 수준 정책에 tft-runtime의 roles/cloudtasks.enqueuer가 존재했다. 실제 처리 API와 OIDC 연결은 아직 없고, Cloud Run 서비스 목록 및 서울 Cloud Build 트리거 목록도 비어 있었다. 다음은 세 FastAPI 앱의 실행 골격과 이미지 빌드 구성을 준비한 뒤 배포 및 실제 작업 처리를 연결하는 단계다.
