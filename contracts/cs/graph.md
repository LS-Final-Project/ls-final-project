# C/S 처리 그래프 계약 (KAN-194)

**상태: 골격과 체크포인트 저장 구현.** 기존 순수 함수 엔진을 LangGraph 노드로 잇는 층이다. 원본은 `backend/engine/cs/graph.py`이고, 체크포인트 표는 `infra/migrations/cs/20261029_KAN-194_checkpoint_schema.sql`이다. 공용 DB에는 운영자가 따로 적용한다. API·워커 연결은 아직 없다. 결정 근거는 `docs/decisions.md`(에이전트·실행 상태)와 `docs/architecture.md`(`checkpoint_cs`)다.

## 노드 순서와 분기

`classify → rules → retrieve → draft → human_review → END`

| 노드 | 호출하는 엔진 | 상태 갱신 | 실패 시 |
| --- | --- | --- | --- |
| `classify` | `classification_call.run_classification_attempt`(시도 1, 형식 위반이면 시도 2) | 마스킹한 분류 입력 `record`, `classification`, 시도 ID | `errors`에 코드를 남기고 `stage=CLASSIFY_FAILED`로 **END** |
| `rules` | `rules.decide` | `rules`(정책 판본·우선순위·부서·SLA·기한, ISO 시각) | 정책 밖 입력은 `CS_RULE_INPUT`을 남기고 계속 |
| `retrieve` | `search.hybrid.token_or_query`(마스킹 제목·본문 토큰 OR) → `scoped_then_global`(문의 제품 `rules.product_family`와 같은 `product_family` 청크 먼저, 모자라면 전체 보충, FLT-042 제외) | `evidence`(chunk_id·snippet=청크 본문 앞 800자·section_path·document_sha256) | 질의 토큰이 없으면 근거 0건, 모르는 제품이면 전체만 검색, 검색기 예외는 원문 없이 `CS_SEARCH_FAILED`를 남기고 계속. 근거 0건이어도 `draft`는 호출한다(문장은 CONDITION·UNCONFIRMED만 허용) |
| `draft` | `drafting_call.run_draft_attempt`(시도 1, 형식 위반이면 시도 2) | `draft`(3선택지), 시도 ID | 시도의 오류 코드를 남기고 **human_review로 계속**(사람이 직접 씀) |
| `human_review` | 없음 | `stage=HUMAN_REVIEW`만 | 없음 |

- 분기는 `route_after_classify` 하나다. 분류 결과가 있으면 `rules`, 없으면 END로 간다.
- 형식 위반 재호출(KAN-194 4차, 2026-10-29 사용자 결정): 분류·초안 시도 1이 `SCHEMA_VIOLATION`·`CS_INVALID_OUTPUT`이면 노드 안에서 같은 입력으로 `attempt_number=2`를 한 번 더 부른다. 금지 약속(`CS_FORBIDDEN_COMMITMENT`)·차단 근거·다른 검증 코드·입력 보류·전송 오류·타임아웃은 다시 부르지 않는다. 두 시도 모두 기록하고 `errors`에는 마지막 시도 코드만 남긴다. `build_graph(invalid_output_retries=1)`이 기본이고 0이면 끈다(configurable에는 넣지 않음). 재호출은 노드 안에서 끝나므로 사람 확인 재개는 분류·초안을 다시 부르지 않는다.
- 초안 호출 설정(KAN-194 7차): `build_graph(evidence_limit=N)`은 검색 결과 중 앞 N건만 초안 근거로 넘긴다(None이면 전부, 검색 limit·제품 우선 보충은 그대로). 실행기 `build_cs_runner`의 기본은 `DRAFT_DEFAULTS`(초안 실측 PROPOSED 설정 2026-10-29: 출력 상한 2048, 근거 5건)이며 `draft_knobs`·`evidence_limit`으로 바꾼다.
- 초안 오류 코드(KAN-194 6차): `drafting.validate_output`은 내용 규칙 위반을 `CS_INVALID_OUTPUT`으로 합치지 않고 그 코드를 그대로 올리며, `drafting_call`이 outcome `SCHEMA_VIOLATION`·`error_code=<코드>`로 남긴다. 그래서 결정적 위반(근거 ID 인용 오류 등)은 위 재호출 대상이 아니다.

  | `error_code` | 뜻 | 재호출 |
  | --- | --- | --- |
  | `CS_INVALID_OUTPUT` | JSON 파싱 실패·중복 키·타입·필드 위반(내용 규칙 위반과 섞인 경우 포함) | 1회 |
  | `CS_UNKNOWN_REFERENCE` | 입력에 없는 근거 chunk_id·정보 코드 인용 | 안 함 |
  | `CS_MISSING_INFO_REQUEST` | INFO_FIRST가 요청 코드를 다 묻지 않음 | 안 함 |
  | `CS_EVIDENCE_REQUIRED` | 근거 ID 없는 EVIDENCE 문장 | 안 함 |
  | `CS_DUPLICATE_EVIDENCE`·`CS_DUPLICATE_INFO`·`CS_DUPLICATE_OPTION` | 근거 ID·정보 요청·선택지 라벨 중복 | 안 함 |
  | `CS_FORBIDDEN_COMMITMENT`·`CS_BLOCKED_EVIDENCE` | 승인·확정 약속, 차단 코드 언급 | 안 함 |
- 엔진은 고치지 않는다. 모델 호출은 `backend/shared/ai.py`·`providers.py` 어댑터를 그대로 쓰며 LangChain 모델 래퍼는 쓰지 않는다.

## 중단점과 재개

- `interrupt_before=["human_review"]`로 컴파일한다. 첫 `invoke(initial_state(ticket_id), thread_config(ticket_id))`는 초안 뒤에 멈춘다. 이때 `get_state(config).next == ("human_review",)`이다.
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

- 게이트: local/test이고 `CS_WORKER_ENABLED`(기본 꺼짐)일 때만 돈다. 아니면 `CS_WORKER_DISABLED`로 끝난다. `CS_JOB_ID`는 표준 하이픈 UUID만 받는다(`INVALID_JOB_ID`).
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
  - 문의 출처(`TicketSource`)는 C/S 고정 모듈 `backend.api.cs.worker_source`의 `build(settings) -> WorkerParts`가 준다(KAN-107). 경로를 환경 변수로 받지 않는다. 모듈이 없으면 인수 뒤 `CS_WORKER_SOURCE_MISSING`으로 실패한다.
  - 허용 정보 코드는 모듈이 비우면 `eval/policy/cs_required_info.csv` required_fields의 합집합이다. 검색 조건은 색인 `chunking-v1`·상위 20건이고 제품 계열은 retrieve가 좁힌다.
  - 공급자는 Gemini(`AISettings`), 검색기는 `cs_search.fts_searcher_from_dsn`이다.
  - 정책 문서 매핑(KAN-194 11차): `CS_POLICY_DOCUMENTS_FILE`(색인 manifest, `cs_index_docs --manifest-out`)이 있으면 저장된 POL-* 문서의 doc_id → sha256을 `build_cs_runner(policy_documents=)`로 넘겨 정책 유형 문의에 정책 문서 1자리를 예약한다. manifest의 색인 판본이 다르면 load 구간 실패(`CS_WORKER_GRAPH_FAILED`)이고, 설정이 없으면 예약하지 않는다. `cs_graph_run --index-manifest`도 같은 매핑을 쓴다. `CS_WORKER_SOURCE_MISSING`은 입력 load 구간에서만 인정하고 처리 구간의 같은 코드는 `CS_WORKER_GRAPH_FAILED`로 바꾼다(tft worker의 `TFT_WORKER_PROCESSOR_MISSING`도 같다).
- 플랫폼 접수의 `CS_JOBS_PROCESSOR_CONNECTED`는 KAN-107 모듈 연결 뒤 운영 단계에서 켠다.

## 범위 밖

- LangSmith 추적. 전송 정책(2026-10-29): local에서 `AI_TRACING_ALLOWED=true`일 때만 전송하며 그래프 상태 전체(마스킹 입력·코드·시도 기록, 원시 응답 없음)가 간다. 다른 환경에서 추적 환경 변수가 있으면 KAN-89 Settings 시작 검사가 프로세스 시작을 막는다. Settings를 거치지 않는 진입점도 `build_graph`가 같은 판정으로 거부한다(호출자가 `Settings.ai_tracing_allowed`를 `allow_tracing`으로 넘긴다). 새 진입점은 Settings를 먼저 만들거나 `tracing_requested(os.environ)`를 확인한다.
- RFP 그래프
- API 연결(KAN-107), 사람 확인 화면, 체크포인트 보존 기간·정리
- 재시도(시도 번호는 항상 1)
