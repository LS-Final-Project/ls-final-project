# TFT 부서 인력요청 계약 (KAN-122)

원본: backend/api/tft/staffing_requests/schemas.py 및 routes.py, 행위자 검사는 backend/api/tft/authz.py. 읽기 모델 `StaffingRequest`는 프로젝트 상세가 같은 객체를 품으므로 backend/api/tft/projects/schemas.py에 있다. 예시: backend/api/tft/examples.py. 마이그레이션: `infra/migrations/tft/20261023_KAN-122_staffing_requests.sql`(KAN-119 파일 다음에 적용).

결정(KAN-57, 2026-10-21): 부서 인력요청은 담당 `sales`가 내용을 검토한 뒤 직접 발행한다. 발행 전 `manager`·책임자의 추가 승인 단계는 없다. 부서·프로젝트 범위 검사, 최종 확정자 검사(T09), 변경 이력은 유지한다.

## 엔드포인트

| 경로 | 응답 | 설명 |
| --- | --- | --- |
| `GET /api/v1/tft/projects/{project_id}/staffing-requests` | `SuccessResponse[StaffingRequestList]` | 프로젝트의 요청서 전체를 `data.items[]`로 생성순 반환. 프로젝트당 소수라 커서가 없다. 없는 프로젝트는 404 `TFT_PROJECT_NOT_FOUND` |
| `GET /api/v1/tft/staffing-requests/{id}` | `SuccessResponse[StaffingRequest]` | 상세. 응답 헤더 `ETag: "<version>"`. 없으면 404 `TFT_STAFFING_REQUEST_NOT_FOUND` |
| `PATCH /api/v1/tft/staffing-requests/{id}` | `SuccessResponse[StaffingRequest]` | `DRAFT` 요청서 수정. `If-Match` 필수. 본문은 `StaffingRequestUpdate`(모두 선택, 하나 이상): `organization_id`, `participation`(LEAD/SUPPORT), `due_at`(시간대 포함 ISO 또는 null), `due_undecided`(bool), `message`(null 가능, 4000자). 값이 실제로 바뀌면 `version`+1과 `EDIT` 이력, 같으면 판본을 올리지 않는다 |
| `POST /api/v1/tft/staffing-requests/{id}/issue` | `SuccessResponse[StaffingRequest]` | `DRAFT` → `ISSUED`. 본문 없음, `If-Match` 필수, `Idempotency-Key` 수신(아래). `issued_by_user_id`·`issued_at`을 기록하고 `version`+1과 `ISSUE` 이력을 남긴다. 해당 역할(`required_roles`)이 아직 `DRAFTING`이면 `ISSUED`로 바꾸고 발행자를 `reviewed_by_user_id`·`reviewed_at`에 기록한다(두 번째 부서 요청처럼 이미 지난 역할은 그대로 둔다) |

## StaffingRequest 필드

`id`, `project_id`, `required_role_id`, `role_name`(역할 제목), `organization_id`, `organization_name`, `organization_dept_code`(`tft.organizations.dept_code`, 연결 전이면 null), `participation`, `status`(DRAFT/ISSUED/ANSWERED/CLOSED), `version`, `due_at`, `due_undecided`, `message`, `issued_by_user_id`, `issued_at`, `created_at`, `updated_at`, `data_origin`, `derivation_kind`.

- 기한은 세 상태를 구분한다. `due_at` 값 = 기한 있음, `due_at` null + `due_undecided` false = 아직 입력 안 함, `due_at` null + `due_undecided` true = 담당 sales가 "미정"으로 표시. 화면(S2)은 미정을 빈칸이 아니라 "미정"으로 보여 준다. 둘 다 채우면 422 `VALIDATION_ERROR`(DB CHECK도 같다).
- 요청서 생성(역할 생성과 함께)과 `ANSWERED`·`CLOSED` 전이는 이 계약에 없다(T05/T09).

## 검사 순서와 오류

[권한 문서 1.3](../platform/permissions.md#13-검사-순서와-오류-확정-코드는-api-계약오류와-동시-수정-사용)의 순서를 따른다. 코드는 [API 계약](../../docs/api-contract.md#오류와-동시-수정)의 공통 코드를 쓰고, 404만 기능 코드다.

| 순서 | 검사 | PATCH | issue |
| --- | --- | --- | --- |
| 1 | 행위자 확인(`current_principal`), cs_agent 제외 | 401 `AUTH_REQUIRED` / 403 `FORBIDDEN` | 같음 |
| 2 | 요청서·프로젝트 존재와 프로젝트 가시성(`sees_project`) | 404 `TFT_STAFFING_REQUEST_NOT_FOUND` | 같음 |
| 3 | `sales` 역할이고 프로젝트의 `sales_user_id`인가 | 403 `FORBIDDEN` | 403 `FORBIDDEN`(**권한 없는 발행 차단**. `sales_user_id`가 비어 있어도 403) |
| 4 | 상태가 `DRAFT`인가 | 409 `INVALID_TRANSITION` | 409 `INVALID_TRANSITION` |
| 5 | `If-Match` | 없으면 428 `VERSION_REQUIRED`, 현재 `version`과 다르거나 숫자가 아니면 412 `VERSION_CONFLICT` | 같음 |
| 6 | 입력·근거 | 수정 필드 없음·불변 필드·null 불가 필드·시간대 없는 `due_at`·프로젝트 회사 밖 또는 없는 `organization_id`·기한과 미정 동시 설정은 422 `VALIDATION_ERROR` | `due_at`도 `due_undecided`도 없으면 422 `EVIDENCE_REQUIRED` |

- `If-Match` 값은 `"3"`처럼 따옴표가 있어도 없어도 되고 `W/` 접두사는 무시한다. 응답의 `ETag`를 그대로 돌려보내면 된다.
- 판본 비교와 변경, 이력 저장은 행을 잠근(`SELECT ... FOR UPDATE`) 한 트랜잭션이다.
- `Idempotency-Key`(1~128자, 기존 호출 호환을 위해 선택): 같은 사용자·요청서·키와 동일한 `If-Match` 재전송은 최초 성공 응답과 ETag를 반환한다. 키를 다른 판본에 재사용하면 409 `IDEMPOTENCY_CONFLICT`다. 현재 조회·발행 권한을 먼저 재검증하며, 요청서 잠금 안에서 변경·이력·응답을 하나의 트랜잭션으로 저장한다. 키가 없으면 기존 상태/판본 검사를 적용한다. 이 후속 변경은 KAN-123의 `recommendation_commands` 마이그레이션 적용 이후 사용한다(별도 `staffing_request_issue` operation).
- 범위 검사는 프로젝트 단위다. 프로젝트 조회 범위(`dept_staff`의 자기 부서, `manager`의 관리 대상) 검사는 KAN-78에서 추가하며, 그때 2단계의 404가 범위 밖 객체도 숨긴다.

## 판본과 변경 이력

`tft.staffing_requests.version`은 1에서 시작한다. 바뀐 값이 있는 PATCH와 issue마다 1씩 올리고 `tft.staffing_request_revisions`에 한 행을 추가한다(`version`은 변경 후 판본, `action`은 `EDIT`/`ISSUE`, `actor_user_id`는 행위자, `changes`는 `{"필드": {"from": 이전, "to": 이후}}`). 이 테이블은 서비스 계정(tft_app)에 UPDATE·DELETE 권한이 없는 추가 전용이다. 이력 조회 API(S5)는 후속이다.

## 행위자 식별과 범위 (KAN-78 공통 코드)

행위자는 [권한 문서 1.4](../platform/permissions.md#14-업무-서비스가-받는-사용자-정보-kan-78-형식-제안)의 `X-Team4-Principal` 헤더(`backend/shared/access.py`의 `Principal`, `current_principal`)다. 헤더가 없거나 잘못되면 401, `local`·`test` 밖에서는 헤더와 무관하게 DB 조회 전에 404다. 플랫폼의 인증된 중계는 KAN-178에서 만든다.

- 프로젝트 가시성(1.3 2단계)은 `backend/api/tft/projects/access.py`의 `sees_project`를 그대로 쓴다. KAN-122에서 담당 sales(`projects.sales_user_id == principal.user_id`)를 가시 조건에 더했다. 즉 manager의 관리 대상 프로젝트, 책임자, 담당 sales가 요청서를 보고, 그 밖의 사용자에게는 요청서가 404다. cs_agent는 403(`require_project_list`). 요청을 받은 부서 dept_staff의 범위(`organizations.dept_code == principal.dept`)는 KAN-123에서 더한다.
- 행동(1.3 3단계)은 `backend/api/tft/authz.py`: PATCH·issue는 `principal.has("sales")`이고 `project.sales_user_id == principal.user_id`일 때만 허용, 아니면 403 `FORBIDDEN`. 책임자·manager는 읽기만 한다. 이력의 `actor_user_id`는 `principal.user_id`다.

## KAN-78 연결 지점 (김나영 요청으로 이 마이그레이션에서 추가)

- `tft.projects.sales_user_id uuid NULL`: 담당 sales의 로그인 사용자 ID(identity.users.id). 교차 스키마라 FK 없이 `responsible_user_id`와 같은 방식이다. 발행 권한 검사의 기준이며 `ProjectSummary`·`ProjectDetail`에 `sales_user_id`로 노출한다. 담당 sales 배정·재배정(`manager`)은 T09 범위다.
- `tft.organizations.dept_code text NULL`: `identity.departments.code`와 같은 값·형식(`^[A-Z][A-Z0-9_]*$`)으로 사용자 부서(`identity.users.dept`)와 TFT 조직을 잇는 키. 회사 안에서 유일(`UNIQUE (company_code, dept_code)` 부분 인덱스). `dept_staff`의 "요청 부서" 범위 검사는 이 값으로 한다.

## 상태

OpenAPI `x-implementation-status`는 `partial`이다. 위 경로는 `local`·`test` 환경에서만 응답하며 `production`에서는 DB 조회 전에 404 `NOT_FOUND`로 닫는다. 합성 예시는 `data_origin=synthetic`이며 해저케이블 시연 흐름에 품질시험팀 협업 요청서 초안(기한 미정 → 기한 지정 수정 → 발행)을 더했다. 수정 후 `python3 scripts/workspace.py contracts tft`를 실행한다.

## 기존 데이터 적용

KAN-119의 기한 없는 발행 이력은 기한이나 sales의 미정 결정을 만들어 보정하지 않는다. `staffing_requests_issued_due`는 `NOT VALID`로 추가해 신규·수정 행을 즉시 검사한다. 기존 위반 행이 없으면 같은 마이그레이션에서 검증 완료하며, 있으면 이력을 그대로 보존한다. 운영 적용 담당자는 `status <> 'DRAFT' AND due_at IS NULL AND NOT due_undecided`인 기존 행을 업무 담당자와 확인하고 실제 기한 또는 명시적 미정 결정을 반영한 뒤 `ALTER TABLE tft.staffing_requests VALIDATE CONSTRAINT staffing_requests_issued_due`를 실행한다. 마이그레이션 재실행도 위반 행이 없을 때 제약을 검증한다. 이 PR은 운영 DB를 수정하지 않는다.
