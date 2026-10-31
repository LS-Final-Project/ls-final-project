# 11/3 배포 승인 점검표 (KAN-159)

11/3 배포를 내보내도 되는지 판정하는 점검표다. KAN-159 Jira 댓글(10721·10729·10782·10785·10791·10886·10938·10941)에
흩어진 항목을 한곳에 모으고, 지금 코드·스크립트와 다시 대조했다(2026-10-31, dev `d09be38`).

- **배포 실행**: 오새결 님이 직접 한다. GCP 명령은 운영자 터미널에서 실행한다.
- **확인·기록**: 김나영. 각 단계의 출력을 KAN-159에 남기고, 끝에 5절 승인 보고서를 채운다.
- 명령의 세부는 각 스크립트·문서가 원본이다. 이 표는 순서와 확인할 것을 적는다.
- **[비용]**: 모델 호출이나 새 유료 자원(빌드·Job·인스턴스)이 생기는 단계다.

## 0. 지금 상태

| 항목 | 값 | 근거 |
| --- | --- | --- |
| 운영 리비전 | 서비스 4개 모두 main `5188b7e`(2026-10-26) | 아래 확인 |
| 배포되지 않은 변경 | dev가 main보다 384커밋 앞섬 | `git rev-list --count origin/main..origin/dev` |
| 공용 DB에 적용할 마이그레이션 | 60개: cs C3–C22(20), platform P10–P32(23), tft T2–T18(17). 번호 빠짐 없음 | [SQL 안내 전체 적용 순서](../infra/sql/README.md#전체-적용-순서) |
| worker Job | 접속 검증 Job 3개뿐. 평가·C/S·RFP worker Job 없음 | 댓글 10729 |
| 공용 DB 적용 상태 | 적용 기록 없음. 공용 상태 열은 T18·P32만 `미적용`, 나머지는 `미확인` | `infra/sql/README.md` |

**배포 전 운영 상태 (2026-10-31 15:40 KST, 김나영 읽기 전용 확인)**. 배포 뒤 같은 항목을 다시 보고 비교한다.

| 항목 | 값 |
| --- | --- |
| 리비전·트래픽 | web 00007·platform 00009·cs 00007·tft 00007, 모두 `5188b7e`, 최신 리비전 100%(`latestRevision: true`) |
| 인스턴스 | 4개 모두 서비스 단위 min 1·max 2 |
| 개발 모드 | 백엔드 3개 `ENVIRONMENT=production`·`DEV_AUTH_ENABLED=false`, 웹 `NODE_ENV=production`. 공개 웹 경유 `/api/v1/platform/dev/accounts` 404 |
| 업무 중계 | 이미 켜져 있음. `BUSINESS_RELAY_ENABLED=true`(4개), 플랫폼 `BUSINESS_CS_URL`·`BUSINESS_TFT_URL`·`BUSINESS_PLATFORM_EMAIL`, cs `CS_MANUAL_DATA_ORIGIN`, 플랫폼 `MICROSOFT_AUTH_ENABLED=true` |
| 꺼져 있는 설정 | 웹 `EVALUATION_READ_RELAY_ENABLED`·`EVALUATION_CONTROL_RELAY_ENABLED`·`MAILBOX_AUTH_ENABLED` 없음, `CS_MAIL_ENABLED` 없음 |
| 모델 설정 | cs·tft `GEMINI_MODEL=gemini-3.6-flash`, `GEMINI_MAX_OUTPUT_TOKENS=1024`. LangSmith 변수 없음 |
| 공개 웹 | `/` 200, `/health` 200, `/api/v1/platform/ready` 200(database·vector·fts 모두 true) |
| Cloud SQL | `db-custom-2-8192`(2 vCPU·8GB), ENTERPRISE, SSD 20GB, ZONAL, POSTGRES_17. 자동 백업 매일 성공(최근 2026-10-30), **시점 복구(PITR) 꺼짐**: 복구는 하루 단위 백업 시점으로만 된다 |

## 1. 선행 결과

| WBS | 티켓 | 담당 | 상태(10/31) | 승인에 필요한 것 |
| --- | --- | --- | --- | --- |
| Q06 | KAN-154 주요 화면 E2E | 김종하 | 해야 할 일 | 배포 환경 화면 통과 기록 |
| Q07 | KAN-155 부하 | 김나영 | 진행 중 | 로컬 결과(`docs/load-test.md`)는 있음. 배포 환경 측정은 배포 뒤 |
| Q08 | KAN-156 품질·비용 회귀 | 오새결 | 진행 중 | 치명 결함 분류 결과 |
| Q09 | KAN-157 시연·되돌리기·복구 | 김나영 | 진행 중 | [런북](release-runbook.md) §2~§4 출력, 복구 훈련(비용 승인 뒤) |
| Q10 | KAN-158 대표 데이터·평가 구성 동결 | 오새결 | 해야 할 일 | 동결한 데이터·구성 판본 |
| V15 | KAN-147 모델 채택 | 오새결 | 완료 | 분류만 채택: `gemini-3.6-flash` v3, 출력 4096(#370). 초안·검증 출력 6000은 **채택 전**이다(`cs_runner.py`, `eval/policy/promotion-exceptions.json`의 초안 NO_ADOPTION) |

## 2. 배포 순서

순서를 바꾸지 않는다. 앞 단계가 실패하면 멈추고 KAN-159에 남긴다.

### A. 배포 전 확인 (비용 없음)

- [ ] **이전 앱 호환**: [런북 §1](release-runbook.md#1-배포-전-이전-앱-호환-확인)대로 `scripts.compat_check`의 `migrations`·`run`을 돌린다.
  - `block` 0, `compatible: true`여야 한다.
  - 2026-10-31 결과: block 0·review 38, main tests 428 passed, 스냅샷 14,179줄 차이 0.
- [ ] **운영 서비스의 커밋**: 서비스 4개의 `commit-sha`가 모두 `5188b7e`인지 확인한다(런북 §1). 다르면 그 커밋으로 호환 검사를 다시 한다.
- [ ] **공용 DB 적용 상태**: SQL 안내 표의 "적용 전 확인" 쿼리(읽기 전용)로 본다. C1–C2·P1–P9·T1은 적용돼 있고 C3 이후는 없어야 한다. 결과는 공용 상태 열에 적는다.
- [ ] **worker 준비물**: `setup_worker_jobs.py prepare`가 기대하는 두 가지가 있는지 본다. Secret `gemini-api-key`와 버킷 `proj-skkuls0106-183255377938-team4-rfp-documents`다.

### B. 공용 DB 마이그레이션 (운영자 터미널, 관리자 연결)

- [ ] SQL 안내 표 순서대로 **C3–C22, P10–P32, T2–T18을 모두** 적용한다. 한 파일씩 `psql -X -v ON_ERROR_STOP=1 -f infra/migrations/<서비스>/<파일>`로 실행한다.
  - 서비스 사이 순서 의존은 없다. 서비스 안에서는 번호 순서를 지킨다. T14는 표에 P29를 선행으로 적었지만, T14가 필요한 역할을 스스로 만들어서 실제 의존은 없다.
  - **C19는 필수다**(시도 사용량 열). 이게 없으면 C/S worker가 저장할 때 `UndefinedColumn`으로 실패하고, C21도 적용되지 않는다.
  - C20(임베딩 표)은 pgvector 0.8 이상이 필요하다. 벡터 검색을 켜지 않아도 적용한다. C18을 다시 적용했다면 C20·C22도 다시 적용한다.
- [ ] 각 파일 전에 "적용 전 확인" 쿼리를 실행하고, true면 건너뛴다. **재실행 불가 행(P10–P14, P16–P19, P23)은 반복하지 않는다.**
- [ ] 실행 역할이 `postgres`인 행(T3·T13·T14·T17·P13·P14·P15·P19·P28·P29)은 운영자 postgres 연결로 실행한다.
- [ ] KAN-81(P13·P19)은 [사서함 보관 준비](../infra/sql/README.md#사서함-보관-준비-kan-81) 절을 따른다. 적용 뒤 platform_app 연결로 `infra/sql/004_check_mailbox_vault.sql`을 돌린다.
- [ ] KAN-71·KAN-74·KAN-75·KAN-200·KAN-201은 SQL 안내의 각 적용 절(추가 단계와 적용 후 권한 확인)을 따른다.
- [ ] 적용한 행마다 공용 상태 열에 날짜를 적는다.

### C. worker DB 로그인 (운영자 터미널)

- [ ] [worker 로그인 절차](../infra/sql/README.md#worker-로그인-kan-159)로 `cs_worker_login`·`tft_worker_login`을 만든다. P28·P29를 적용한 뒤에 한다.
  - `GRANT CONNECT ON DATABASE team4 TO cs_worker_login, tft_worker_login;`을 포함한다(#392). 빠지면 `permission denied for database "team4"`가 난다.

### D. 검색 색인 (운영자 터미널, 서비스 배포와 처리 켜기보다 먼저)

처리가 켜진 뒤 색인이 옛 판본이면, worker가 `chunking-v3` 조건으로 검색해 근거 0건 초안을 만든다. 그래서 색인을 먼저 맞춘다.

- [ ] 공용 문서 색인이 `chunking-v3`인지 확인한다. 아니면 `scripts/cs_index_docs.py --execute --allow-remote`로 재색인한다(#297).
  - `DATABASE_URL`은 운영자 프록시를 거친 **cs_app** 연결이다. `--docs`·`--data-origin`은 스크립트 도움말을 따른다.
- [ ] 벡터 검색을 켤 거면 다음을 한다(댓글 10886).
  - **[비용]** `cs_index_docs --embed --allow-remote`로 임베딩을 적재한다(`gemini-embedding-2`, 768, 약 891청크, 근사 $0.0103). 끝나면 `VACUUM cs.chunk_embeddings`.
  - 서비스 설정은 G에서 켠다.

### E. 서비스 배포 (dev → main)

- [ ] **[비용, Cloud Build]** [평소 배포 절차](service-delivery.md#오새결의-평소-배포-dev에서-main으로)로 dev → main PR을 만들고 병합한다. 병합하면 CD가 바뀐 서비스를 배포한다.
- [ ] Actions의 Deploy changed services 성공을 확인한다.
- [ ] 공개 웹 `/api/v1/platform/ready` 200과 오류 로그를 확인한다. 리비전 digest 기록(동일 이미지)은 F가 리비전을 다시 만들므로 I에서 한다.

### F. worker 실행 계정·이미지·Job (운영자 터미널)

`infra/setup_worker_jobs.py` 머리글의 순서를 따른다: prepare → Secret 등록·이미지 빌드·Job 배포 → grant → enable.

- [ ] `python3 infra/setup_worker_jobs.py prepare`로 worker 실행 계정과 전송 주기(Scheduler `jobs-dispatch`, 멈춘 상태)를 만든다. 미리보기를 확인한 뒤 `--apply`로 실행한다.
- [ ] `infra/register_db_secrets.py`로 DB 비밀번호 Secret을 등록한다. **이 스크립트는 다른 스크립트와 반대로, 기본이 실제 실행이다.** 미리보기는 `--dry-run`이고, `--apply` 옵션은 없다.
  - 필요한 환경 변수는 8개다.
    - `LS_PROJECT_ID`·`LS_GCP_ACCOUNT`·`LS_REGION`
    - 비밀번호 5개: 기존 `CS_DB_PASSWORD`·`TFT_DB_PASSWORD`·`PLATFORM_DB_PASSWORD`, 새 `CS_WORKER_DB_PASSWORD`·`TFT_WORKER_DB_PASSWORD`
  - 실행 순서: 8개를 export → `--dry-run` → 실제 실행. 값은 화면에 남기지 않는다.
- [ ] **[비용, Cloud Build]** E에서 병합된 main 커밋으로 worker 이미지를 빌드한다: `python3 infra/release.py build --service cs-worker`(`tft-worker`). CD는 worker 이미지를 만들지 않는다.
- [ ] worker마다 `python3 infra/deploy_workers.py <worker 이미지 빌드 ID> --worker cs`(`tft`)로 Job을 배포한다. 미리보기를 확인한 뒤 `--apply`로 실행한다.
  - C/S worker 모델은 `gemini-3.6-flash`다. 분류 4096, 초안·검증 6000 출력 상한은 코드가 호출마다 준다(`backend/agent/cs_worker.py`·`cs_runner.py`). Job 환경의 `GEMINI_MAX_OUTPUT_TOKENS=1024`는 이 호출들에 쓰이지 않는다.
- [ ] `python3 infra/setup_worker_jobs.py grant`로 권한을 준다(미리보기 → `--apply`).
- [ ] **[비용] `python3 infra/setup_worker_jobs.py enable`로 처리를 켠다(미리보기 → `--apply`).** 실제 처리와 모델 호출이 여기서 켜진다.
  - 플랫폼에 켜지는 설정:
    - `JOBS_SWEEP_ENABLED`·`JOBS_SWEEP_SERVICE_URL`·`JOBS_SWEEP_INVOKER_EMAIL`
    - `EVALUATION_DISPATCH_ENABLED`·`EVALUATION_JOBS_ENABLED`·`EVALUATION_JOBS_PROJECT_NUMBER`·`EVALUATION_JOBS_JOB=evaluation-worker`
    - `CS_/TFT_JOBS_ENABLED`·`CS_/TFT_JOBS_PROCESSOR_CONNECTED`·`CS_/TFT_WORKER_JOBS_ENABLED`
  - team4-tft에 켜지는 설정: `TFT_PROCESSING_ENABLED`·`TFT_PROCESSING_PLATFORM_URL`·`TFT_DOCUMENT_BUCKET`
  - team4-cs에 켜지는 설정: `CS_ANALYSIS_ENABLED`·`CS_ANALYSIS_PLATFORM_URL`
  - Scheduler `jobs-dispatch`를 다시 켠다(매 분 dispatch 호출).
  - 서비스 3개에 새 리비전이 생긴다. 뒤의 CD 재배포는 `--update-env-vars`라 이 설정을 지우지 않는다.
  - 평가 worker Job(`evaluation-worker`)은 이번 묶음에서 만들지 않는다. 운영에서는 평가 실행을 접수하지 않고 반입 실행 요약만 조회한다. **오새결 님 확인 필요.**

### G. 화면·메일·검색 설정

절차는 [외부 연동 점검](integration-checks.md)과 각 스크립트를 따른다.

| 설정 | 서비스 | 켜는 곳 | 메모 |
| --- | --- | --- | --- |
| `BUSINESS_RELAY_ENABLED` 외 중계 설정 | 4개 | (이미 켜짐) | 그대로 둔다. 끄는 순서는 K |
| `EVALUATION_READ_RELAY_ENABLED=true` | team4-web | 수동 `gcloud run services update --update-env-vars` | **평가 화면 필수.** 없으면 웹이 `/api/v1/platform/evaluations/*`를 넘기지 않아 404다(`frontend/server.mjs`) |
| `EVALUATION_CONTROL_RELAY_ENABLED` | team4-web | 수동 | 평가 실행 조작 중계. 운영에서 실행을 접수하지 않으면 꺼 둔다 |
| `MAILBOX_AUTH_ENABLED` | team4-web·team4-platform | 수동 | 사서함 연결 화면과 API. "실메일"에 필요 |
| `MAIL_SYNC_ENABLED` | 플랫폼 | `infra/setup_mail_sync.sh` | 메일 수집 |
| `MAIL_SEND_ENABLED` | 플랫폼 | `infra/setup_mail_send.sh` | 메일 발송 |
| `CS_MAIL_ENABLED` | team4-cs | 수동 | C/S 고객 메일 발송 입구 |
| `NOTIFICATION_PUSH_ENABLED` | 플랫폼 | `infra/setup_notification_push.sh` | 브라우저 알림(선택) |
| `CS_VECTOR_SEARCH_ENABLED=true`, `CS_EMBEDDING_MODEL=gemini-embedding-2`, `CS_EMBEDDING_QUERY_TASK_TYPE=RETRIEVAL_DOCUMENT` | team4-cs·cs-worker Job | 수동 | D의 임베딩 적재 뒤. 실패하면 FTS로 넘어가고 로그에 `CS_VECTOR_SEARCH_FAILED`가 남는지 본다 |

### H. 평가 화면

- [ ] 플랫폼 `EVALUATION_APPROVED_CONFIG_HASHES`에 화면에 보일 실행의 config_hash를 등록하고, 평가자 ID를 정한다.
  - 댓글 10721의 해시(`303739fa…`)는 flash-lite v3 기준이다. 그 뒤 #370으로 3.6-flash v3가 채택됐으므로 **오새결 님이 지금 쓸 해시를 다시 확인**한다.
- [ ] `EVALUATION_LOCAL_ADMISSION_ENABLED`는 꺼 둔다(기본 false, production에서는 켜도 무효).
- [ ] G의 웹 `EVALUATION_READ_RELAY_ENABLED=true`가 들어갔는지 본다.

### I. 배포 뒤 확인

- [ ] [런북 §2](release-runbook.md#2-배포-뒤-검증한-이미지가-그대로-나갔는지)대로 동일 이미지를 기록한다. 서비스별 100% 리비전 digest = 이미지 빌드 digest = CI 성공 커밋이어야 한다. worker Job digest = worker 이미지 빌드 digest도 함께 적는다.
- [ ] [런북 §3·§4](release-runbook.md#3-시연-준비-확인)를 확인한다.
  - 서비스 단위 min 1·max 2
  - 백엔드 `ENVIRONMENT=production`·`DEV_AUTH_ENABLED=false`, 개발 계정 API 404, 웹 첫 화면이 계정 화면
- [ ] 운영 환경에 `AI_TRACING_ALLOWED`와 `LANGSMITH_*`·`LANGCHAIN_*`가 없는지 본다. LangSmith는 local 전용이다(KAN-89, 댓글 10785).
- [ ] Scheduler `jobs-dispatch`가 ENABLED인지, 플랫폼 로그에 dispatch 호출이 2xx로 남는지 본다.
- [ ] 오류 로그를 확인한다([기본 운영 확인](operations-basics.md#서비스와-오류-확인)).

### J. 실제 업무 검증 (완료 조건 "두 업무, 실메일, 평가 화면 배포 실행")

선행: F의 enable, G의 웹 평가 중계·사서함·메일 설정.

- [ ] **[비용] C/S**: 문의 1건을 분석 접수하고 분류 → 검색 → 초안까지 확인한다(`GET …/analysis`). 로컬 성공 기록은 KAN-194 댓글 10708이다.
- [ ] **[비용] RFP(TFT)**: 문서 1건을 처리한다. TFT는 새 개발을 멈췄지만 배포된 기능은 확인한다.
- [ ] **실메일**: 수신 1건 수집, 발송 1건 접수, 발송 상태 반영을 확인한다.
- [ ] **평가 화면**: 반입 실행 요약이 웹에서 조회되는지 본다.
- [ ] **[비용] 배포 환경 부하 소규모 측정**: KAN-155에서 배포 뒤 하기로 한 측정이다.

### K. 문제가 생기면

- DB는 되돌리지 않는다. 마이그레이션을 되돌리는 SQL이 없다. 이전 리비전이 새 스키마에서 도는지는 A의 호환 확인이 전제다.
- 처리부터 끈다.
  - Scheduler를 멈춘다: `gcloud scheduler jobs pause jobs-dispatch --location=asia-northeast3`
  - F·G에서 켠 설정을 false로 돌린다. 중계를 끌 때는 웹·플랫폼부터 끈다([KAN-178 중계 확인](service-delivery.md#kan-178-업무-중계-활성화-전-확인) 5항).
- 서비스는 [런북 §5](release-runbook.md#5-이전-리비전으로-되돌리기)대로 직전 리비전으로 트래픽을 돌린다.
  - 고친 배포가 나간 뒤에는 `--to-latest`로 자동 배분을 다시 켠다. 켜지 않으면 다음 배포가 트래픽 0%로 남는다.
- worker Job은 `deploy_workers.py`로 직전 worker 이미지 digest를 다시 배포한다.

## 3. 기능 설정 전체 목록

백엔드 설정 클래스와 웹 서버(`frontend/server.mjs`)에서 참/거짓 설정을 모두 뽑았다(2026-10-31). 기본값은 모두 안전한 쪽이다.

| 설정 | 기본 | 켜는 곳 |
| --- | --- | --- |
| `DEV_AUTH_ENABLED` | false | production에서는 켤 수 없다(설정 검사) |
| `AI_TRACING_ALLOWED` | false | local 전용 |
| `CS_DRAFT_VERIFICATION_ENABLED` | **true** | 초안 문장 검증(KAN-200). 끌 이유가 없으면 그대로 둔다 |
| `EVALUATION_LOCAL_ADMISSION_ENABLED` | false | 운영에서 켜지 않는다 |
| `JOBS_SWEEP_ENABLED`, `EVALUATION_DISPATCH_ENABLED`, `EVALUATION_JOBS_ENABLED`, `CS_JOBS_ENABLED`, `CS_JOBS_PROCESSOR_CONNECTED`, `CS_WORKER_JOBS_ENABLED`, `TFT_JOBS_ENABLED`, `TFT_JOBS_PROCESSOR_CONNECTED`, `TFT_WORKER_JOBS_ENABLED`, `TFT_PROCESSING_ENABLED`, `CS_ANALYSIS_ENABLED` | false | `setup_worker_jobs.py enable`(2-F) |
| `CS_WORKER_ENABLED`, `TFT_WORKER_ENABLED` | false | `deploy_workers.py`(Job 환경, 2-F) |
| `CS_VECTOR_SEARCH_ENABLED` | false | 수동(2-G) |
| `MICROSOFT_AUTH_ENABLED`, `MAILBOX_AUTH_ENABLED`, `MAIL_SYNC_ENABLED`, `MAIL_SEND_ENABLED`, `CS_MAIL_ENABLED`, `NOTIFICATION_PUSH_ENABLED`, `BUSINESS_RELAY_ENABLED` | false | 2-G 표(`MICROSOFT_AUTH`·`BUSINESS_RELAY`는 이미 켜짐) |
| 웹: `BUSINESS_RELAY_ENABLED`, `EVALUATION_READ_RELAY_ENABLED`, `EVALUATION_CONTROL_RELAY_ENABLED`, `MAILBOX_AUTH_ENABLED` | false | team4-web 수동(2-G). `BUSINESS_RELAY`는 이미 켜짐 |

## 4. 철회되거나 바뀐 항목

| 댓글의 항목 | 지금 | 근거 |
| --- | --- | --- |
| worker 환경에 LangSmith 키가 있으면 `INVALID_WORKER_CONFIGURATION` | 철회. 원인은 로컬 명령이었다. 운영에는 LangSmith 변수 자체를 두지 않는다 | 댓글 10785 |
| worker 이미지에 `cs_required_info.csv`가 없음 | 철회. runtime 이미지가 `eval/`을 복사한다 | 댓글 10791, `Dockerfile` |
| `EVALUATION_READ_RELAY_ENABLED`를 켤지 결정 | 웹 서버 설정이다. 평가 화면을 보이려면 켜야 한다(2-G). 10/31 운영 웹에는 없다 | `frontend/server.mjs` |
| 평가 승인 해시 `303739fa…` | 채택이 3.6-flash v3로 바뀌어 다시 확인 필요 | #370, 2-H |
| 분류 출력 상한 | 4096(호출마다 지정). Job 환경 1024와 무관 | `cs_worker.py` `classification_max_output_tokens` |

## 5. 승인 보고서 (배포 뒤 채움)

KAN-159에 아래를 채워 붙이고 검토자(김종하)에게 확인받는다.

```
배포 승인 점검 (YYYY-MM-DD HH:MM KST, 배포: 오새결, 기록: 김나영)
판정: 승인 / 조건부 승인(조건: …) / 보류(이유: …)

완료 조건
- 두 업무 배포 실행: C/S 분석 1건(작업 ID, 결과) / RFP 1건(작업 ID, 결과)
- 실메일: 수집 1건 / 발송 1건(상태)
- 평가 화면: 요약 조회(실행 ID)
- 치명 결함: N건(0이어야 승인). 목록: 티켓 번호
- 근거·보고서: 선행 Q06~Q10 결과 링크
- 동일 이미지: 서비스별 리비전 · digest · commit-sha = 이미지 빌드 digest · CI 성공 커밋, worker Job digest = worker 이미지 빌드 digest

마이그레이션: C3–C22 / P10–P32 / T2–T18 적용 시각, 공용 상태 열 갱신 여부
켠 설정: 3절 목록 중 켠 것
미검증 한계: (예) 배포 환경 부하는 소규모만, 복구 훈련 미실행(비용 승인 대기), 초안·검증 모델 설정은 채택 전,
  PITR 꺼짐(복구는 하루 단위), 모델 품질은 평가 세트 기준
```
