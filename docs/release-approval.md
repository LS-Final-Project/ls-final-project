# 11/3 배포 승인 점검표 (KAN-159)

11/3 배포를 내보내도 되는지 판정하는 점검표다. KAN-159 Jira 댓글(10721·10729·10782·10785·10791·10886·10938·10941)에
흩어진 항목을 한곳에 모으고, 지금 코드와 다시 대조했다(2026-10-31, dev `d09be38`).

- **배포 실행**: 오새결 님이 직접 한다. GCP 명령은 운영자 터미널에서 실행한다.
- **확인·기록**: 김나영. 각 단계의 출력을 KAN-159에 남기고, 끝에 5절 승인 보고서를 채운다.
- 명령의 세부는 각 스크립트·문서가 원본이다. 이 표는 순서와 확인할 것만 적는다.

## 0. 지금 상태

| 항목 | 값 | 근거 |
| --- | --- | --- |
| 운영 리비전 | 서비스 4개 모두 main `5188b7e`(2026-10-26) | 댓글 10729 |
| 배포되지 않은 변경 | dev가 main보다 384커밋 앞섬 | `git rev-list --count origin/main..origin/dev` |
| 공용 DB에 적용할 마이그레이션 | 60개: cs C3–C22(20), platform P10–P32(23), tft T2–T18(17). 번호 빠짐 없음 | [SQL 안내 전체 적용 순서](../infra/sql/README.md#전체-적용-순서) |
| worker Job | 접속 검증 Job 3개뿐. 평가·C/S·RFP worker Job 없음 | 댓글 10729 |
| 공용 DB 적용 상태 | 기록 없음(`공용 상태` 열 모두 미확인) | `infra/sql/README.md` |

## 1. 선행 결과

| WBS | 티켓 | 담당 | 상태(10/31) | 승인에 필요한 것 |
| --- | --- | --- | --- | --- |
| Q06 | KAN-154 주요 화면 E2E | 김종하 | 해야 할 일 | 배포 환경 화면 통과 기록 |
| Q07 | KAN-155 부하 | 김나영 | 진행 중 | 로컬 결과(`docs/load-test.md`)는 있음. 배포 환경 측정은 배포 뒤 |
| Q08 | KAN-156 품질·비용 회귀 | 오새결 | 진행 중 | 치명 결함 분류 결과 |
| Q09 | KAN-157 시연·되돌리기·복구 | 김나영 | 진행 중 | [런북](release-runbook.md) §2~§4 출력, 복구 훈련(비용 승인 뒤) |
| Q10 | KAN-158 대표 데이터·평가 구성 동결 | 오새결 | 해야 할 일 | 동결한 데이터·구성 판본 |
| V15 | KAN-147 모델 채택 | 오새결 | 완료 | 분류 `gemini-3.6-flash` v3, 출력 4096(#370) |

## 2. 배포 순서

순서를 바꾸지 않는다. 앞 단계가 실패하면 멈추고 KAN-159에 남긴다. **[비용]**은 모델 호출이나 GCP 자원 요금이 나는 단계다.

### A. 배포 전 확인 (비용 없음)

- [ ] **이전 앱 호환**: [런북 §1](release-runbook.md#1-배포-전-이전-앱-호환-확인)대로 `scripts.compat_check`
  `migrations`·`run`을 돌린다. `block` 0, `compatible: true`여야 한다. 2026-10-31 결과는 block 0·review 38,
  main tests 428 passed, 스냅샷 14,179줄 차이 0이다.
- [ ] **운영 서비스의 커밋**: 서비스 4개의 `commit-sha`가 모두 `5188b7e`인지 확인한다(런북 §1). 다르면 그 커밋으로 호환 검사를 다시 한다.
- [ ] **공용 DB 적용 상태**: SQL 안내 표의 "적용 전 확인" 쿼리(읽기 전용)로 C1–C2·P1–P9·T1이 적용돼 있고 C3 이후는 없는지 본다. 공용 상태 열에 날짜를 적는다.

### B. 공용 DB 마이그레이션 (운영자 터미널)

- [ ] SQL 안내 표 순서대로 C3–C22, P10–P32, T2–T18을 적용한다. 서비스 사이 순서 의존은 없고, 서비스 안에서는 번호 순서다.
- [ ] 벡터 검색을 켤 거면 C19·C20을 적용한다. pgvector 0.8 이상이어야 한다. C18을 다시 적용했으면 C20도 다시 적용한다(댓글 10886).
- [ ] 적용한 행마다 공용 상태 열에 날짜를 적는다.

### C. worker DB 로그인 (운영자 터미널)

- [ ] [worker 로그인 절차](../infra/sql/README.md#worker-로그인-kan-159)로 `cs_worker_login`·`tft_worker_login`을 만든다.
  `GRANT CONNECT ON DATABASE team4 TO cs_worker_login, tft_worker_login;`을 포함한다(#392, 없으면 `permission denied for database "team4"`).

### D. 서비스 배포 (dev → main)

- [ ] [평소 배포 절차](service-delivery.md#오새결의-평소-배포-dev에서-main으로)로 dev → main PR을 만들고 병합한다. 병합하면 CD가 바뀐 서비스를 배포한다.
- [ ] Actions의 Deploy changed services 성공을 확인한다.
- [ ] [런북 §2](release-runbook.md#2-배포-뒤-검증한-이미지가-그대로-나갔는지)로 서비스별 100% 리비전 digest = 이미지 빌드 digest = CI 성공 커밋을 기록한다(동일 이미지 기록).

### E. worker 실행 계정·이미지·Job (운영자 터미널)

`infra/setup_worker_jobs.py` 머리글의 순서를 따른다: prepare → Secret 등록·이미지 빌드·Job 배포 → grant → enable.
모든 스크립트는 기본이 미리보기다. 출력을 확인한 뒤 `--apply`로 실행한다.

- [ ] `python3 infra/setup_worker_jobs.py prepare`로 worker 실행 계정(`cs-worker-runtime`·`tft-worker-runtime`)과 전송 주기를 만든다.
- [ ] `python3 infra/register_db_secrets.py`로 worker DB 비밀번호(`cs-worker-db-password`·`tft-worker-db-password`)를 Secret Manager에 등록한다. 값은 화면에 남기지 않는다.
- [ ] worker 이미지를 빌드하고, worker마다 `python3 infra/deploy_workers.py <이미지 빌드 ID> --worker cs`(`tft`)로 Job을 배포한다.
  - C/S worker 모델은 `gemini-3.6-flash`다. 분류 4096, 초안·검증 6000 출력 상한은 코드가 호출마다 준다(`backend/agent/cs_runner.py`). Job 환경의 `GEMINI_MAX_OUTPUT_TOKENS=1024`는 이 호출들에 쓰이지 않는다.
- [ ] `python3 infra/setup_worker_jobs.py grant`로 권한을 준다.
- [ ] `python3 infra/setup_worker_jobs.py enable`로 처리를 켠다.
  - **[비용] enable은 실제 처리와 모델 호출을 켠다.** 켜지는 설정은 다음과 같다.
    - 플랫폼: `JOBS_SWEEP_ENABLED`, `EVALUATION_DISPATCH_ENABLED`, `EVALUATION_JOBS_ENABLED`, `CS_/TFT_JOBS_ENABLED`, `CS_/TFT_JOBS_PROCESSOR_CONNECTED`, `CS_/TFT_WORKER_JOBS_ENABLED`
    - 서비스: `TFT_PROCESSING_ENABLED`(team4-tft), `CS_ANALYSIS_ENABLED`·`CS_ANALYSIS_PLATFORM_URL`(team4-cs)

### F. 검색 색인 (운영자 터미널)

- [ ] 공용 문서 색인이 `chunking-v2`인지 확인하고, 아니면 `scripts/cs_index_docs.py --execute`로 재색인한다. 배포 전 재색인은 필수다(#297). 대상 DB는 `DATABASE_URL`(운영자 프록시)이고 `--docs`·`--data-origin`은 스크립트 도움말을 따른다.
- [ ] 벡터 검색을 켤 거면 다음을 한다(댓글 10886).
  - **[비용]** `cs_index_docs --embed`로 임베딩을 적재한다(`gemini-embedding-2`, 768, 약 1,098청크, 근사 $0.012). 끝나면 `VACUUM cs.chunk_embeddings`.
  - 환경 설정: `CS_VECTOR_SEARCH_ENABLED=true`, `CS_EMBEDDING_MODEL=gemini-embedding-2`, `CS_EMBEDDING_QUERY_TASK_TYPE=RETRIEVAL_DOCUMENT`.
  - 확인: 임베딩이 실패하면 FTS로 넘어가고 `CS_VECTOR_SEARCH_FAILED`가 로그에 남는지 본다.

### G. 로그인·메일·알림 켜기

"실메일" 완료 조건에 필요하다. 절차는 [외부 연동 점검](integration-checks.md)과 각 스크립트를 따른다.

| 설정 | 켜는 곳 | 메모 |
| --- | --- | --- |
| `MICROSOFT_AUTH_ENABLED` | `infra/prepare_microsoft_auth.py` | 제품 로그인 |
| `MAILBOX_AUTH_ENABLED` | 수동 환경 설정 | 사서함 연결. 켜는 스크립트가 없다. 현재 값 확인 |
| `MAIL_SYNC_ENABLED` | `infra/setup_mail_sync.sh` | 메일 수집 |
| `MAIL_SEND_ENABLED` | `infra/setup_mail_send.sh` | 메일 발송 |
| `CS_MAIL_ENABLED` | 수동 환경 설정 | C/S 고객 메일 발송 입구. 켜는 스크립트가 없다. 현재 값 확인 |
| `NOTIFICATION_PUSH_ENABLED` | `infra/setup_notification_push.sh` | 브라우저 알림(선택) |
| `BUSINESS_RELAY_ENABLED` | 수동 환경 설정 | 웹 → 플랫폼 → C/S·TFT 중계. [중계 활성화 전 확인](service-delivery.md#kan-178-업무-중계-활성화-전-확인). 현재 값 확인 |

### H. 평가 화면

- [ ] 평가 승인 목록(`EVALUATION_APPROVED_CONFIG_HASHES`, 플랫폼)에 화면에서 보일 실행의 config_hash를 등록하고, 평가자 ID를 정한다.
  - 댓글 10721의 해시(`303739fa…`)는 flash-lite v3 기준이다. 그 뒤 #370으로 3.6-flash v3가 채택됐으므로 **오새결 님이 지금 쓸 해시를 다시 확인**한다.
- [ ] `EVALUATION_LOCAL_ADMISSION_ENABLED`는 꺼 둔다(기본 false).

### I. 배포 뒤 확인

- [ ] [런북 §3·§4](release-runbook.md#3-시연-준비-확인)를 확인한다.
  - minScale 1·maxScale 2
  - 백엔드 `ENVIRONMENT=production`·`DEV_AUTH_ENABLED=false`, 개발 계정 API 404, 웹 첫 화면이 계정 화면
- [ ] 운영 환경에 `AI_TRACING_ALLOWED`와 `LANGSMITH_*`·`LANGCHAIN_*`가 없는지 본다. LangSmith는 local 전용이다(KAN-89, 댓글 10785).
- [ ] 오류 로그를 확인한다([기본 운영 확인](operations-basics.md#서비스와-오류-확인)).

### J. 실제 업무 검증 (완료 조건 "두 업무, 실메일, 평가 화면 배포 실행")

- [ ] **[비용] C/S**: 문의 1건을 분석 접수하고 분류 → 검색 → 초안까지 확인한다(`GET …/analysis`). 로컬 성공 기록은 KAN-194 댓글 10708이다.
- [ ] **[비용] RFP(TFT)**: 문서 1건을 처리한다. TFT는 새 개발을 멈췄지만 배포된 기능은 확인한다.
- [ ] **실메일**: 수신 1건 수집, 발송 1건 접수, 발송 상태 반영을 확인한다.
- [ ] **평가 화면**: 반입 실행 요약이 조회되는지 본다.
- [ ] **[비용] 배포 환경 부하 소규모 측정**: KAN-155에서 배포 뒤 하기로 한 측정이다.

## 3. 기능 설정 전체 목록

코드의 설정 클래스에서 참/거짓 설정을 모두 뽑았다(2026-10-31). 기본값은 모두 안전한 쪽이다.

| 설정 | 기본 | 켜는 곳 |
| --- | --- | --- |
| `DEV_AUTH_ENABLED` | false | production에서는 켤 수 없다(설정 검사) |
| `AI_TRACING_ALLOWED` | false | local 전용 |
| `CS_DRAFT_VERIFICATION_ENABLED` | **true** | 초안 문장 검증(KAN-200). 끌 이유가 없으면 그대로 둔다 |
| `EVALUATION_LOCAL_ADMISSION_ENABLED` | false | 운영에서 켜지 않는다 |
| `JOBS_SWEEP_ENABLED`, `EVALUATION_DISPATCH_ENABLED`, `EVALUATION_JOBS_ENABLED`, `CS_JOBS_ENABLED`, `CS_JOBS_PROCESSOR_CONNECTED`, `CS_WORKER_JOBS_ENABLED`, `TFT_JOBS_ENABLED`, `TFT_JOBS_PROCESSOR_CONNECTED`, `TFT_WORKER_JOBS_ENABLED`, `TFT_PROCESSING_ENABLED`, `CS_ANALYSIS_ENABLED` | false | `setup_worker_jobs.py enable`(2-E) |
| `CS_WORKER_ENABLED`, `TFT_WORKER_ENABLED` | false | `deploy_workers.py`(Job 환경, 2-E) |
| `CS_VECTOR_SEARCH_ENABLED` | false | 수동(2-F) |
| `MICROSOFT_AUTH_ENABLED`, `MAILBOX_AUTH_ENABLED`, `MAIL_SYNC_ENABLED`, `MAIL_SEND_ENABLED`, `CS_MAIL_ENABLED`, `NOTIFICATION_PUSH_ENABLED`, `BUSINESS_RELAY_ENABLED` | false | 2-G 표 |

## 4. 철회되거나 바뀐 항목

| 댓글의 항목 | 지금 | 근거 |
| --- | --- | --- |
| worker 환경에 LangSmith 키가 있으면 `INVALID_WORKER_CONFIGURATION` | 철회. 원인은 로컬 명령이었다. 운영에는 LangSmith 변수 자체를 두지 않는다 | 댓글 10785 |
| worker 이미지에 `cs_required_info.csv`가 없음 | 철회. runtime 이미지가 `eval/`을 복사한다 | 댓글 10791, `Dockerfile` |
| `EVALUATION_READ_RELAY_ENABLED` 결정 | 코드에 이 설정이 없다. 평가 API는 플랫폼 자체 경로(`backend/api/platform/evaluations`)라 업무 중계 설정과도 무관하다. 평가 화면에 무엇이 보일지는 평가 승인 목록(2-H)으로 정해진다 | 3절 목록 |
| 평가 승인 해시 `303739fa…` | 채택이 3.6-flash v3로 바뀌어 다시 확인 필요 | #370, 2-H |
| 분류 출력 상한 | 4096(호출마다 지정). Job 환경 1024와 무관 | `cs_runner.py` `CLASSIFICATION_DEFAULTS` |

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
- 동일 이미지: 서비스별 리비전 · digest · commit-sha = 이미지 빌드 digest · CI 성공 커밋, worker Job digest

마이그레이션: C3–C22 / P10–P32 / T2–T18 적용 시각, 공용 상태 열 갱신 여부
켠 설정: 3절 목록 중 켠 것
미검증 한계: (예) 배포 환경 부하는 소규모만, 복구 훈련 미실행(비용 승인 대기), 모델 품질은 평가 세트 기준
```
