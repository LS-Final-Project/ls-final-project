# C/S 처리 그래프 계약 (KAN-194)

**상태: 골격과 체크포인트 저장 구현.** 기존 순수 함수 엔진을 LangGraph 노드로 잇는 층이다. 원본은 `backend/engine/cs/graph.py`이고, 체크포인트 표는 `infra/migrations/cs/20261029_KAN-194_checkpoint_schema.sql`이다. 공용 DB에는 운영자가 따로 적용한다. API·워커 연결은 아직 없다. 결정 근거는 `docs/decisions.md`(에이전트·실행 상태)와 `docs/architecture.md`(`checkpoint_cs`)다.

## 노드 순서와 분기

`classify → rules → retrieve → draft → verify → [rewrite → reverify] → human_review → END`

| 노드 | 호출하는 엔진 | 상태 갱신 | 실패 시 |
| --- | --- | --- | --- |
| `classify` | `classification_call.run_classification_attempt`(시도 1, 형식 위반이면 시도 2) | 마스킹한 분류 입력 `record`, `classification`, 시도 ID | `errors`에 코드를 남기고 `stage=CLASSIFY_FAILED`로 **END** |
| `rules` | `rules.decide` | `rules`(정책 판본·우선순위·부서·SLA·기한, ISO 시각) | 정책 밖 입력은 `CS_RULE_INPUT`을 남기고 계속 |
| `retrieve` | `search.hybrid.token_or_query`(마스킹 제목·본문 토큰 OR) → `scoped_then_global`(문의 제품 `rules.product_family`와 같은 `product_family` 청크 먼저, 모자라면 전체 보충, FLT-042 제외) | `evidence`(chunk_id·snippet=청크 본문 앞 800자·section_path·document_sha256) | 질의 토큰이 없으면 근거 0건, 모르는 제품이면 전체만 검색, 검색기 예외는 원문 없이 `CS_SEARCH_FAILED`를 남기고 계속. 근거 0건이어도 `draft`는 호출한다(문장은 CONDITION·UNCONFIRMED만 허용) |
| `draft` | `drafting_call.run_draft_attempt`(시도 1, 형식 위반이면 시도 2) | `draft`(3선택지), 시도 ID | 시도의 오류 코드를 남기고 **human_review로 계속**(사람이 직접 씀) |
| `verify` | `verification_call.run_verification_attempt` 1회 | 문장별 판정·시도 ID | 호출 실패는 미검증으로 사람 확인, `UNSUPPORTED`가 있으면 rewrite |
| `rewrite` | 피드백으로 `run_draft_attempt` 1회(형식 재시도 없음) | 새 초안·시도 ID | 기존 초안·판정을 보존해 사람 확인 |
| `reverify` | 새 초안의 검증 1회 | 최종 문장 판정·시도 ID | 결과와 무관하게 사람 확인 |
| `human_review` | 없음 | `stage=HUMAN_REVIEW`만 | 없음 |

- 분류 분기는 `route_after_classify`다. 분류 결과가 있으면 `rules`, 없으면 END로 간다.
- 형식 위반 재호출(KAN-194 4차, 2026-10-29 사용자 결정): 분류·초안 시도 1이 `SCHEMA_VIOLATION`·`CS_INVALID_OUTPUT`이면 노드 안에서 같은 입력으로 `attempt_number=2`를 한 번 더 부른다. 금지 약속(`CS_FORBIDDEN_COMMITMENT`)·차단 근거·다른 검증 코드·입력 보류·전송 오류·타임아웃은 다시 부르지 않는다. 두 시도 모두 기록하고 `errors`에는 마지막 시도 코드만 남긴다. `build_graph(invalid_output_retries=1)`이 기본이고 0이면 끈다(configurable에는 넣지 않음). 재호출은 노드 안에서 끝나므로 사람 확인 재개는 분류·초안을 다시 부르지 않는다.
- 초안 호출 설정(KAN-194 7차): `build_graph(evidence_limit=N)`은 검색 결과 중 앞 N건만 초안 근거로 넘긴다(None이면 전부, 검색 limit·제품 우선 보충은 그대로). 실행기 `build_cs_runner`의 기본은 `DRAFT_DEFAULTS`(측정 뒤 PROPOSED 설정 2026-10-31: 출력 상한 6000, 근거 5건)이며 `draft_knobs`·`evidence_limit`으로 바꾼다.
- 초안 오류 코드(KAN-194 6차): `drafting.validate_output`은 내용 규칙 위반을 `CS_INVALID_OUTPUT`으로 합치지 않고 그 코드를 그대로 올리며, `drafting_call`이 outcome `SCHEMA_VIOLATION`·`error_code=<코드>`로 남긴다. 그래서 결정적 위반(근거 ID 인용 오류 등)은 위 재호출 대상이 아니다.

  | `error_code` | 뜻 | 재호출 |
  | --- | --- | --- |
  | `CS_INVALID_OUTPUT` | JSON 파싱 실패·중복 키·타입·필드 위반(내용 규칙 위반과 섞인 경우 포함) | 1회 |
  | `CS_UNKNOWN_REFERENCE` | 입력에 없는 근거 chunk_id·정보 코드 인용 | 안 함 |
  | `CS_MISSING_INFO_REQUEST` | INFO_FIRST가 요청 코드를 다 묻지 않음 | 안 함 |
  | `CS_EVIDENCE_REQUIRED` | 근거 ID 없는 EVIDENCE 문장 | 안 함 |
  | `CS_DUPLICATE_EVIDENCE`·`CS_DUPLICATE_INFO`·`CS_DUPLICATE_OPTION` | 근거 ID·정보 요청·선택지 라벨 중복 | 안 함 |
  | `CS_FORBIDDEN_COMMITMENT`·`CS_BLOCKED_EVIDENCE` | 승인·확정 약속, 차단 코드 언급 | 안 함 |
- 기존 엔진의 안전·출력 검사는 유지한다. 모델 호출은 `backend/shared/ai.py`·`providers.py` 어댑터를 그대로 쓰며 LangChain 모델 래퍼는 쓰지 않는다.

## 중단점과 재개

- `interrupt_before=["human_review"]`로 컴파일한다. 첫 `invoke(initial_state(ticket_id), thread_config(ticket_id))`는 검증(필요하면 재작성·재검증) 뒤 사람 확인 직전에 멈춘다. 이때 `get_state(config).next == ("human_review",)`이다.
- 사람이 확인한 뒤 같은 thread로 `invoke(None, config)`를 부르면 `human_review`를 지나 END가 된다.
- 분류 실패는 중단 없이 바로 끝난다.
- thread_id는 문의 ID다. 체크포인트가 DB에 있으므로 새 프로세스·새 그래프 인스턴스도 같은 thread를 이어간다.

## 상태 키 (`CsState`)

| 키 | 내용 |
| --- | --- |
| `ticket_id` | 문의 ID |
| `record` | `build_input`으로 마스킹한 분류 입력(channel·body_language·subject·body)만. 분류 실패면 null |
| `classification` | `ClassificationOutput` 5필드 |
| `attempt_ids` | `record_attempt`가 돌려준 시도 ID(분류, 초안 순, 재호출 시도 포함) |
| `attempts` | 시도 요약 `{kind, attempt_id, attempt_number, outcome, error_code, is_final, finish_reasons}`(finish_reasons는 KAN-194 7차, 출력 잘림 확인용). 원문·raw 없음. 이 키가 생기기 전 체크포인트에는 없을 수 있다 |
| `rules` | `RuleDecision`을 JSON으로 바꾼 값 |
| `evidence` | `EvidenceSnippet` 목록 |
| `draft` | `DraftOutput` |
| `errors` | `{node, code}` 목록. 원문·예외 이름은 없음 |
| `stage` | `RECEIVED`·`CLASSIFIED`·`CLASSIFY_FAILED`·`RULED`·`RETRIEVED`·`DRAFTED`·`HUMAN_REVIEW` |

**상태에 넣지 않는 것:** 마스킹 전 원문, 민감값 원값, 정답·사후 기록, raw_response, 시도 기록 본체, 키·자격 증명, 고객 등급·접수 시각 같은 사례 입력. 시도 기록은 `record_attempt`가 상태 밖에 저장하고 ID만 돌려준다.

## 주입

모든 주입은 `build_graph` 인자로 한다. `config["configurable"]`에는 `thread_id`만 둔다.

**왜 configurable을 쓰지 않는가:** LangGraph는 `configurable`·`metadata`의 문자열·수 값을 체크포인트 metadata(JSONB)에 그대로 복사한다(`langgraph.checkpoint.base.get_checkpoint_metadata`). 그래서 원문이나 식별자를 거기 넣으면 DB에 남는다.

| 인자 | 내용 |
| --- | --- |
| `classification_provider`, `draft_provider` | 공통 `Generation`을 돌려주는 공급자 |
| `fts_searcher`, `vector_searcher`(선택) | `Searcher` |
| `search_filters` | 두 검색기에 똑같이 넘기는 `SearchFilters` |
| `allowed_info_codes` | 검수된 정보 코드 집합 |
| `load_ticket(ticket_id) -> TicketInputs` | 원문 레코드·민감값·제품 코드·고객 등급·접수 시각·필요/이미 요청한 정보 코드. 선택 `required_info_by_category`(제품 계열의 유형별 필요 코드)가 있으면 `draft`는 분류 결과 `category_code`의 행을 쓴다(없는 유형은 빈 집합, 정답 유형 불필요). 노드 안에서만 부른다 |
| `record_attempt(kind, ticket_id, attempt) -> id` | 분류·초안 시도를 상태 밖에 저장 |
| `checkpointer` | 검사는 `InMemorySaver`, 운영은 `checkpoint_cs`의 `PostgresSaver` |

## 체크포인트 저장

- 라이브러리는 `langgraph-checkpoint-postgres` 3.2.0이다. 표는 `base.py`의 `MIGRATIONS[0..9]`를 최종 형태로 옮겼다.
  - `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`를 만든다. `blob`은 null 허용이고 `task_path`가 처음부터 있다.
  - 인덱스는 트랜잭션 안이라 CONCURRENTLY 없이 만든다.
  - `checkpoint_migrations`에는 v 0~9를 미리 넣는다.
- 앱은 `setup()`을 부르지 않는다. 연결은 `PostgresSaver.from_conn_string`과 같은 `autocommit=True`·`prepare_threshold=0`·`row_factory=dict_row`에 `options='-c search_path=checkpoint_cs'`를 더한 것이다.
- `cs_app` 권한은 스키마 USAGE와 세 표의 SELECT·INSERT·UPDATE다.
  - 저장은 `INSERT ... ON CONFLICT DO UPDATE/DO NOTHING`이다.
  - DELETE·TRUNCATE는 주지 않는다. 라이브러리의 `delete_thread()`·`prune()`만 DELETE를 쓰며 앱은 부르지 않는다.
  - `checkpoint_migrations`에는 권한이 없다.

## worker 실행 — KAN-194 9차 3/3

플랫폼 전송([작업 계약](../platform/jobs.md) 2/3)이 C/S 이미지의 `cs-worker` 대상(`python -m backend.agent.cs_worker`)을 `CS_JOB_ID`=작업 ID로 실행한다.

- 게이트: `CS_WORKER_ENABLED`(기본 꺼짐)가 켜졌을 때만 돈다. 아니면 `CS_WORKER_DISABLED`로 끝난다. `CS_JOB_ID`는 표준 하이픈 UUID만 받는다(`INVALID_JOB_ID`).
- 분류 설정: `CS_WORKER_CLASSIFICATION_MAX_OUTPUT_TOKENS`(기본 4096, thinking 포함)와 `CS_WORKER_CLASSIFICATION_MODEL`(없으면 공통 `GEMINI_MODEL`)은 분류에만 쓴다. 공통 `GEMINI_MAX_OUTPUT_TOKENS`는 분류·초안이 쓰지 않는다. 값을 바꾸려면 채택 기록이 필요하다(`eval/promotion.py`).
- DB: `DATABASE_URL` 하나(`cs_worker` 멤버 로그인)로 `runtime`·cs 표·`checkpoint_cs`를 쓴다. 앱·마이그레이터 역할 멤버이거나 특권 속성이 있으면 `WORKER_ROLE_REQUIRED`다. cs 표·`checkpoint_cs` 권한은 KAN-107 cs 마이그레이션이 준다.
- 소유권(`runtime.job_control`, RFP worker와 공통 `backend/agent/job_runtime.py`)
  - 인수: QUEUED이거나 lease가 끊긴 RUNNING이고 취소 요청이 없을 때만이다. generation+1, holder, lease(`CS_WORKER_LEASE_SECONDS`, 기본 120초), RUNNING, `job.started`를 한 트랜잭션에 남긴다. 아니면 `NOT_CLAIMED`다.
  - 노드가 끝날 때마다 holder·generation·lease를 재검사하고 lease를 연장하며 `job.progress{step, stage}`를 남긴다. 소유권을 잃었으면 아무것도 쓰지 않고 `LEASE_LOST`로 끝난다.
  - 취소 요청(`cancel_requested_at`)이 보이면 다음 노드 전에 멈추고 CANCELLED·`job.completed{state:CANCELLED}`로 끝낸다. 취소를 거는 경로는 아직 없다.
- 그래프: thread_id는 작업 ID다.
  - 체크포인트가 없으면 처음부터, 중간 체크포인트가 있으면 그 뒤부터 잇는다. 이미 사람 확인 직전에 멈춘 스레드는 다시 호출하지 않는다.
  - 사람 확인 직전에 멈추면 SUCCEEDED·`job.completed{state, stage, retried, recovered, classification_outcome, draft_outcome}`다. 그래프 안 오류(`CLASSIFY_FAILED` 등)는 상태에 남고 작업은 SUCCEEDED다.
  - worker 예외는 FAILED·`job.failed{state:FAILED}`이고 `job_control.failure_code`에 고정 코드만 둔다(`CS_WORKER_SOURCE_MISSING`, `CS_WORKER_GRAPH_FAILED` 등). 이벤트에는 오류 코드·원문을 넣지 않는다. 문의 출처 등 worker 밖 코드가 올린 `ApplicationError`의 코드는 쓰지 않고 `CS_WORKER_GRAPH_FAILED`로 바꾼다.
- 입력(`backend/agent/ticket_source_registry.py`)
  - 문의 출처(`TicketSource`)는 C/S 고정 모듈 `backend.api.cs.worker_source`의 `build(settings) -> WorkerParts`가 준다(KAN-107, 구현과 읽고 쓰는 범위는 [분석 계약](analysis.md) "worker 문의 출처"). 경로를 환경 변수로 받지 않는다. 모듈이 없으면 인수 뒤 `CS_WORKER_SOURCE_MISSING`으로 실패한다.
  - 허용 정보 코드는 모듈이 비우면 `eval/policy/cs_required_info.csv` required_fields의 합집합이다. 검색 조건은 색인 `INDEX_VERSION`(현재 `chunking-v2`, [청킹 판본](../common/chunking.md))·상위 20건이고 제품 계열은 retrieve가 좁힌다.
  - 공급자는 Gemini(`AISettings`), 검색기는 `cs_search.fts_searcher_from_dsn`이다.
  - 정책 문서 매핑(KAN-194 11차): `CS_POLICY_DOCUMENTS_FILE`(색인 manifest, `cs_index_docs --manifest-out`)이 있으면 저장된 POL-* 문서의 doc_id → sha256을 `build_cs_runner(policy_documents=)`로 넘겨 정책 유형 문의에 정책 문서 1자리를 예약한다. manifest의 색인 판본이 다르면 load 구간 실패(`CS_WORKER_GRAPH_FAILED`)이고, 설정이 없으면 예약하지 않는다. `cs_graph_run --index-manifest`도 같은 매핑을 쓴다. `CS_WORKER_SOURCE_MISSING`은 입력 load 구간에서만 인정하고 처리 구간의 같은 코드는 `CS_WORKER_GRAPH_FAILED`로 바꾼다(tft worker의 `TFT_WORKER_PROCESSOR_MISSING`도 같다).
- 플랫폼 접수의 `CS_JOBS_PROCESSOR_CONNECTED`는 KAN-107 모듈 연결 뒤 운영 단계에서 켠다.

## 범위 밖

- LangSmith 추적. 전송 정책(2026-10-29): local에서 `AI_TRACING_ALLOWED=true`일 때만 전송하며 그래프 상태 전체(마스킹 입력·코드·시도 기록, 원시 응답 없음)가 간다. 다른 환경에서 추적 환경 변수가 있으면 KAN-89 Settings 시작 검사가 프로세스 시작을 막는다. Settings를 거치지 않는 진입점도 `build_graph`가 같은 판정으로 거부한다(호출자가 `Settings.ai_tracing_allowed`를 `allow_tracing`으로 넘긴다). 새 진입점은 Settings를 먼저 만들거나 `tracing_requested(os.environ)`를 확인한다.
- RFP 그래프
- API 연결(KAN-107), 사람 확인 화면, 체크포인트 보존 기간·정리
- 재시도(시도 번호는 항상 1)


## 문장 검증·유한 재작성 (KAN-200)

채택 측정은 `scripts/cs_draft_run.py --verify`가 같은 순서로 돈다(분류는 입력 파일을 쓴다). 호출·비용·판정 분포와 재작성을 반영한 초안 지표를 기록하고 합격 판정은 하지 않는다. 합격선은 사람이 정한다. 검증 출력 상한은 후보의 `verification_max_output_tokens`(없으면 `max_output_tokens`)다.

- `verify`는 선택지의 `EVIDENCE` 문장과 그 문장이 인용한 근거만 `verification_call.run_verification_attempt`로
  한 번 보낸다. 레지스트리 `verification/v1`, 출력은 `SUPPORTED`·`UNSUPPORTED`·`UNVERIFIABLE`과 이유·근거 ID다.
  `EVIDENCE` 문장 전수 1:1 대응과 입력 인용 범위를 검사한다. 문의 원문·정답을 새 입력으로 추가하지 않는다.
- 검증 대상은 `EVIDENCE` 문장뿐이다(입력 판본 `cs-verification-input-v2`). `CONDITION`·`UNCONFIRMED` 문장은 근거를 달지 않아 입력에서 뺀다. 참조·차단어 검사는 모든 문장에 그대로 한다. `EVIDENCE` 문장이 없으면 호출 없이 `NOT_VERIFIED`, 사유 `CS_NO_EVIDENCE_SENTENCES`다. 재작성본에 `EVIDENCE` 문장이 없으면 다시 부르지 않고 첫 판정과 상태를 그대로 두며 사유만 남긴다(측정 집계는 `reverify_skipped`).
- 사례 상태: `EVIDENCE` 문장이 모두 `SUPPORTED`면 `VERIFIED`, 하나라도 아니면 `NEEDS_REVIEW`다. `UNVERIFIABLE`은 재작성하지 않고 표시만 한다. 초안·검증 기본 출력 상한은 6000이다(채택 전). 두 호출의 시간 제한은 60초이고 전용 클라이언트(`draft_ai_settings`)를 쓴다. 공용 `gemini_timeout_ms`(30초)와 분류는 그대로다. 일시 오류 재시도 1회면 호출 하나의 최악은 121초다. lease는 노드마다 연장하고 노드 안에 형식 재호출이 있어 노드 최악은 242초다. `CS_WORKER_LEASE_SECONDS` 기본은 300이고, `main`이 시작할 때 `check_lease(calls=2)`로 더 짧은 설정을 `INVALID_WORKER_CONFIGURATION`으로 막는다.
- 정상 검증에서 `UNSUPPORTED`가 있으면 `rewrite`로 간다. 기존 초안과 실패 문장 이유를
  정제 초안 입력에 붙여 **공급자 호출 한 번만** 재작성한다. 재작성에는 기존 형식 재시도를 적용하지 않는다.
  최초 draft의 기존 형식 재시도(0/1회)는 유지한다. 기존 draft/v1 지시문·입력은 재작성 이외에는 바뀌지 않는다.
- 재작성 성공이면 `reverify`로 새 초안 전체를 한 번 더 검사한다. 결과에 관계없이 사람 확인으로 간다.
  재작성 실패면 이전 초안·판정을 남기고 바로 사람 확인으로 간다. 검증 노드 자체의 형식 재시도는 없으며
  그래프에 되돌아가는 간선이 없다. 공급자 내부의 전송 재시도는 기존 정책이며 시도 `call_attempts`로 구분한다.
- 검증 timeout/전송/출력검사 실패는 재작성하지 않고 `NOT_VERIFIED` 및 문장별 미검증 이유로 남긴다.
  초안이 없으면 검증 호출이 없고 사람 확인으로 간다. 비활성·공급자 없는 경로도 미검증으로 표시한다.
- `CsState` 추가 키: `draft_attempt_id`, `verification`(status·reason·sentences), `draft_rewritten`.
  기존 체크포인트의 누락 키는 기본값으로 읽고 과거 draft ID는 시도 요약에서 찾는다. raw·모델 시도 본체는
  상태에 넣지 않는다. `attempt_ids`·`attempts`에는 검증도 포함하지만 `retried/recovered`는 분류·초안 형식
  재시도만 세며 round=2 검증을 재시도로 세지 않는다.
- `build_graph` 주입에 `verification_provider`·`verification_enabled`를 추가한다. 공급자가 없는 경로는
  미검증이고 제품 `build_cs_runner`는 같은 명시 모델의 `gemini_text_provider(VerificationOutput)`를 연결한다.
  `Settings.cs_draft_verification_enabled`(환경 변수 `CS_DRAFT_VERIFICATION_ENABLED`) 기본은 true다.
  실제 모델의 근거 판정 품질은 이번 fake 검사의 검증 대상이 아니다.
- worker는 verify/rewrite/reverify마다 기존 `step=draft`, `stage=DRAFTED`로 진행을 기록하고 lease·취소를
  확인한다. 플랫폼 SSE 스키마를 확장하지 않는다. 사람 확인 재개는 모델을 다시 부르지 않는다.
- 저장·담당자 조회 API는 [분석 계약](analysis.md#초안-문장-검증-kan-200), C21 마이그레이션 적용 후 사용한다.
- `scripts/cs_graph_run.py`는 검증 사용량을 기존 감사 합계에 포함한다. fake는 외부 호출하지 않는다.
  batch dry-run은 정상 경로 3회, 형식 재시도 및 재작성·재검증 포함 논리 호출 최대 7회/문의
  (분류2+초안2+재작성1+검증2)로 안내한다. 비활성은 기존 최대4회다. 검증·피드백 입력 크기 상한은 아직
  증명하지 않아 활성 시 총비용 상한은 null(`CS_VERIFICATION_INPUT_BOUND_UNKNOWN`)이다.

`cs_graph_run`의 `calls.verification`은 재작성·재검증 없는 정상 경로 기준이며, 최대 호출 수는 `max_calls_with_retries`로 확인한다.
