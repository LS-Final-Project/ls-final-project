# API와 비동기 작업 계약

KAN-56 검토안. C/S, TFT, 공통 플랫폼 API에 적용할 규약 초안이다. 플랫폼의 서버 로그인, 세션 쿠키와 업무 API 중계를 사용한다. 앱별 OpenAPI와 예시 응답으로 구체화하고, 업무별 전체 API는 KAN-63 및 각 기능 작업에서 확장한다.

2026-10-21 KAN-63의 부분 구현으로 세 앱의 프로세스 및 서비스별 health, 성공 및 오류 envelope와 요청 ID 처리를 추가했다. 실제 생성한 OpenAPI는 [C/S](openapi/cs.json), [TFT](openapi/tft.json), [플랫폼](openapi/platform.json)이다. 현재 health 두 경로만 포함하며 아래 업무 API, 인증, 작업 및 SSE의 구현 완료를 뜻하지 않는다. 실행과 검증은 [백엔드 안내](../backend/README.md)를 따른다.

## 경로와 응답

| 경로 | 소유 모듈 | 대표 작업 |
| --- | --- | --- |
| `/api/v1/cs/` | C/S | 문의 조회, 분석 작업 요청 |
| `/api/v1/tft/` | TFT | 프로젝트 등록 및 조회, RFP 처리 작업 요청 |
| `/api/v1/platform/` | 플랫폼 | 로그인 사용자 조회, 승인된 메일 발송 요청, 평가 실행 요청 |
| `/api/v1/{owner}/jobs/{job_id}` | 작업 소유 모듈 | 작업 상태 조회와 SSE 이벤트 |
| `/internal/v1/` | 각 실행 앱 | OIDC로 인증한 Tasks 및 Scheduler 호출. 브라우저 API와 분리 |

`owner`는 `cs`, `tft`, `platform` 중 하나다. ID만 알면 다른 소유자의 작업을 볼 수 있는 전역 조회 API를 만들지 않는다. 작업 GET, SSE, 결과 파일 모두 사용자와 업무 접근 범위를 다시 확인한다. 다른 업무의 리소스와 존재를 숨길 때는 404로 응답한다.

동기 성공은 `data`, `meta.request_id`를 사용한다. 목록은 `data.items`와 `data.next_cursor`를 사용한다. `limit` 기본값 20, 최대 100이며 첫 요청은 cursor를 생략한다. 정렬은 `created_at DESC, id DESC`로 고정한다. 커서는 서명된 불투명 값이며 필터, 정렬, 접근 범위에 묶는다. 잘못된 커서는 400 `INVALID_CURSOR`, 끝은 `next_cursor: null`이다. 기본 목록은 스냅샷이 아니므로 조회 도중 변경될 수 있다.

분석과 문서 처리, 발송, 평가 실행 요청은 작업과 outbox가 DB에 저장된 뒤 202와 `job_id`, `status_url`, `events_url`을 반환한다. `Location`은 상태 URL, `Retry-After`는 초 단위 조회 대기값이다. 202는 처리 완료나 사람의 승인 완료를 뜻하지 않는다.

## 오류와 동시 수정

오류 형식은 `error.code`, `error.message`, `error.retryable`, `error.details`, `meta.request_id`다. Pydantic 검증 오류, 프록시 오류와 예외도 가능한 경우 이 형식으로 정규화한다. 인증 토큰, 고객 원문과 제공자 내부 응답을 오류 메시지에 넣지 않는다.

| HTTP | 코드 예 | 처리 |
| --- | --- | --- |
| 400 | `INVALID_CURSOR`, `INVALID_EVENT_CURSOR` | 입력 수정 |
| 401 | `AUTH_REQUIRED`, `SESSION_EXPIRED` | 로그인 갱신. 권한 없는 개발 모드로 우회하지 않음 |
| 403 | `FORBIDDEN`, `APPROVAL_REQUIRED` | 허용된 역할과 범위를 확인 |
| 404 | `NOT_FOUND` | 없거나 접근할 수 없는 객체 |
| 409 | `IDEMPOTENCY_CONFLICT`, `INVALID_TRANSITION` | 같은 키의 다른 요청 또는 허용되지 않는 상태 전이 |
| 410 | `EVENT_CURSOR_EXPIRED` | 작업 스냅샷을 다시 조회하고 새 커서로 구독 |
| 412 | `VERSION_CONFLICT` | 최신 객체 조회 후 사용자 변경 재확인 |
| 422 | `VALIDATION_ERROR`, `EVIDENCE_REQUIRED` | 누락 필드 및 근거 수정 |
| 428 | `VERSION_REQUIRED` | `If-Match`를 포함해 재요청 |
| 429 | `RATE_LIMITED` | `Retry-After` 후 제한된 재시도 |
| 503 | `DEPENDENCY_UNAVAILABLE` | 작업 상태를 조회하고 재시도 여부 결정 |

리소스 GET은 `ETag: "<version>"`를 반환한다. 기존 객체 변경과 승인, 발송에는 `If-Match`가 필수다. DB의 판본 검사와 변경은 한 트랜잭션으로 처리한다. 키가 같은 재전송은 기존 응답을 돌려주고 판본을 다시 증가시키지 않는다. 멱등성 레코드와 권한을 먼저 확인한 뒤, 새로운 실행에만 현재 판본을 검사한다.

## 중복 요청과 복구

상태를 만드는 POST에는 `Idempotency-Key`가 필수다. 8~128자의 새 UUID 등 충분히 충돌하기 어려운 문자열을 클라이언트가 생성한다. 서버 키 범위는 `(사용자, owner, HTTP method, route, key)`이고 정규화한 본문과 조건 헤더의 SHA-256을 함께 저장한다.

같은 키와 같은 요청은 최초 응답의 코드, 본문, Location을 반환한다. 다른 본문이나 `If-Match`면 409다. 동시에 들어온 요청은 DB 유일성 제약으로 한 번만 접수한다. 키는 최소 24시간 보관하는 초기안을 사용하고, 메일 발송 의도의 유일성은 업무 기록으로 별도 유지한다. 저장 기간이 지나도 같은 승인 판본을 다시 발송하지 않는다.

API의 DB 트랜잭션에 작업과 outbox를 함께 저장한다. 큐 등록 실패 시 outbox dispatcher가 재시도한다. dispatcher 자체도 Cloud Scheduler의 인증된 호출 등 요청이 보장되는 실행으로 돌린다. API 인스턴스의 상주 스레드에만 의존하지 않는다.

Cloud Tasks는 `(job_id, step, input_version)` 유일 키와 lease를 검사한다. 성공 결과가 이미 있으면 2xx로 종료한다. 실행 중이거나 일시 장애면 큐 재시도 정책을 적용한다. 영구 오류는 실패 상태와 이벤트를 먼저 저장한 뒤 큐 재시도를 멈춘다. lease 만료 및 큐 소진 후에도 RUNNING에 남는 작업을 재조정하는 처리가 필요하다. 같은 단계의 LLM 호출이 실제로 두 번 실행됐다면 두 사용량을 모두 기록한다.

## 작업과 SSE

작업 상태는 `QUEUED`, `RUNNING`, `SUCCEEDED`, `PARTIAL`, `FAILED`, `CANCELLED`다. `PARTIAL`은 일부 결과만 보관된 종료 상태다. 확인이 필요한지는 별도 `needs_review`로 표시한다. 취소 기능은 기능 작업에서 정의하며 이 대표 계약에는 취소 API를 넣지 않는다.

작업에는 종류, 소유 서비스, 업무 ID 및 입력 판본, actor, 시각, 시도 횟수, 단계, 진행률, 결과 참조, 오류와 사용량을 둔다. 진행률을 계산할 수 없으면 null이고 임의의 퍼센트를 만들지 않는다. 재시도는 같은 작업에서 attempt만 늘리며, 모델과 입력을 바꿔 재실행하면 새 작업을 만든다.

이벤트는 작업별 증가하는 정수 `sequence`를 가지며 DB 트랜잭션에서 저장한다. SSE는 `id: <sequence>`, `event: <type>`, `data: <JSON>`과 빈 줄로 전달한다. JSON은 `job_id`, `sequence`, `type`, `occurred_at`, `payload`다. 타입은 `job.queued`, `job.started`, `job.progress`, `job.completed`, `job.failed`다. 취소 종료는 `job.completed`와 payload의 `state=CANCELLED`로 전달한다. PARTIAL도 완료 이벤트의 state로 구분한다.

재접속은 `Last-Event-ID` 또는 `after` 쿼리로 마지막 수신 sequence를 전달한다. 둘 다 있으면 `Last-Event-ID`를 우선한다. 첫 연결은 0부터다. 서버는 커서 이후 저장 이벤트를 재생한 후 새 이벤트를 전달하고, 클라이언트는 sequence로 중복을 제거한다. 미래 커서나 다른 작업의 커서는 400, 보관 기간이 지난 커서는 410이다. 숫자 커서는 해당 URL의 작업 안에서만 해석한다.

종료 이벤트를 받으면 상태를 다시 조회하고 스트림을 닫는다. 종료된 작업에 재접속하면 남은 종료 이벤트를 보낸 뒤 닫는다. 이미 종료 sequence까지 받은 클라이언트에는 204로 재연결 중단을 알린다. 서버는 스트림 헤더를 보내기 전에 인증과 커서를 확인한다. 시작한 뒤의 전송 장애는 연결을 닫고 클라이언트의 상태 조회 및 재접속으로 복구한다.

15초마다 주석 heartbeat를 보낸다는 초기안을 사용한다. 프록시 버퍼링을 끄고 `Cache-Control: no-cache`를 적용한다. SSE 보관 기간은 최소 7일 제안이며 작업 결과 보관과 별개다. 인증 헤더가 필요한 배포라면 `fetch` 스트림을 사용한다. 브라우저 기본 EventSource에 임의 헤더를 넣을 수 있다고 가정하지 않는다.

```text
id: 3
event: job.progress
data: {"job_id":"22222222-2222-4222-8222-222222222222","sequence":3,"type":"job.progress","occurred_at":"2026-10-22T01:00:10Z","payload":{"step":"embedding","progress":0.5}}

```

## 메일 발송의 특별 규칙

사람이 검토한 `draft_id`, 본문 판본, 수신자, 첨부 버전과 payload hash를 승인 기록에 묶는다. 발송 시 해당 판본이 그대로인지 확인한다. 승인 후 수정하면 승인을 다시 받아야 한다. TFT는 지정 범위의 권한과 승인 정보를 서버에서 검증해 공통 메일 API에 전달한다. 브라우저가 보낸 `approved=true`는 승인 근거가 아니다.

발송 의도 하나에 외부 전송을 한 번 시도한다. Graph가 응답하기 전에 timeout이 나면 발송 결과를 `UNKNOWN`으로 두고 자동 재전송하지 않는다. Sent Items 등으로 대조하고 판단 근거를 남긴 뒤 재전송 여부를 사람이 결정한다. 명확한 전송 거부 등 안전한 재시도는 제공자 오류별 정책으로 구분한다.

Graph의 202는 접수이며 배달 완료가 아니다. 상태는 `QUEUED`, `SUBMITTING`, `ACCEPTED`, `UNKNOWN`, `FAILED`로 나눈다. 실제 배달 확인 근거가 없는 시연에서는 배달 완료라고 표시하지 않는다. 이 상태는 작업 상태 및 문의 종결과도 별도다. [Microsoft Graph sendMail 응답](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0)

## 계약 검증과 후속 확장

후속 OpenAPI 문서는 대표 경로, DTO, 오류, 요청 헤더와 예시를 검증하는 기준으로 작성한다. 파일 업로드 서명 및 완료, 메일 연결 및 동기화, 승인 및 역할별 상태 전이, TFT 후보와 구성안 API는 각 기능 작업에서 이 계약을 확장한다.

KAN-63에서 앱별 OpenAPI를 생성하고 이 계약과 응답 형식, 필수 헤더, enum 변경을 비교한다. 기능 통합 테스트는 두 번 클릭한 생성 요청, 상충 수정, 다른 부서 작업 열람, SSE 재연결, Tasks 중복 실행, 메일 발송 결과 불명확을 검증한다. 평가 데이터 test 597건을 이 계약 검증에 사용하지 않는다.
