# 플랫폼 작업 접수 저장 계약 — KAN-69 초기 구현

현재 구현은 평가 실행 접수에 필요한 저장 함수·마이그레이션과 아래 제한 상태 조회·SSE API다. KAN-90의 local/test 전용 합성 접수 API에서 사용하며 [평가 접수 계약](evaluations.md)을 따른다. 로컬 합성 worker의 상태 전이·lease는 평가 계약에 추가했다. local/test 합성 평가의 저장 이벤트 SSE를 제공한다. 실제 평가·Cloud Run Jobs 전송·공용 배포의 SSE 활성화는 아직 연결하지 않았다. KAN-90의 운영 실행 잠금은 그대로 유지한다. 공통 규약은 [작업 계약](../../docs/api-contract.md)을 따른다.

## 저장과 소유권

`infra/migrations/platform/20261025_KAN-69_job_admission.sql`을 관리자 연결로 한 번 적용한다. 기존 초기 역할과 identity 마이그레이션이 선행한다. 새 로컬 DB의 초기화 루프에도 포함된다. 기존 환경의 적용은 명시적으로 수행하며 공용 DB에는 이번 작업에서 적용하지 않았다.

```bash
python3 scripts/workspace.py migrate platform 20261025_KAN-69_job_admission.sql
```

`runtime` 소유자는 `platform_migrator`다. 초기 지원 조합은 `owner=platform`, `kind=evaluation.run`, `POST /api/v1/platform/evaluations/runs` 하나다. `platform_app`은 기본 세 테이블에 SELECT·INSERT만 가능하다. 후속 KAN-90 smoke worker migration은 jobs.status 열의 UPDATE만 추가하며 DELETE·TRUNCATE는 없다. C/S·TFT 계정에는 schema 접근과 테이블 권한을 부여하지 않는다. 다른 업무 종류와 상태 변경 권한은 실제 소비자 구현 시 별도 마이그레이션으로 확장한다.

| 테이블 | 저장 단위와 제약 |
| --- | --- |
| `runtime.jobs` | 작업 UUID, 서버 생성 run UUID, 동결 입력 SHA-256, 내부 사용자 UUID, 멱등 범위와 요청 hash. 사용자·owner·method·route·key가 유일하며 업무 run당 작업 하나 |
| `runtime.job_events` | 작업별 양의 sequence. 최초 접수는 sequence=1, type=job.queued, 빈 payload |
| `runtime.dispatch_outbox` | 작업당 전송 의도 한 건. dispatcher 상태·전송 확인·lease 구현 전의 최소 저장 구조 |

임의 명령·URL·서비스 계정·원문·정답·자격증명은 받거나 저장하지 않는다. 멱등 키에는 고객 정보나 토큰을 넣지 않는다. 현재 삭제/만료 기능은 없으며 24시간 이후 자동으로 새 실행을 허용하지 않는다.

## 호출자 트랜잭션

`backend/api/platform/jobs/admission.py`의 `admit_evaluation_job()`은 현재 열린 READ COMMITTED 트랜잭션과 `dict_row` 연결을 받는다. 함수 내부에서 commit·새 연결·외부 네트워크 호출을 하지 않는다. 연결이 transaction 밖이면 SQL 실행 전에 거부한다.

1. 호출자는 현재 세션·지정 평가자·실험 접근 범위·시험 차단을 검사한다. 재전송도 동일 검사를 먼저 수행한다.
2. 요청 본문과 조건 헤더를 정규화한 hash, 동결 입력 판본, 서버 생성 run UUID로 접수 함수를 호출한다. hash는 신뢰한 서버 계산값이어야 한다. 브라우저가 제공한 hash를 그대로 사용하지 않는다.
3. 함수는 유일 키로 작업을 INSERT한다. 같은 키 경합은 PostgreSQL이 이전 transaction 종료까지 직렬화한다. 같은 hash·판본이면 기존 job/run과 `created=false`를 반환하며 다른 내용이면409 충돌을 발생시킨다.
4. 신규면 최초 이벤트·outbox까지 저장하고 `created=true`를 반환한다. 호출자는 이 경우에만 반환된 run ID로 run/cases/control 등 업무 기록을 같은 연결에 저장한다.
5. 어느 단계든 실패하면 호출자가 전체 rollback한다. commit 성공 전에는202를 반환하지 않는다. 재전송의 임시 생성 run ID는 버리고 반환된 기존 run ID를 사용한다.

이 순서는 작업 유일 키를 먼저 확보하므로 run을 중복 생성하지 않게 한다. 작업·업무 레코드의 최종 원자성은 KAN-90 통합에서 검증해야 하며, 이 함수 자체가 평가 run이나 허용 실험 검증을 생성·수행하지 않는다. 입력 hash·사용자 권한이 맞다는 것을 저장 함수만으로 보장하지 않는다.

DB 대기 시간 제한과 오류→HTTP 변환은 호출자 경계의 후속 구현이다. DB 장애·commit 불명확 시 새 키를 발급하지 말고 현재 권한 확인 후 동일 키로 재조회/재요청한다. 현재 함수가 보존하는 것은 최초 job/run 식별자이며 최초 HTTP 응답 envelope·Location의 완전 재생은 KAN-90에서 추가해야 한다.

## 검증 범위

`tests/platform/test_job_admission.py`는 전용 PostgreSQL과 platform_app 계정으로 동시 동일 요청, 다른 hash 충돌, 후속 업무 SQL 실패, outbox INSERT 실패, transaction 없는 호출 거부, 다른 서비스 권한을 검사한다. 기존 auth-database CI에 이 파일을 추가해 DB 테스트가 단순 skip으로 끝나지 않게 한다.

공통 작업 전송·인증·재시도·SSE 재접속과 평가 run 소유권은 이 접수 저장과 별도다. 이 저장 구조의 성공을 모델 호출의 중복 방지, 최종 시험 보호 전체 또는 KAN-69 전체 완료로 보고하지 않는다.

## 평가 작업 상태 조회 — KAN-69 첫 조회 단위

`GET /api/v1/platform/jobs/{job_id}`는 local/test에서 명시적으로 접수를 허용한 평가 작업만 조회한다. 현재 Microsoft 서버 세션·활성 등록 계정·유효 업무 역할·지정 평가자 조건을 요청마다 기존 인증 경계로 검사한다. manager 역할만으로 평가자가 되거나 타인의 작업을 조회하지 않는다. 작업 actor, 실행 요청자, 실험 생성자가 모두 현재 사용자여야 한다. 없는 작업·다른 소유자의 작업·최종 시험은404로 숨긴다. 세션 만료/회수·계정 비활성·역할 전체 회수는401, 평가자 지정 회수는403이다. 구성 승인 hash 철회 후에도 본인의 제한 조회는 유지한다. production 또는 접수 비활성 상태는503이며 실행 잠금 해제 수단이 아니다.

성공 envelope의 `data`는 `job_id`, `run_id`, `owner=platform`, `kind=evaluation.run`, `status`, `run_status`, `version`, `last_event_sequence`, `needs_review`만 포함한다. 원시 출력·정답·대상·event payload·사용자 식별 정보·입력 hash·멱등 키·내부 전송/worker 정보는 반환하지 않는다. 계산되지 않은 진행률과 사용량을 만들어내지 않는다. `ETag: "<version>"`, `Cache-Control: no-store`를 반환하며 DB 장애는503이다.

상태의 권위는 evaluation run이다. `TIMED_OUT`은 작업 `status=FAILED`로 투영하고 `run_status=TIMED_OUT`으로 구분한다. runtime의 전송 기록이나 job 상태를 실행 권한으로 사용하지 않는다. run 상태·제어 판본·해당 작업의 마지막 저장 sequence를 단일 SELECT의 동일 PostgreSQL 스냅샷으로 읽는다. `version`은 실행 제어 판본이고 `last_event_sequence`는 이벤트 위치이므로 서로 대체하지 않는다. 현재 이벤트 삭제/보관 만료 기능은 없으며 이벤트가 없으면 sequence는0이다.

기존 접수 응답의 `status_url`과 최초 응답 재생은 변경하지 않는다. 소비자는 응답의 job_id로 위 경로를 구성할 수 있다. 새 migration이나 추가 DB 권한은 필요하지 않다.

### 평가 SSE 재접속 — local/test 구현

`GET /api/v1/platform/jobs/{job_id}/events`는 위 상태 조회와 같은 세션·지정 평가자·소유권·최종 시험/환경 잠금을 적용한다. 스트림 헤더 전에 권한과 커서를 검사한다. 응답은 `text/event-stream`, `Cache-Control: no-cache, no-store`, `X-Accel-Buffering: no`다. 기존 접수 응답 및 최초 응답 재생은 변경하지 않으며 클라이언트는 job_id로 events 경로를 구성한다.

- `Last-Event-ID` 헤더가 있으면 `after` 쿼리보다 우선한다. 둘 다 없으면0. 0~9223372036854775807의 ASCII 숫자만 받으며 빈 값·음수·잘못된 형식·미래 커서는400 INVALID_EVENT_CURSOR다. 숫자 위치는 URL 작업 안에서만 해석한다. 다른 작업에서 유래한 같은 숫자를 식별하는 토큰은 아니다. 다른 작업 접근은 소유권 검사에서404로 막는다.
- 마지막 수신 위치 이후 이벤트를 sequence 오름차순으로 최대100개씩 읽는다. 상태·최종 sequence·보관 시작 위치·배치를 같은 REPEATABLE READ 읽기 전용 트랜잭션으로 조회하며, 전송/대기 중에는 DB 연결·트랜잭션을 보유하지 않는다.
- 이벤트 JSON은 job_id, sequence, type, occurred_at, payload다. payload는 `state`(공통 작업 상태 enum), `step=smoke`, `action=cancel|resume`, `resumed`/`synthetic`/`needs_review`(bool)만 허용하고 null은 생략한다. 내부 generation·code 및 미지정 키는 제거한다. 허용 키의 잘못된 타입/값은503 EVENT_DATA_UNAVAILABLE로 거부하며 DB 원문이나 검증 오류 상세를 반환하지 않는다. 이는 합성 worker의 기존 이벤트에 한정한 공개 DTO다.
- 매 배치와 유휴 poll(기본1초)에서 기존 서버 세션·활성 계정/업무 역할·지정 평가자·소유권을 재검사한다. 구성 승인 hash 철회 후에도 제한 조회는 유지한다. 권한 회수·DB 오류가 확인되면 스트림을 닫는다. 헤더 이후 JSON 오류나 합성 완료 이벤트로 실패를 숨기지 않는다. 이미 검증해 전송 중인 배치까지 회수할 수 있다는 보장은 하지 않는다.
- 유휴 상태는15초마다 `: heartbeat` 주석을 보낸다. 클라이언트 연결 종료 시 generator를 종료한다. 유휴 poll 주기와 배치 크기는 초기 구현값이며 운영 부하 검증은 후속이다.
- 종료된 작업도 커서 이후 저장 이벤트를 모두 보낸 뒤 닫는다. 종료 위치까지 받은 재접속은204다. 클라이언트는 종료 이벤트 수신 후 상태 GET으로 복구하고 재구독을 멈춘다. 상태 snapshot의 last_event_sequence 이후를 구독하면 조회와 구독 사이 commit을 놓치지 않는다.
- 보관 시작보다 이전에 누락된 구간이 있으면410 EVENT_CURSOR_EXPIRED로 상태 재조회를 요구한다. 현재 이벤트 삭제/보관 만료 작업은 구현하지 않았고 최소7일 보관은 기존 제안 상태다. 모든 이벤트를 삭제하는 정책은 watermark 저장 계약 없이 도입하지 않는다. 현 구현의410 검사는 남아 있는 prefix 경계에 한정한다.

`tests/platform/test_job_events.py`는 실제 DB 세션과 loopback HTTP 스트림에서 저장·실시간 이벤트 순서,100건 배치 경계, 헤더 우선/커서 거부, 연결 종료 후 취소 재접속/204, heartbeat, 연결 중 세션·계정·역할·평가자·소유권 회수/DB 장애, 민감 payload 차단 및 보관 구간 손실을 검증한다. 공용 GCP 프록시·브라우저 UI 통합은 이 검증에 포함하지 않는다. 로컬 웹 중계 검증은 아래 별도 범위를 따른다.

### 공개 경로와 후속 구현 순서

웹 서버의 `EVALUATION_READ_RELAY_ENABLED`는 기본false다. 명시적으로 켜면 정확한 UUID의 `GET /api/v1/platform/jobs/{job_id}` 및 `/events` 두 경로만 고정 PLATFORM_URL로 전달한다. 이는 배포 리소스/환경을 변경하거나 플랫폼의 local/test·지정 평가자·최종 시험 잠금을 해제하지 않는다. 평가 접수·제어·policy 및 임의 경로/메서드는 추가 허용하지 않는다. 플랫폼 C/S·TFT JSON 업무 중계와는 별개다.

웹은 서버 IAM 토큰 공급자를 재사용하며 브라우저 Authorization·내부 사용자/역할/호출자 헤더를 전달하지 않는다. 쿠키는 __Host-team4_session만 전달한다. SSE는 Last-Event-ID와 after 하나만 전달하고, 상태 GET의 쿼리 및 임의/중복 쿼리를 거부한다. 커서는 최대64자로 제한하며 값의 유효성·헤더 우선은 플랫폼 계약을 따른다. upstream 리디렉션은 따라가지 않는다.

상태·오류 응답은 최대1MiB의 application/json으로 제한하고 HTTP 상태·ETag·X-Request-ID·Retry-After를 보존한다. Set-Cookie·Location·내부 인증 헤더는 반환하지 않는다. SSE200은 text/event-stream만 허용하여 첫 프레임부터 전달하며 Cache-Control: no-cache, no-store와 X-Accel-Buffering: no를 적용한다. 느린 소비자에는 write/drain 역압력을 적용하며 전체 스트림을 메모리에 모으지 않는다.204는 본문 없이 그대로 반환한다.

upstream 헤더/JSON 대기는15초, SSE의 다음 chunk 대기/소비자 역압력 대기는45초로 제한한다. 클라이언트 연결 종료 시 upstream을 abort하고 타이머·이벤트 리스너를 정리한다. 헤더 전 중계 오류는 안전한502, 전송 시작 후 오류는 연결을 끊는다. API 오류를 합성 성공·정적 HTML·완료 이벤트로 바꾸지 않는다.

`frontend/server.test.mjs`는 허용 경로·쿠키/헤더 제거·상태/커서/204·첫 프레임 즉시 전달·클라이언트 단절 취소·잘못된 upstream·중간 스트림 실패·헤더 시간 제한을 검사한다. `tests/platform/test_web_job_events.py`는 실제 Node handler→FastAPI→PostgreSQL로 상태 snapshot→SSE heartbeat→단절→취소→재접속→종료 GET/204 및 세션 회수 후 연결 종료/401을 확인한다. 테스트의 IAM 공급자만 합성 값이며 실제 Cloud Run IAM·GCP 프록시 검증은 아니다. CI의 실제 DB job은 프론트 변경 때에도 실행한다.

PR45(김종하)의 웹 diff는 정적 파일 canonical 경로 처리다. 해당 영역은 건드리지 않고 별도 evaluation-relay.mjs와 작은 진입 분기만 추가한다. KAN-140의 공통 프론트 소유권은 유지한다. 평가 React 화면과 브라우저 자동 갱신/재접속은 [평가 화면 계약](evaluations.md#kan-137-sse-자동-갱신과-재접속)을 따른다. 취소/보류 해제 중계는 평가 계약의 별도 기본 비활성 설정을 따르며 접수 중계와 공용 활성화는 미구현이다. 이 HTTP 통합 회귀만으로 평가 화면 전체 완료를 뜻하지 않는다.

1. GET 및 SSE의 실제 세션·DB 접근, 상태 스냅샷·재생·권한 회수·종료 복구 검증까지 구현.
2. 읽기 전용 웹 SSE 중계·HTTP 소비자 복구 회귀까지 구현. 평가 화면의 읽기 연결과 단절/권한 오류 UI까지 구현. 취소/보류 해제 중계와 화면까지 구현. 접수 화면/중계는 후속 범위.
3. dispatcher 전송 lease/시도/결과 불명확 기록을 runtime에 구현. 평가 run의 generation/holder/lease와 실행 허가를 대체하지 않음.
4. Cloud Run Jobs API 호출자의 IAM과 Job 컨테이너 실행 서비스 계정·DB 실행 권한을 구분하여 최소 연결. HTTP Tasks worker의 OIDC 인증을 Jobs 컨테이너 인증으로 복사하지 않음. 중복 전달·전송 결과 불명확 시에도 run 소유권 재검사.
5. 실제 모델은 외부 호출 전 intent, UNKNOWN, 사용량 보존·취소 경계를 먼저 구현. 현재 fake transaction 안의 호출 패턴을 복사하지 않음.

운영 dispatcher·Jobs 인증·실모델·production 활성화·공용 SQL 적용·main 배포는 이번 범위 밖이다. `tests/platform/test_job_status.py`는 전용 PostgreSQL의 실제 세션으로 정상/종료/구성 철회 조회, 다른 평가자·소유권·시험 차단, 세션/계정/역할/지정 회수 및 DB 장애를 검사한다. CI의 실제 DB 검사 목록에도 포함한다.
