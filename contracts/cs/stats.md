# C/S 집계 계약 (KAN-111, KAN-233 확장)

원본: `backend/api/cs/stats/schemas.py`, `routes.py`, `repository.py`. [권한 문서](../platform/permissions.md) 4.1의 "직원별·고객별 대응 집계"와 5.9의 고객 이력·관리자 현황(F13, KAN-141)이 쓴다. 새 테이블 없이 **DB에 저장된 기록만** 센다.

| 경로 (`/api/v1/cs/stats` 아래) | 허용 | 내용 |
| --- | --- | --- |
| `GET /overview?start&end&dept&customer` | manager(전체), cs_agent(자기 담당만) | 문의 접수·종결·진행 수, 상태별·출처별 수, 진행 문의 경과 기간, 이관 부서별 결과, 고객별 응대 부담 상위 10, 개선요청 상태·유형별 수(manager만), SLA 처리 현황 `sla`(KAN-233, 아래) |
| `GET /owners?start&end&dept` | manager | C/S 담당자별 접수·종결 수(기간 적용)와 **현재 진행 수**(기간과 무관한 지금의 부담), **현재 상태별 수** `by_state`(기간과 무관, 여섯 상태 모두), 오늘 기한·기한 초과·평균 처리일·진행 개선요청(KAN-233, 아래). 기간 안 접수·종결이 없어도 진행 중인 문의가 있으면 목록에 나온다. 담당자 없는 문의는 `owner_user_id: null`. cs_agent 자기 화면의 "내 담당 상태별 수"는 기간 없이 부른 `GET /overview`의 `tickets.by_state`(자기 담당만)를 쓴다 |
| `GET /customers/{customer_external_id}?start&end` | 전체 문의를 보는 사용자(cs_agent, C/S 부서 manager) | 한 고객의 문의 수·상태별·제품별, 최근 문의 200건, 설치 장비와 장비별 확정 문의 수, 근거로 연결된 개선요청 |

- **기간**: `start`는 포함, `end`는 미포함이며 시간대가 있어야 한다. 운영 현황의 `open`은 기간 안에 접수된 문의 중 진행 중인 수이고, 담당자별의 `open`은 기간과 관계없는 현재 진행 수다. 접수 수는 접수 시각, 종결 수는 종결 시각, 이관 결과는 이관 시각, 개선요청은 작성 시각으로 기간을 적용한다. 진행 문의 경과 기간은 기간 끝(없으면 현재)에서 잰다. 잘못된 기간은 422 `CS_INVALID_PERIOD`.
- **부서**: `dept`는 이관 부서 코드(`GCS_L1` 제외)다. 그 부서로 한 번이라도 이관된 문의만 센다. 개선요청은 그 부서가 제안·수신 부서인 요청을 센다.
- **고객**: `customer`를 주면 그 고객의 문의와, 그 고객 문의를 근거로 가진 개선요청만 센다. `customer` query와 고객 이력 경로 값은 고객 ID 규약([문의 계약](tickets.md))을 따르며, 아니면 query는 422, 경로는 422 `CS_INVALID_CUSTOMER`다.
- **합성 기록 표시**: 문의 수를 `data_origin`(synthetic/demo/real)별로 나눠 준다. 장비 이력에는 `derivation_kind`(가정 자료는 ASSUMED)를 함께 준다.
- **과거 범위를 복원하지 않음**: `data_range`는 필터에 맞는 첫·마지막 접수 시각이다. 그 이전 기간이나 매출·성과 같은 저장되지 않은 값은 만들지 않는다.
- **미확인 표시**: 아직 저장되지 않은 지표는 `unconfirmed`로 알린다. 운영 현황(`/overview`)은 KAN-233부터 SLA를 조회 때 계산하므로 `RULE_ASSIGNED_DEPT`(규칙상 담당 부서)만 남는다. 고객 이력은 SLA를 보여 주지 않아 `SLA_DUE`·`RULE_ASSIGNED_DEPT`를 그대로 준다. 부서 필터는 이관 부서 기준이다. 접수 후 경과 기간(`open_age`)은 SLA와 별개로 그대로 준다.
- **열린 개선요청**: 완료되지 않았고 반려 종료(현재 회차 모든 부서가 반려하고 반려 확인됨)가 아닌 요청. 수신 부서가 비어 있는 요청 수(`receiver_unconfirmed`)도 준다.
- **권한**: manager의 집계는 상세 조회 권한을 주지 않는다. 집계 응답에는 원문·파일이 없고, 고객 이력(문의 목록)은 전체 문의를 보는 사용자만 볼 수 있다. 다른 역할은 403.

## 업무 통계 확장 칸 (KAN-233)

관리자 업무 통계 화면(CS0901)용으로 기존 응답에 **선택 칸**을 더했다. 새 경로·새 테이블·마이그레이션은 없고, 기존 칸의 뜻은 바뀌지 않는다. 권한은 위 표와 같다(`overview`는 manager 전체·cs_agent 자기 담당, `owners`는 manager). 서버가 칸을 주지 않으면(null·없음) 화면은 0이 아니라 '미확인'으로 보인다. 시험: `tests/cs/test_stats_sla.py`(DB 없음), `tests/cs/test_stats_sla_db.py`.

### SLA (`overview.sla`, 원본 `backend/api/cs/stats/sla.py`)

- **기한 계산**: SLA 기한은 저장하지 않는다. 분석 화면(`GET /tickets/{id}/analysis`의 `rules.first_response_due`·`resolution_due`, [분석 계약](analysis.md))과 똑같이, 최신 VALID 분류와 모든 사람 정정으로 규칙(`backend/engine/cs/rules.py`)을 돌려 AI 기한과 정정 기한 중 **이른 쪽**을 쓰고 접수 시각부터 센다. 고객 등급이 없으면 표준 등급으로 계산한다. 고객 대기 시간 정지는 분석 화면처럼 적용하지 않는다(24x7 달력 시간). 우선순위는 유효 값(정정 반영) 규칙의 우선순위다.
- **SLA 미확인** `unknown`: 분류가 없거나(VALID 실행·정정 모두 없음) 규칙 밖 값이라 기한을 못 구한 문의. 기간 안에 접수되거나 종결된 것 중 이런 문의 수이며 다른 SLA 칸에는 넣지 않는다. 추정하지 않는다.
- **첫 응답 시각**: `cs.ticket_customer_sends`에서 메일 플랫폼이 받아들이고 업무 상태에 반영된(`recorded_at` 있음) 첫 고객 메시지(답변 `ANSWER` 또는 추가 정보 요청 `INFO_REQUEST`)의 `accepted_at`(없으면 `recorded_at`). `asked_info_codes`와 같은 기준이다. 승인만 되고 나가지 않은 메시지, 다른 채널로 복사해 보낸 회신은 기록이 없어 세지 않는다.
- **첫 응답 준수율** `first_response`(기간 안 접수 문의): `met` 첫 응답이 첫 응답 기한 안, `breached` 기한 뒤에 응답했거나 응답 없이 진행 중인데 기한이 지남, `pending` 응답 없이 진행 중이고 아직 기한 전(분모 제외), `unrecorded` 고객 메시지 기록 없이 종결됨(분모 제외). 화면 준수율 = met / (met + breached).
- **해결 준수율** `resolution`(기간 안 종결 문의): 종결 시각이 해결 기한 안이면 `met`, 아니면 `breached`. 종결 사유가 저장되지 않아 "고객 무응답 종결(N/A)"은 구분하지 못하고 모두 센다.
- **기한 임박** `at_risk`·**기한 초과** `overdue`(기간 안 접수, 지금 진행 중): `measured_at`(DB 현재 시각)에서 잰다. 해결 목표 시간의 **75% 이상 경과**(= 남은 시간 25% 이하, 기획서 AT_RISK)면 임박, 해결 기한을 지나면 초과(`rules.sla_status`).
- **우선순위별** `by_priority`: P1~P4 모두. `met`·`breached`는 기간 안 종결의 해결 준수·초과, `at_risk`·`overdue`는 위 진행 문의.
- **기한 초과 문의** `overdue_tickets`: 위 초과 문의를 오래 넘긴 순으로 최대 10건. 문의 ID·번호, 제목, 우선순위, 해결 기한, 초과 시간(`overdue_hours`, 소수 1자리), 담당자 ID. **제목은 그 문의를 상세 조회할 수 있는 사용자에게만** 주고(전체 문의 조회자, 또는 관리 대상 부서로 이관된 문의), 그 밖에는 null이다([권한 문서](../platform/permissions.md) 1.1 집계권은 상세 조회를 주지 않음). 본문은 주지 않는다.

### 담당자별 (`owners.items[]`)

- `due_today`: 지금 진행 중이고 해결 기한이 **아직 안 지났으며 오늘(Asia/Seoul 날짜) 안**인 문의. `overdue`: 진행 중이고 해결 기한이 지난 문의. 둘은 겹치지 않는다. `sla_unknown`: 진행 중인데 SLA를 못 구한 문의. 세 칸 모두 `open`처럼 기간과 무관한 현재 값이다.
- `avg_handle_days`: 기간 안 종결 문의의 접수→종결 평균(일, 소수 1자리). 없으면 null.
- `improvements_open`: `cs_owner`가 그 담당자이고 열린(완료·반려 종료 아님) 개선요청 수. 기간과 무관하고 부서 필터(제안·수신 부서)는 적용한다. 문의가 없는 담당자는 목록에 나오지 않는다.

### 부서별 (`overview.departments[]`, 기간 안 이관 기준)

- `waiting`: 그 이관이 지금 `OPEN`(결과 없음·종료 안 됨). `waiting_over_7_days`: `OPEN`이고 이관 후 7일 넘음(지금 기준). `avg_reply_days`: 이관→부서 결과 제출(`dept_results.submitted_at`) 평균 일수(결과 있는 이관만, 소수 1자리), 없으면 null. 기존 `transferred`(요청), `results_submitted`(답변), `results_accepted`(확인 완료), `returned_or_recalled`(반송/취소)는 그대로다.

### 개선요청 (`overview.improvements`, manager만)

- `completed_in_period`: 지금 `COMPLETED`이고 `completed_at`이 기간 안인 요청(작성 시각 무관). 나머지 칸은 기존처럼 작성 시각 기준이다.
- `info_requested`: 열린 요청 중 현재 회차 수신 부서에 `INFO_REQUESTED`가 있는 요청 수. `rejected`: 현재 회차 수신 부서에 `REJECTED`가 있는 요청 수(반려 종료 포함).
- `reply_overdue`: 열린 요청 중 현재 회차 수신 부서의 `reply_due_at`이 지났는데 그 회차에 그 부서 회신(`dept_replies`)이 없는 요청 수.
- `recurrence_after_completion`: 완료(`COMPLETED`) 고장 요청마다 KAN-117 재발 판정(`followup-signals`와 같은 계산, 지금 시각)을 돌린 결과. `checked` 판정한 요청 수, `recurrence` 판정이 `RECURRENCE`인 수, `not_applicable` 판정 대상이 아닌 완료 문서 요청 수. 계산하지 못하면 null.
