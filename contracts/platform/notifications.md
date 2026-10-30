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
