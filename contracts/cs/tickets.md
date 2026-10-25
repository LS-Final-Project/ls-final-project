# C/S 문의 계약 시작점

원본: backend/api/cs/tickets/schemas.py 및 routes.py. 예시: backend/api/cs/examples.py.

## 문의 조회·등록·수정·감사 (KAN-102)

| 경로 | 응답 | 설명 |
| --- | --- | --- |
| `GET /api/v1/cs/tickets?limit=&cursor=` | `SuccessResponse[TicketList]` | 접수 시각 최신순. `limit` 1~100(기본 50). `next_cursor`를 그대로 다음 요청의 `cursor`로 보낸다. 잘못된 커서는 400 `CS_INVALID_CURSOR` |
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
- 접근 검사(KAN-78, [권한 문서](../platform/permissions.md) 1.3·1.4·4.1): 사용자 정보가 없거나 잘못되면 401. 조회는 `cs_agent`와 C/S 부서(`GCS_L1`)를 관리하는 manager는 전체, dept_staff와 다른 부서 manager는 자기(관리) 부서로 이관된 문의만이며 이관 부서 칸이 생기기 전(KAN-108)에는 빈 목록이다. 범위 밖 문의는 없는 문의와 같은 404 `CS_TICKET_NOT_FOUND`. sales의 목록과 cs_agent가 아닌 사용자의 등록·정정은 403.
- 업무 중계가 비활성일 때 위 경로는 `local`·`test`에서만 응답하고 `production`에서는 DB 조회 전에 404 `NOT_FOUND`로 닫는다. KAN-178의 명시 활성화는 [플랫폼 호출자 검증](../platform/auth.md#업무-json-중계와-호출자-검증-kan-178)과 기존 사용자·업무 권한 검사를 함께 요구한다. OpenAPI 상태는 `partial`이다. 설비·장비 일치 확인은 KAN-102의 후속 PR에서 추가한다.

중계를 켠 C/S는 `CS_MANUAL_DATA_ORIGIN=demo` 또는 `real`을 명시해야 시작한다.
공용 시연은 production 인증 정책에서도 demo를 유지하고, 실제 문의만 처리하는 배포에서
real을 선택한다. 인증 환경이나 요청의 출처 필드로 실제 여부를 추정하지 않는다.
한 배포의 수동 접수 출처는 한 값이며 실제·시연 자료를 섞어 접수하는 기능은 제공하지 않는다.
설정 변경은 이후 새 문의에만 적용하며 기존 행을 일괄 승격·변경하지 않는다.

## 문의 분류 출력 계약

최종 C/S 기획서 4.2의 다섯 필드를 사용한다. 아래는 KAN-103에서 구현할 업무 계약이며 현재 목록 stub에 분류 기능이 구현됐다는 뜻은 아니다. priority, assigned_dept, product_family, 누락 정보와 SLA는 프로그램이 계산한다. 보조 유형이 있다고 별도 문의를 생성하거나 담당 부서를 자동 변경하지 않는다.

| 필드 | 형식과 의미 |
| --- | --- |
| `category_code` | 주된 처리 의도 하나. `TECH_SPEC`, `INSTALL`, `WARRANTY`, `ORDER_LEAD`, `MANUAL`, `DEFECT`, `COMPLIANCE`, `QUOTE`, `FW_UPDATE`, `RETURN` 중 하나 |
| `secondary_category` | 원문에 별도의 처리 의도가 명시된 경우 보조 유형 하나. 주분류와 같은 enum이며 주분류와 같은 값은 금지. 복합 의도가 없으면 JSON null. 여러 보조 의도 중 선택 및 정답 검수 기준은 KAN-95에서 잠금 |
| `urgency_flag` | urgency_codebook의 `SAFETY`, `SITE_DOWN`, `PROD_IMPACT`, `COMMISSION`, `DEADLINE`, `NONE` 중 하나. 우선순위 자체를 생성하지 않음 |
| `provided_info` | 원문에서 제공 여부를 확인한 정보 항목 코드의 중복 없는 배열. info_field_codebook에 있는 코드만 사용하며 없으면 `[]`. 시리얼 원값이나 개인정보를 반환하지 않음 |
| `return_subtype` | 반품 의도가 확인되면 품질·불량 반품 `DEFECT` 또는 비불량 반품 `NON_DEFECT`. 반품 의도가 없거나 근거가 부족하면 null. POL-RETURN 및 routing_rules의 NON_DEFECT 예외에 맞춰 KAN-95에서 정답 매핑 검수 |

다섯 키는 모두 존재해야 하며 허용되지 않은 키·enum, 키 누락과 잘못된 타입은 원시 스키마 위반으로 기록한다. 코드 값은 표시용 한글 이름과 분리한다. 원천 CSV의 빈 값은 어댑터에서 null 또는 빈 배열로 정규화하고 정답 원본을 수정하지 않는다. 분류 실패·보류는 실행 상태로 기록하며 category에 임의의 UNKNOWN 값을 추가하거나 성공으로 숨기지 않는다. return_subtype이 null인 반품은 비불량으로 간주하지 않는다.

규칙 평가는 주분류와 긴급도 및 확인된 입력을 사용한다. 반품 하위유형이 불명확하면 예외 부서를 추측하지 않고 기본 정책과 사람의 확인 절차를 사용한다. secondary_category를 규칙 입력으로 추가하는 변경은 별도 계약과 회귀 검증이 필요하다.

## 입력과 검증

build_input() 한 곳에서 채널, 본문 언어, 제목, 본문만 분류 입력으로 만든다. 제목 없는 채팅은 본문으로 판정한다. 정답 category·secondary_category·urgency·provided_info와 사후 해결 기록, 별도 에러코드·펌웨어 칸을 입력에 섞지 않는다. PHONE 본문의 `Urgency: <정답>` 주석은 원본을 보존한 채 모델용 입력에서 제거하며 처리 판본을 기록한다. 개인정보·시리얼 원값을 마스킹하되 정보 제공 여부를 판정할 수 있는 표시는 보존한다.

KAN-103의 완료에는 정상·빈 보조 유형·복합 의도·불명확한 반품, 잘못된 enum·키·타입, 정답 주입과 PHONE 주석, 시리얼 비노출 및 FLT-042 인용 차단 회귀가 포함된다. 원시 출력과 재시도·폴백 후 결과를 구분하며, 품질 계산과 분모는 [평가 명세](../../docs/evaluation.md)를 따른다. 이 계약의 확장이 입력 화이트리스트나 안전 정책을 확장하지 않는다.

수정 후 `python3 scripts/workspace.py contracts cs`를 실행한다.
