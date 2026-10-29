# 보안 회귀 검사 대조표 (KAN-149)

KAN-149 완료 조건 "미등록/다른부서/프로젝트 접근, 개발우회, 이전승인, 프롬프트지시로 도구/발송 실행 차단"을 막는 검사의 위치다. 대부분은 각 기능 PR에서 만든 검사이고, 프롬프트 지시문 회귀는 이 티켓에서 더했다(`tests/cs/test_prompt_injection_regression.py`). 표의 검사 이름은 `tests/test_security_regression_index.py`가 실제로 있는지 확인하므로, 검사 이름을 바꾸면 이 표도 같이 고친다.

**실행**: 모델·DB 없는 검사는 `uv run --frozen pytest -q <파일>`, DB 검사는 [공통 온보딩](team-start.md)의 전용 PostgreSQL과 `AUTH_TEST_DATABASE_URL`로 돌린다. 모두 CI(`application-checks.yml`의 backend·auth-database)에 이미 들어 있다.

## 1. 미등록 사용자

| 막는 것 | 검사 |
| --- | --- |
| Microsoft 로그인만으로 사용자·세션을 만들지 않음 | `tests/platform/test_session_store.py::test_unregistered_subject_never_creates_user_or_session` |
| 등록된 주체만 로그인 | `tests/platform/test_authorization_store.py::test_login_matches_registered_subject_without_creating_users_or_sessions` |
| 무효 세션·등록 해제 사용자 거부 | `tests/platform/test_session_context.py::test_context_rejects_invalid_session_or_registration` |
| 비활성·다른 역할 사용자를 담당자로 지정 불가 | `tests/platform/test_assignees_db.py::test_only_an_active_cs_agent_is_a_valid_owner` |

## 2. 다른 부서 접근

| 막는 것 | 검사 |
| --- | --- |
| 부서 사용자는 이관받은 문의만 봄 | `tests/cs/test_tickets.py::test_transfer_department_users_see_only_tickets_transferred_to_them` |
| 범위 밖 문의 쓰기는 없는 것처럼 404 | `tests/cs/test_ticket_writes.py::test_users_outside_the_ticket_scope_get_not_found` |
| 업무 동작의 범위·역할·상태·판본 검사 순서 | `tests/cs/test_workflow_db.py::test_scope_role_state_and_version_are_checked` |
| 개선요청 부서 범위와 쓰기 권한 | `tests/cs/test_improvements_db.py::test_department_scope_and_writers` |
| 부서 사용자에게 다른 고객 식별자 가림 | `tests/cs/test_improvement_followups_db.py::test_department_users_do_not_see_other_customers_and_old_requests_get_round_one` |
| 영업(sales)은 C/S 범위 없음 | `tests/cs/test_tickets.py::test_sales_has_no_c_s_scope` |

## 3. 다른 프로젝트 접근

| 막는 것 | 검사 |
| --- | --- |
| TFT 프로젝트 범위 검사, 범위 회수가 다음 요청부터 적용 | `tests/platform/test_business_relay.py::test_tft_scope_is_enforced_and_revoked_on_next_request` |
| 관리 범위가 없어진 manager는 범위를 되찾지 못함 | `tests/platform/test_session_context.py::test_missing_grants_or_removed_manager_never_restore_scope` |
| 다른 프로젝트의 요구를 가리키는 변경 제안 거부(DB) | `tests/tft/test_rfp_schema_db.py::test_proposal_cannot_point_at_another_projects_requirement` |

## 4. 개발용 인증 우회

| 막는 것 | 검사 |
| --- | --- |
| 운영 환경에서 개발 인증 켜면 시작 거부 | `tests/test_applications.py::test_production_rejects_development_authentication` |
| 운영 로그인과 개발 로그인을 함께 켤 수 없음 | `tests/platform/test_login_http.py::test_operational_and_development_auth_cannot_be_enabled_together` |
| 개발 세션은 운영 세션이 아님 | `tests/platform/test_session_store.py::test_development_session_is_not_an_operational_session` |
| 운영에서 호출자가 보낸 사용자 정보 무시 | `tests/test_access.py::test_production_ignores_a_caller_supplied_user_context` |
| 업무 서비스는 플랫폼 실행 계정 토큰 없는 직접 호출 거부 | `tests/platform/test_business_relay.py::test_service_rejects_direct_or_wrong_platform_caller` |
| 운영에서 문서·미구현 인증 경로 숨김 | `tests/test_applications.py::test_production_hides_docs_and_unimplemented_auth_routes` |

## 5. 이전 승인으로 발송·변경

| 막는 것 | 검사 |
| --- | --- |
| 승인 뒤 바뀐 내용은 발송 거부(승인 해시) | `tests/platform/test_mail_send_db.py::test_content_changed_after_approval_is_refused` |
| 저장된 발송 행이 바뀌면 보내지 않음 | `tests/platform/test_mail_send_db.py::test_stored_row_changed_before_sending_is_not_sent` |
| 실행 계정은 승인 내용을 고칠 수 없음 | `tests/platform/test_mail_send_db.py::test_runtime_role_cannot_rewrite_approved_content` |
| 승인자 본인의 사서함으로만 발송 | `tests/platform/test_mail_send_db.py::test_only_the_approvers_connected_mailbox_can_send` |
| 결과 불명 재발송은 확인과 승인자 사유가 있어야 함 | `tests/platform/test_mail_send_db.py::test_resend_needs_a_late_not_found_check_and_the_approvers_reason` |
| 고객 답변은 플랫폼 접수 뒤에만 상태 전이, 승인 판본 충돌 | `tests/cs/test_customer_mail_db.py::test_answer_moves_the_ticket_only_when_the_platform_accepted` |
| 정정은 읽은 판본·최신 AI 실행 기준(오래된 판단 거부) | `tests/cs/test_analysis_db.py::test_a_new_ai_run_does_not_replace_a_correction` |

## 6. 프롬프트 지시로 도구·발송 실행

| 막는 것 | 검사 |
| --- | --- |
| 문의 본문 지시문은 프롬프트 입력 자료 칸에만 들어감 | `tests/cs/test_prompt_injection_regression.py::test_the_injected_text_reaches_the_model_only_as_input_data` |
| 지시를 따른 분류 출력의 실행 필드(발송·상태 변경) 거부 | `tests/cs/test_prompt_injection_regression.py::test_a_classifier_that_obeys_the_injection_cannot_add_an_action` |
| 지시를 따른 초안 출력의 `tool_calls` 거부, 사람 확인 전 정지 | `tests/cs/test_prompt_injection_regression.py::test_a_drafter_that_obeys_the_injection_cannot_add_tool_calls` |
| 처리 그래프에 발송·업무 상태 변경 경로와 도구 의존성이 없음 | `tests/cs/test_prompt_injection_regression.py::test_the_graph_has_no_send_or_workflow_path` |
| 분류 원시 출력 위반을 고쳐 쓰지 않음 | `tests/cs/test_classification.py::test_raw_output_violations_are_not_repaired` |
| 초안의 확약 문구·형식 위반 거부 | `tests/cs/test_drafting.py::test_schema_and_commitment_violations_keep_raw_and_usage` |
| 협력사 프로필 문자열의 지시문이 등급을 바꾸지 못함 | `tests/tft/test_partner_grading.py::test_instructions_in_profile_strings_do_not_change_the_result` |
| 협력사 메모의 지시문은 자료일 뿐 | `tests/tft/test_partners.py::test_instruction_in_notes_is_only_data` |
| 메일 회신 연결은 헤더·스레드만 근거(제목·분류 지시로 연결 안 됨) | `tests/platform/test_mail_links_db.py::test_decision_rules` |

발송은 어느 경로에서도 모델이 직접 하지 않는다. 고객 메일은 cs_agent가 승인한 판본만 플랫폼 발송 의도가 되고(KAN-74·110), 모델은 도구를 받지 않는다.

## 범위 밖

- 운영(Cloud Run) 환경의 IAM·Secret 설정 점검은 인프라 검사(`infra/tests`)와 운영 적용 확인(KAN-72 운영 묶음)이다.
- 실제 모델을 써서 지시문 공격을 재는 평가(적대적 평가셋)는 평가 티켓의 범위다. 여기서는 모델이 지시를 따르더라도 시스템이 실행하지 않는지를 고정한다.
