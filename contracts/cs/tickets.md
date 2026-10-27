# C/S 문의 계약 시작점

원본: backend/api/cs/tickets/schemas.py 및 routes.py. 예시: backend/api/cs/examples.py.

## 문의 조회·등록·수정·감사 (KAN-102)

| 경로 | 응답 | 설명 |
| --- | --- | --- |
| `GET /api/v1/cs/tickets?limit=&cursor=` | `SuccessResponse[TicketList]` | 접수 시각 최신순. 항목마다 업무 상태 `workflow_state`(아래 절). `limit` 1~100(기본 50). `next_cursor`를 그대로 다음 요청의 `cursor`로 보낸다. 잘못된 커서는 400 `CS_INVALID_CURSOR` |
| `GET /api/v1/cs/tickets/{ticket_id}` | `SuccessResponse[TicketDetail]` | 접수 원문과 연결된 해결 사례. 없는 문의는 404 `CS_TICKET_NOT_FOUND` |
| `POST /api/v1/cs/tickets` | 201 `SuccessResponse[TicketDetail]` | 수동 접수. 요청은 `TicketCreate`(채널, 본문 언어, 고객·제품 코드, 제목, 본문, 보고된 펌웨어·에러코드)만 받는다. `id`, `external_id`(null), `version`(1), `received_at`, `data_origin`(중계 비활성은 demo, 활성은 서버의 CS_MANUAL_DATA_ORIGIN), `derivation_kind`(SYSTEM)는 서버가 정하며 요청에 넣으면 422 |
| `PATCH /api/v1/cs/tickets/{ticket_id}` | `SuccessResponse[TicketDetail]` | 식별 정보 정정. `version`과 바꿀 필드(customer_external_id, product_code, subject, fw_version_reported, error_code_reported)를 보낸다. 본문·채널·접수 시각은 수정할 수 없다(422). 저장된 판본과 다르면 409 `CS_TICKET_VERSION_CONFLICT`, 값이 같으면 판본을 올리지 않는다 |
| `GET /api/v1/cs/tickets/{ticket_id}/audit` | `SuccessResponse[AuditList]` | 판본 순 감사 이력. 등록은 `{"필드": {"to": 값}}`, 정정은 `{"필드": {"from": 이전, "to": 이후}}`. 원문 본문은 감사 이력에 복제하지 않는다 |

저장 구조는 C/S 기획서 6.1의 제공 테이블(tickets, resolution_cases)과 [공통 데이터 계약](../../docs/data-contract.md)의 식별자·시점·출처 필드를 따른다. 마이그레이션은 `infra/migrations/cs/20261022_KAN-102_tickets.sql`, 감사 이력은 `20261023_KAN-102_ticket_audit.sql`이다.

- 정정은 행을 잠근(`SELECT ... FOR UPDATE`) 상태에서 판본을 비교하고, 문의 변경과 감사 기록을 한 트랜잭션으로 저장한다. `cs.audit_events`는 서비스 계정(cs_app)에 UPDATE·DELETE 권한이 없는 추가 전용 테이블이다.
- `actor_user_id`는 요청한 사용자 ID다(KAN-78). KAN-78 이전에 만든 기록은 null이다.
- 오류 코드는 현재 기능별 `ApplicationError`를 쓰며 KAN-56의 오류 계약이 확정되면 맞춘다.

- `cs.tickets`는 접수 입력만 저장한다. 운영 중 생기는 AI 분류(KAN-103), 규칙 결과(KAN-104)와 업무 상태(KAN-108)는 각 티켓에서 별도로 저장한다.
- 제공 원천이 기록한 분류·규칙 결과·처리 이력(category_code, priority, assigned_dept, status, SLA, final_reply, data_split 등)은 `cs.ticket_source_records`에 문의당 한 행으로 보존한다. 이 값은 평가 정답과 사후 기록이므로 분류 입력(build_input)이나 API 응답에 섞지 않으며, 운영 중의 AI 결과·사람 수정과 같은 칸을 공유하지 않는다. 제공 CSV에는 `return_subtype` 열이 없어 정답 매핑은 KAN-95에서 정한다.
- 원천 ID(`CSV1-000044`, `RC-0007`)는 `external_id`, URL과 내부 참조는 서버 UUID `id`를 쓴다.
- 제목이 없는 채팅 문의의 `subject`는 null이다.
- 해결 사례는 `known_at`이 지난 경우에만 상세에 포함한다. 제공 데이터는 해결 시각을 known_at으로 가정한다.
- 접근 검사(KAN-78, [권한 문서](../platform/permissions.md) 1.3·1.4·4.1): 사용자 정보가 없거나 잘못되면 401. 조회는 `cs_agent`와 C/S 부서(`GCS_L1`)를 관리하는 manager는 전체, dept_staff와 다른 부서 manager는 자기(관리) 부서로 **한 번이라도 이관된** 문의만 본다(KAN-108, 이관이 끝난 뒤에도 조회 유지). 범위 밖 문의는 없는 문의와 같은 404 `CS_TICKET_NOT_FOUND`. sales의 목록과 cs_agent가 아닌 사용자의 등록·정정은 403.
- 업무 중계가 비활성일 때 위 경로는 `local`·`test`에서만 응답하고 `production`에서는 DB 조회 전에 404 `NOT_FOUND`로 닫는다. KAN-178의 명시 활성화는 [플랫폼 호출자 검증](../platform/auth.md#업무-json-중계와-호출자-검증-kan-178)과 기존 사용자·업무 권한 검사를 함께 요구한다. OpenAPI 상태는 `partial`이다. 설비·장비 일치 확인은 아래 절을 따른다.

중계를 켠 C/S는 `CS_MANUAL_DATA_ORIGIN=demo` 또는 `real`을 명시해야 시작한다.
공용 시연은 production 인증 정책에서도 demo를 유지하고, 실제 문의만 처리하는 배포에서
real을 선택한다. 인증 환경이나 요청의 출처 필드로 실제 여부를 추정하지 않는다.
한 배포의 수동 접수 출처는 한 값이며 실제·시연 자료를 섞어 접수하는 기능은 제공하지 않는다.
설정 변경은 이후 새 문의에만 적용하며 기존 행을 일괄 승격·변경하지 않는다.

## 담당 검토·이관·부서 처리 결과·종결 (KAN-108)

원본: backend/api/cs/workflow/schemas.py 및 routes.py. 상태와 전이는 [권한 문서](../platform/permissions.md) 2.1을 따른다. 마이그레이션은 `infra/migrations/cs/20261027_KAN-108_ticket_workflow.sql`이다. 제공 CSV의 `status`(CLOSED, RESOLVED 등)는 `cs.ticket_source_records`의 사후 기록이며 이 상태와 섞지 않는다.

| 경로 (`/api/v1/cs/tickets/{ticket_id}/workflow` 아래) | 허용 | 전이 |
| --- | --- | --- |
| `GET` | 문의 조회 범위 | 현재 상태·판본·담당자·이관 회차·부서 처리 결과. 시작 전 문의는 `RECEIVED`, `version: 0` |
| `POST /start-review` | cs_agent | `RECEIVED` → `IN_REVIEW`. 담당자가 없으면 실행한 사람이 담당자 |
| `POST /transfer` (`dept_code`, `reason`) | cs_agent | `IN_REVIEW` → `TRANSFERRED`. 새 이관 회차. `GCS_L1`과 목록 밖 부서는 422 |
| `POST /recall` (`reason`) | cs_agent | `TRANSFERRED` → `IN_REVIEW`. C/S 회수 |
| `POST /return` (`reason`) | 현재 이관 부서의 dept_staff | `TRANSFERRED` → `IN_REVIEW`. 부서 반송("우리 부서 일이 아님") |
| `POST /dept-result` (`result`, `reply_content`) | 현재 이관 부서의 dept_staff | `TRANSFERRED` → `IN_REVIEW`. 결과는 `SUBMITTED`(수신) |
| `POST /dept-results/{result_id}/review` (`result_version`, `decision`, `note`) | cs_agent | `ACCEPT`: 결과 `ACCEPTED`, 상태 유지. `REWORK`: 결과 `REWORK_REQUESTED`, 같은 부서로 새 회차 `TRANSFERRED`. 보완 요청은 `note` 필수 |
| `POST /close` | cs_agent | `ANSWERED` 또는 `IN_REVIEW` → `CLOSED` |
| `PUT /owner` (`owner_user_id`) | C/S 부서(`GCS_L1`) manager | 상태 변경 없음. 담당자 재배정 |
| `POST /api/v1/cs/tickets/{ticket_id}/resolution-case` | cs_agent | `CLOSED`에서만. 상태 변경 없음. 해결 사례 1건 등록(선택) |

- **수신 ≠ 검토 ≠ 완료**: 부서가 등록한 결과는 `SUBMITTED`이고, cs_agent가 채택해야 `ACCEPTED`가 된다. 채택해도 문의는 `IN_REVIEW`로 남고 종결은 cs_agent가 따로 확정한다.
- **검토된 회신만 반영**: 종결 조건은 `ANSWERED`이거나, `IN_REVIEW`에서 **채택된** 부서 결과 또는 고객 발송 이력(`customer_sent_at`)이 있는 경우다. 아니면 409 `CS_CLOSE_CONDITION_UNMET`. 검토 전(`SUBMITTED`) 결과가 있으면 종결과 새 이관은 409 `CS_DEPT_RESULT_UNREVIEWED`. 반송·회수된 이관은 처리 결과가 아니다.
- **승인 판본 감사**: 모든 요청은 결정한 화면의 업무 판본 `version`을 보낸다. 다르면 409 `CS_WORKFLOW_VERSION_CONFLICT`. 부서 결과 검토는 결과 판본 `result_version`도 보내며 다르면 409 `CS_DEPT_RESULT_VERSION_CONFLICT`, 이미 검토했으면 409 `CS_DEPT_RESULT_ALREADY_REVIEWED`. 전이마다 `cs.audit_events`에 `ticket_workflow`(action `transition`, 바뀐 칸·동작 이름·부서·사유)와 `dept_result`(`create`, `review`와 검토한 결과 판본)를 남긴다. 결과 본문은 감사에 복제하지 않는다.
- **검사 순서**(권한 문서 1.3): 조회 범위 404 → 역할 403 → 상태 409 `CS_INVALID_TRANSITION` → 판본 409 → 입력 422. 이관 이력이 있지만 지금 이관 부서가 아닌 dept_staff의 결과 등록·반송은 403이다.
- **답변·추가 정보 요청 발송**과 **고객 회신 재투입**은 `backend/api/cs/workflow/service.py`의 내부 함수다. 손으로 "보냈음"을 표시하는 API는 없다.
  - `record_customer_send(conn, ticket_id, kind, version, message_id, actor)`: 메일 발송(KAN-74)이 공급자 접수 뒤 같은 트랜잭션에서 호출한다. `IN_REVIEW` → `ANSWERED`(`ANSWER`) 또는 `WAITING_CUSTOMER`(`INFO_REQUEST`), `customer_sent_at` 기록. `version`은 담당자가 승인한 업무 판본이다. 검토 전 부서 결과가 있으면 409 `CS_DEPT_RESULT_UNREVIEWED`. 발송 결과 불명(`UNKNOWN`) 처리는 KAN-74에서 정한다.
  - `reopen_for_customer_reply(conn, ticket_id, message_id, actor)`: 회신 연결(KAN-75·110)이 호출한다. `WAITING_CUSTOMER`·`ANSWERED`·`CLOSED` → `IN_REVIEW`(종결 정보는 지움). 회신은 아무 때나 오므로 판본을 비교하지 않고, 검토 중·이관 중이면 상태를 바꾸지 않는다. 시스템 처리는 `actor`가 null이다.
  - 둘 다 감사에 동작 이름과 `message_id`를 남긴다.
- **부서 사용자의 조회**: `GET .../workflow`와 모든 동작 응답에서 dept_staff와 다른 부서 manager는 **자기 부서의** 이관 회차와 처리 결과만 본다. 다른 부서가 보고한 내용은 보이지 않는다.
- **해결 사례 등록**(워크플로 ⑧): 종결된 문의에만, 문의당 1건(이미 있으면 409 `CS_RESOLUTION_CASE_EXISTS`). 요청은 사람이 확인한 `fault_id`(`FLT-000` 형식), 증상·원인·원인 유형(`SITE_CONDITION`·`HARDWARE`·`SETTING`·`WEAR_PART`·`FIRMWARE`)·조치·현장 방문·처리 부서와 선택 칸(RMA 번호, 펌웨어 판본, 재발 방지), 종결 화면의 업무 판본 `version`이다. `dept_result_id`를 주면 이 문의에서 **채택된** 결과만 쓸 수 있다(아니면 422 `CS_DEPT_RESULT_NOT_ACCEPTED`). 서버가 제품 코드(문의 값), 출처(`manual`/`cs_api`, `SYSTEM`, 문의의 `data_origin`), `resolved_at`(종결 시각), `known_at`(등록 시각, DB 시계 기준), `registered_by`를 정한다. 등록 즉시 문의 상세의 근거 사례로 보인다. 종결 뒤 고객 회신으로 문의가 다시 열려도 등록한 사례는 그대로 남는다(재검토 시 처리는 KAN-110에서 정한다). 업무 판본은 바뀌지 않고, 감사에 `resolution_case`와 등록한 업무 판본을 남긴다. 서비스 계정은 사례를 삭제할 수 없다. 마이그레이션은 `20261028_KAN-108_resolution_cases.sql`이다.
- **담당자**: C/S 서비스는 identity 스키마를 읽을 수 없어 새 담당자의 역할을 서버에서 확인하지 않는다. 화면은 등록된 cs_agent만 고르게 한다.
- **부서 코드**: `backend/shared/departments.py`가 `identity.departments`와 같은 목록을 두고, 테스트가 두 목록이 같은지 확인한다.
- 서비스 계정(cs_app)은 업무 상태·이관·부서 결과 행을 삭제할 수 없다. 문의당 열린 이관은 하나다(부분 유일 인덱스).

## 설치 장비 확인과 같은 장비 이력 (KAN-102)

원본: backend/api/cs/units/schemas.py 및 routes.py. 기획서 4.3(고객 이력 프로필)과 6.1의 가정 테이블 `installed_units`, `ticket_unit_link`를 따른다. 마이그레이션은 `infra/migrations/cs/20261027_KAN-102_installed_units.sql`이다.

| 경로 | 응답 | 설명 |
| --- | --- | --- |
| `GET /api/v1/cs/tickets/{ticket_id}/units` | `SuccessResponse[TicketUnitList]` | 문의와 고객·제품이 같은 장비와 이미 연결된 장비. 장비마다 `matches_ticket`과 현재 연결(`link`, 없으면 null)을 준다 |
| `PUT /api/v1/cs/tickets/{ticket_id}/units/{unit_id}` | `SuccessResponse[TicketUnit]` | 연결을 `CANDIDATE`·`CONFIRMED`·`REJECTED`로 기록한다. 새 연결은 `version: null`, 기존 연결은 저장된 판본을 보낸다. cs_agent만 쓸 수 있다 |
| `GET /api/v1/cs/tickets/{ticket_id}/unit-history` | `SuccessResponse[UnitHistory]` | 확정 장비의 이전 문의. 확정 장비가 없으면 `NO_CONFIRMED_UNIT`과 빈 목록 |

- **고객·장비 일치 확인**: 장비의 `customer_external_id`·`product_code`가 문의와 다르면 후보·확정으로 둘 수 없다(422 `CS_UNIT_MISMATCH`). 거절은 항상 기록할 수 있다. 없는 장비는 404 `CS_UNIT_NOT_FOUND`.
- **확정은 문의당 1대**: 다른 장비가 확정돼 있으면 409 `CS_TICKET_UNIT_ALREADY_CONFIRMED`. 먼저 기존 확정을 거절로 바꾼다. DB의 부분 유일 인덱스도 같은 제한을 둔다. 여러 대 중 특정 장비를 모르면(시나리오 S09) 후보로만 둔다.
- **판본**: 연결마다 `version`이 있다. 새 연결인데 이미 있거나, 판본이 다르면 409 `CS_UNIT_LINK_VERSION_CONFLICT`. 값이 같으면 판본과 결정 시각을 유지한다. `CONFIRMED`·`REJECTED`는 결정한 사용자(`decided_by`)와 시각을 남기고, `CONFIRMED`의 `repeat_type`은 `DIFFERENT_UNIT`일 수 없다(422). 변경은 `cs.audit_events`에 `entity_type = 'ticket_unit_link'`로 기록한다.
- **정정 잠금**: 확정 장비가 있는 문의의 `customer_external_id`·`product_code` 정정은 409 `CS_TICKET_UNIT_LINKED`. 연결을 거절로 바꾼 뒤 정정한다. 문의 행을 잠근 상태에서 연결과 정정을 처리해 서로 끼어들지 않는다.
- **다른 장비 이력 복사 차단**: 이력은 이 문의의 확정 장비와, 그 장비가 교체한 장비(`replaced_by_unit_id` 역방향)에 **확정** 연결된 문의만 담는다. 같은 고객·제품의 다른 장비, 후보·거절 연결은 담지 않는다(시나리오 S13: CSV1-000991의 2024-05 설치 장비를 CSV1-003317에 쓰지 않는다). 교체 전 장비의 문의는 `relation = REPLACED_UNIT`으로 표시하고, 교체 전 장비의 설치일·출하일 같은 값은 `unit`에 섞지 않는다.
- **시점 제한**: 이 문의 접수 시각보다 먼저 접수·인지된 문의만, 해결 사례는 그 시각 이전에 알려진 것만 담는다. 최대 100건, 최신순.
- **접근**: 장비 목록·연결·이력은 전체 문의를 보는 사용자(cs_agent, C/S 부서 manager)만 쓴다. 이관 부서 사용자는 문의 상세는 볼 수 있지만 이 경로는 404다. 이력에 그 부서로 이관되지 않은 이전 문의가 섞일 수 있기 때문이며, 범위로 거르는 기능은 필요할 때 추가한다. 장비 연결 기록은 cs_agent만 한다.
- **데이터**: 장비는 CRM 설치기반을 가정한 자료(`derivation_kind = ASSUMED`, 가상 시리얼 `VSN-제품약어-번호`)이며 어댑터가 적재한다. C/S 서비스 계정은 장비를 읽기만 하고, 연결은 삭제할 수 없다. 이 PR은 장비 시연 데이터를 생성하지 않는다. 같은 장비 여부의 평가 정답은 모델 입력·검색과 분리된 별도 자료로 둔다.

## 문의 분류 출력 계약

최종 C/S 기획서 4.2의 다섯 필드를 사용한다. 아래는 KAN-103의 업무 계약이다. 입력·출력 검사의 첫 단위만 구현됐으며 업무 API에 모델 분류가 연결됐다는 뜻은 아니다. priority, assigned_dept, product_family, 누락 정보와 SLA는 프로그램이 계산한다. 보조 유형이 있다고 별도 문의를 생성하거나 담당 부서를 자동 변경하지 않는다.

| 필드 | 형식과 의미 |
| --- | --- |
| `category_code` | 주된 처리 의도 하나. `TECH_SPEC`, `INSTALL`, `WARRANTY`, `ORDER_LEAD`, `MANUAL`, `DEFECT`, `COMPLIANCE`, `QUOTE`, `FW_UPDATE`, `RETURN` 중 하나 |
| `secondary_category` | 원문에 별도의 처리 의도가 명시된 경우 보조 유형 하나. 주분류와 같은 enum이며 주분류와 같은 값은 금지. 복합 의도가 없으면 JSON null. 여러 보조 의도 중 선택 및 정답 검수 기준은 KAN-95에서 잠금 |
| `urgency_flag` | urgency_codebook의 `SAFETY`, `SITE_DOWN`, `PROD_IMPACT`, `COMMISSION`, `DEADLINE`, `NONE` 중 하나. 우선순위 자체를 생성하지 않음 |
| `provided_info` | 원문에서 제공 여부를 확인한 정보 항목 코드의 중복 없는 배열. info_field_codebook에 있는 코드만 사용하며 없으면 `[]`. 시리얼 원값이나 개인정보를 반환하지 않음 |
| `return_subtype` | 반품 의도가 확인되면 품질·불량 반품 `DEFECT` 또는 비불량 반품 `NON_DEFECT`. 반품 의도가 없거나 근거가 부족하면 null. POL-RETURN 및 routing_rules의 NON_DEFECT 예외에 맞춰 KAN-95에서 정답 매핑 검수 |

다섯 키는 모두 존재해야 하며 허용되지 않은 키·enum, 키 누락과 잘못된 타입은 원시 스키마 위반으로 기록한다. 코드 값은 표시용 한글 이름과 분리한다. 원천 CSV의 빈 값은 어댑터에서 null 또는 빈 배열로 정규화하고 정답 원본을 수정하지 않는다. 분류 실패·보류는 실행 상태로 기록하며 category에 임의의 UNKNOWN 값을 추가하거나 성공으로 숨기지 않는다. return_subtype이 null인 반품은 비불량으로 간주하지 않는다.

규칙 평가는 주분류와 긴급도 및 확인된 입력을 사용한다. 반품 하위유형이 불명확하면 예외 부서를 추측하지 않고 기본 정책과 사람의 확인 절차를 사용한다. secondary_category를 규칙 입력으로 추가하는 변경은 별도 계약과 회귀 검증이 필요하다.

## 규칙 결과: 우선순위·제품군·담당 부서·SLA (KAN-104)

원본: `backend/engine/cs/rules.py`, 정책표 `backend/engine/cs/policy/`. LLM을 쓰지 않는 순수 함수이며 결과마다 `POLICY_VERSION`(정책표 해시)을 붙인다. 규칙 결과를 문의에 저장하는 일은 문의 처리 작업(KAN-107)이 한다.

| 함수 | 입력 | 결과 |
| --- | --- | --- |
| `decide_priority` | 주분류, 긴급도 | `P1`~`P4`와 규칙 ID(`PR-01`~`PR-07`, 위에서부터 처음 맞는 규칙) |
| `product_family` | 제품 코드 | 제품군 |
| `route` | 제품군, **주분류**, 반품 하위유형 | 담당 부서, 규칙 ID·출처, 에스컬레이션 후보와 조건, 사람 확인 사유 |
| `sla_target`, `due_times` | 주분류, 고객 등급, 우선순위, 접수 시각 | 첫 응답·해결 목표 시간과 기한 |
| `sla_elapsed_hours`, `sla_status` | 접수·종료 시각, 고객 대기 시간, 해결 여부 | 경과 시간, `MET`/`BREACHED`/`AT_RISK`/`ON_TRACK`/`N/A` |
| `decide` | 위 입력 전부 | 위 결과 전부 |

- **보조 분류는 입력이 아니다.** `decide`에는 `secondary_category` 인자가 없다. 반품 하위유형은 주분류가 `RETURN`일 때만 쓰며, 다른 주분류에 보조 반품 의도가 있어도 부서가 바뀌지 않는다.
- **반품 하위유형 미확인 ≠ 비불량.** `NON_DEFECT`로 확인된 반품만 SCM(`RT-ALL-RETURN-NON_DEFECT`)으로 간다. 하위유형이 null이면 제품군 기본 반품 부서를 쓰고 `review_reasons`에 `RETURN_SUBTYPE_UNCONFIRMED`를 넣어 사람이 확인하게 한다.
- **에스컬레이션은 후보다.** `escalation_dept`와 조건 문구를 함께 돌려줄 뿐 자동으로 이관하지 않는다. 조치 유형(RMA·현장·안전)에 따른 에스컬레이션 규칙은 이 함수가 판단하지 않는다.
- **SLA 시계**: 모든 정책이 24x7 달력 시간이다. 주말·야간 보정이 없고, 고객 회신 대기 시간만 경과 시간에서 뺀다. 위험(`AT_RISK`)은 목표의 75% 이상, 해결 전 목표 이상은 `BREACHED`, 해결 후 목표 이하(같음 포함)는 `MET`. 고객 무응답 종결은 `N/A`.
- **무효 입력**: 모르는 분류·긴급도·등급·우선순위·제품·제품군·하위유형, 시간대 없는 시각, 종료가 접수보다 이른 시각, 음수 대기 시간은 `RuleInputError`. 추측해서 값을 채우지 않는다.
- **정책표 변경**: 파일 해시와 코드가 맞지 않으면 C/S 앱이 시작할 때(`create_app`) 실패한다. 절차는 `backend/engine/cs/policy/README.md`.
- **제공 데이터 재현**(`tests/cs/test_rules_reproduction.py`): 데이터 관리자가 물리적으로 분리한 학습·검증 파일과 manifest를 `CS_DEV_MANIFEST`로 지정했을 때만 실행한다. 형식과 검사는 `tests/cs/cs_dev_data.py`가 정한다. 파일마다 행을 읽기 전에 manifest의 SHA-256과 비교하고, manifest가 시험 분할을 포함하거나 파일에 시험 행이 있으면 거부한다. 통합 `tickets.csv`는 쓰지 않는다([평가 명세](../../docs/evaluation.md)). 반품 하위유형 정답(KAN-95)은 manifest의 `return_subtype_gold`(`ticket_id,return_subtype`, 해시·검수 판본 필수)로 받는다. 중복 ID, 대상 반품 집합과 다른 행(누락·추가), 허용값 밖의 값은 거부하고, 빈 값은 미확인으로 따로 보고한다.
  - 10/27 통합 파일로 실행한 기록(시험 행 파싱 포함, 계산은 학습·검증 3,403건): 우선순위 `priority_adjusted_by_agent=N` 3,303건 일치(1.000), 제품군 3,403건, 반품 외 담당 부서·규칙 ID 3,105건, SLA 목표 3,403건, 경과 시간·상태 3,400건 일치. 반품 298건은 두 정책 부서 중 하나와 일치. `customer_wait_hours`가 음수인 3건(CSV1-003890, CSV1-003946, CSV1-003951, 첫 응답이 기준일 뒤)은 경과 시간 검사에서 ID로 제외한다. 분리 자료로 다시 실행하기 전까지 이 숫자는 회귀 참고값이다.
  위 수치는 PR70의 기존 검사 보고다. 재현 테스트는 통합 CSV의 행을 파싱한 뒤 train/valid를 선택하므로 시험 비접근을 보장하지 않는다. 이번 C/S 감사에서는 재실행하지 않았으며, [사용 이력 판정과 분리 입력 인수 조건](../../docs/evaluation.md#cs-사용-이력과-검수-준비-kan-83)을 먼저 적용한다.

## 입력과 검증

build_input() 한 곳에서 채널, 본문 언어, 제목, 본문만 분류 입력으로 만든다. 제목 없는 채팅은 본문으로 판정한다. 정답 category·secondary_category·urgency·provided_info와 사후 해결 기록, 별도 에러코드·펌웨어 칸을 입력에 섞지 않는다. PHONE 본문의 `Urgency: <정답>` 주석은 원본을 보존한 채 모델용 입력에서 제거하며 처리 판본을 기록한다. 개인정보·시리얼 원값을 마스킹하되 정보 제공 여부를 판정할 수 있는 표시는 보존한다.

KAN-103의 완료에는 정상·빈 보조 유형·복합 의도·불명확한 반품, 잘못된 enum·키·타입, 정답 주입과 PHONE 주석, 시리얼 비노출 및 FLT-042 인용 차단 회귀가 포함된다. 원시 출력과 재시도·폴백 후 결과를 구분하며, 품질 계산과 분모는 [평가 명세](../../docs/evaluation.md)를 따른다. 이 계약의 확장이 입력 화이트리스트나 안전 정책을 확장하지 않는다.

수정 후 `python3 scripts/workspace.py contracts cs`를 실행한다.


### KAN-103 입력·원시 출력 검사 첫 단위

`backend/engine/cs/classification.py`는 모델·DB·파일에 접근하지 않는 순수 함수다. KAN-92의 적재/정규화 어댑터를 대체하지 않는다. `build_input(record, sensitive_values=...)`는 정규화 레코드의 channel/body_language/subject/body만 선택한다. subject는 null을 허용하지만 네 키 모두 필요하다. body_language는 en/ko/en-US 같은 언어 태그 형식이며 빈/주석만 있는 본문은 거부한다. 원본은 수정하지 않는다.

PHONE의 제목·본문에서 알려진 긴급도 enum을 가진 `Urgency: <값>` 주석을 대소문자와 공백 차이를 허용해 제거한다. 반환값의 payload만 모델 입력이며 version(`cs-classification-input-v2`)과 removed_annotations는 실행 감사 기록용이다. 다른 채널의 같은 문구는 이 규칙으로 삭제하지 않는다.

v2는 `*`·`_`·백틱으로 감싼 라벨/값과 전각 콜론(`：`)도 처리하고, 주석만 있던 본문은 마크다운 표식을 남겨 성공으로 통과시키지 않는다. `Urgency:` 또는 `Urgency：` 라벨이 남는 미지원 값(예: `HIGH`, `SAFETY-extra`, `SITE_DOWN/SAFETY`)은 일부 enum만 지우지 않고 원문 없는 `CS_UNSUPPORTED_PHONE_ANNOTATION`으로 보류한다. `Note-Urgency:`·`x*Urgency:`처럼 접두사가 붙어 제거 형식 밖에 있는 경우에도 남은 라벨을 찾아 보류한다. 라벨과 콜론 또는 값 사이가 줄바꿈으로 나뉜 경우도 자동 삭제하지 않고 보류한다. 줄바꿈을 건너 다음 줄의 단어를 주석 값으로 가져오지 않는다. 호출자는 이를 입력 실패로 기록해야 하며 원본을 다시 모델에 보내는 폴백으로 우회하지 않는다. v1과 v2 결과는 입력 처리 판본으로 구분한다. 이 처리는 명시한 라벨 형식에 한정하며 모든 누설 변형을 탐지한다는 뜻은 아니다.

민감값은 어댑터가 식별한 원값→SERIAL/PERSONAL 매핑을 명시적으로 전달받아 긴 값부터 정확 일치로 마스킹한다. 이미 만들어진 `[PERSONAL_PROVIDED]`·`[SERIAL_PROVIDED]` 표시는 시리얼 라벨 뒤에 있더라도 종류를 바꾸지 않는다. 이메일 형식과 `serial:`, `Serial number:`, `S/N:`, `시리얼 번호:` 같은 명시 표기도 마스킹하고 제공 표시를 남긴다. **임의 이름·주소·전화번호·표기 없는 시리얼 전체를 자동 탐지하는 개인정보 처리기가 아니다.** 자동 시리얼 표기 검사는 공백 없는 한 토큰만 대상으로 하므로 `Serial number: AB 1234` 같은 공백 포함 원값은 반드시 sensitive_values에 전체 값으로 전달해야 한다. 실제 데이터 연결 전 KAN-92와 식별/마스킹 범위를 검증해야 하며 이 함수만으로 실제 문의의 안전한 외부 전송이 완성됐다고 판단하지 않는다. 이번에는 합성 입력만 검사하며 실제 모델 경로에 연결하지 않는다.

`validate_output(raw, allowed_info_codes=...)`는 원시 JSON 전체를 검사한다. 검수된 info_field_codebook 코드 집합을 필수 인자로 받고 기본 코드나 추측 값을 만들지 않는다. 테스트의 TEST_SERIAL/TEST_PHOTO는 검사 전용이며 실제 코드북이 아니다. 다섯 키 필수·추가 키 금지·엄격한 타입/enum·중복 JSON 키/정보 코드 금지·주/보조 분류 중복 금지를 검사한다. 반품 의도가 주/보조 분류에 없으면 return_subtype의 비null 값을 거부한다. 반품의 null은 그대로 보존한다.

원시 위반은 원문 없는 `CS_INVALID_OUTPUT` 오류이며 자동 JSON 수선·재시도·폴백은 하지 않는다. 호출자가 최초/재시도별 실패를 별도로 기록해야 한다. 코드 구조 검사는 의미상 정답이나 품질 합격을 뜻하지 않는다. 실제 코드북/어댑터 연결, 사례별 실행·채점·최초/재시도 저장, 개인정보 처리 확대, FLT-042 검색·인용 차단, 화면 연결은 남아 있으므로 KAN-103 전체 완료가 아니다.
