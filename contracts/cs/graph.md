# C/S 처리 그래프 계약 (KAN-194)

**상태: 골격과 체크포인트 저장 구현.** 기존 순수 함수 엔진을 LangGraph 노드로 잇는 층이다. 원본은 `backend/engine/cs/graph.py`이고, 체크포인트 표는 `infra/migrations/cs/20261029_KAN-194_checkpoint_schema.sql`이다. 공용 DB에는 운영자가 따로 적용한다. API·워커 연결은 아직 없다. 결정 근거는 `docs/decisions.md`(에이전트·실행 상태)와 `docs/architecture.md`(`checkpoint_cs`)다.

## 노드 순서와 분기

`classify → rules → retrieve → draft → human_review → END`

| 노드 | 호출하는 엔진 | 상태 갱신 | 실패 시 |
| --- | --- | --- | --- |
| `classify` | `classification_call.run_classification_attempt`(시도 1, 형식 위반이면 시도 2) | 마스킹한 분류 입력 `record`, `classification`, 시도 ID | `errors`에 코드를 남기고 `stage=CLASSIFY_FAILED`로 **END** |
| `rules` | `rules.decide` | `rules`(정책 판본·우선순위·부서·SLA·기한, ISO 시각) | 정책 밖 입력은 `CS_RULE_INPUT`을 남기고 계속 |
| `retrieve` | `search.hybrid.run_hybrid`(FTS, 선택적 벡터) | `evidence`(chunk_id·snippet·section_path·document_sha256) | 검색기 예외는 원문 없이 `CS_SEARCH_FAILED`를 남기고 계속 |
| `draft` | `drafting_call.run_draft_attempt`(시도 1, 형식 위반이면 시도 2) | `draft`(3선택지), 시도 ID | 시도의 오류 코드를 남기고 **human_review로 계속**(사람이 직접 씀) |
| `human_review` | 없음 | `stage=HUMAN_REVIEW`만 | 없음 |

- 분기는 `route_after_classify` 하나다. 분류 결과가 있으면 `rules`, 없으면 END로 간다.
- 형식 위반 재호출(KAN-194 4차, 2026-10-29 사용자 결정): 분류·초안 시도 1이 `SCHEMA_VIOLATION`·`CS_INVALID_OUTPUT`이면 노드 안에서 같은 입력으로 `attempt_number=2`를 한 번 더 부른다. 금지 약속(`CS_FORBIDDEN_COMMITMENT`)·차단 근거·다른 검증 코드·입력 보류·전송 오류·타임아웃은 다시 부르지 않는다. 두 시도 모두 기록하고 `errors`에는 마지막 시도 코드만 남긴다. `build_graph(invalid_output_retries=1)`이 기본이고 0이면 끈다(configurable에는 넣지 않음). 재호출은 노드 안에서 끝나므로 사람 확인 재개는 분류·초안을 다시 부르지 않는다.
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
| `attempts` | 시도 요약 `{kind, attempt_id, attempt_number, outcome, error_code, is_final}`. 원문·raw 없음. 이 키가 생기기 전 체크포인트에는 없을 수 있다 |
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
| `load_ticket(ticket_id) -> TicketInputs` | 원문 레코드·민감값·제품 코드·고객 등급·접수 시각·필요/이미 요청한 정보 코드. 노드 안에서만 부른다 |
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

## 범위 밖

- LangSmith 추적. 전송 정책(2026-10-29): local에서 `AI_TRACING_ALLOWED=true`일 때만 전송하며 그래프 상태 전체(마스킹 입력·코드·시도 기록, 원시 응답 없음)가 간다. 다른 환경에서 추적 환경 변수가 있으면 KAN-89 Settings 시작 검사가 프로세스 시작을 막는다. Settings를 거치지 않는 진입점도 `build_graph`가 같은 판정으로 거부한다(호출자가 `Settings.ai_tracing_allowed`를 `allow_tracing`으로 넘긴다). 새 진입점은 Settings를 먼저 만들거나 `tracing_requested(os.environ)`를 확인한다.
- RFP 그래프
- API·워커 연결, 사람 확인 화면, 체크포인트 보존 기간·정리
- 재시도(시도 번호는 항상 1)
