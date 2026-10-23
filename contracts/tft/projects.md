# TFT 프로젝트 계약 시작점

원본: backend/api/tft/projects/schemas.py 및 routes.py. 예시: backend/api/tft/examples.py.

## 프로젝트 조회 (KAN-119)

| 경로 | 응답 | 설명 |
| --- | --- | --- |
| `GET /api/v1/tft/projects?limit=&cursor=` | `SuccessResponse[ProjectList]` | `created_at DESC, id DESC` 고정 정렬. `limit` 1~100(기본 20). `next_cursor`를 그대로 다음 요청의 `cursor`로 보내며 끝은 null. 잘못된 커서는 400 `TFT_INVALID_CURSOR` |
| `GET /api/v1/tft/projects/{project_id}` | `SuccessResponse[ProjectDetail]` | 프로젝트와 여섯 단계 기록 전체. 없는 프로젝트는 404 `TFT_PROJECT_NOT_FOUND` |

`ProjectSummary`에는 내부 UUID, `external_id`, 이름, `company_code`, 프로젝트 `status`, `data_origin`·`derivation_kind`, `responsible_user_id`(로그인 사용자 ID), `current_composition_version`(최신 구성안 판본 번호 또는 null), `created_at`이 있다. 쓰기 API(요청 발행 T04, 추천·검증 T05, 구성안·책임자·확정 T09)는 이 계약에 아직 없다.

## 저장 구조와 여섯 단계 분리

마이그레이션은 `infra/migrations/tft/20261023_KAN-119_projects.sql`이다. 모든 업무 테이블은 [공통 데이터 계약](../../docs/data-contract.md)의 `data_origin`(synthetic/demo/real)과 `derivation_kind`(PROVIDED/DERIVED/SYSTEM/ASSUMED) 두 축을 가진다. 로그인 사용자 ID(`*_user_id`)는 `identity.users`를 가리키는 uuid이며 스키마 간 FK는 두지 않는다. 후보 인력(`tft.candidates`)은 로그인 사용자와 별개 ID다. 조직(`tft.organizations`)은 `company_code`를 보존하고 `tft.organization_field_tags`로 업무 분야 태그(DESIGN, CIVIL_STRUCTURE, CONSTRUCTION_TRANSPORT, QUALITY_TEST, PROJECT_MANAGEMENT)를 여러 개 가진다. 태그는 실제 부서명이나 로그인 역할을 대신하지 않는다.

| 단계 | 테이블 | 상태와 기록 | 상세 응답 필드 |
| --- | --- | --- | --- |
| 초기검토 | `tft.rfp_requirements`, `tft.required_roles`, `tft.role_requirement_links`(요구↔역할 N:M, 도출 이유), `tft.role_competencies`(필수/우대 분리), `tft.staffing_requests` | 역할 `status`는 기획서 4.5의 역할 요청 상태(DRAFTING → ISSUED → RECOMMENDATIONS_RECEIVED → DEPT_VERIFIED → COMPARING → IN_COMPOSITION → CONFIRMED). DRAFTING 외에는 `reviewed_by_user_id`·`reviewed_at`(담당 sales 검토)이 필수. 부서 요청은 sales가 검토 후 발행하며(`issued_by_user_id`, `issued_at`) 중간 승인 단계가 없다. 후보 없음·정보 부족은 `next_action`, `next_action_organization_id`, `reply_due_at`로 남긴다 | `rfp_requirements`, `required_roles[].requirements/competencies`, `staffing_requests` |
| 부서추천 | `tft.candidate_recommendations` | 요청별 후보, 추천자 ID, 추천 시각, 추천 이유, `RECOMMENDED`/`WITHDRAWN`. 검증 기록과 다른 테이블이며 같은 부서 담당자가 추천과 검증을 모두 해도 기록은 구분된다 | `recommendations[]` |
| 경험 | `tft.experience_evidence`, `tft.competency_checks` | 유사 프로젝트·기간·실제 과업·근거 출처. 역량 확인은 역할의 필수/우대 역량별 행. 상태는 `UNSPECIFIED`(미기재)/`NEEDS_CHECK`(추가 확인, 자료 없음은 역량 부족이 아님)/`CONFIRMED`/`UNMET`. CONFIRMED·UNMET은 확인자·확인 시각·근거가 필수(DB CHECK) | `recommendations[].experience_evidence`, `recommendations[].competency_checks` |
| 가용성검증 | `tft.availability_checks` | 대상 기간, 투입률(%), 업무 제약, 확인 주체, 확인 시각, 근거. `CONFIRMED`는 기간·투입률·확인자·시각·근거가 모두 있어야 하고(DB CHECK) `UNMET`은 확인자·시각·근거가 필요하다. `UNSPECIFIED`/`NEEDS_CHECK`와 구분한다 | `recommendations[].availability_checks` |
| 구성버전 | `tft.composition_versions`, `tft.composition_members` | 프로젝트별 `version` 유일. 판본과 구성원은 서비스 계정(tft_app)에 UPDATE·DELETE 권한이 없어 이전 판본을 덮어쓰지 않는다. 구성원 상태는 `PROPOSED`/`NEEDS_REINFORCEMENT`/`CONFIRMED`이며 공백은 `recommendation_id` null + `gap_note`·`next_action_organization_id`·`follow_up_due_at`으로 남긴다. 일부 역할 확정은 전체 완료가 아니다 | `current_composition`(최신 판본과 구성원) |
| 책임자결정 | `tft.responsible_changes`, `tft.final_confirmations` | 책임자 지정·변경 이력(프로젝트 ID, 변경자 ID, 이전·신규 책임자 ID, 사유, 시각)과 최종 확정 기록(확정자 ID, 대상 구성안 판본). 둘 다 추가 전용. `projects.responsible_user_id`는 이력 행과 함께만 바뀐다. 책임자 미지정 시 확정 거부와 "현재 책임자만 확정" 검사는 쓰기 API(T09)에서 구현한다 | `responsible`(최신 이력과 일치할 때만 요약), `final_confirmation` |

- `projects.status`(INITIAL_REVIEW/STAFFING/COMPOSING/CONFIRMED)는 목록용 대략적 단계이며 각 단계 테이블의 상태를 대신하지 않는다.
- 상세의 `responsible`는 `responsible_user_id`와 최신 `responsible_changes.new_user_id`가 일치할 때만 채운다. 불일치는 지정 없음으로 보여 주며 임의로 지정을 만들지 않는다.
- 접근 검사(KAN-78, [권한 문서](../platform/permissions.md) 1.3·1.4·4.2): 사용자 정보가 없거나 잘못되면 401, cs_agent는 403. 목록·상세는 manager의 관리 대상 프로젝트와 사용자가 책임자(`responsible_user_id`)인 프로젝트만이며 범위 밖은 404 `TFT_PROJECT_NOT_FOUND`. 담당 sales(KAN-122)와 인력요청을 받은 부서의 dept_staff는 사용자와 연결할 정보가 생기기 전까지 빈 목록이다.
- 플랫폼의 사용자 정보 중계가 생기기 전까지 위 경로는 `local`·`test`에서만 응답하고 `production`에서는 DB 조회 전에 404 `NOT_FOUND`로 닫는다. OpenAPI 상태는 `partial`이다.
- 합성 예시는 `data_origin=synthetic`이며 기획서 4.6의 해저케이블 시연 흐름(설계 후보 확인 완료, 시공 후보는 육상 경험만이라 필수 역량 추가 확인·가용성 미기재·역할 보강 필요)을 따른다. 사용자 ID는 로컬 시연 계정이다.

수정 후 `python3 scripts/workspace.py contracts tft`를 실행한다.
