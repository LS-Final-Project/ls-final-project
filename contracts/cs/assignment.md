# C/S 문의 담당자 자동 배정 계약 (KAN-214)

원본: `backend/engine/cs/assignment.py`(규칙, 순수 함수), `backend/api/cs/assignments/`(저장·API), 마이그레이션 `infra/migrations/cs/20261111_KAN-214_agent_skills.sql`(README C29)·`20261114_KAN-234_ticket_no_match.sql`(C32, NO_MATCH 기록), 로컬 기준표 `infra/local/seed_demo.sql`, 공용 DB 등록 `infra/sql/register_agent_skills.sql`.

**상태: API·화면 구현(규칙 준수 검사).** 2026-11-04 김나영 결정. 새 문의의 담당자는 **AI가 아니라 규칙**으로 정한다(기획서 원칙: 우선순위·부서·SLA처럼 규칙표에서 나온다, LLM에 묻지 않는다). 관리자는 언제든 기존 재배정(`PUT /workflow/owner`)으로 바꿀 수 있다. 검사는 규칙대로 배정되는지만 본다. 이 규칙이 좋은 정책인지는 평가하지 않는다.

## 기준표 `cs.agent_skills` (가정 자료)

기획서 p.23 직원 기준표의 최소 칸만 둔다. LS가 실제 표를 주기 전까지는 **가정 자료**다(`derivation_kind = ASSUMED`, 화면에 "가정 자료" 표시).

| 칸 | 뜻 |
| --- | --- |
| `user_id` | `identity.users.id`. 활성 cs_agent여야 한다(C/S 서비스는 identity를 읽지 못해 등록 스크립트가 확인) |
| `languages` | 읽을 수 있는 언어, 소문자 ISO 639 코드(`en`, `th`, …). 비어 있으면 안 된다 |
| `families` | 맡는 제품군, `product_master.csv`의 `family` 코드(11개: `LV_BREAKER`·`MV_EQUIP`·`TRANSFORMER`·`DRIVE`·`PLC`·`HMI`·`CONTROL`·`METER`·`PV`·`ESS`·`UPS`). 비어 있으면 안 된다 |
| `active` | false면 배정 후보에서 빠진다 |
| `data_origin` | `synthetic`·`demo`·`real` (다른 가정 표와 같은 규약) |

- **API·화면으로 고치지 않는다.** 계정·역할·관리 범위와 같이(2026-10-23 KAN-78, [권한 문서](../platform/permissions.md)) 서버 밖 스크립트로만 쓴다. 로컬은 `infra/local/seed_demo.sql`, 공용 DB는 사용자 등록(`register_user.sql`) 뒤 `register_agent_skills.sql`(활성 cs_agent 확인 후 행 교체). 서비스 계정 `cs_app`은 SELECT만 한다.
- 계정을 비활성화하면 기준표의 `active`도 같은 스크립트로 false로 바꾼다. 중계 경로의 일괄 적용은 플랫폼이 제안된 담당이 활성 cs_agent인지 다시 확인한다(아래). 접수 시 자동 배정은 C/S 안에서 돌아 이 확인을 거치지 않으므로 기준표를 identity와 맞춰 둔다.
- 5개 기술지원 묶음(TS_LV·TS_MV·TS_TRF·TS_AUTO·TS_ENERGY)은 제품군 11개를 `tech_support_dept`로 묶은 것이다. 기준표는 제품군 코드로 적고 화면은 한국어 이름으로 보인다.

## 규칙 (`ASSIGNMENT_RULES_VERSION = cs-assign-v1`)

입력: 문의 언어(`cs.tickets.body_language`의 주 언어 부분, `en-US` → `en`), 제품군(`rules.product_family(product_code)`, 표에 없는 제품은 없음), 우선순위(최근 유효 분석의 규칙 우선순위, 없으면 없음), 접수 시각, 후보 담당자(기준표의 활성 행)와 각자의 현재 진행 건수(담당 중이고 종결이 아닌 문의 수).

1. `LANGUAGE_AND_FAMILY`: 언어와 제품군을 모두 맡는 활성 담당자.
2. `LANGUAGE_ONLY`: 1이 없으면 언어만 맞는 담당자(제품군 무관).
3. `FAMILY_ENGLISH_FALLBACK`: 2도 없으면 제품군을 맡고 영어(`en`)를 읽는 담당자.
4. `NO_MATCH`: 셋 다 없으면 배정하지 않는다. 판단 기록만 남고 미배정으로 남아 업무 배정 화면의 "맞는 담당자 없는 문의"에 사유와 함께 보인다(아래 조회 API).

처음으로 후보가 있는 단계에서 **진행 건수가 가장 적은** 사람을 고른다. 같으면 `user_id`가 작은 사람(입력 순서와 무관). 일괄 배정은 우선순위(P1→P4, 없음은 마지막) → 접수 시각 → 문의 ID 순으로 처리하고, 한 건 배정할 때마다 그 사람의 진행 건수를 1 올려 다음 건이 본다(부하가 퍼진다).

## 저장

- 담당자 자체는 기존대로 `cs.ticket_workflows.owner_user_id`다. 업무 `save`로 바꾸며 감사(`ticket_workflow`/`transition`, `action = auto_assign`, `assignment_id`·`reason_code`·`rule_version`·`trigger`)를 남긴다. 접수 상태(RECEIVED)는 바뀌지 않고 업무 판본이 1 오른다. 접수 시 배정의 감사 행위자는 null(시스템), 일괄 배정은 적용한 관리자다.
- 왜 그 사람인지는 추가 전용 `cs.ticket_assignments`에 남긴다: `user_id`, `rule_version`, `reason_code`, `matched_language`, `matched_family`, `candidate_count`(결정한 단계의 후보 수), `chosen_open_count`(배정 직전 그 사람의 진행 건수), `trigger`(`INTAKE`·`BATCH`), `decided_by`(BATCH의 관리자), `created_at`. `cs_app`은 SELECT·INSERT만 한다.
- `NO_MATCH`도 행을 남긴다(KAN-234, C32. 개선요청 표와 같이 `(reason_code = 'NO_MATCH') = (user_id IS NULL)`). 이때 `matched_language`·`matched_family`는 맞춘 값이 아니라 **아무도 맡지 않은 언어(주 언어)와 제품군**이고(제품군 null = `product_master`에 없는 제품), `candidate_count` 0, `chosen_open_count` null이다. 문의 상태·담당·업무 판본·감사·알림은 바뀌지 않는다. 같은 문의에 여러 번 쌓일 수 있고(접수 1회, 관리자 적용마다 1회) 조회는 **최신 판단**만 본다. 업무 상태 조회의 `assignment`는 NO_MATCH 행을 배정 근거로 쓰지 않는다.
- 배정을 거치지 않은 문의(제공 데이터 적재 등)는 판단 기록이 없다. 그래서 "맞는 담당자 없음"과 "아직 판단 안 함"이 구분된다.
- 업무 표(`ticket_workflows`)에 열을 더하지 않고 표를 따로 둔 이유: 담당자 칸은 사람이 재배정해도 같은 칸이고, 배정 근거는 이력이라 덮어쓰면 안 되기 때문이다. 업무 상태 조회의 `assignment`는 최근 배정 근거가 **지금 담당자와 같을 때만** 준다. 재배정·검토 시작으로 사람이 정한 담당이면 null이다.

## 언제 도나

- **접수 직후**: `POST /api/v1/cs/tickets`(수동 등록)와 내부 `POST /internal/v1/cs/tickets/from-mail`(메일 접수). 문의를 커밋한 뒤 별도 연결에서 한 번 배정한다. 맞는 사람이 없으면 NO_MATCH 판단만 기록한다. 기록이나 배정이 실패해도 접수(201)는 그대로이고 실패는 로그 코드 `CS_AUTO_ASSIGN_FAILED`만 남는다. 제공 데이터 적재 등 다른 경로로 들어온 문의는 배정하지 않는다(일괄 배정 대상).
- **관리자 일괄 배정**: 아래 API. 미리보기 → 확인 → 적용.

## API (`/api/v1/cs/assignments`)

| 메서드·경로 | 허용 | 동작 |
| --- | --- | --- |
| `GET /agents` | cs_agent, C/S 관리자 | 기준표 전체(`user_id` 순)와 각자의 현재 진행 건수, `rule_version`. 이름은 없다(C/S는 identity를 못 읽음). 화면은 `GET /api/v1/platform/users?ids=`로 이름을 읽는다 |
| `POST /preview` | C/S 관리자 | 본문 `{"limit": 1~200, 기본 50}`. 담당자 없는 RECEIVED 문의를 오래된 순으로 `limit`건 골라 위 규칙으로 일괄 결정한 제안(문의, 언어, 제품, 우선순위, 제안 담당, 사유, 맞춘 언어·제품군, 후보 수, 진행 건수)을 결정 순서대로 준다. 아무것도 쓰지 않는다 |
| `POST /apply` | C/S 관리자 | 본문 `{"rule_version", "items": [{"ticket_id", "user_id"}]}`(미리보기 그대로, 1~200건, 중복 422). 규칙 판본이 다르면 409 `CS_ASSIGNMENT_RULES_CHANGED`. 문의마다 잠그고, 아직 담당이 없는 RECEIVED 문의만 미리보기와 같은 순서·현재 부하로 **다시 결정해** 결과가 제안과 같은 것만 쓴다 |

| `GET /unmatched` | C/S 관리자 | 맞는 담당자 없는 문의(KAN-234). 쿼리 `limit` 1~200(기본 100). 지금 담당이 없고 RECEIVED(업무 행 없음 포함)이며 **최신 배정 판단이 NO_MATCH**인 문의를 최근 접수 순으로 `limit`건. 항목: `ticket_id`, `external_id`, `subject`, `subject_ko`(지금 원문과 맞는 한국어 번역 제목, 없으면 null), `channel`, `product_code`, `body_language`, `received_at`, `reason_code`(`NO_MATCH`), `language`(아무도 읽지 않은 주 언어), `family`(영어 담당도 없던 제품군, null = 미등록 제품), `trigger`(`INTAKE`·`BATCH`), `decided_at`. 그 밖에 `has_more`(더 있음) |
| `GET /improvements/unmatched` | C/S 관리자 | 맞는 담당자 없는 개선요청(KAN-234). 쿼리 `limit` 같음. C/S 담당이 없는 진행 요청(진행 = 집계 정의) 중 최신 판단이 NO_MATCH인 것을 최근 생성 순으로. 항목: `request_id`, `request_type`, `product_code`, `fault_id`, `status`, `created_at`, `reason_code`, `family`(**지금 제품 코드**에서 구한 제품군, null = 미등록 제품. 개선요청의 NO_MATCH 행은 제품군을 적지 않는다), `trigger`(`CANDIDATE`·`BATCH`), `decided_at`. 그 밖에 `has_more` |

적용 결과 `items[].outcome`: `ASSIGNED`(씀), `ALREADY_ASSIGNED`(그 사이 담당이 생김, `user_id`는 현재 담당), `NOT_RECEIVED`(그 사이 검토 시작 등), `NOT_FOUND`, `PREVIEW_CHANGED`(지금 규칙 결과가 제안과 다름, 쓰지 않음, `user_id`는 새 제안), `NO_MATCH`(제안도 미배정, 이 적용의 NO_MATCH 판단을 `BATCH`·적용한 관리자로 기록). `assigned`·`skipped` 건수를 같이 준다. 같은 본문을 다시 보내면 모두 `ALREADY_ASSIGNED`라 아무것도 바뀌지 않는다(멱등).

- C/S 관리자 = `manager` 역할이면서 관리 부서에 `GCS_L1`이 있는 사용자. 재배정과 같은 검사이며 그 밖은 403. 쓰기는 다른 쓰기처럼 웹 중계의 Origin·CSRF 검사를 거친다.
- 운영 플랫폼 중계는 `POST /apply`를 넘기기 전에 C/S 관리 권한과 **제안된 모든 담당이 활성 cs_agent인지** 확인한다(KAN-195와 같은 검사, 아니면 422 `CS_INVALID_OWNER`, 본문 형식 위반 422 `VALIDATION_ERROR`). 로컬에서 C/S 서비스를 직접 부르면 이 검증을 거치지 않는다.
- 업무 상태 조회(`GET /tickets/{id}/workflow` 등 `Workflow` 응답)의 `assignment`: 위 저장 절의 배정 근거. 부서 사용자에게는 null.

## 개선요청 담당 (`cs_owner`, `IMPROVEMENT_RULES_VERSION = cs-assign-request-v1`)

2026-11-04 김나영 추가 결정. 개선요청은 여러 언어의 문의를 묶고 부서에 가는 내용은 한국어라 **언어를 보지 않고 제품군만** 본다.

- 규칙: 요청 제품의 제품군(`product_family`)을 맡는 활성 담당자 중 **진행 개선요청이 가장 적은** 사람(`FAMILY`). 같으면 `user_id` 순. 없으면 `NO_MATCH`로 비워 둔다. 진행 = 집계와 같은 정의(`COMPLETED`가 아니고 반려 확인으로 끝나지 않음).
- 언제: 시스템 후보를 저장할 때(`save_candidates`: 수집 `POST /improvement-candidates/collect`, 해결 사례 등록 직후 판정, 로컬 부트스트랩). 한 번의 실행에서 여러 후보를 저장하면 배정할 때마다 부하를 센다. 기준표를 읽지 못하면(C29 없음 등) 아무도 배정하지 않고 후보 저장은 그대로다(로그 `CS_REQUEST_ASSIGN_FAILED`). 수동 작성 요청은 기존대로 작성자가 담당이다.
- 검토: 검토 완료를 기록한 사람은 **담당이 비어 있을 때만** 담당이 된다. 규칙으로 정해진 담당은 바꾸지 않는다. 관리자 재배정(`PUT /improvement-requests/{id}/owner`)은 그대로다.
- 저장: `cs.improvement_assignments`(추가 전용, `NO_MATCH` 포함, `trigger` = `CANDIDATE`·`BATCH`). 문의용 표와 따로 둔 이유는 각자 외래 키(문의/개선요청)와 사유 목록이 달라서다. 담당 자체는 `improvement_requests.cs_owner`이고, 후보 저장의 `create` 감사와 판본 내용에 `cs_owner`·`assignment_reason`이 들어간다.
- 일괄 배정: `POST /api/v1/cs/assignments/improvements/preview`(본문 `{"limit"}`, 담당 없는 진행 요청을 오래된 순으로)와 `POST /api/v1/cs/assignments/improvements/apply`(본문 `{"rule_version", "items": [{"request_id", "user_id"}]}`). 권한·재확인·멱등·결과 코드는 문의와 같고, 진행이 아니면 `NOT_OPEN`이다. 적용은 요청 판본을 올리고 `update` 감사를 남긴다. 운영 중계는 제안 담당이 활성 cs_agent인지 같은 방식으로 확인한다. 화면 단추는 두지 않는다(아래 화면 절). API는 운영 정비용으로 남긴다.
- `GET /agents`는 사람마다 `open_request_count`(진행 개선요청)와 `request_rule_version`을 함께 준다.

- 자동 배정(#592) 전에 만들어져 `cs_owner`가 빈 진행 요청은 따로 채우는 작업(backfill)을 두지 않는다. 필요하면 C/S 관리자가 위 일괄 배정 API(미리보기 → 적용)로 배정한다. 화면 단추는 없다. 새 로컬 DB는 설치 때 후보 생성(`save_candidates`)이 이미 배정하므로 해당 없다.

## 담당 배정 알림 (`cs.assigned`)

2026-11-04 김나영 승인. 문의의 담당이 정해지면 **새 담당 한 사람에게만** 앱 내 알림 하나를 남긴다([알림 계약](../platform/notifications.md) E2 표).

- 언제: 접수 시 자동 배정(행위자 없음), 관리자 일괄 배정 적용(행위자 = 관리자), 관리자 재배정 `PUT /workflow/owner`(행위자 = 관리자). 담당을 바꾼 업무 `save`와 **같은 트랜잭션**의 savepoint 안에서 쓴다. 본업무가 rollback되면 알림도 없다.
- 만들지 않음: 검토 시작으로 상담원이 스스로 담당이 될 때, 관리자가 자기 자신에게 재배정할 때, `NO_MATCH`, 멱등 재적용(`ALREADY_ASSIGNED`). 개선요청 담당(`cs_owner`)은 알림 대상이 아니다(링크 허용 경로가 문의·프로젝트뿐).
- 내용: 고정 제목 "새 문의 배정", 링크 `/cs/tickets/{id}`. 고객명·문의 원문을 넣지 않는다. 받는 범위 `[{user: 새 담당}]`. 중복 키 `cs.assigned:{ticket_id}:{owner}:{업무 판본}`.
- 마이그레이션 P35(`platform/20261111_KAN-214_assignment_notification.sql`)가 알림 kind·title CHECK에 새 값을 더한다. P35가 없으면 알림 INSERT만 실패해 되돌려지고(`NOTIFICATION_INTENT_FAILED`) 배정은 그대로 성공한다.

## 화면

- 문의 조회(CS0101): C/S 관리자에게 제목 줄 "자동 배정" 단추. 미리보기 표(문의, 언어, 제품군, 배정 대상, 사유) → "배정 적용"/"취소" → 결과 건수(배정 N, 미배정 M과 사유별 건수) 뒤 목록을 다시 읽는다. 관리자가 아니면 단추를 잠그고 이유를 툴팁으로 보인다.
- 문의 처리(CS0102): 담당 옆에 "자동 배정: 태국어·저압 차단기 담당 2명 중 진행 1건으로 가장 적음" 같은 한 줄.
- 업무 배정(CS0902) "맞는 담당자 없는 문의/개선요청" 상자(C/S 부서 관리자): 위 두 조회 API를 그대로 보인다(표는 앞 20건, 건수는 상한 100건이며 더 있으면 `+`). 사유는 서버 값을 한국어로 옮긴다. 문의 예: "헝가리어 가능자 없음 · 미등록 제품 PLC-NX-32", "폴란드어 가능자 없음 · PLC·영어 담당 없음". 개선요청 예: "PLC 제품군 담당자 없음", "미등록 제품 …". 줄마다 "담당 지정"(기존 재배정).
- 운영 현황(CS0901) "담당 배정" 구역(C/S 부서 관리자만 보임): 역할별 메뉴에서 관리자는 통계/보고서만 보므로 배정 기능을 여기 모은다. "미배정 문의 자동 배정"(위 미리보기 창과 같음), 진행 문의 표(담당자 조건·10건씩, 고른 문의를 "선택 담당 변경"으로 한 사람에게), 진행 개선요청의 C/S 담당과 요청 상세 재배정 링크. 서버 권한 검사는 그대로다.
- 운영 현황(CS0901): "담당자 배정 기준" 구역. 담당자, 언어, 제품군, 사용 여부, 진행 문의, 진행 개선요청. 읽기 전용, "가정 자료" 표시.
- 개선요청 목록(CS0301): 배정·새 요청 작성 단추는 두지 않는다. 새 요청은 후보 생성 때 규칙으로 배정된다(작성 API는 그대로 둔다). 목록 머리의 "총 N건 중 M건" 옆에 누구에게나 "미배정 N건"(고장 요청 중 `cs_owner` 없고 `COMPLETED` 아님, 받아 온 최근 200건 기준)을 보인다.
- 업무 홈(CS0001): "개선요청 할 일"은 내가 C/S 담당인 요청만 센다(`?owner=` 서버 필터). 초안(검토 완료 기록)도 오늘 할 일에 들어간다. 목록을 못 읽으면 합계에 `+`와 빠진 이유를 보인다.

## 로컬 시연 기준표

`infra/local/seed_demo.sql`이 새 로컬 DB를 만들 때 넣는다(실행 중인 환경은 바꾸지 않는다). 기존 시연 C/S 담당자(`…0001`, 영어·한국어)와 로그인 없이 배정만 받는 합성 상담원 6명(`…0011`~`…0016`, 이름에 "가정" 표시). 제공 문의의 `body_language` 15개(학습 분할 2,800건 기준 en 1,762 · es 169 · fr 91 · th 90 · id 79 · it 77 · zh 77 · tr 75 · ru 64 · ar 63 · vi 58 · pt 56 · de 54 · ja 48 · pl 37, 영어 외 37%)와 제품군 11개를 모두 한 명 이상이 맡고, 모두 영어를 읽는다.
