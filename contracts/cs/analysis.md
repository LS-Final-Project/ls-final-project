# C/S 문의 분석 결과와 사람 정정 계약 (KAN-107, 중간)

원본: `backend/api/cs/analysis/`(조회·정정·저장 함수), `backend/engine/cs/analysis.py`(유효값·기한 계산), 마이그레이션 `infra/migrations/cs/20261030_KAN-107_ticket_analysis.sql`(README C14). 기획서 4.1 워크플로의 ①(AI 판정은 확정값이 아님)과 ⑥ 담당자 확인 게이트("수정"), p.18 추적성("AI 제안과 사람이 확정한 내용을 구분")을 구현한다.

**상태: 저장·조회·정정 구현, worker 문의 출처 연결(아래 "worker 문의 출처").** 접수 API·발송 시 재개는 아직 없다(다음 PR). 지금 행은 검사·시연용 합성 행뿐이다.

## 저장

| 표 | 출처 | 내용 |
| --- | --- | --- |
| `cs.ticket_analyses` | `ai_draft` | 분류 실행 1회 = 1행. `run_seq`(문의별 순번), `job_id`(KAN-69 전에는 null), `outcome`(`VALID`·`SCHEMA_VIOLATION`·`TRANSPORT_FAILURE`·`TIMEOUT`·`HELD`), `VALID`일 때만 분류 5필드 `classification`, 입력 판본·해시, 모델·프롬프트 판본, 오류 코드 |
| `cs.ticket_drafts` | `ai_draft` | 답변 초안 실행 1회 = 1행. `VALID`일 때만 3선택지 `draft`, 근거가 된 분석 실행 `analysis_id` |
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
- 규칙을 못 돌리면 이유를 돌려준다: 분석 없음 `CS_ANALYSIS_MISSING`, 분류·긴급도 없음 `CS_CLASSIFICATION_MISSING`, 고객 등급 없음(수동 접수 문의) `CS_CUSTOMER_TIER_UNKNOWN`, 정책 밖 값 `CS_RULE_INPUT`. 고객 등급은 제공 기록(`cs.ticket_source_records.customer_tier`)에서만 읽는다.
- 반품: 주장값(`return_subtype`, AI)은 유효 주분류가 `RETURN`일 때만 쓰고, 사람 확정값(업무 상태)이 있으면 그것을 우선한다(KAN-193).

## API

| 경로 (`/api/v1/cs/tickets/{ticket_id}/analysis` 아래) | 허용 | 동작 |
| --- | --- | --- |
| `GET` | 문의 조회 범위 | `runs`·`drafts`·`corrections`(오래된 순), 필드별 `ai_value`·`corrected_value`·`effective_value`·`source`, `rules.ai`·`rules.effective`, 기한 `first_response_due`·`resolution_due`(`ai`·`corrected`·`effective`·`source`), `version`(정정 수), `latest_valid_run_id`, `latest_valid_draft_id` |
| `POST /corrections` (`version`, `analysis_id`, `reason`, 바꿀 필드) | cs_agent | 정정 1행 저장 뒤 새 분석 보기. 업무 상태 판본은 바뀌지 않는다 |

- **검사 순서**(권한 문서 1.3): 조회 범위 404 → 역할 403 → 상태 409 `CS_INVALID_TRANSITION`(`RECEIVED`·`CLOSED`, return-subtype과 같음) → 판본 409 → 입력 422.
- **판본**: `version`이 지금 정정 수와 다르면 409 `CS_ANALYSIS_VERSION_CONFLICT`(다른 사람이 먼저 정정). `analysis_id`가 지금 최신 `VALID` 실행과 다르면 409 `CS_ANALYSIS_STALE`(못 본 새 AI 결과). 유효한 실행이 없으면 `analysis_id`는 null이다.
- **입력**: 본문에 넣은 필드만 정정한다. 넣지 않은 필드는 그대로이고, `secondary_category: null`은 보조 분류를 지운다. `category_code`·`urgency_flag`·`provided_info`는 지울 수 없다(422 `CS_CORRECTION_INVALID`). 보조 분류가 주분류와 같으면 422 `CS_CORRECTION_INVALID`. 지금 유효값과 같은 값은 저장하지 않으며, 바뀌는 값이 하나도 없으면 422 `CS_CORRECTION_EMPTY`. `provided_info`는 집합으로 다룬다(순서·중복 무시). 정보 코드는 형식(`^[a-z][a-z0-9_]{0,63}$`)만 검사한다. 검수된 코드표(`info_field_codebook`)가 저장소에 없어서, 코드표 대조는 실행기의 `allowed_info_codes`와 함께 붙인다.
- **감사**: `cs.audit_events`에 `entity_type=ticket_analysis`, `action=update`, `version`=정정 순번으로 남긴다. 바뀐 필드의 from/to, 그 결과 우선순위와 적용 기한의 from/to, 사유, 기준 분석 실행, `provenance_kind=human_input`.

## worker 문의 출처 (`backend/api/cs/worker_source.py`)

플랫폼 worker(KAN-194 `backend/agent/cs_worker.py`)가 고정 모듈로 부르는 `build(settings) -> WorkerParts`다. 역할 경계는 KAN-107 댓글 10719·10760.

- **`load_ticket`**: `cs.tickets`의 채널·언어·제목·본문·제품·접수 시각과 `cs.ticket_source_records.customer_tier`를 읽는다. 고객 식별자·기록된 정답·사후 기록은 읽지 않는다.
  - 유형별 필요 정보는 제품 계열(`rules.product_family`)의 `eval/policy/cs_required_info.csv` 행이고, 초안 노드가 분류 결과 유형으로 고른다. 정책 밖 제품은 빈 지도다.
  - 이미 요청한 정보: 플랫폼이 접수(ACCEPTED)해 업무 상태에 반영된 발송(`recorded_at` 있음)의 `requested_info_codes` 합집합. 승인만 되고 나가지 않은 메시지는 넣지 않는다.
  - 민감값 목록은 빈 값이다(제공 자료는 적재 때 마스킹, 2026-10-30 결정). 적재 어댑터(KAN-92)가 주면 여기서 읽는다. 고객 등급이 없는 문의(직접 등록)는 빈 값이라 규칙 노드가 `CS_RULE_INPUT`으로 남긴다.
- **`record_attempt`**: 분류·초안 시도마다 위 표에 한 행씩 `job_id`(worker의 `CS_JOB_ID`)와 함께 넣는다. 초안의 `analysis_id`는 같은 작업의 마지막 `VALID` 분류 실행이다. 형식 위반 재호출(시도 2)도 각각 한 행이다.
- **업무 상태와 그래프 상태는 따로다**: worker는 `cs.ticket_workflows`를 읽지도 쓰지도 않는다. 그래프 진행은 체크포인트(`checkpoint_cs`, thread_id=작업 ID)에만 있다.
- **`cs_worker` 권한**(마이그레이션 `20261101_KAN-107_worker_source.sql`, README C18): 위 열의 SELECT, 분석·초안 표의 SELECT·INSERT, 검색용 `cs.document_chunks` SELECT, 체크포인트 표 SELECT·INSERT·UPDATE. 업무 상태·정정·발송 본문 읽기, 결과 수정·삭제, 문의 잠금은 거부된다(`tests/cs/test_worker_source_db.py`).

## 남은 것 (KAN-107 완료 조건)

- 접수 API(`POST …/analysis/jobs` → 플랫폼 `POST /internal/v1/platform/jobs/cs`, 진행 중 409·멱등)와 상태·SSE(플랫폼 jobs API).
- 재개: 담당자의 고객 발송이 플랫폼에 접수된 시점에 `cs_runner.resume(job_id)`로 그래프를 끝낸다(2026-10-30 결정).
- 로컬 플래그(`CS_JOBS_ENABLED`·`CS_JOBS_PROCESSOR_CONNECTED`·`CS_WORKER_ENABLED`) 종단 확인. 운영은 KAN-159 배포 묶음.
- 정정된 `provided_info`로 부족 정보를 다시 계산하는 연결.
