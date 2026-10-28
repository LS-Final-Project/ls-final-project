# TFT 프로젝트 계약 시작점 (`tft` 서비스, RFP 자사 대응·협력사 보완)

원본: backend/api/tft/projects/schemas.py 및 routes.py. 예시: backend/api/tft/examples.py.

## 프로젝트 조회 (KAN-119)

| 경로 | 응답 | 설명 |
| --- | --- | --- |
| `GET /api/v1/tft/projects?limit=&cursor=` | `SuccessResponse[ProjectList]` | `created_at DESC, id DESC` 고정 정렬. `limit` 1~100(기본 20). `next_cursor`를 그대로 다음 요청의 `cursor`로 보내며 끝은 null. 잘못된 커서는 400 `TFT_INVALID_CURSOR` |
| `GET /api/v1/tft/projects/{project_id}` | `SuccessResponse[ProjectDetail]` | 프로젝트와 RFP 요구사항(`rfp_requirements`). 없는 프로젝트는 404 `TFT_PROJECT_NOT_FOUND` |

`ProjectSummary`에는 내부 UUID, `external_id`, 이름, `company_code`, 프로젝트 `status`, `data_origin`·`derivation_kind`, `responsible_user_id`(로그인 사용자 ID), `sales_user_id`(담당 sales의 로그인 사용자 ID, 미배정이면 null. KAN-122에서 추가), `created_at`이 있다. `ProjectDetail`은 여기에 `version`·`known_at`·`client_name`과 `rfp_requirements`(요구 코드, 분야 태그, 제목·설명, 문서명·`document_id`·`document_version_id`, 조항·페이지·발췌, 두 출처 축)를 더한다.

## TFT 인력 구성 단계 제거 (KAN-181)

2026-10-28 범위 변경(TFT 팀 구성 → RFP 자사 대응·협력사 보완)으로 부서 인력요청·후보 추천·경험/역량/가용성 확인·구성안·책임자 결정 API와 화면을 제거했다. 제거한 경로: `/api/v1/tft/projects/{project_id}/staffing-requests`, `/api/v1/tft/staffing-requests/...`, `/api/v1/tft/department-requests`, `/api/v1/tft/projects/{project_id}/{verification,compositions,confirm,reject,reopen,decisions}`. 상세 응답의 `required_roles`·`staffing_requests`·`recommendations`·`current_composition`·`responsible`·`final_confirmation`과 목록의 `current_composition_version`도 뺐다. 이전 판본은 [인수인계](../../handoff.md)의 'TFT 정리(KAN-181)' 절에서 찾는다.

## 저장 구조

마이그레이션은 `infra/migrations/tft/20261023_KAN-119_projects.sql`이고, 그 뒤에 `20261023_KAN-122_staffing_requests.sql`이 `projects.sales_user_id`(담당 sales)와 `organizations.dept_code`를 더한다. 모든 업무 테이블은 [공통 데이터 계약](../../docs/data-contract.md)의 `data_origin`(synthetic/demo/real)과 `derivation_kind`(PROVIDED/DERIVED/SYSTEM/ASSUMED) 두 축을 가진다. 로그인 사용자 ID(`*_user_id`)는 `identity.users`를 가리키는 uuid이며 스키마 간 FK는 두지 않는다. API가 읽는 테이블은 `tft.projects`와 `tft.rfp_requirements`뿐이다. 인력 구성 단계 테이블(`required_roles`·`role_requirement_links`·`role_competencies`·`staffing_requests`·`staffing_request_revisions`·`candidate_recommendations`·`experience_evidence`·`competency_checks`·`availability_checks`·`composition_versions`·`composition_members`·`responsible_changes`·`final_confirmations`·`recommendation_commands`·`decision_commands`·`decision_history`, 그리고 `organizations`·`candidates`)은 공용 DB 적용 이력을 확인하지 않았으므로 DROP 마이그레이션 없이 남겨 두며 읽는 코드가 없다.

- `projects.status`(INITIAL_REVIEW/STAFFING/COMPOSING/CONFIRMED)는 옛 TFT 상태이며 DB CHECK 값 그대로 응답한다. RFP 대응 상태는 [RFP API 명세](https://docs.google.com/document/d/1lh6blP9IqACKvr3nyPp7NuU07jOmliIvIH_h87HDu9g/edit) §2.1의 `output_status`(INTAKE/ANALYZING/REVIEW_DRAFT)·`judgement_status`(NEEDS_REVIEW/CONDITION_CHECK/OWNER_CONFIRMED) 두 열을 KAN-182에서 추가하며 기존 `status` 열은 손대지 않는다.
- 접근 검사(KAN-78, [권한 문서](../platform/permissions.md) 1.3·1.4·4.2): 사용자 정보가 없거나 잘못되면 401, cs_agent는 403. 목록·상세는 manager의 관리 대상 프로젝트, 사용자가 책임자(`responsible_user_id`)인 프로젝트, 사용자가 담당 sales(`sales_user_id`, KAN-122)인 프로젝트만이며 범위 밖은 404 `TFT_PROJECT_NOT_FOUND`. 인력요청을 받은 부서의 dept_staff 범위는 인력요청과 함께 제거했다(KAN-181). dept_staff·sales는 배정이 없으면 빈 목록이다.
- 플랫폼의 사용자 정보 중계가 생기기 전까지 위 경로는 `local`·`test`에서만 응답하고 `production`에서는 DB 조회 전에 404 `NOT_FOUND`로 닫는다. OpenAPI 상태는 `partial`이다.
- 합성 예시는 `data_origin=synthetic`인 해저케이블 RFP 프로젝트와 요구사항 두 건, 요구사항 없는 프로젝트 하나다. 사용자 ID는 로컬 시연 계정이다.

수정 후 `python3 scripts/workspace.py contracts tft`를 실행한다.
