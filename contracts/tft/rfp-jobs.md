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
- **`TFT_JOBS_PROCESSOR_CONNECTED`를 켜는 조건**: 전송기의 종류 구분, tft 작업 상태·SSE 접근 검사, 처리 worker(아래 'worker' 절)는 KAN-199로 갖췄다. 남은 조건은 (1) 처리 함수 모듈(`backend.api.tft.worker_processor`, KAN-186·KAN-184 구현)과 그 tft 표 권한 마이그레이션, (2) P29 적용과 `tft_worker` 로그인, (3) `TFT_WORKER_JOBS_ENABLED`·`TFT_WORKER_ENABLED`다. 그 전에 켜면 작업이 FAILED(`TFT_WORKER_PROCESSOR_MISSING`)나 QUEUED로 남는다.
- **처리기 연결 전에는 저장하지 않는다.** `TFT_JOBS_PROCESSOR_CONNECTED=false`(기본)이면 호출자 검증 뒤 503 `TFT_PROCESSOR_NOT_CONNECTED`를 돌려주고 행을 만들지 않는다. worker 없이 저장하면 QUEUED가 남아 그 프로젝트의 다음 접수가 계속 409가 되기 때문이다. tft 처리 worker를 연결할 때 함께 켠다.
- 호출자 검증은 본문 검증보다 먼저 한다(비활성 404·불일치 401이 422보다 앞선다).
- **본문**: `actor_id`, `kind`, `project_id`, `input_version`(동결 입력 SHA-256), `idempotency_key`(8~128자), `request_hash`(정규화 원 요청 SHA-256). 다른 키는 422다. 원문·프롬프트·모델 응답은 받지 않는다.
- **응답**: 새 작업 201, 같은 요청 재전송 200. `data`는 `job_id`, `kind`, `project_id`, `status`, `status_url`(`/api/v1/platform/jobs/{job_id}`), `created`. 같은 키에 hash·입력 판본·프로젝트가 다르면 409 `IDEMPOTENCY_CONFLICT`. 등록되지 않은 `actor_id`는 422 `JOB_ACTOR_UNKNOWN`.
- 작업, 첫 이벤트(`job.queued`), 전송 의도(`dispatch_outbox`), 작업 제어 행(`runtime.job_control`)을 한 트랜잭션에 저장한다(KAN-199). 처리기 연결 전에는 위 503 게이트 때문에 아무 행도 만들지 않고, tft 경로 11·28·30이 503을 그대로 전달한다(A07).

## 전송 (KAN-199 1/2)

- 전송 장부는 종류별로 열린다. rfp.* 행은 local/test에서 `TFT_JOBS_ENABLED`·`TFT_JOBS_PROCESSOR_CONNECTED`가 켜졌을 때만 확보하고, 그렇지 않으면 PENDING으로 남는다. 평가·C/S 전송은 rfp 행을 집지 않는다.
- 전송 직전 재검사는 C/S와 같은 `runtime.job_control` 규칙이다: 게이트, 작업 QUEUED·RUNNING, 취소 요청 없음, 살아 있는 worker lease 없음. 아니면 그 시도는 BLOCKED다.
- Jobs 호출: `TFT_WORKER_JOBS_JOB`(기본 `tft-worker`)·`TFT_WORKER_JOBS_CONTAINER`, 환경 `TFT_JOB_ID`=작업 ID. `TFT_WORKER_JOBS_ENABLED`(기본 꺼짐)가 없으면 rfp 행을 확보하지 않는다. 프로젝트·리전·시간 제한은 평가 Jobs 설정(`EVALUATION_JOBS_*`)을 같이 쓴다.

## 상태 조회·SSE 접근 (KAN-199 1/2)

- `GET /api/v1/platform/jobs/{job_id}`와 `/events`는 tft 작업을 **요청 사용자(`actor_id`) 본인에게만** 보여 준다. 그 밖의 사용자는 역할(sales·manager 포함)과 관계없이 404다. 커서 오류보다 권한 검사가 먼저다.
- 프로젝트 범위(명세 §2.2: 담당 sales·검토 부서·책임자·관리 manager)는 tft 서비스 규칙이다. 플랫폼은 이를 알지 못하며, tft 서비스가 내부 접수를 부르기 전에 이미 검사했다는 전제로 요청자 본인 여부만 본다.
- 응답 `data`는 `TftJobStatus{job_id, project_id, owner:'tft', kind, status, version, last_event_sequence}`다. `version`은 `runtime.job_control.version`이다.

## SSE payload 허용 키 (명세 §4.3)

`state`(공통 작업 상태), `step`(위 단계 값), `document_version_id`(표준 하이픈 소문자 UUID 문자열), `counts`(`requirements`·`proposals` 둘 다 0 이상 정수, 다른 키 없음), `needs_review`(bool). 원문·프롬프트·모델 응답은 이벤트에 넣지 않는다. 플랫폼 `EventPayload`가 이 키만 SSE로 내보내고 모르는 키(오류 코드 등)는 버린다. 표에 없는 `step` 값이나 형식이 틀린 값이 저장되면 SSE는 503 `EVENT_DATA_UNAVAILABLE`이다.

## worker (KAN-199 2/2)

플랫폼 전송이 tft 이미지의 `tft-worker` 대상(`python -m backend.agent.tft_worker`)을 `TFT_JOB_ID`=작업 ID로 실행한다. 이 이미지에는 `backend/api/platform`·`backend/api/cs`가 없다.

- 게이트: local/test이고 `TFT_WORKER_ENABLED`(기본 꺼짐)일 때만 돈다. 아니면 `TFT_WORKER_DISABLED`다. `TFT_JOB_ID`는 표준 하이픈 UUID만 받는다(`INVALID_JOB_ID`).
- DB: `DATABASE_URL` 하나(`tft_worker` 멤버 로그인). 앱·마이그레이터 역할 멤버이거나 특권 속성이 있으면 `WORKER_ROLE_REQUIRED`다. P29는 runtime 열 권한만 준다(jobs 조회 열·status UPDATE, job_events 열 INSERT, job_control SELECT와 취소 열을 뺀 UPDATE). tft 표 권한은 처리 함수 쪽 tft 마이그레이션이 준다.
- 소유권(`runtime.job_control`, C/S worker와 공통 `job_runtime`)
  - 인수: owner가 tft이고 QUEUED이거나 lease가 끊긴 RUNNING이며 취소 요청이 없을 때만이다. generation+1·holder·lease(`TFT_WORKER_LEASE_SECONDS`, 기본 300초)·RUNNING·`job.started`. 아니면 `NOT_CLAIMED`다.
  - 처리 함수가 단계마다 `emit`을 부르면 소유권을 재검사하고 lease를 연장하며 `job.progress`를 남긴다. 소유권을 잃었으면 아무것도 쓰지 않고 `LEASE_LOST`, 취소 요청이면 CANCELLED·`job.completed{state:CANCELLED}`다.
- 처리 함수 등록(`backend/agent/processor_registry.py`)
  - 고정 모듈 `backend.api.tft.worker_processor`의 `build(settings) -> Processor`. 경로를 환경 변수로 받지 않는다. 모듈이 없으면 인수 뒤 FAILED·`TFT_WORKER_PROCESSOR_MISSING`이다.
  - `processor(job, emit)`: `job`은 `TftJob{job_id, kind, project_id, input_version, actor_id}`(원문 없음)다. 프로젝트 접근 검사는 tft 서비스가 접수 때 마쳤다.
  - `emit`이 올리는 취소·소유권 상실 예외를 처리 함수가 삼키지 않는다. 재실행 멱등(이미 만든 판본 행 재사용)은 처리 함수 몫이다.
  - 반환값은 종료 요약(`counts`·`needs_review`·`document_version_id`) 또는 None이다.
- 결과: 처리 함수가 정상 반환하면 SUCCEEDED·`job.completed{state, counts?, needs_review?, document_version_id?}`다. `needs_review`가 참이어도 SUCCEEDED이고 PARTIAL은 쓰지 않는다. 예외는 FAILED·`job.failed{state:FAILED}`이고 `failure_code`는 `TFT_WORKER_PROCESSOR_MISSING`·`TFT_WORKER_PROCESSING_FAILED` 같은 고정 코드만이다.
- payload 검사: worker가 처리 함수 값에서 위 허용 키만 형식을 확인해 저장한다. 표에 없는 step, 음수·빠진 counts 키, bool이 아닌 needs_review, UUID가 아닌 문서 판본은 저장하지 않고 FAILED다. 모르는 키(오류 코드·원문)는 버린다.

## 아직 없는 것

- 처리 함수 구현(`backend.api.tft.worker_processor`)과 그 tft 표 권한: KAN-186 판정·KAN-184 후보 풀.
- 취소를 거는 경로. 지금은 `cancel_requested_at`을 읽기만 한다(KAN-194 이연 P3와 같은 범위).
