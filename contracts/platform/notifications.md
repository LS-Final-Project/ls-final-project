# 앱 내 알림 N1/N2/E1 (KAN-201)

수신 범위를 업무 트랜잭션에 기록하고 플랫폼이 활성 사용자로 펼친다. 앱 내 알림이 원본이다.
이 구현은 N1/N2와 worker E1·C/S 업무 E2·메일 E3 이벤트를 포함한다. 화면·중계 U, Web Push·구독·키·외부 호출은 포함하지 않는다.

## 쓰기 계약 N1

`backend.shared.notifications.enqueue_notification(conn, *, kind, subject_id, audience, actor_id, dedupe_key, needs_review=False) -> bool`
은 비동기 연결, `enqueue_notification_sync`는 동기 worker 연결용이다. 두 함수는 같은 입력 검증과 INSERT SQL을 쓴다.
제공 conn에 INSERT 한 번만 실행하며 commit·SELECT·RETURNING·identity 조회·외부 호출은 없다. 성공 INSERT는 true,
유일 dedupe_key 충돌은 false다. 호출자가 업무와 같은 트랜잭션 안에서 부르며 rollback되면 의도도 없다.

- kind는 `cs.review_wait`, `cs.safety`, `cs.reply`, `mail.unknown`, `cs.transfer`, `rfp.done`, `rfp.failed`다.
- owner·제목은 kind의 고정 매핑이며, link는 UUID subject_id로 만든 `/cs/tickets/{id}` 또는 `/tft/projects/{id}`만 허용한다.
  고객명·문의/회신 원문·오류 코드를 제목에 넣지 않는다. `rfp.done`의 needs_review만 고정 제목의 검토 필요 변형을 허용한다.
- audience는 1~64개의 단일 키 범위다: `{user: UUID}`, `{role: cs_agent}`, `{dept: 부서코드}`, `{manager_dept: 부서코드}`.
  같은 항목은 합친다. actor_id가 있으면 펼침에서 그 사용자를 제외한다. 시스템 RFP 완료/실패는 actor_id=null이고
  접수자는 audience.user로 포함한다. 알림의 actor와 작업의 접수자를 같은 개념으로 사용하지 않는다.
- `notify.notification_intents` payload는 append-only다. 플랫폼만 처리 메타데이터 expanded_at을 갱신한다.
  `notify.user_notifications`는 `(intent_id,user_id)` PK이며 플랫폼만 read_at을 갱신한다. push 필드는 없다.
- writer cs_app/tft_app/cs_worker/tft_worker는 intent 지정 열 INSERT만 가진다. 신규 intent 조회·수정·삭제, 수신 표 접근,
  identity 읽기 권한을 주지 않는다. 업무 앱(cs_app/tft_app)에는 runtime USAGE를 주지 않는다. 기존 역할의 다른 업무 표 권한을 변경하는 정책은 아니다.

## 펼침·조회 N2

`expand_pending(conn, batch_size=100)`은 미펼침 의도를 created_at/id 순으로 `FOR UPDATE SKIP LOCKED` 잠근다.
한 batch는 최대 100개다. identity.users.is_active인 사용자 중 다음 합집합을 DISTINCT로 고르고 actor를 제외한다.
user 범위는 해당 사용자, role은 cs_agent 역할, dept는 부서 일치 및 dept_staff 역할,
manager_dept는 manager 역할 및 identity.manager_departments 범위가 모두 필요하다.
수신 INSERT는 PK 충돌을 무시하며 expanded_at과 **같은 트랜잭션**에 쓴다. 수신자 0명도 펼침 완료다.
수신자는 플랫폼이 polling에서 의도를 최초로 펼치는 시점의 활성 상태·역할·부서·관리범위로 정한다.
이벤트 발생 시점의 수신자 스냅샷을 저장하지 않으며, 펼침 완료 뒤 변경은 소급 반영하지 않는다. 이는 시연 범위에서 승인된 동작이다. 실패는 빈 성공으로 숨기지 않고 전체 rollback한다.

| API | 동작 |
| --- | --- |
| GET `/api/v1/platform/notifications` | 내 목록, limit 기본50/최대100, offset 기본0/최대100000, created_at/id 내림차순, next_offset |
| GET `/api/v1/platform/notifications/unread-count` | 내 read_at=null 수 |
| POST `/api/v1/platform/notifications/{intent_id}/read` | 본인 수신 행만 읽음, 성공204; 이미 읽었으면 시각 유지; 남의 것과 없는 것 모두404 |

두 GET은 bounded batch 펼침 후 조회하므로 대기 의도가 많으면 다음 polling에서 계속한다. 스케줄러가 필요하지 않다.
반환 필드는 id·kind·고정 title·link_path·created_at·read_at뿐이다. audience·actor·dedupe·수신자 목록을 공개하지 않는다.
사용자 ID는 기존 me의 서버 세션에서만 얻고 query/body로 선택하지 않는다. 알림 링크는 업무 자원 접근 권한을 부여하지 않는다.
운영 인증은 기존 me, 변경은 기존 mutation_token(동일 Origin·세션 결속 CSRF)을 사용한다.
명시적 local/test 개발 세션은 기존 guard_local/guard_origin 동작을 그대로 따른다. 인증 없음401, 비활성 사용자도 기존 세션 검증으로 거부된다.
알림 응답은 no-store다. 임의 query/중복 query를 거부한다. 프론트 UI/중계 허용 목록은 별도 작업이다.

## worker 이벤트 E1

기존 `job_runtime.finish`의 동기 트랜잭션 안에서 held의 holder/generation/lease 검사 뒤 상태·종료 이벤트·알림을 함께 쓴다.
알림용 owner/책임자 조회와 INSERT는 같은 outer transaction 안의 별도 SAVEPOINT로 격리한다.
알림 표 미적용·조회 권한/INSERT 오류가 나면 알림 부분만 rollback하고 원문 없는 고정 경고
`NOTIFICATION_INTENT_FAILED`를 남긴다. 본작업 상태와 종료 이벤트는 정상 완료한다(예: SUCCEEDED·job.completed).
이는 위 N1의 일반적인 caller rollback 규칙과 다르다. 정상 알림은 별도 commit 없이 같은 outer transaction에 속하고 dedupe도 유지한다.
본작업 상태 갱신·종료 이벤트 오류와 소유권 상실·중복 완료의 LeaseLost는 삼키지 않으며 기존 동작을 유지한다. 외부 발송은 없다.
취소와 CS worker 실패에는 알림을 만들지 않는다. 강제 종료처럼 finish를 지나지 않는 실패도 이 범위에서 알리지 못한다.

| 이벤트 | 조건·범위 | dedupe_key |
| --- | --- | --- |
| C/S 확인 대기 | 최종 human_review 정지, SAFETY 아님. 현재 owner, 없으면 cs_agent 전체 | cs.review_wait:{job_id} |
| 안전 문의 | 최종 human_review 정지 및 urgency_flag=SAFETY. owner+cs_agent 전체+manager_dept GCS_L1. 같은 실행의 확인 대기 생략 | cs.safety:{ticket_id} |
| RFP 완료 | rfp.process SUCCEEDED. 접수자+프로젝트 책임자, 중복 제거. actor=null | rfp.done:{job_id} |
| RFP 실패 | rfp.process FAILED. 같은 범위. CANCELLED 제외 | rfp.failed:{job_id} |

CS는 재시작/재작성 뒤에도 실제 최종 체크포인트 next가 human_review인지를 확인한다. 안전 문의는 이 정상 정지에 포함한다.
분류 실패는 END로 끝나므로 알림 없이 남는다. 이 공백의 별도 실패/담당자 알림은 **2차 후보**이며 현재 실패 경로를 추가하지 않는다.
CS 시스템 완료 시에도 기존 계획대로 lease.actor_id를 제외한다. worker는 identity를 읽지 않는다.
새 CS 마이그레이션은 ticket_workflows의 ticket_id·owner_user_id만, 새 TFT 마이그레이션은 projects의 id·responsible_user_id만 허용한다.

## 적용·검증

[SQL 적용 순서](../../infra/sql/README.md)의 C22·T16·P30을 코드 배포 전에 적용한다. 모두 재실행 가능하며 기존 역할을 확장/승격하지 않는다.
실제 writer 역할의 최초/중복 INSERT가 SELECT 권한 없이 되는지, 신규 표 권한, SKIP LOCKED 동시성·rollback·본인 API·CSRF·Origin·
안전/확인 대기 상호배제·RFP 접수자 포함을 검증한다. 로컬에 전용 DB가 없으면 실제 DB 검사는 미실행으로 구분하고 CI에 위임한다.
단위 fake 성공은 실제 DB 동시성·운영 배포·화면 표시·Web Push 성공의 근거가 아니다.


알림은 전용 `notify` 스키마(platform_migrator 소유)에만 저장한다. 플랫폼과 writer는 필요한 notify USAGE만 받으며
기존 작업용 runtime 권한을 확장하지 않는다. P30 재적용은 구버전 runtime 알림 표를 데이터·PK/FK와 함께 notify로
이동하고 잘못된 업무 앱 runtime USAGE를 회수한다. worker/platform의 기존 runtime 권한은 유지한다.
양쪽에 동명 표가 있는 비정상 상태는 `NOTIFICATION_SCHEMA_CONFLICT`로 중단하며 데이터를 조용히 버리지 않는다.


## C/S 업무 E2 및 메일 E3

기존 업무/문의 잠금·버전·역할·승인·발송 처리는 유지한다. 알림은 같은 업무 conn의 SAVEPOINT 안에서만 쓰고
별도 commit이나 외부 호출을 하지 않는다. 알림용 조회·검증·INSERT 실패는 그 부분만 rollback한 뒤
`NOTIFICATION_INTENT_FAILED` 고정 경고만 남긴다. 본업무는 정상 진행하며 원문·참조·승인자 ID를 경고에 넣지 않는다.
이관 INSERT/인가/상태 저장 오류, 정정·감사 저장 오류, 메일 본 UPDATE/이벤트 오류는 이 경계 밖이며 기존대로 전파한다.
정상 알림도 outer 업무 transaction의 일부이므로 뒤의 본업무 실패·rollback 시 함께 취소된다.

| 이벤트 | 정확한 생성 조건 | 수신 범위·actor | dedupe_key |
| --- | --- | --- | --- |
| 고객 회신 재투입 | record_customer_reply가 이번 호출에서 recorded AND reopened | owner, 없으면 cs_agent 전체; 외부 회신이므로 actor=null | cs.reply:{message_id} |
| 이관 수신 | transfer 또는 부서 결과 REWORK의 insert_transfer 성공 | 대상 dept의 dept_staff와 manager_dept; actor=행동한 principal.user_id | cs.transfer:{실제 저장 transfer_id} |
| 사람 안전 정정 | 현재 effective urgency와 비교해 changed.urgency_flag가 SAFETY인 실제 전이 | owner+cs_agent 전체+manager_dept GCS_L1; actor=정정자 | cs.safety:{ticket_id} |
| 발송 결과 불명 | mail.finish 또는 expire_submitting의 UPDATE가 실제 반환한 행이 UNKNOWN으로 전이 | approved_by+manager_dept GCS_L1; 시스템 결과이므로 actor=null | mail.unknown:{intent_id}:{attempts} |

중복 회신, 검토/이관 중의 재투입 없는 회신, ACCEPT 검토, 이미 SAFETY인 문의의 다른 필드 정정은 알리지 않는다.
안전 알림은 E1과 같은 문의 단위 키를 사용해 AI와 사람이 함께 감지해도 한 번만 남는다.
이관은 INSERT에 사용한 UUID를 그대로 키에 쓰며 재작업은 새 transfer UUID로 구분한다.
메일 알림 metadata(business_ref·approved_by·attempts) 조회도 SAVEPOINT 안에 있으며, 기존 UPDATE는 변경하지 않는다.
같은 attempts의 재호출은 중복 알림을 만들지 않는다. 만료 전환은 UPDATE RETURNING의 ID만 순회한다.
C/S 링크는 business_ref가 `cs_ticket:{정규 UUID}`일 때만 생성한다. 다른 업무 참조에는 임의 링크를 만들지 않고,
잘못된 cs_ticket 참조는 알림 실패로만 표시한다. 승인·수신자·첨부·발송·재발송 검증은 바꾸지 않는다.

새 권한이나 표·API는 추가하지 않는다. cs_app의 기존 workflow 조회와 P30의 notify INSERT 열 권한,
platform_app의 기존 메일 metadata SELECT와 P30의 notify INSERT 열 권한을 재사용한다. identity 조회는 추가하지 않는다.
실제 역할의 중복·전이·SAVEPOINT 실패 격리는 전용 CI DB 검사로 확인하며, 로컬 fake 성공을 DB 검증으로 보고하지 않는다.


## Web Push P1/P2 (KAN-201)

앱 내 수신함이 원본이다. Web Push는 Chrome 데스크톱의 추가 알림이며 브라우저 종료/권한 거부,
네트워크 장애에서 수신을 보장하지 않는다. 화면·서비스워커·실제 발송·운영 스크립트 실행은 이번
서버 변경 검증 범위 밖이다. 기본 `NOTIFICATION_PUSH_ENABLED=false`이며 운영자가 명시 구성한다.

### 구독 API

기존 세션 `me`의 활성 사용자를 사용한다. 다른 user_id/수신자 지정은 받지 않는다.
모든 응답은 no-store이며 endpoint/p256dh/auth/개인키를 응답·로그에 넣지 않는다.

| 경로 | 입력/응답 | 권한 |
| --- | --- | --- |
| GET `/api/v1/platform/push/public-key` | SuccessResponse.data.public_key | 로그인 필수 |
| PUT `/api/v1/platform/push/subscription` | `{endpoint,keys:{p256dh,auth}}`, 204 | 기존 Origin·CSRF 정책 |
| DELETE `/api/v1/platform/push/subscription` | `{endpoint}`, 소유 여부와 무관하게 204 | 현재 사용자 소유 구독만 삭제, 기존 Origin·CSRF |

쿼리/추가 필드는 거부한다. endpoint는 HTTPS `fcm.googleapis.com`의 `/fcm/send/` 또는 `/wp/`
토큰 경로만 허용한다. 포트 명시/userinfo/query/fragment/다른 호스트/인코딩 우회는 거부한다.
키는 canonical base64url, auth 16 bytes, p256dh는 유효한 uncompressed P-256 65 bytes이다.
`notify.push_subscriptions`는 사용자/브라우저 구독을 보관한다. 같은 endpoint·사용자·키 재등록은
멱등이며 생성 시각을 유지한다. 사용자 또는 키 변경은 구세대를 삭제하고 새 UUID/생성 시각으로
교체한다. 제공 endpoint의 소유권은 현재 세션으로 이동한다(동일 브라우저 계정 전환 계약).
다른 사용자의 endpoint 조회/삭제는 제공하지 않는다.

platform_app은 구독 표 SELECT/INSERT/DELETE와 `UPDATE(lock_marker)`만 가진다.
`lock_marker`는 PostgreSQL `FOR UPDATE`의 열 권한 요구를 충족하는 비업무 메타데이터이며
발송/등록 코드가 그 값을 갱신하지 않는다. id/user_id/endpoint/p256dh/auth/created_at UPDATE는
허용하지 않는다. 세대 교체는 기존 DELETE+INSERT이며 발송의 `FOR UPDATE OF s,d`를 유지한다.
P31 재적용은 기존 행을 보존하고 이 최소 열 권한을 다시 설정한다.

### 요청 밖 발송과 중복/실패

Scheduler는 Google OIDC의 고정 audience 및 지정 실행 계정으로만
`POST /internal/v1/platform/notifications/push`를 호출한다. 본문·쿼리는 받지 않는다.
이 경로는 고정 프로젝트/서울 리전/Job에 실행 요청만 보내고 202를 반환한다. 앱 요청 내 Web Push,
BackgroundTasks/비동기 잔여 작업을 사용하지 않는다. Job launch 응답 불명/실패는 503이다.
중복 실행 요청이 여러 Job을 만들 수 있지만 실제 발송 확보는 DB에서 배제한다.

별도 `python -m backend.api.platform.notifications.push_worker` Job은 유한 batch(기본100)로
기존 펼침을 실행하고 현재 활성 사용자·미읽음·생성 후 1시간 이내 알림만 처리한다.
새 구독 이전 과거 알림은 발송하지 않는다. 플랫폼 최초 polling/Job 펼침 시점의 수신자 정책을
그대로 사용한다. `notify.push_deliveries`는 (intent_id,subscription_id) 유일 키다.
SKIP LOCKED로 한 시도를 확보하고 SENDING/시도 수를 별도 TX로 commit한 뒤 구독 세대를
다시 잠가 현재 소유권·미읽음·활성·TTL을 확인한다. 확인 실패는 SKIPPED이다.

외부 발송 동안 구독 세대 row 잠금을 유지한다. 이는 동시에 계정 변경/삭제가 성공한 뒤 이전
사용자 알림이 전송되거나 구세대 410이 신구독을 삭제하는 일을 막기 위한 직렬화다.
발송은 별도 자식 프로세스에서 DNS/암호화/HTTP 전체를 수행한다. 부모는 시작 후 10초 대기하고
초과하면 kill 및 1초 join 후 종료를 확인해 UNKNOWN으로 기록한다(프로세스 시작/DB lock 획득 시간은 별도).
종료가 확인되지 않는 예외적 OS 장애에서는 고정 경고만 남기고 kill/join을 반복하며 구독 잠금을
유지한다. 이 비정상 종료대기에는 11초 상한을 주장하지 않는다. 부모 예외/취소도 종료확인 뒤에만
TX를 해제한다. 종료 미확인 상태는 DB 연결·구독 잠금을 장시간 점유할 수 있으므로 운영자가
Job 및 DB 잠금을 확인해야 한다. 정상 재확인은 1초 join, join 예외도 1초 대기를 사용한다.
spawn 실패/자식 결과 없음/비정상 종료는 UNKNOWN이다.
서버/Job 종료로 결과 기록을 못한 SENDING은 2분 후 UNKNOWN으로 복구한다.
응답불명·timeout·중단은 자동 재발송하지 않는다. 중복보다 전달손실을 허용하는 승인 정책이다.

HTTP 200~202는 공급자 접수 SENT이며 브라우저 수신 확인이 아니다. 404/410은 잠근 구독 UUID만
삭제하고 해당 세대 장부는 FK cascade된다. 429/503의 명시 응답만 Retry-After(초 또는 HTTP-date,
누락/잘못된 값은60초)를 따라 PENDING으로 전환한다. 1시간보다 긴 Retry-After는 FAILED이다.
총 시도 최대3회/알림1시간 TTL 밖으로는 재시도하지 않는다. 그 외 HTTP 실패는 FAILED이다.
어떤 푸시 실패도 앱 내 알림/본업무를 롤백하지 않는다.

### 전송·비밀·운영 경계

pywebpush==2.5.0의 requests_session 주입점만 사용한다. TTL3600, Urgency normal,
본문은 고정 title·안전 앱내 url·알림 UUID만 포함한다. DNS의 모든 반환 주소가 global이어야 하며
그 중 검증 주소 하나에 소켓을 고정한다. TLS SNI/인증서는 원래 fcm.googleapis.com으로 검증한다.
redirect/proxy환경/라이브러리 재시도는 비활성이다. connect/read 각각5초와 별개로 전체 시도는
위 자식 프로세스 제한을 받는다. 응답은 헤더 후 즉시 close하고 본문은 읽지 않는다.
외부 예외 문자열/provider 본문/능력 URL은 기록하지 않는다.

VAPID private scalar는 전용 notification-push-runtime의 Secret Manager 버전1에만 주입한다.
API는 공개키만 받는다. Job은 시작 시 공개/개인키 일치와 mailto 운영 연락처를 검사한다.
임의 키회전·latest 버전·로컬 키파일 생성은 없다. 설정/권한/구독 표는 platform 소유이며
업무앱/기존 worker의 identity/runtime/notify 접근 권한을 확대하지 않는다.
O1/O2는 기본 preview 및 명시 --apply 보호를 제공한다. 실행은 운영자만 한다.
O2 재적용은 기존 Scheduler 조회 후 pause를 첫 변경으로 수행하며 조회/pause 실패는 즉시 중단한다.
그 뒤 API dispatch를 OFF로 설정하고 Job/IAM을 준비한다. Scheduler create/update는 실제 dispatch
경로 대신 안전한 POST `/health` 대상(405)을 사용한다. create가 활성 상태로 시작하거나 update가
상태를 바꾸거나 기존 API revision이 남아 있어도 이 대상은 발송을 요청할 수 없다.
다시 pause 및 PAUSED 확인 뒤 공개 API 설정만 OFF 상태로 적용한다. 준비 스크립트는 활성화하지
않는다. 중간 실패에서 새 dispatch를 활성화하는 경로는 없고 생성/갱신 후 남은 Scheduler는
비발송 대상이다. 선행 조회/pause/OFF 갱신 자체가 실패하면 즉시 중단하고 기존 상태를 운영자가
확인한다. 또한 OFF 설정이 모든 serving revision에 적용됐다고 자동 가정하지 않는다. 이미 실행 중이던
요청/Job을 소급 취소하지 않으므로 운영자가 pause 이전의 진행 중 작업도 별도로 확인해야 한다.
활성화는 운영자의 별도 행동이다. 모든 serving revision의 OFF/기존 진행 작업을 확인한 뒤
paused Scheduler의 URI를 실제 dispatch로 변경하고 PAUSED를 확인한 상태에서 API를 켠 뒤
명시 resume한다. 이 활성화 명령은 준비 스크립트가 실행하지 않는다. DB·GCP 배포·Chrome 실수신은 CI/운영 후속
검증으로 남기고 fake 전송 성공을 실제 수신 성공으로 보고하지 않는다.
