# C/S 문의 담당자 자동 배정 계약 (KAN-214)

원본: `backend/engine/cs/assignment.py`(규칙, 순수 함수), `backend/api/cs/assignments/`(저장·API), 마이그레이션 `infra/migrations/cs/20261111_KAN-214_agent_skills.sql`(README C29), 로컬 기준표 `infra/local/seed_demo.sql`, 공용 DB 등록 `infra/sql/register_agent_skills.sql`.

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
4. `NO_MATCH`: 셋 다 없으면 배정하지 않는다(미배정으로 남아 관리자 일괄 배정 미리보기에 사유와 함께 보인다).

처음으로 후보가 있는 단계에서 **진행 건수가 가장 적은** 사람을 고른다. 같으면 `user_id`가 작은 사람(입력 순서와 무관). 일괄 배정은 우선순위(P1→P4, 없음은 마지막) → 접수 시각 → 문의 ID 순으로 처리하고, 한 건 배정할 때마다 그 사람의 진행 건수를 1 올려 다음 건이 본다(부하가 퍼진다).

## 저장

- 담당자 자체는 기존대로 `cs.ticket_workflows.owner_user_id`다. 업무 `save`로 바꾸며 감사(`ticket_workflow`/`transition`, `action = auto_assign`, `assignment_id`·`reason_code`·`rule_version`·`trigger`)를 남긴다. 접수 상태(RECEIVED)는 바뀌지 않고 업무 판본이 1 오른다. 접수 시 배정의 감사 행위자는 null(시스템), 일괄 배정은 적용한 관리자다.
- 왜 그 사람인지는 추가 전용 `cs.ticket_assignments`에 남긴다: `user_id`, `rule_version`, `reason_code`, `matched_language`, `matched_family`, `candidate_count`(결정한 단계의 후보 수), `chosen_open_count`(배정 직전 그 사람의 진행 건수), `trigger`(`INTAKE`·`BATCH`), `decided_by`(BATCH의 관리자), `created_at`. `NO_MATCH`는 행을 남기지 않는다. `cs_app`은 SELECT·INSERT만 한다.
- 업무 표(`ticket_workflows`)에 열을 더하지 않고 표를 따로 둔 이유: 담당자 칸은 사람이 재배정해도 같은 칸이고, 배정 근거는 이력이라 덮어쓰면 안 되기 때문이다. 업무 상태 조회의 `assignment`는 최근 배정 근거가 **지금 담당자와 같을 때만** 준다. 재배정·검토 시작으로 사람이 정한 담당이면 null이다.

## 언제 도나

- **접수 직후**: `POST /api/v1/cs/tickets`(수동 등록)와 내부 `POST /internal/v1/cs/tickets/from-mail`(메일 접수). 문의를 커밋한 뒤 별도 연결에서 한 번 배정한다. 맞는 사람이 없거나 실패해도 접수(201)는 그대로이고 실패는 로그 코드 `CS_AUTO_ASSIGN_FAILED`만 남는다. 제공 데이터 적재 등 다른 경로로 들어온 문의는 배정하지 않는다(일괄 배정 대상).
- **관리자 일괄 배정**: 아래 API. 미리보기 → 확인 → 적용.

## API (`/api/v1/cs/assignments`)

| 메서드·경로 | 허용 | 동작 |
| --- | --- | --- |
| `GET /agents` | cs_agent, C/S 관리자 | 기준표 전체(`user_id` 순)와 각자의 현재 진행 건수, `rule_version`. 이름은 없다(C/S는 identity를 못 읽음). 화면은 `GET /api/v1/platform/users?ids=`로 이름을 읽는다 |
| `POST /preview` | C/S 관리자 | 본문 `{"limit": 1~200, 기본 50}`. 담당자 없는 RECEIVED 문의를 오래된 순으로 `limit`건 골라 위 규칙으로 일괄 결정한 제안(문의, 언어, 제품, 우선순위, 제안 담당, 사유, 맞춘 언어·제품군, 후보 수, 진행 건수)을 결정 순서대로 준다. 아무것도 쓰지 않는다 |
| `POST /apply` | C/S 관리자 | 본문 `{"rule_version", "items": [{"ticket_id", "user_id"}]}`(미리보기 그대로, 1~200건, 중복 422). 규칙 판본이 다르면 409 `CS_ASSIGNMENT_RULES_CHANGED`. 문의마다 잠그고, 아직 담당이 없는 RECEIVED 문의만 미리보기와 같은 순서·현재 부하로 **다시 결정해** 결과가 제안과 같은 것만 쓴다 |

적용 결과 `items[].outcome`: `ASSIGNED`(씀), `ALREADY_ASSIGNED`(그 사이 담당이 생김, `user_id`는 현재 담당), `NOT_RECEIVED`(그 사이 검토 시작 등), `NOT_FOUND`, `PREVIEW_CHANGED`(지금 규칙 결과가 제안과 다름, 쓰지 않음, `user_id`는 새 제안), `NO_MATCH`(제안도 미배정). `assigned`·`skipped` 건수를 같이 준다. 같은 본문을 다시 보내면 모두 `ALREADY_ASSIGNED`라 아무것도 바뀌지 않는다(멱등).

- C/S 관리자 = `manager` 역할이면서 관리 부서에 `GCS_L1`이 있는 사용자. 재배정과 같은 검사이며 그 밖은 403. 쓰기는 다른 쓰기처럼 웹 중계의 Origin·CSRF 검사를 거친다.
- 운영 플랫폼 중계는 `POST /apply`를 넘기기 전에 C/S 관리 권한과 **제안된 모든 담당이 활성 cs_agent인지** 확인한다(KAN-195와 같은 검사, 아니면 422 `CS_INVALID_OWNER`, 본문 형식 위반 422 `VALIDATION_ERROR`). 로컬에서 C/S 서비스를 직접 부르면 이 검증을 거치지 않는다.
- 업무 상태 조회(`GET /tickets/{id}/workflow` 등 `Workflow` 응답)의 `assignment`: 위 저장 절의 배정 근거. 부서 사용자에게는 null.

## 화면

- 문의 조회(CS0101): C/S 관리자에게 제목 줄 "자동 배정" 단추. 미리보기 표(문의, 언어, 제품군, 배정 대상, 사유) → "배정 적용"/"취소" → 결과 건수(배정 N, 미배정 M과 사유별 건수) 뒤 목록을 다시 읽는다. 관리자가 아니면 단추를 잠그고 이유를 툴팁으로 보인다.
- 문의 처리(CS0102): 담당 옆에 "자동 배정: 태국어·저압 차단기 담당 2명 중 진행 1건으로 가장 적음" 같은 한 줄.
- 운영 현황(CS0901): "담당자 배정 기준" 구역. 담당자, 언어, 제품군, 사용 여부, 현재 진행. 읽기 전용, "가정 자료" 표시.

## 로컬 시연 기준표

`infra/local/seed_demo.sql`이 새 로컬 DB를 만들 때 넣는다(실행 중인 환경은 바꾸지 않는다). 기존 시연 C/S 담당자(`…0001`, 영어·한국어)와 로그인 없이 배정만 받는 합성 상담원 6명(`…0011`~`…0016`, 이름에 "가정" 표시). 제공 문의의 `body_language` 15개(학습 분할 2,800건 기준 en 1,762 · es 169 · fr 91 · th 90 · id 79 · it 77 · zh 77 · tr 75 · ru 64 · ar 63 · vi 58 · pt 56 · de 54 · ja 48 · pl 37, 영어 외 37%)와 제품군 11개를 모두 한 명 이상이 맡고, 모두 영어를 읽는다.
