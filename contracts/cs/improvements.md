# C/S 개선요청 계약 (KAN-112)

원본: `backend/api/cs/improvements/schemas.py`, `routes.py`, `next_owner.py`. 기획서 4.4·6.1과 [권한 문서](../platform/permissions.md) 2.2·2.2.1을 따른다. 마이그레이션은 `infra/migrations/cs/20261028_KAN-112_improvement_requests.sql`이다.

이 티켓은 **저장 구조와 작성 API**다. 검토 완료·전달·부서 회신·조치·후속 확인·완료 같은 상태 전이는 KAN-116, 후보 자동 생성은 KAN-113, 재요청·재발 재개는 KAN-117이 같은 테이블을 쓴다.

## 저장 구조

| 테이블 | 내용 |
| --- | --- |
| `cs.improvement_requests` | 요청서. 유형(`FAULT`/`DOCUMENT`), 묶음 키(제품 + 고장 ID 또는 문서 신호), 사용 조건, 펌웨어 수정판, 상태, 판본, 회차, C/S 담당자, 내용(검토할 문제·확인된 사실·불일치·부족한 정보·단정하지 않는 내용·검토 과제·제안 부서), 검토·전달·후속 확인·완료 기록 칸 |
| `cs.improvement_request_versions` | 판본마다 내용 전체(작성 칸, 담당자, 유효한 근거 목록). 한 번 쓰면 바꾸지 않는다 |
| `cs.request_evidence` | 근거 문의(고장은 해결 사례 포함). 제외해도 행은 남고 제외자·시각·사유를 기록 |
| `cs.request_departments` | 회차별 수신 부서 상태(2.2.1), 이월 여부, 보낸 판본, 회신 기한, 반려 확인 |
| `cs.dept_replies` | 부서 회신(수신 확인·채택·추가 정보 요청·반려)과 **답한 판본**. 반려는 사유 필수 |
| `cs.request_actions` | 부서 조치(내용·적용 대상·실제 적용일·근거). 고객 한 명의 수리와 분리 |

서비스 계정(cs_app)은 이 테이블들의 행을 지울 수 없고, 판본·회신·조치는 고칠 수 없다.

## API (`/api/v1/cs/improvement-requests`)

| 경로 | 허용 | 설명 |
| --- | --- | --- |
| `GET` | cs_agent, manager, dept_staff | 최신순 최대 200건. 조회 범위만 |
| `POST` | cs_agent | 초안(`DRAFT`, 판본 1) 작성. 작성자가 C/S 담당자. 출처는 수동 문의와 같은 규칙(`CS_MANUAL_DATA_ORIGIN`) |
| `GET /{id}` | 조회 범위 | 요청서, 근거, 현재 회차 부서, 회신, 조치, **다음 담당** |
| `PATCH /{id}` | cs_agent | 내용 수정. 바뀐 값이 있을 때만 새 판본 |
| `GET /{id}/versions` | 조회 범위 | 판본별 내용 |
| `POST /{id}/evidence` | cs_agent | 근거 추가. 새 판본 |
| `POST /{id}/evidence/{evidence_id}/remove` | cs_agent | 근거 제외(사유 필수). 새 판본 |
| `PUT /{id}/owner` | C/S 부서(`GCS_L1`) manager | 담당자 재배정. 새 판본 |

- **판본**: 모든 쓰기는 `version`을 보낸다. 다르면 409 `CS_REQUEST_VERSION_CONFLICT`. 완료된 요청은 고칠 수 없다(409 `CS_REQUEST_COMPLETED`). 재발 재개는 KAN-117 경로로만 연다. 변경마다 `audit_events`에 `improvement_request`로 남긴다.
- **유형별 규칙**: 고장 요청은 `fault_id`(`FLT-000`)와 제안 부서 1개 이상이 필요하다(비우면 422 `CS_DEPARTMENT_REQUIRED`). 문서 요청은 `doc_signal`(`NOT_FOUND`·`VALUE_CONFLICT`·`BROKEN_LINK`·`REPEATED_SPEC`)이 필요하고, 문서 담당 부서가 부서 목록에 없으므로 제안 부서가 비어 있을 수 있다. 이때 `receiver_unconfirmed = true`이고 검토 뒤 다음 할 일은 `CONFIRM_RECEIVER`다(전달은 KAN-116에서 막는다). 묶음 키(유형·제품·고장·신호)는 만든 뒤 바꾸지 않는다.
- **근거 검사**: 문의의 제품이 요청과 같아야 한다. 고장 요청은 그 문의의 **이미 알려진** 해결 사례가 필요하고 사례의 고장 ID가 요청과 같아야 한다. 문서 요청은 사례를 붙이지 않는다. 어긋나면 422 `CS_EVIDENCE_INVALID`, 이미 근거인 문의는 409 `CS_EVIDENCE_EXISTS`, 모르는 제품은 422 `CS_UNKNOWN_PRODUCT`.
- **사용 조건**: `condition_status`가 `UNKNOWN`(기본)이면 조건을 확인하지 못한 묶음이다. 조건이 다른 사례는 다른 요청으로 나눈다. 조건 비교 판단은 KAN-114·113이 한다.
- **문의 종결 ≠ 개선 완료**: 요청과 문의 상태는 서로 바꾸지 않는다. 종결된 문의도 근거가 될 수 있고, 요청을 완료해도 문의는 그대로다.
- **조회 범위**: cs_agent와 C/S 부서 manager는 전체. dept_staff는 자기 부서가 수신 부서인 요청(전달된 회차가 있을 때)만, 다른 부서 manager는 관리 부서가 수신 부서이거나 제안 부서인 요청을 본다. 범위 밖은 404 `CS_REQUEST_NOT_FOUND`.

## 다음 담당 (`next_owners`)

요청 상태 하나로는 누가 할 일인지 알 수 없어서, 현재 회차의 부서 상태를 읽어 할 일을 모두 나열한다. C/S 할 일이 먼저다.

| 상황 | 할 일 |
| --- | --- |
| `DRAFT` | C/S `REVIEW` |
| `REVIEWED` | C/S `SEND`, 수신 부서가 없으면 `CONFIRM_RECEIVER` |
| 부서 `SENT` / `ACKNOWLEDGED` / `ADOPTED` | 그 부서 `ACKNOWLEDGE` / `REPLY` / `RECORD_ACTION` |
| 부서 `INFO_REQUESTED` | C/S `SUPPLEMENT`(그 부서) |
| 부서 `REJECTED`, 반려 확인 전 | C/S `REVIEW_REJECTION`(그 부서) |
| 모든 부서가 `ACTION_TAKEN`이거나 반려 확인됐고 조치가 1개 이상 | C/S `PLAN_FOLLOWUP` |
| `FOLLOW_UP` | C/S `RECORD_FOLLOWUP` |
| `COMPLETED` | 없음 |

C/S 할 일의 `user_id`는 요청의 담당자이며 아직 없으면 null이다. 부서 할 일은 부서 코드만 준다.
