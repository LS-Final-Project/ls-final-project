# RFP 자사 대응 작업 접수 (KAN-182, A01 플랫폼 범위)

원본 합의: [RFP API 명세 v1.1](https://docs.google.com/document/d/1lh6blP9IqACKvr3nyPp7NuU07jOmliIvIH_h87HDu9g) §4.3·결정 4. 처리·내보내기·메모 해석 작업은 플랫폼 `runtime.jobs`를 `owner=tft`로 쓴다. 저장 구조는 [RFP 데이터 계약](rfp-data.md), 공통 작업 규칙은 [플랫폼 작업 계약](../platform/jobs.md)을 따른다.

| 원본 | 파일 |
| --- | --- |
| 작업 종류 등록 | `infra/migrations/platform/20261029_KAN-182_tft_jobs.sql` |
| 내부 접수 | `backend/api/platform/jobs/tft.py` |
| 검사 | `tests/platform/test_tft_jobs_db.py` |

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
- **본문**: `actor_id`, `kind`, `project_id`, `input_version`(동결 입력 SHA-256), `idempotency_key`(8~128자), `request_hash`(정규화 원 요청 SHA-256). 다른 키는 422다. 원문·프롬프트·모델 응답은 받지 않는다.
- **응답**: 새 작업 201, 같은 요청 재전송 200. `data`는 `job_id`, `kind`, `project_id`, `status`, `status_url`(`/api/v1/platform/jobs/{job_id}`), `created`. 같은 키에 hash·입력 판본·프로젝트가 다르면 409 `IDEMPOTENCY_CONFLICT`. 등록되지 않은 `actor_id`는 422 `JOB_ACTOR_UNKNOWN`.
- 작업과 첫 이벤트(`job.queued`)를 한 트랜잭션에 저장한다. **전송 의도(`dispatch_outbox`)는 만들지 않는다.** 평가 전송기는 outbox 행을 모두 평가 실행으로 보내므로, tft 처리 worker를 연결할 때 전송기의 종류 구분과 함께 연다. 그 전까지 tft 경로 11·28·30은 503 `TFT_PROCESSOR_NOT_CONNECTED`를 낸다(A07).

## SSE payload 허용 키 (명세 §4.3)

`state`(공통 작업 상태), `step`(위 단계 값), `document_version_id`, `counts`(`requirements`·`proposals` 정수), `needs_review`(bool). 원문·프롬프트·모델 응답은 이벤트에 넣지 않는다.

## 아직 없는 것

- **tft 작업의 상태 조회·SSE.** 지금 `GET /api/v1/platform/jobs/{job_id}`와 `/events`는 평가 작업만 읽고 tft 작업은 404다. 플랫폼은 tft 프로젝트 범위(담당 sales·검토 부서·책임자·관리 manager)를 모르므로 접근 검사 방식을 정해야 한다. 후보: (가) 접수자 본인만, (나) 플랫폼이 tft 내부 경로로 범위를 물음. worker 연결(11/1 대안 판단)과 함께 정한다.
- tft 처리 worker와 전송, 위 SSE 허용 키의 공개 DTO 검사.
