# RFP 자사 대응 작업 접수 (KAN-182, A01 플랫폼 범위)

원본 합의: [RFP API 명세 v1.1](https://docs.google.com/document/d/1lh6blP9IqACKvr3nyPp7NuU07jOmliIvIH_h87HDu9g) §4.3·결정 4. 처리·내보내기·메모 해석 작업은 플랫폼 `runtime.jobs`를 `owner=tft`로 쓴다. 저장 구조는 [RFP 데이터 계약](rfp-data.md), 공통 작업 규칙은 [플랫폼 작업 계약](../platform/jobs.md)을 따른다.

| 원본 | 파일 |
| --- | --- |
| 작업 종류 등록 | `infra/migrations/platform/20261029_KAN-182_tft_jobs.sql` |
| 내부 접수 | `backend/api/platform/jobs/tft.py` |
| 전송 분기·상태·SSE 접근(KAN-199) | `backend/api/platform/jobs/{dispatch,cloud_run,status,events}.py` |
| worker 역할(KAN-199) | `infra/migrations/platform/20261031_KAN-199_tft_worker.sql`(P29) |
| worker·처리 함수 등록(KAN-199) | `backend/agent/tft_worker.py`, `backend/agent/processor_registry.py`, `backend/agent/job_runtime.py` |
| 검사 | `tests/platform/test_tft_jobs_db.py`, `tests/platform/test_tft_job_runtime_db.py`, `tests/platform/test_tft_worker_db.py`, `tests/agent/test_processor_registry.py` |

## 작업 종류

| `kind` | 만드는 경로(멱등 범위) | 단계 `step` |
| --- | --- | --- |
| `rfp.process` | `POST /api/v1/tft/projects/{project_id}/process` | `parse`·`extract`·`search`·`compare`·`supplement`·`candidates`·`draft` |
| `rfp.export` | `POST /api/v1/tft/projects/{project_id}/draft/exports` | `export` |
| `rfp.annotate` | `POST /api/v1/tft/projects/{project_id}/annotations/process` | `annotate` |

- DB CHECK 하나가 소유자·종류·경로 조합을 묶는다. 평가(`platform`·`evaluation.run`) 조합은 그대로다.
- `subject_id`는 프로젝트 ID이고 tft 작업에만 있다. 같은 프로젝트에 `QUEUED`·`RUNNING` tft 작업은 하나뿐이다(부분 유일 인덱스, 409 `TFT_JOB_IN_PROGRESS`).
- `business_id`는 작업 ID와 같다. 처리 결과는 tft 스키마의 판본 행이 가진다.

## 내부 접수 `POST /internal/v1/platform/jobs/tft`

tft 서비스가 §2.2 접근 검사와 본문 검증을 마친 뒤 부른다. `tft_app`에는 runtime 권한을 주지 않고 플랫폼(`platform_app`)이 저장한다.

- **기본 비활성(404)**. `TFT_JOBS_ENABLED=true`, `TFT_JOBS_SERVICE_URL`(플랫폼 서비스 주소, ID 토큰 audience), `TFT_JOBS_INVOKER_EMAIL`(tft 실행 서비스 계정)을 함께 정해야 켜진다. 켜면 `Authorization: Bearer <Google ID 토큰>`을 고정 Google 서명키·audience·실행 계정으로 다시 검증한다(Cloud Run IAM과 별도, 불일치 401).
- **`TFT_JOBS_PROCESSOR_CONNECTED`를 켜는 조건**: 전송기의 종류 구분, tft 작업 상태·SSE 접근 검사, 처리 worker(아래 'worker' 절), 처리 함수(아래 '처리 함수' 절)는 갖췄다. 남은 조건은 (1) P29와 `infra/migrations/tft/20261104_KAN-199_tft_worker.sql`(tft 표 권한) 적용과 `tft_worker` 로그인, (2) 원문 읽기 설정 `TFT_DOCUMENT_ROOT`와 추출 모델 설정, (3) `TFT_WORKER_JOBS_ENABLED`·`TFT_WORKER_ENABLED`다. 그 전에 켜면 작업이 FAILED나 QUEUED로 남는다.
- **처리기 연결 전에는 저장하지 않는다.** `TFT_JOBS_PROCESSOR_CONNECTED=false`(기본)이면 호출자 검증 뒤 503 `TFT_PROCESSOR_NOT_CONNECTED`를 돌려주고 행을 만들지 않는다. worker 없이 저장하면 QUEUED가 남아 그 프로젝트의 다음 접수가 계속 409가 되기 때문이다. tft 처리 worker를 연결할 때 함께 켠다.
- 호출자 검증은 본문 검증보다 먼저 한다(비활성 404·불일치 401이 422보다 앞선다).
- **본문**: `actor_id`, `kind`, `project_id`, `input_version`(동결 입력 SHA-256), `idempotency_key`(8~128자), `request_hash`(정규화 원 요청 SHA-256). 다른 키는 422다. 원문·프롬프트·모델 응답은 받지 않는다.
- **응답**: 새 작업 201, 같은 요청 재전송 200. `data`는 `job_id`, `kind`, `project_id`, `status`, `status_url`(`/api/v1/platform/jobs/{job_id}`), `created`. 같은 키에 hash·입력 판본·프로젝트가 다르면 409 `IDEMPOTENCY_CONFLICT`. 등록되지 않은 `actor_id`는 422 `JOB_ACTOR_UNKNOWN`.
- 작업, 첫 이벤트(`job.queued`), 전송 의도(`dispatch_outbox`), 작업 제어 행(`runtime.job_control`)을 한 트랜잭션에 저장한다(KAN-199). 처리기 연결 전에는 위 503 게이트 때문에 아무 행도 만들지 않고, tft 경로 11·30이 503을 그대로 전달한다(아래 'tft 접수' 절, 28은 A07).

## tft 접수 경로 11·30 (KAN-199·KAN-188)

브라우저가 부르는 tft 경로가 위 내부 접수를 대신 부른다(`backend/api/tft/rfp/jobs.py`, C/S 분석 접수와 같은 방식).

- **권한**: 프로젝트를 볼 수 없으면 404 `TFT_PROJECT_NOT_FOUND`다. 11 처리는 담당 sales·책임자, 30 메모 해석은 그에 더해 검토 부서 `dept_staff`이고(permissions.md 역할별 행동표), 아니면 403이다.
- **입력 검사(플랫폼 호출 전)**
  - 11: `document_version_ids`가 비면 프로젝트 문서마다 최신 판본을 처리한다. 프로젝트 밖 판본이 섞였거나 처리할 문서가 없으면 422 `VALIDATION_ERROR`다. 프로젝트에 요구가 이미 있으면(다시 처리하는 것이면) `reason`이 필요하고 없으면 422 `TFT_REASON_REQUIRED`다. tft는 `runtime.jobs`를 읽지 못하므로 '두 번째 실행'을 요구 유무로 판정한다.
  - 30: 두 판본이 모두 프로젝트 것이어야 한다(아니면 422).
- **플랫폼 본문**: `actor_id`=요청 사용자, `kind`(`rfp.process`·`rfp.annotate`), `project_id`, `input_version`=정렬한 문서 판본 ID 목록 JSON의 SHA-256, `idempotency_key`=요청 헤더, `request_hash`=경로 ID와 본문의 정렬 JSON SHA-256. 호출은 tft 실행 계정 ID 토큰이다(`backend/shared/platform_jobs.py`. tft 이미지에 `backend/api/cs`가 없어 C/S 클라이언트와 같은 동작을 shared에 따로 둔다).
- **재전송**: 플랫폼이 받은 접수는 `tft.rfp_commands`(`project.process`·`annotation.process`)에 키·본문 해시·입력 판본과 함께 남긴다. 같은 키·같은 본문이 다시 오면 입력 검사(사유·판본)를 다시 하지 않고 남긴 입력 판본으로 플랫폼을 부른다(첫 접수 뒤 요구가 생겨도 422가 아니라 기존 작업이다). 같은 키·다른 본문은 플랫폼을 부르지 않고 409 `IDEMPOTENCY_CONFLICT`다. 같은 키가 동시에 오면 플랫폼이 같은 작업을 돌려주므로 먼저 남긴 기록을 둔다.
- **응답**: 202 `JobAccepted{job_id, status, status_url}`. 같은 키 재전송도 202이고 `status`는 기존 작업의 지금 상태(플랫폼 6값)다.
- **오류**
  - 플랫폼 409 `TFT_JOB_IN_PROGRESS`·`IDEMPOTENCY_CONFLICT`와 503 `TFT_PROCESSOR_NOT_CONNECTED`는 그대로 옮긴다.
  - tft 설정 `TFT_PROCESSING_ENABLED`(기본 꺼짐, `TFT_PROCESSING_PLATFORM_URL`=플랫폼 HTTPS origin)가 꺼져 있으면 플랫폼을 부르지 않고 503 `TFT_PROCESSOR_NOT_CONNECTED`다.
  - 연결 실패·결과 모름·그 밖의 거절(예: 422 `JOB_ACTOR_UNKNOWN`), 2xx지만 접수 결과로 읽을 수 없는 본문은 503 `DEPENDENCY_UNAVAILABLE`이다.
- 28 내보내기는 담당 sales·책임자가 저장된 검토본을 DOCX로 접수한다. `ExportAccepted`는 기존 접수 필드에 `export_id`를 더한다. `input_version`은 예약 UUID 문자열의 SHA-256이며 process/annotate의 문서 판본 hash와 구분한다. PDF enum은 유지하지만 예약·접수 전에 422 `TFT_EXPORT_FORMAT_UNAVAILABLE`로 거부한다(PDF 생성 후속).
- 검사: `tests/tft/test_rfp_jobs_db.py`(가짜 플랫폼, `tft_app`).

## 전송 (KAN-199 1/2)

- 전송 장부는 종류별로 열린다. rfp.* 행은 `TFT_JOBS_ENABLED`·`TFT_JOBS_PROCESSOR_CONNECTED`가 켜졌을 때만 확보하고(사용자 결정: 환경이 아니라 명시 설정으로만 연다, 평가는 local/test 잠금 그대로), 그렇지 않으면 PENDING으로 남는다. 평가·C/S 전송은 rfp 행을 집지 않는다.
- 전송 직전 재검사는 C/S와 같은 `runtime.job_control` 규칙이다: 게이트, 작업 QUEUED·RUNNING, 취소 요청 없음, 살아 있는 worker lease 없음. 아니면 그 시도는 BLOCKED다.
- 트리거(KAN-159, `jobs/sweep.py`): `JOBS_SWEEP_ENABLED`가 켜지면 Scheduler가 `POST /internal/v1/platform/jobs/dispatch`를 호출 계정 ID 토큰으로 부르고(호출당 최대 5건), 접수 직후에도 한 건을 먼저 보낸다. 접수 직후 전송 실패는 접수를 실패시키지 않는다. 결과가 `UNKNOWN`인 RFP·C/S 행의 자동 복구는 없다(`recovery`는 평가 전용): 그 프로젝트의 다음 접수가 409로 남는다.
- Jobs 호출: `TFT_WORKER_JOBS_JOB`(기본 `tft-worker`)·`TFT_WORKER_JOBS_CONTAINER`, 환경 `TFT_JOB_ID`=작업 ID. `TFT_WORKER_JOBS_ENABLED`(기본 꺼짐)가 없으면 rfp 행을 확보하지 않는다. 프로젝트·리전·시간 제한은 평가 Jobs 설정(`EVALUATION_JOBS_*`)을 같이 쓴다.

## 상태 조회·SSE 접근 (KAN-199 1/2)

- `GET /api/v1/platform/jobs/{job_id}`와 `/events`는 tft 작업을 **요청 사용자(`actor_id`) 본인에게만** 보여 준다. 그 밖의 사용자는 역할(sales·manager 포함)과 관계없이 404다. 커서 오류보다 권한 검사가 먼저다.
- 프로젝트 범위(명세 §2.2: 담당 sales·검토 부서·책임자·관리 manager)는 tft 서비스 규칙이다. 플랫폼은 이를 알지 못하며, tft 서비스가 내부 접수를 부르기 전에 이미 검사했다는 전제로 요청자 본인 여부만 본다.
- 응답 `data`는 `TftJobStatus{job_id, project_id, owner:'tft', kind, status, version, last_event_sequence}`다. `version`은 `runtime.job_control.version`이다.

## SSE payload 허용 키 (명세 §4.3)

`state`(공통 작업 상태), `step`(위 단계 값), `document_version_id`(표준 하이픈 소문자 UUID 문자열), `counts`(`requirements`·`proposals` 둘 다 0 이상 정수, 다른 키 없음), `needs_review`(bool). 원문·프롬프트·모델 응답은 이벤트에 넣지 않는다. 플랫폼 `EventPayload`가 이 키만 SSE로 내보내고 모르는 키(오류 코드 등)는 버린다. 표에 없는 `step` 값이나 형식이 틀린 값이 저장되면 SSE는 503 `EVENT_DATA_UNAVAILABLE`이다.

## worker (KAN-199 2/2)

플랫폼 전송이 tft 이미지의 `tft-worker` 대상(`python -m backend.agent.tft_worker`)을 `TFT_JOB_ID`=작업 ID로 실행한다. 이 이미지에는 `backend/api/platform`·`backend/api/cs`가 없다.

- 게이트: `TFT_WORKER_ENABLED`(기본 꺼짐)가 켜졌을 때만 돈다. 아니면 `TFT_WORKER_DISABLED`다. `TFT_JOB_ID`는 표준 하이픈 UUID만 받는다(`INVALID_JOB_ID`).
- DB: `DATABASE_URL` 하나(`tft_worker` 멤버 로그인). 앱·마이그레이터 역할 멤버이거나 특권 속성이 있으면 `WORKER_ROLE_REQUIRED`다. P29는 runtime 열 권한만 준다(jobs 조회 열·status UPDATE, job_events 열 INSERT, job_control SELECT와 취소 열을 뺀 UPDATE). tft 표 권한은 처리 함수 쪽 tft 마이그레이션이 준다.
- 소유권(`runtime.job_control`, C/S worker와 공통 `job_runtime`)
  - 인수: owner가 tft이고 QUEUED이거나 lease가 끊긴 RUNNING이며 취소 요청이 없을 때만이다. generation+1·holder·lease(`TFT_WORKER_LEASE_SECONDS`, 기본 300초)·RUNNING·`job.started`. 아니면 `NOT_CLAIMED`다.
  - 처리 함수가 단계마다 `emit`을 부르면 소유권을 재검사하고 lease를 연장하며 `job.progress`를 남긴다. 소유권을 잃었으면 아무것도 쓰지 않고 `LEASE_LOST`, 취소 요청이면 CANCELLED·`job.completed{state:CANCELLED}`다.
- 처리 함수 등록(`backend/agent/processor_registry.py`)
  - 고정 모듈 `backend.api.tft.worker_processor`의 `build(settings) -> Processor`. 경로를 환경 변수로 받지 않는다. 모듈이 없으면 인수 뒤 FAILED·`TFT_WORKER_PROCESSOR_MISSING`이다.
  - `processor(job, emit)`: `job`은 `TftJob{job_id, kind, project_id, input_version, actor_id}`(원문 없음)다. 프로젝트 접근 검사는 tft 서비스가 접수 때 마쳤다.
  - `emit`이 올리는 취소·소유권 상실 예외를 처리 함수가 삼키지 않는다. 재실행 멱등(이미 만든 판본 행 재사용)은 처리 함수 몫이다.
  - 반환값은 종료 요약(`counts`·`needs_review`·`document_version_id`) 또는 None이다.
- 결과: 처리 함수가 정상 반환하면 SUCCEEDED·`job.completed{state, counts?, needs_review?, document_version_id?}`다. `needs_review`가 참이어도 SUCCEEDED이고 PARTIAL은 쓰지 않는다. 예외는 FAILED·`job.failed{state:FAILED}`이고 `failure_code`는 `TFT_WORKER_PROCESSOR_MISSING`·`TFT_WORKER_PROCESSING_FAILED` 고정 코드만이다. 처리 함수가 올린 `ApplicationError`의 코드는 쓰지 않고 `TFT_WORKER_PROCESSING_FAILED`로 바꾼다.
- payload 검사: worker가 처리 함수 값에서 위 허용 키만 형식을 확인해 저장한다. 표에 없는 step, 음수·빠진 counts 키, bool이 아닌 needs_review, UUID가 아닌 문서 판본은 저장하지 않고 FAILED다. 모르는 키(오류 코드·원문)는 버린다.

## 처리 함수 (KAN-199 완료 조건 4, KAN-188 접착)

`backend/api/tft/worker_processor.py`의 `build(settings)`. 나영님 엔진 함수를 부르기만 하고 새 판정 규칙은 없다.

| 단계 | 하는 일 | 부르는 함수 | 쓰는 표 |
| --- | --- | --- | --- |
| `parse` | 접수 입력 판본(아래)의 규격서·추가 문서(`RFP_SPEC`·`ADDENDUM`) 원문을 읽어 조항 트리를 만든다(저장은 compare 트랜잭션에서) | `storage.read_document`, `documents.router.extract`, `clauses.build_clause_tree` | 없음(메모리) |
| `extract` | 절마다 요구를 뽑는다. 모델 호출이 끝날 때마다 진행(counts 없음)을, 절이 끝날 때마다 누적 counts를 남겨 lease를 연장한다. lease(`TFT_WORKER_LEASE_SECONDS`, 기본 300초)는 호출 하나의 최악 시간 `TFT_EXTRACTION_TIMEOUT_MS`×(`GEMINI_TRANSIENT_RETRIES`+1)+재시도 대기보다 커야 한다(기본 120초×2+1초 = 241초, 아래 'lease 검사'). 절 하나는 긴 절 나눠 부르기·빠진 문장 보완으로 여러 번 부를 수 있어 절마다만 남기면 넘을 수 있다. 절은 번호로 하위를 묶는 조항 중 가장 위의 것이다(`PART 2` 아래 `2.1`·`2.2`). 너무 긴 절·실패한 절은 건너뛰고 `needs_review` | `requirements.build_inputs`, `extract_section` | 없음(메모리) |
| `search` | 검색기 연결 전이라 자사 제품 전체가 후보 범위다(`search_trace.filters=["own_products=all"]`) | — | 없음 |
| `compare` | 자사 비교. 인증 요구 비교의 `matched_attribute`는 null. 추출 요구 → 비교 입력 매핑은 `to_comparison` 한 곳(`COMPLIANCE`는 인증 요구이고 규격은 추출 `value`(없으면 `attribute`. 14번 실측에서 `attribute`는 'standard compliance' 같은 이름표였다), 그 밖은 추출 v3의 `attribute_key`(자사 사양 어휘 단일 원본 `backend/engine/rfp/vocabulary.py`, 연결하지 못했으면 `OTHER` → 비교하지 않음), 계열은 `equipment_family`로 고른다). 동의어 표는 없다(#324의 `canonical`은 v3 연결로 대체해 지웠다. 14번 문구 동의어를 코드에 두지 않는다). 등급 입력·비교의 `matched_attribute`도 같은 `attribute_key`를 쓰고, 요구 판본의 `attribute`는 추출 원문 그대로 저장한다 | `comparison.compare` | `clauses`, `clause_deleted_values`, `clause_flags`, `requirements`, `requirement_versions`(판본 1, `ai_draft`, 판단·지정품 모델·제조사 포함), `requirement_conditions`, `product_comparisons`, `comparison_evidence` |
| `supplement` | 보완 선택지. 고객 허용은 `UNSTATED`, 허용 조항은 null(그 값을 정하는 코드가 아직 없다) | `supplement.options_for` | `supplement_options` |
| `candidates` | 사양 차이(`SPEC_GAP`)·근거 부족(`INSUFFICIENT_EVIDENCE`) 요구에 협력사 등급. 문서 제목의 데이터셋 번호(`RFP-14`)로 `document_pools.json` 풀을 고르고(번호나 풀을 모르는 문서가 있으면 전체 후보), 후보는 자기 계열 요구만 채점한다(`partners/loader.PROFILE_FAMILIES`, 계열이 `OTHER`·null인 요구는 채점하지 않음). `requires_integrator`는 이번 처리의 추출 요구로 정한다. DB `partner_profiles` 행만 쓰고 없으면 건너뛰고 `needs_review`. 나머지 RFP 맥락은 기본값(제조사 목록 없음·`UNSTATED`·지역 없음) | `grading.grade_candidate` | `partner_grades` |
| `draft` | 같은 연결의 최신 공개 snapshot → `build_draft` → 불변 초안 판본·문장·근거 저장. 같은 source_job_id 재시도는 원래 판본을 재사용하고 사람 편집 head를 덮어쓰지 않음 | `drafts.generate` | `drafts`, `draft_versions`, `draft_sentences`, `draft_sentence_evidence`, `rfp_audit_events` |

- **접수 입력 판본**: 처리 함수는 `input_version`(정렬 문서 판본 ID의 SHA-256)만 받는다. 프로젝트 문서의 최신 판본 묶음부터 맞춰 보고, 아니면 문서 판본 12개까지 조합을 다 본다. 못 찾으면 실패다.
- **원문**: local/test는 `TFT_DOCUMENT_ROOT`/`storage_key` 파일을 읽고 판본 행의 sha256·size와 맞는지 본다. 운영은 `TFT_DOCUMENT_BUCKET` 버킷을 읽고, 둘 다 있으면 버킷이 먼저다(`rfp/storage.py`).
- **추출 모델**: `TFT_EXTRACTION_PROVIDER=gemini`(기본) 또는 `fake`(요구를 뽑지 않는 고정 응답, 키 없는 로컬). gemini 호출 값은 공통 AI 기본(출력 1024토큰)이 아니라 추출 전용 `TFT_EXTRACTION_MODEL`(기본 `gemini-3.6-flash`)·`TFT_EXTRACTION_MAX_OUTPUT_TOKENS`(16384)·`TFT_EXTRACTION_TIMEOUT_MS`(120000)이고, 키·공급자·재시도는 공통 `GEMINI_*`다(평가 스크립트와 같은 모델·출력 상한).
- **lease 검사**: 처리 함수를 만들 때(gemini) `TFT_WORKER_LEASE_SECONDS`가 호출 하나의 최악 시간(시간 제한 × (`GEMINI_TRANSIENT_RETRIES`+1) + 재시도 대기 1·2·…초)보다 길지 않으면 모델 클라이언트를 만들기 전에 `INVALID_WORKER_CONFIGURATION`으로 멈춘다(기본 120초×2+1초 = 241초 < 300초). 인수 뒤의 load 실패라 그 작업은 FAILED(`TFT_WORKER_PROCESSING_FAILED`)로 남는다.
- **트랜잭션**: 조항과 `compare`부터 `candidates`까지 쓰는 행은 한 트랜잭션이다. 그 사이에 멈추면(소유권 상실·취소·오류) 조항도 요구 행도 남지 않는다. **그래서 조항(경로 9 조항 트리 포함)은 그 문서 판본의 처리가 끝난 뒤에만 보인다**(처리 중에는 빈 목록이다. 경로 9 구현은 KAN-188). `parse` 직후 조항을 읽는 소비자는 없다(2026-10-30 확인: 조항 트리 경로 9는 미구현, 질문의 조항 확인·요구 상세의 조항 조인은 처리가 끝난 뒤에만 의미가 있다).
- **재실행 멱등**: 조항이 저장된 문서 판본(= 끝까지 처리된 판본, 요구 0건 문서 포함)은 다시 읽거나 추출하지 않는다(모델을 다시 부르지 않음). 그 실행의 counts는 새로 만든 요구 수라 0일 수 있다. 요구 코드는 `<문서 판본 ID 앞 8자>:<조항 번호>:<순번>`이다. 조항이 하나도 없는 문서는 처리 표시가 남지 않지만 절이 없어 모델도 부르지 않는다.
- **SSE 값**: 단계마다 `counts{requirements, proposals}`(proposals는 0). `skipped` 같은 새 키는 두지 않는다(허용 키 표 그대로).
- `rfp.export`는 예약·작업 입력 판본·actor/job을 확인하고 저장된 검토본의 실제 DOCX를 private DocumentStore에 쓴 뒤 해시·크기·완료를 기록한다. 완료 재시도는 기존 파일의 무결성을 확인한다. 누락 예약·파일 실패를 성공으로 숨기지 않는다. `rfp.annotate`는 아직 단계 하나(counts 0)만 남긴다.
- **권한(`20261104_KAN-199_tft_worker.sql`)**: `tft_worker`는 문서·문서 판본·요구 판본·협력사 프로필 SELECT, 조항 SELECT·INSERT, 위 표 INSERT만 가진다. UPDATE·DELETE는 없다.
- 검사: `tests/tft/test_worker_processor_db.py`(가짜 추출기·합성 Markdown·`tft_worker` 권한), 종단 `tests/platform/test_tft_worker_db.py::test_worker_runs_the_real_processor_end_to_end`.

## 아직 없는 것

- 검색기(R10 FTS·벡터) 연결, 고객 허용 조항 해석, RFP 맥락(제조사 목록 등)의 원천, PDF 생성·메모 해석 단계.
- 입력 판본 복원(후속): 접수 때 `rfp_commands` 결과에 `document_version_ids`를 남기고 worker가 작업 ID로 읽게 바꾼다(지금은 해시 조합 역산, 판본 12개 상한).
- 취소를 거는 경로. 지금은 `cancel_requested_at`을 읽기만 한다(KAN-194 이연 P3와 같은 범위).
