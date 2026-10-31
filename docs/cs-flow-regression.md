# C/S 문의·개선 전체 흐름 검사 대조표 (KAN-152)

KAN-152 완료 조건 "접수/회신/장비확인/종결→개선요청/조치/새3건/조치후재발1건, 구형별도"를 확인하는 검사의 위치다. 단계별 검사는 각 기능 PR에서 만들었다. 이 티켓에서는 그 단계를 **한 문의에서 시작해 HTTP API만으로 끝까지 잇는 검사** `tests/cs/test_cs_full_flow_db.py::test_a_ticket_and_its_improvement_run_end_to_end_through_the_api`를 더했다. 단계별 검사는 단계마다 상태를 직접 만들지만, 이어 가는 검사는 앞 단계 결과를 뒤 단계가 그대로 이어받는지 본다. 표의 검사 이름은 `tests/cs/test_cs_flow_regression_index.py`가 실제로 있는지 확인하므로, 이름을 바꾸면 이 표도 같이 고친다.

**실행**: 전용 PostgreSQL(`pgvector/pgvector:pg17`)과 `AUTH_TEST_DATABASE_URL`로 돌린다. CI(`application-checks.yml`)의 auth-database 작업에 들어 있다. 메일은 가짜 플랫폼(`tests/cs/test_customer_mail_db.py`의 `FakePlatform`)이고 실제 모델·Graph는 부르지 않는다. DB에 직접 넣는 것은 합성 설치 장비 1대뿐이다(장비 마스터는 적재 자료라 API가 없다).

## 이어 가는 검사의 단계

| 단계 | API | 확인 |
| --- | --- | --- |
| 1. 접수·장비 확인 | `POST /api/v1/cs/tickets`, `PUT …/units/{unit_id}` | RECEIVED, 장비 CONFIRMED |
| 2. 정보 요청·회신 | `…/workflow/start-review`, `…/workflow/send`(INFO_REQUEST) → 플랫폼 ACCEPTED → `…/sends/{id}/refresh`, `POST /internal/v1/cs/tickets/{id}/customer-reply` | WAITING_CUSTOMER → 회신으로 IN_REVIEW(reopened) |
| 3. 부서·종결·해결 기록 | `…/transfer` → `…/dept-result` → `…/dept-results/{id}/review` → `…/close` → `…/resolution-case` | CLOSED, 해결 기록(구형 펌웨어 1.60) |
| 4. 개선요청 | `POST /api/v1/cs/improvement-requests`(근거 3건, 첫 근거가 이 문의) → `review` → `send` | SENT, 신호 BEFORE_ACTION |
| 5. 새 문의 3건 | 문의 3건을 접수부터 해결 기록까지 API로 → `followup-signals` → `rounds`(REQUEST_AGAIN) | 2차수, 새 3건이 근거로 들어감 |
| 6. 부서 조치·완료 | `departments/QA_CLAIM/acknowledge`·`reply`·`action`(펌웨어 1.72 이상), `followup-plan`, `complete` | COMPLETED |
| 7. 구형 장비 별도 | 펌웨어 1.60 문의 1건 | 재발 0·조치 전 장비 1, 그 고객은 업데이트 안내 대상, 결정 NONE |
| 8. 조치 후 재발 | 펌웨어 1.72 문의 1건 → `rounds`(RECURRENCE) | 3차수로 다시 열림, 앞 완료 기록 보존 |
| 끝 | 감사 기록 | 첫 문의 업무 전이(start_review … close), 개선요청 차수(REQUEST_AGAIN, RECURRENCE) |

변형 검사: 재발 판정이 펌웨어 적용 여부를 보지 않게 바꾸면 7단계, 고객 회신이 문의를 다시 열지 않게 바꾸면 2단계에서 실패한다(2026-10-30 확인).

## 단계별 검사

| 단계 | 검사 |
| --- | --- |
| 전체 흐름(이 티켓) | `tests/cs/test_cs_full_flow_db.py::test_a_ticket_and_its_improvement_run_end_to_end_through_the_api` |
| 접수·검토·종결은 별개 단계, 부서 결과는 채택돼야 셈 | `tests/cs/test_workflow_db.py::test_received_reviewed_and_closed_are_separate_steps` |
| 정보 요청 대기와 회신으로 다시 열림 | `tests/cs/test_customer_mail_db.py::test_information_request_waits_and_a_linked_reply_reopens` |
| 발송·회신에 따른 상태 이동 | `tests/cs/test_workflow_cases_db.py::test_send_and_customer_reply_move_the_state` |
| 장비 확인 하나와 판본 변경 | `tests/cs/test_units_db.py::test_one_confirmed_unit_and_versioned_changes` |
| 종결 뒤 채택된 결과로 해결 기록 한 번 | `tests/cs/test_workflow_cases_db.py::test_case_is_registered_once_after_closing_from_an_accepted_result` |
| 고장 개선요청과 근거의 판본 | `tests/cs/test_improvements_db.py::test_a_fault_request_is_versioned_with_its_evidence` |
| 검토 뒤 발송이 첫 차수 | `tests/cs/test_improvement_transitions_db.py::test_review_then_send_opens_the_first_round` |
| 부서별 진행과 요약 상태 | `tests/cs/test_improvement_transitions_db.py::test_departments_progress_independently_and_the_status_is_summarized` |
| 문의 종결과 요청 완료는 별개 | `tests/cs/test_improvements_db.py::test_ticket_closing_and_request_completion_are_independent` |
| 발송 뒤 새 3건이면 재요청 차수 | `tests/cs/test_improvement_followups_db.py::test_three_new_records_after_send_open_a_re_request_round` |
| 새 3건 규칙(마지막 발송 기준) | `tests/cs/test_improvement_followups.py::test_every_three_new_records_after_the_last_send_is_a_request_again_candidate` |
| 적용 장비 재발 1건이면 완료 요청 다시 열림 | `tests/cs/test_improvement_followups_db.py::test_a_recurrence_on_an_applied_unit_reopens_a_completed_request_keeping_its_record` |
| 적용 장비 재발 1건, 구형 장비는 따로 셈 | `tests/cs/test_improvement_followups.py::test_one_record_on_an_applied_unit_is_a_recurrence_and_old_units_are_counted_apart` |
| 조치 전·후는 발생일로 판단 | `tests/cs/test_improvement_followups.py::test_the_occurrence_date_decides_before_or_after_not_the_load_time` |
| 기준 시각 뒤의 기록은 쓰지 않음(미래 정보 차단) | `tests/cs/test_improvement_followups.py::test_records_rounds_and_actions_after_as_of_are_not_used` |

## 한계

- 메일 수신에서 문의가 자동으로 만들어지는 경로(KAN-73 메일 접수)와 플랫폼 중계(웹 → 플랫폼 → C/S)는 이 검사에 없다. 각각 `tests/platform/test_mail_intake_db.py`, `tests/platform/test_business_relay.py`가 본다. 부서 경로의 중계 매칭은 KAN-116 #310의 공유 사례가 본다.
- 분석 그래프(분류·초안)는 이 흐름에 넣지 않았다. 분석 작업과 발송 뒤 재개는 `tests/platform/test_cs_analysis_e2e_db.py`가 본다.
