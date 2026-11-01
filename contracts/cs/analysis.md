# C/S 문의 분석 결과와 사람 정정 계약 (KAN-107)

원본: `backend/api/cs/analysis/`(조회·정정·저장 함수), `backend/engine/cs/analysis.py`(유효값·기한 계산), 마이그레이션 `infra/migrations/cs/20261030_KAN-107_ticket_analysis.sql`(README C14). 기획서 4.1 워크플로의 ①(AI 판정은 확정값이 아님)과 ⑥ 담당자 확인 게이트("수정"), p.18 추적성("AI 제안과 사람이 확정한 내용을 구분")을 구현한다.

**상태: KAN-107 완료 조건 구현 완료(2026-10-31, 로컬 종단 검사).** 업무 상태와 그래프 상태 분리, 중단·재개, 재요청 중복 방지가 들어 있다. 저장·조회·정정, worker 문의 출처, 작업 접수, 발송 뒤 재개, 부족 정보 재계산도 포함한다. 운영 적용과 확인은 KAN-159 배포 묶음이고, 화면 연결은 KAN-131이다.

## 저장

| 표 | 출처 | 내용 |
| --- | --- | --- |
| `cs.ticket_analyses` | `ai_draft` | 분류 실행 1회 = 1행. `run_seq`(문의별 순번), `job_id`(KAN-69 전에는 null), `outcome`(`VALID`·`SCHEMA_VIOLATION`·`TRANSPORT_FAILURE`·`TIMEOUT`·`HELD`), `VALID`일 때만 분류 5필드 `classification`, 입력 판본·해시, 모델·프롬프트 판본(worker 저장분은 실행기가 공급자에 넘긴 분류 판본), 오류 코드, 시도 사용량(`latency_ms`·토큰 5종·`cost_usd`·`pricing_version`, 마이그레이션 `20261102_KAN-107_attempt_usage.sql`, `evaluation.case_attempts`와 같은 이름·검사. 비용은 입력·답변 토큰과 USD 단가가 모두 있을 때만, 그때 가격표 판본 필수. 조회 응답에는 내지 않음) |
| `cs.ticket_draft_verifications` | `ai_draft` | KAN-200 문장 검증 1회 = append-only 1행. 실제 `draft_id`·`ticket_id`·`job_id`, 검증 round(1 또는 2), 호출 `outcome`·오류, 문장별 판정·이유·인용 ID, 입력/프롬프트 판본·해시·지문, 요청/응답 모델, 공급자 호출 수 및 기존 시도와 같은 토큰·지연·비용. raw 응답과 문의 원문은 저장하지 않는다. C21 적용 필요 |
| `cs.ticket_drafts` | `ai_draft` | 답변 초안 실행 1회 = 1행. `VALID`일 때만 3선택지 `draft`, 근거가 된 분석 실행 `analysis_id`, 분석 실행과 같은 시도 사용량 칸 |
| `cs.ticket_classification_corrections` | `human_input` | 사람 정정 1회 = 1행. `seq`, 바꾼 필드만 담은 `fields`, 사유(필수, 1~2000자), 정정자, 기준이 된 분석 실행 `based_on_analysis_id` |

- 세 표 모두 서비스 계정(`cs_app`)이 UPDATE·DELETE할 수 없다. AI 행을 고치지 않고, 정정은 새 행으로만 쌓인다(KAN-73 `mail_triage`와 같은 모양).
- 저장 함수 `record_analysis(conn, ticket_id, values)`·`record_draft(conn, ticket_id, values)`는 문의 행을 잠그고 다음 순번으로 넣는다. 원문·`raw_response`·시도 기록 본체는 저장하지 않는다(그래프 계약과 같은 원칙).
- worker는 문의 행을 잠그지 않고(`lock=False`, 문의 UPDATE 권한을 주지 않으려고) 넣는다. 플랫폼이 문의마다 진행 중 작업 하나만 받고, 순번이 겹치면 `UNIQUE(ticket_id, run_seq)`·`UNIQUE(ticket_id, seq)`가 막는다.
- 사람이 고쳐 승인한 발송 본문은 초안 행이 아니라 발송 기록(KAN-110 `cs.ticket_customer_sends`, `draft_id`·`draft_option`으로 어느 초안·선택지를 검토했는지 연결)에 남는다.

## 유효값

- 대상 필드: `category_code`, `secondary_category`, `urgency_flag`, `provided_info`. `return_subtype`은 정정 대상이 아니다. 반품 사유 확정은 `POST …/workflow/return-subtype`(KAN-193)만 쓴다.
- AI 값은 **최신 `VALID` 실행**의 값이다. 뒤에 실패한 실행이 있어도 가리지 않는다.
- 필드마다 가장 최근 정정값이 있으면 그 값, 없으면 AI 값이 유효값이다. **AI를 다시 돌려도 정정은 덮어쓰지 않는다.** 새 AI 결과는 `runs` 이력과 `ai_value`에만 보인다(permissions.md 3.2 RFP "손댄 요구는 재실행이 덮어쓰지 않음"과 같은 원칙).
- 정정값은 평가 정답이 아니다. KAN-95 잠금 정답과 평가에 섞지 않고, 정정 건수·비율은 운영 기록으로만 쓴다.

## 규칙과 SLA 기한 (2026-10-29 결정)

- 규칙(`rules.decide`)을 두 번 계산한다. AI 값으로 한 번(`rules.ai`), 유효값으로 한 번(`rules.effective`). 정정이 없으면 둘은 같다.
- 우선순위·담당 부서·에스컬레이션 후보·사람 확인 사유는 `rules.effective`를 따른다.
- 첫 응답·해결 기한은 각각 **AI 기준 기한과 정정 기준 기한 중 이른 쪽**이다. 둘 다 접수 시각부터 센다. 긴급도를 올리는 정정은 기한을 당기고, 낮추는 정정은 기한을 AI 기준보다 늦추지 못한다. 같으면 AI 기한이고 출처는 `ai_draft`다. 정정이 늦었으면 바로 위반 시각이 지나 있을 수 있다.
- 규칙을 못 돌리면 이유를 돌려준다: 분석 없음 `CS_ANALYSIS_MISSING`, 분류·긴급도 없음 `CS_CLASSIFICATION_MISSING`, 정책 밖 값 `CS_RULE_INPUT`. 고객 등급은 접수 때 받은 `cs.tickets.customer_tier`, 없으면 제공 기록 `cs.ticket_source_records.customer_tier`다. 둘 다 없으면(메일 등록 등) 실패하지 않고 `STANDARD`로 계산하며 `rules.tier_note`에 `등급 미확인, 표준 기준 계산`을 낸다. 없는 값을 저장하지 않으므로 고객 등급 표가 생기면 그대로 채울 수 있다.
- 반품: 주장값(`return_subtype`, AI)은 유효 주분류가 `RETURN`일 때만 쓰고, 사람 확정값(업무 상태)이 있으면 그것을 우선한다(KAN-193).

## 부족 정보

- `missing_info`는 고객에게 아직 받아야 할 정보 코드다. 초안 입력과 같은 식으로 센다: **필요 − 고객이 준 정보 − 이미 요청한 정보**(`backend/engine/cs/required_info.py`).
  - 필요 정보는 정책표 `eval/policy/cs_required_info.csv`에서 문의 제품 계열·주분류의 행이다. worker(초안)도 같은 표를 쓴다.
  - 이미 요청한 정보는 플랫폼이 접수해 업무 상태에 반영된 발송(`ticket_customer_sends.recorded_at`이 있음)의 `requested_info_codes`다. 승인만 되고 나가지 않은 메시지의 질문은 빼지 않는다.
- 두 번 센다. AI 주분류·제공 정보로 한 번(`missing_info.ai`), 유효값(정정 반영)으로 한 번(`missing_info.effective`). 화면은 `effective`를 따른다. 담당자가 `provided_info`나 `category_code`를 정정하면 `effective`가 바로 다시 계산된다. AI 쪽과 저장된 초안은 바뀌지 않는다.
- 각 쪽에 `category_code`(쓴 행), `required`, `provided`, `already_asked`, `missing`(모두 정렬)이 있다. 표에 없는 유형이면 `required`와 `missing`이 빈 목록이다.
- 셀 수 없으면 `error`를 준다: 분석·정정 모두 없음 `CS_ANALYSIS_MISSING`, 정책 밖 제품 `CS_RULE_INPUT`.

## API

| 경로 (`/api/v1/cs/tickets/{ticket_id}/analysis` 아래) | 허용 | 동작 |
| --- | --- | --- |
| `GET` | 문의 조회 범위 | `runs`·`drafts`·`corrections`(오래된 순), 필드별 `ai_value`·`corrected_value`·`effective_value`·`source`, `rules.ai`·`rules.effective`, 기한 `first_response_due`·`resolution_due`(`ai`·`corrected`·`effective`·`source`), `missing_info.ai`·`missing_info.effective`·`missing_info.error`, `version`(정정 수), `latest_valid_run_id`, `latest_valid_draft_id` |
| `POST /corrections` (`version`, `analysis_id`, `reason`, 바꿀 필드) | cs_agent | 정정 1행 저장 뒤 새 분석 보기. 업무 상태 판본은 바뀌지 않는다 |

- **검사 순서**(권한 문서 1.3): 조회 범위 404 → 역할 403 → 상태 409 `CS_INVALID_TRANSITION`(`RECEIVED`·`CLOSED`, return-subtype과 같음) → 판본 409 → 입력 422.
- **판본**: `version`이 지금 정정 수와 다르면 409 `CS_ANALYSIS_VERSION_CONFLICT`(다른 사람이 먼저 정정). `analysis_id`가 지금 최신 `VALID` 실행과 다르면 409 `CS_ANALYSIS_STALE`(못 본 새 AI 결과). 유효한 실행이 없으면 `analysis_id`는 null이다.
- **입력**: 본문에 넣은 필드만 정정한다. 넣지 않은 필드는 그대로이고, `secondary_category: null`은 보조 분류를 지운다. `category_code`·`urgency_flag`·`provided_info`는 지울 수 없다(422 `CS_CORRECTION_INVALID`). 보조 분류가 주분류와 같으면 422 `CS_CORRECTION_INVALID`. 지금 유효값과 같은 값은 저장하지 않으며, 바뀌는 값이 하나도 없으면 422 `CS_CORRECTION_EMPTY`. `provided_info`는 집합으로 다룬다(순서·중복 무시). 정보 코드는 형식(`^[a-z][a-z0-9_]{0,63}$`)만 검사한다. 검수된 코드표(`info_field_codebook`)가 저장소에 없어서, 코드표 대조는 실행기의 `allowed_info_codes`와 함께 붙인다.
- **감사**: `cs.audit_events`에 `entity_type=ticket_analysis`, `action=update`, `version`=정정 순번으로 남긴다. 바뀐 필드의 from/to, 그 결과 우선순위와 적용 기한의 from/to, 사유, 기준 분석 실행, `provenance_kind=human_input`.

## worker 문의 출처 (`backend/api/cs/worker_source.py`)

플랫폼 worker(KAN-194 `backend/agent/cs_worker.py`)가 고정 모듈로 부르는 `build(settings) -> WorkerParts`다. 역할 경계는 KAN-107 댓글 10719·10760.

- **`load_ticket`**: `cs.tickets`의 채널·언어·제목·본문·제품·접수 시각과 고객 등급(`cs.tickets.customer_tier`, 없으면 제공 기록)을 읽는다. 고객 식별자·기록된 정답·사후 기록은 읽지 않는다.
  - 유형별 필요 정보는 제품 계열(`rules.product_family`)의 `eval/policy/cs_required_info.csv` 행이고, 초안 노드가 분류 결과 유형으로 고른다. 정책 밖 제품은 빈 지도다.
  - 이미 요청한 정보: 플랫폼이 접수(ACCEPTED)해 업무 상태에 반영된 발송(`recorded_at` 있음)의 `requested_info_codes` 합집합. 승인만 되고 나가지 않은 메시지는 넣지 않는다.
  - 민감값 목록은 빈 값이다(제공 자료는 적재 때 마스킹, 2026-10-30 결정). 적재 어댑터(KAN-92)가 주면 여기서 읽는다. 고객 등급이 없는 문의(메일 등록 등)는 빈 값이고 규칙 노드가 `STANDARD`로 계산한다. 실패로 남기지 않는다.
- **`record_attempt`**: 분류·초안 시도마다 위 표에 한 행씩 `job_id`(worker의 `CS_JOB_ID`)와 함께 넣는다. 초안의 `analysis_id`는 같은 작업의 마지막 `VALID` 분류 실행이다. 형식 위반 재호출(시도 2)도 각각 한 행이다.
- **업무 상태와 그래프 상태는 따로다**: worker는 `cs.ticket_workflows`를 읽지도 쓰지도 않는다. 그래프 진행은 체크포인트(`checkpoint_cs`, thread_id=작업 ID)에만 있다.
- **`cs_worker` 권한**(마이그레이션 `20261101_KAN-107_worker_source.sql`, README C18): 위 열의 SELECT, 분석·초안 표의 SELECT·INSERT, 검색용 `cs.document_chunks` SELECT, 체크포인트 표 SELECT·INSERT·UPDATE. 업무 상태·정정·발송 본문 읽기, 결과 수정·삭제, 문의 잠금은 거부된다(`tests/cs/test_worker_source_db.py`).

## 분석 작업 접수와 재개 (`backend/api/cs/analysis/jobs.py`)

- **`POST /api/v1/cs/tickets/{id}/analysis`** (헤더 `Idempotency-Key` 8~128자, 본문 없음) → 202 `AnalysisJob`(`job_id`, `status`, `status_url`, `created`). cs_agent만(manager 403, 범위 밖 404), 종결된 문의는 409 `CS_INVALID_TRANSITION`. 접수 직후(업무 상태 없음)부터 요청할 수 있다.
  - cs가 접근·역할·상태를 검사한 뒤 플랫폼 `POST /internal/v1/platform/jobs/cs`(`ticket_id`, `input_version`, `idempotency_key`, `requested_by`)를 C/S 실행 계정 ID 토큰으로 부른다. `input_version`은 그래프 입력 칸(채널·언어·제목·본문·제품)의 SHA-256이라 문의를 고치면 바뀐다.
  - 플랫폼 답: 같은 문의 진행 중 409 `CS_JOB_IN_PROGRESS`, 같은 키·다른 판본 409 `IDEMPOTENCY_CONFLICT`는 그대로 옮기고, 같은 키 재전송은 기존 작업(`created=false`). 처리기 미연결(플랫폼 503 `CS_PROCESSOR_NOT_CONNECTED`)은 503 `CS_ANALYSIS_UNAVAILABLE`, 그 밖의 거절·무응답은 503 `CS_PLATFORM_UNAVAILABLE`.
  - 상태·진행 이벤트는 플랫폼 `GET /api/v1/platform/jobs/{id}`·`/events`(SSE)다. 결과는 끝난 뒤 `GET …/analysis`의 실행·초안으로 보인다.
- **재개(2026-10-30 결정)**: 고객 발송이 플랫폼에 접수(ACCEPTED)돼 업무 상태가 바뀌는 그 갱신(`…/sends/{id}/refresh` 또는 화면 읽기의 상태 갱신)이 끝난 뒤, 그 문의의 마지막 작업 그래프를 사람 확인 다음으로 이어 끝낸다. 사람 확인 직전에 멈춘 경우만 잇고(`NOTHING_TO_RESUME`), 모델·검색은 부르지 않는다. 재개가 체크포인트 DB 일시 오류(`psycopg.OperationalError`: 연결 끊김·재시작·연결 수 초과·교착 등)로 실패하면 0.5초·2초 뒤 두 번 더 시도한다. 재개의 체크포인트 연결은 연결 대기 3초, 문장 실행 5초(`statement_timeout`)를 상한으로 둔다. 대기가 발송 갱신 응답 안에서 일어나므로 장애 때 응답은 대기 2.5초와 시도 3번만큼 늦어질 수 있다. 문장 상한 초과는 다시 하면 상한이 시도 수만큼 늘어나므로 다시 하지 않는다. 다른 오류(권한·표 없음·그래프 오류)는 다시 해도 같으므로 기다리지 않는다. 재개 전 마지막 작업 조회가 DB 오류로 실패해도 갱신 응답은 실패시키지 않고 `RESUME_FAILED`로 기록만 한다. 그래도 실패하거나 일시 오류가 아니면 발송 결과를 바꾸지 않고 작업 ID·예외 종류만 기록한다(`RESUME_FAILED`). 이때 그래프는 사람 확인 직전에 멈춘 채 남으며, 같은 발송을 다시 갱신해도 재개하지 않는다(이미 반영된 발송이라 `recorded=false`). 남은 그래프 정리는 운영 작업으로 둔다.
- **업무 상태 ≠ 그래프 상태**: 그래프는 업무 상태를 바꾸지 않고(접수 직후 문의는 분석이 끝나도 `RECEIVED`), 업무 상태 변화(검토 시작·발송·종결)는 사람의 API 동작으로만 일어난다. 그래프 재개는 그 결과를 따라갈 뿐이다.
- **활성화**: C/S `CS_ANALYSIS_ENABLED=true`, `CS_ANALYSIS_PLATFORM_URL`(플랫폼 HTTPS origin). 플랫폼 `CS_JOBS_ENABLED`·`CS_JOBS_SERVICE_URL`·`CS_JOBS_INVOKER_EMAIL`·`CS_JOBS_PROCESSOR_CONNECTED`, worker `CS_WORKER_ENABLED`. 꺼져 있으면 접수는 404이고 재개도 하지 않는다.
- **종단 검사**(`tests/platform/test_cs_analysis_e2e_db.py`): 전용 PostgreSQL 하나에서 C/S 접수 → 플랫폼 작업(QUEUED) → worker(cs_worker 로그인, 실제 문의 출처·체크포인터, 가짜 모델) SUCCEEDED → 같은 작업 재실행은 `NOT_CLAIMED` → `GET …/analysis`에 결과, 업무 상태는 `RECEIVED` 그대로 → 검토·추가 정보 요청 발송 접수 → 그래프 끝남. 두 서비스 사이 HTTP는 같은 프로세스의 ASGI 전송, ID 토큰은 합성 검증기다.

## 완료 조건과 근거 (KAN-107)

| 완료 조건 | 구현 | 검사 |
| --- | --- | --- |
| DB 업무 상태와 LangGraph 상태 분리 | 그래프는 업무 상태를 바꾸지 않는다. 분석이 끝나도 접수 직후 문의는 `RECEIVED`이고, 업무 상태는 사람의 API 동작으로만 바뀐다(#298) | `tests/platform/test_cs_analysis_e2e_db.py` |
| 중단·재개 | 그래프가 사람 확인 직전에 멈추고, 고객 발송이 접수되면 재개해 끝낸다. 체크포인트 DB 일시 오류만 다시 시도하고, 연결 3초·문장 5초 상한을 둔다(#298·#306·#413·#421) | `tests/cs/test_analysis_jobs_db.py` |
| 재요청 중복 방지 | 같은 문의에 진행 중 작업이 있으면 409 `CS_JOB_IN_PROGRESS`, 같은 멱등 키는 기존 작업을 돌려준다(#298) | `tests/cs/test_analysis_jobs_db.py` |

함께 넣은 것(기획서 ⑥ 확인 게이트 범위): 사람 정정 API와 SLA 재계산(#239), 정정된 제공 정보·주분류로 부족 정보 재계산(#426).

넘긴 것:
- 운영 확인(실제 ID 토큰·Cloud Run Jobs 전송·공용 DB 마이그레이션 C18·C19)은 KAN-159 배포 묶음([배포 승인 점검표](../../docs/release-approval.md))이다.
- 분석 화면 연결은 KAN-131이다.


## 초안 문장 검증 (KAN-200)

기존 `GET …/analysis`의 `drafts[]`에 `verification_status`와 `verifications`를 추가한다. 실제 프론트 화면은
이번 범위 밖이며, 이 API 확장만으로 화면 표시가 완료된 것은 아니다.

- `NOT_VERIFIED`: 과거/수동 초안, `CS_DRAFT_VERIFICATION_ENABLED=false`, 검증 공급자 없음 또는 검증 호출 실패.
  기록이 없으면 `verifications=[]`다. 실패 기록이 있으면 `outcome`·`error_code`와 모든 문장의
  `UNVERIFIABLE`·고정 오류 이유가 있어 모델의 정상 판정과 구분된다.
- `VERIFIED`: 해당 초안의 마지막 검증 호출이 `VALID`이며 모든 문장이 `SUPPORTED`.
- `NEEDS_REVIEW`: 정상 검증에 `UNSUPPORTED` 또는 `UNVERIFIABLE`이 하나라도 있음. 사람의 승인 상태가 아니다.
- 검증 기록은 해당 초안별 생성 순서다. 문장 키는 선택지 `label`과 0부터 시작하는 `sentence_index`,
  항목은 `verdict`·`reason`·`evidence_chunk_ids`다. 입력 문장 전수의 누락·중복·알 수 없는 인용을 거부한다.
- `record_verification`은 같은 문의·작업의 `VALID` 초안인지 검사한다. 다른 문의/작업의 초안 ID는
  `CS_VERIFICATION_DRAFT_MISMATCH`다. `DbTicketSource.record_attempt`는 별도 `verification` kind로 저장한다.
- 재작성은 새 `cs.ticket_drafts` 행으로 추가한다. 성공 시 그 행을 재검증하고 기존 `latest_valid_draft_id`
  규칙이 새 초안을 가리킨다. 실패한 재작성은 이전 유효 초안과 그 판정을 보존한다. 검증 실패가 초안을 숨기거나
  업무 발송·최종 승인 권한을 대신하지 않는다.

## 문의 처리 화면의 명시 분석 요청 (KAN-140)

문의 접수·메일 수집은 분석을 자동 접수하지 않는다. 문의 처리 화면에서 실제 API의
`cs_agent`가 비용 안내를 확인하고 분석 또는 재분석을 요청한다. 종결 문의는 요청하지 않는다.
기존 `POST …/analysis`(본문 없음)와 플랫폼 작업 GET만 사용하며 새 API나 업무 상태는 만들지 않는다.
작업 완료 후 문의·업무 상태·분석 결과를 다시 읽는다. 분석 완료가 검토 시작·고객 승인·발송을 대신하지 않는다.

요청 시도는 계정·문의·멱등 키에 귀속한다. 쓰기 직전 현재 계정/역할과 CSRF를 확인하며
계정 변경 시 이전 요청을 전송하지 않는다. 통신 오류·408·5xx·응답 해석 실패는 결과불명으로
표시하고 새 키로 자동 재접수하지 않는다. 사용자의 명시 재확인은 같은 키를 사용한다.
입력 변경으로 같은 키가 충돌하거나 진행 중인 작업이 있으면 자동으로 새 키를 만들지 않는다.
공개 API에는 문의별 진행 작업 목록이 없으므로 기존 작업 ID를 모르는 409 충돌의 상태를
화면이 복원하거나 추측하지 않는다. 화면을 떠나면 요청/폴링을 취소하고 계정/문의별 UI를 분리한다.

작업의 QUEUED/RUNNING만 주기적으로 조회하며 실패·취소·권한 만료·조회 실패에서 자동 폴링을
멈춘다. 조회 실패의 명시 재조회는 새 분석을 접수하지 않는다. 작업 상태와 결과 품질은 별개다.
분석 없음, 결과 조회 실패, 분류 시도 실패, 유효 초안 없음, 초안 문장 검증 상태를 구분하고
기존 유효 결과가 있더라도 최신 실패를 성공으로 숨기지 않는다. 페이지 새로고침 후 미보관 작업
ID/결과불명 시도는 자동 복원되지 않으며 새 분석은 별도 비용 확인이 필요한 명시 동작이다.
