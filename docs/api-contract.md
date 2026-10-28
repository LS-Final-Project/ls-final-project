# API와 비동기 작업 계약

C/S·TFT·공통 플랫폼 API의 공통 HTTP·작업 설계 규약이다. 세부 합의는 KAN-56에서 관리한다. 플랫폼의 서버 로그인, 세션 쿠키와 업무 API 중계를 사용한다. 앱별 OpenAPI와 예시 응답으로 구체화하고, 업무별 API는 각 기능 작업에서 확장한다.

계약 원본은 서비스·기능별로 나눠 관리한다. 일상적인 수정은 [contracts/README.md](../contracts/README.md)의 원본 파일과 자기 서비스 생성 명령을 따른다. 이 문서는 공통 HTTP 및 작업 설계 설명으로 유지하고 업무 필드를 추가할 때마다 함께 수정하지 않는다. 어떤 경로가 구현됐는지는 이 문서에 나열하지 않는다. 기준은 서비스별 생성 OpenAPI와 기능 계약(`contracts/<service>/`)이다. 기능 계약이 이 문서와 다른 값을 정하면 기능 계약이 우선하며, 아래 각 절에 알려진 예외를 적는다. 브라우저 모의 응답은 로컬에서만 사용한다.

실제 생성한 OpenAPI는 [C/S](openapi/cs.json), [TFT](openapi/tft.json), [플랫폼](openapi/platform.json)이다. 공통 성공 및 오류 envelope와 요청 ID를 모든 서비스가 같이 쓴다. Microsoft 로그인·서버 세션은 [플랫폼 인증 계약](../contracts/platform/auth.md), 작업 접수·상태·SSE는 [플랫폼 작업 계약](../contracts/platform/jobs.md)에 구현 범위가 있다. 작업과 SSE는 현재 플랫폼 평가 실행에만 연결돼 있고 local/test 합성 평가에서만 켜져 있다. C/S·TFT의 작업 경로와 공용 배포의 SSE는 아직 구현하지 않았다. 실행과 검증은 [백엔드 안내](../backend/README.md)를 따른다.

## 경로와 응답

| 경로 | 소유 모듈 | 대표 작업 |
| --- | --- | --- |
| `/api/v1/cs/` | C/S | 문의 조회, 분석 작업 요청 |
| `/api/v1/tft/` | 제안 지원(RFP 자사 대응·협력사 보완) | 프로젝트 조회(구현), 접수·문서·처리 작업·요구·비교·보완·질문·제안·초안([RFP API 명세](https://docs.google.com/document/d/1lh6blP9IqACKvr3nyPp7NuU07jOmliIvIH_h87HDu9g/edit), KAN-182~190) |
| `/api/v1/platform/` | 플랫폼 | 로그인 사용자 조회, 승인된 메일 발송 요청, 평가 실행 요청 |
| `/api/v1/{owner}/jobs/{job_id}` | 작업 소유 모듈 | 작업 상태 조회와 SSE 이벤트 |
| `/internal/v1/` | 각 실행 앱 | OIDC로 인증한 Tasks 및 Scheduler 호출. 브라우저 API와 분리 |

`owner`는 `cs`, `tft`, `platform` 중 하나다. ID만 알면 다른 소유자의 작업을 볼 수 있는 전역 조회 API를 만들지 않는다. 작업 GET, SSE, 결과 파일 모두 사용자와 업무 접근 범위를 다시 확인한다. 다른 업무의 리소스와 존재를 숨길 때는 404로 응답한다.

동기 성공은 `data`, `meta.request_id`를 사용한다. 목록은 `data.items`와 `data.next_cursor`를 사용한다. `limit`은 최대 100이고 기본값은 기능 계약이 정한다(예: TFT 프로젝트 20, C/S 문의 50). 첫 요청은 cursor를 생략한다. 정렬은 기능 계약이 정한 시각 내림차순에 `id DESC`를 더해 결정적으로 고정한다(예: TFT `created_at`, C/S 접수 시각 `received_at`). 클라이언트는 커서를 불투명 문자열로 다루고 해석하거나 만들지 않는다. 현재 커서는 서명하지 않으며 필터·접근 범위에도 묶지 않는다(미구현). 잘못된 커서는 400이고 코드는 서비스 접두를 붙인다(`CS_INVALID_CURSOR`, `TFT_INVALID_CURSOR`). 끝은 `next_cursor: null`이다. 기본 목록은 스냅샷이 아니므로 조회 도중 변경될 수 있다.

분석과 문서 처리, 발송, 평가 실행 요청은 작업과 outbox가 DB에 저장된 뒤 202와 `job_id`, `status_url`을 반환한다. 이벤트 위치는 별도 필드 없이 `{status_url}/events`이며 상태 조회의 `last_event_sequence`로 재접속 위치를 정한다. 그 밖의 본문 필드는 기능 계약이 정한다(평가 실행은 `run_id`·`status=QUEUED`를 더한다. [평가 계약](../contracts/platform/evaluations.md)). `Location`은 상태 URL, `Retry-After`는 초 단위 조회 대기값이다(평가 실행 5). 202는 처리 완료나 사람의 승인 완료를 뜻하지 않는다.

## 오류와 동시 수정

오류 형식은 `error.code`, `error.message`, `error.retryable`, `error.details`, `meta.request_id`다. `details`는 문자열 키·값 객체의 배열이다. `retryable`은 429·502·503·504에서 true이고 나머지는 false다. Pydantic 검증 오류는 422 `VALIDATION_ERROR`, 처리하지 않은 예외는 500 `INTERNAL_ERROR`, 프레임워크 HTTP 오류는 상태별 공통 코드로 정규화한다(`backend/api/common/application.py`). 인증 토큰, 고객 원문과 제공자 내부 응답을 오류 메시지에 넣지 않는다.

아래 코드는 공통 예다. 기능 오류는 서비스·기능 접두 코드를 쓸 수 있으며(예: `CS_INVALID_TRANSITION`, `MAILBOX_TOKEN_UNAVAILABLE`) 목록은 기능 계약과 OpenAPI가 기준이다.

| HTTP | 코드 예 | 처리 |
| --- | --- | --- |
| 400 | `CS_INVALID_CURSOR`, `INVALID_EVENT_CURSOR` | 입력 수정 |
| 401 | `AUTH_REQUIRED`, `SESSION_EXPIRED` | 로그인 갱신. 권한 없는 개발 모드로 우회하지 않음 |
| 403 | `FORBIDDEN`, `APPROVAL_REQUIRED` | 허용된 역할과 범위를 확인 |
| 404 | `NOT_FOUND` | 없거나 접근할 수 없는 객체 |
| 409 | `IDEMPOTENCY_CONFLICT`, `INVALID_TRANSITION` | 같은 키의 다른 요청 또는 허용되지 않는 상태 전이 |
| 410 | `EVENT_CURSOR_EXPIRED` | 작업 스냅샷을 다시 조회하고 새 커서로 구독 |
| 412 | `VERSION_CONFLICT` | 최신 객체 조회 후 사용자 변경 재확인 |
| 422 | `VALIDATION_ERROR`, `EVIDENCE_REQUIRED` | 누락 필드 및 근거 수정 |
| 428 | `VERSION_REQUIRED` | `If-Match`를 포함해 재요청 |
| 429 | `RATE_LIMITED` | `Retry-After` 후 제한된 재시도 |
| 503 | `DEPENDENCY_UNAVAILABLE`(프레임워크 공통), 기능 예 `EVENT_DATA_UNAVAILABLE`, `EVALUATION_RUNNER_NOT_CONNECTED` | 작업 상태를 조회하고 재시도 여부 결정 |

리소스 GET은 `ETag: "<version>"`를 반환한다. 기존 객체 변경과 승인, 발송에는 `If-Match`가 필수다. DB의 판본 검사와 변경은 한 트랜잭션으로 처리한다. 적용 예는 [평가 실행](../contracts/platform/evaluations.md)의 취소·재개다. 예외: C/S 업무 흐름·개선요청·조건 비교는 `If-Match` 대신 본문에 판본을 보내고 불일치는 409 `CS_*_VERSION_CONFLICT`다([C/S 계약](../contracts/cs/)). 키가 같은 재전송은 기존 응답을 돌려주고 판본을 다시 증가시키지 않는다. 멱등성 레코드와 권한을 먼저 확인한 뒤, 새로운 실행에만 현재 판본을 검사한다.

## 중복 요청과 복구

상태를 만드는 POST에는 `Idempotency-Key`가 필수다. 8~128자의 새 UUID 등 충분히 충돌하기 어려운 문자열을 클라이언트가 생성한다. 1~128자를 받던 TFT 추천·결정 API는 KAN-181에서 제거돼 예외가 없다. 서버 키 범위는 `(사용자, owner, HTTP method, route, key)`이고 정규화한 본문과 조건 헤더의 SHA-256을 함께 저장한다.

같은 키와 같은 요청은 최초 응답의 코드, 본문, Location을 반환한다. 다른 본문이나 `If-Match`면 409다. 동시에 들어온 요청은 DB 유일성 제약으로 한 번만 접수한다. 키는 최소 24시간 보관하는 초기안이며 만료 키 정리는 아직 구현하지 않았다. 메일 발송 의도의 유일성은 업무 기록으로 별도 유지한다. 저장 기간이 지나도 같은 승인 판본을 다시 발송하지 않는다.

API의 DB 트랜잭션에 작업과 outbox를 함께 저장한다. 큐 등록 실패 시 outbox dispatcher가 재시도한다. dispatcher 자체도 Cloud Scheduler의 인증된 호출 등 요청이 보장되는 실행으로 돌린다. API 인스턴스의 상주 스레드에만 의존하지 않는다.

Cloud Tasks는 `(job_id, step, input_version)` 유일 키와 lease를 검사한다. 성공 결과가 이미 있으면 2xx로 종료한다. 실행 중이거나 일시 장애면 큐 재시도 정책을 적용한다. 영구 오류는 실패 상태와 이벤트를 먼저 저장한 뒤 큐 재시도를 멈춘다. lease 만료 및 큐 소진 후에도 RUNNING에 남는 작업을 재조정하는 처리가 필요하다. 같은 단계의 LLM 호출이 실제로 두 번 실행됐다면 두 사용량을 모두 기록한다.

## 작업과 SSE

작업 상태는 `QUEUED`, `RUNNING`, `SUCCEEDED`, `PARTIAL`, `FAILED`, `CANCELLED`다. `PARTIAL`은 일부 결과만 보관된 종료 상태다. 확인이 필요한지는 별도 `needs_review`로 표시한다. 업무 실행의 상태는 작업 상태와 별도 enum일 수 있다(평가 실행은 `TIMED_OUT`을 더한다). 취소·재개 API는 기능 계약에서 정의한다. 첫 적용은 평가 실행의 `POST .../runs/{run_id}/cancel|resume`이다([평가 계약](../contracts/platform/evaluations.md)).

작업에는 종류, 소유 서비스, 업무 ID 및 입력 판본, actor, 시각, 시도 횟수, 단계, 진행률, 결과 참조, 오류와 사용량을 둔다. 진행률을 계산할 수 없으면 null이고 임의의 퍼센트를 만들지 않는다. 재시도는 같은 작업에서 attempt만 늘리며, 모델과 입력을 바꿔 재실행하면 새 작업을 만든다.

이벤트는 작업별 증가하는 정수 `sequence`를 가지며 DB 트랜잭션에서 저장한다. SSE는 `id: <sequence>`, `event: <type>`, `data: <JSON>`과 빈 줄로 전달한다. JSON은 `job_id`, `sequence`, `type`, `occurred_at`, `payload`다. 타입은 `job.queued`, `job.started`, `job.progress`, `job.completed`, `job.failed`다. 취소 종료는 `job.completed`와 payload의 `state=CANCELLED`로 전달한다. PARTIAL도 완료 이벤트의 state로 구분한다. payload는 자유형이 아니며 기능별로 허용한 필드만 내보낸다. 평가 작업은 `state`, `step`, `action`(`cancel`·`resume`, 재개는 `job.progress`), `resumed`, `synthetic`, `needs_review`만 허용한다([플랫폼 작업 계약](../contracts/platform/jobs.md)).

재접속은 `Last-Event-ID` 또는 `after` 쿼리로 마지막 수신 sequence를 전달한다. 둘 다 있으면 `Last-Event-ID`를 우선한다. 첫 연결은 0부터다. 서버는 커서 이후 저장 이벤트를 재생한 후 새 이벤트를 전달하고, 클라이언트는 sequence로 중복을 제거한다. 미래 커서나 다른 작업의 커서는 400, 보관 기간이 지난 커서는 410이다. 숫자 커서는 해당 URL의 작업 안에서만 해석한다.

종료 이벤트를 받으면 상태를 다시 조회하고 스트림을 닫는다. 종료된 작업에 재접속하면 남은 종료 이벤트를 보낸 뒤 닫는다. 이미 종료 sequence까지 받은 클라이언트에는 204로 재연결 중단을 알린다. 서버는 스트림 헤더를 보내기 전에 인증과 커서를 확인한다. 시작한 뒤의 전송 장애는 연결을 닫고 클라이언트의 상태 조회 및 재접속으로 복구한다.

15초마다 주석 heartbeat(`: heartbeat`)를 보낸다. 프록시 버퍼링을 끄도록 `X-Accel-Buffering: no`를 보내고 `Cache-Control: no-cache, no-store`를 적용한다. SSE 보관 기간은 최소 7일 제안이며 작업 결과 보관과 별개다. 보관 기간 정리는 아직 구현하지 않았다. 인증 헤더가 필요한 배포라면 `fetch` 스트림을 사용한다. 브라우저 기본 EventSource에 임의 헤더를 넣을 수 있다고 가정하지 않는다.

```text
id: 3
event: job.progress
data: {"job_id":"22222222-2222-4222-8222-222222222222","sequence":3,"type":"job.progress","occurred_at":"2026-10-22T01:00:10Z","payload":{"step":"smoke"}}

```

## 메일 발송의 특별 규칙

메일 발송은 플랫폼 내부 함수로 구현했다(KAN-74, [메일 계약](../contracts/platform/mail.md#승인-메일-발송-kan-74)). 업무 서비스가 부르는 HTTP 입구와 운영 활성화는 아직 없다. 아래 규칙을 지킨다.

사람이 검토한 `draft_id`, 본문 판본, 수신자, 첨부 버전과 payload hash를 승인 기록에 묶는다. 발송 시 해당 판본이 그대로인지 확인한다. 승인 후 수정하면 승인을 다시 받아야 한다. 업무 서비스는 지정 범위의 권한과 승인 정보를 서버에서 검증해 공통 메일 API에 전달한다. RFP 대응은 외부 메일 발송이 범위 밖이다. 브라우저가 보낸 `approved=true`는 승인 근거가 아니다.

발송 의도 하나에 외부 전송을 한 번 시도한다. Graph가 응답하기 전에 timeout이 나면 발송 결과를 `UNKNOWN`으로 두고 자동 재전송하지 않는다. Sent Items 등으로 대조하고 판단 근거를 남긴 뒤 재전송 여부를 사람이 결정한다. 명확한 전송 거부 등 안전한 재시도는 제공자 오류별 정책으로 구분한다.

Graph의 202는 접수이며 배달 완료가 아니다. 상태는 `QUEUED`, `SUBMITTING`, `ACCEPTED`, `UNKNOWN`, `FAILED`로 나눈다. 실제 배달 확인 근거가 없는 시연에서는 배달 완료라고 표시하지 않는다. 이 상태는 작업 상태 및 문의 종결과도 별도다. [Microsoft Graph sendMail 응답](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0)

## 계약 검증과 후속 확장

서비스별 Pydantic 모델과 라우터에서 OpenAPI를 생성하고 대표 경로·DTO·오류·헤더와 예시를 검증한다. 파일 업로드 서명 및 완료, 메일 연결 및 동기화, 승인 및 역할별 상태 전이, RFP 대응 API는 각 기능 작업에서 이 계약을 확장한다.

기존 앱별 OpenAPI 생성기를 사용해 응답 형식·필수 헤더·enum 변경을 비교한다. 기능 통합 테스트는 두 번 클릭한 생성 요청, 상충 수정, 다른 부서 작업 열람, SSE 재연결, Tasks 중복 실행, 메일 발송 결과 불명확을 검증한다. 평가 데이터 test 597건을 이 계약 검증에 사용하지 않는다.
