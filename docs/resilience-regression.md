# 재시도·동시 요청·중단·복구 검사 대조표 (KAN-150)

KAN-150 완료 조건 "Tasks 중복/순서변경, checkpoint재개, 토큰철회, 메일결과불명, DB상태 불변식"을 막는 검사의 위치다. 대부분은 각 기능 PR에서 만든 검사다. 이 티켓에서 더한 것은 `tests/platform/test_job_resilience_db.py`(동시 인수 경합, 끝난 작업의 늦은 재전달, TFT 인수 거부, 시나리오 뒤 불변식)와 공통 불변식 검사 `tests/platform/runtime_invariants.py`다. 표의 검사 이름은 `tests/platform/test_resilience_regression_index.py`가 실제로 있는지 확인하므로, 검사 이름을 바꾸면 이 표도 같이 고친다.

**실행**: DB 검사는 [공통 온보딩](team-start.md)의 전용 PostgreSQL(`pgvector/pgvector:pg17`)과 `AUTH_TEST_DATABASE_URL`로 돌린다. DB 검사는 CI(`application-checks.yml`)의 auth-database 작업에, DB가 필요 없는 검사(`tests/cs/test_graph.py`)는 backend-cs 작업에 들어 있다. 실제 모델·Graph·Cloud Tasks는 부르지 않고 가짜 공급자·가짜 응답을 쓴다.

## 1. 작업 전달 중복·순서 바뀜

| 막는 것 | 검사 |
| --- | --- |
| 같은 작업을 여러 worker가 동시에 인수해도 소유권·시작 이벤트는 하나(C/S·TFT) | `tests/platform/test_job_resilience_db.py::test_concurrent_claims_hand_out_exactly_one_lease` |
| 끝난 작업에 전달이 늦게 다시 와도 모델·처리 함수를 부르지 않고 상태 그대로(C/S·TFT) | `tests/platform/test_job_resilience_db.py::test_a_late_redelivery_after_success_changes_nothing` |
| 끝났거나 살아 있는 소유자가 있는 C/S 작업은 인수하지 않음 | `tests/platform/test_cs_worker_db.py::test_jobs_that_must_not_start_are_not_claimed` |
| 같은 조건의 TFT 작업도 인수하지 않음 | `tests/platform/test_job_resilience_db.py::test_tft_jobs_that_must_not_start_are_not_claimed` |
| 평가 작업: 대상 아닌 실행·중복 전달은 아무것도 안 함 | `tests/platform/test_job_worker.py::test_only_target_and_duplicate_noop` |
| 평가 작업: 동시 worker 중 하나만 실행 | `tests/platform/test_job_worker.py::test_concurrent_workers` |
| 같은 멱등 키의 동시 접수는 작업 하나와 재응답 | `tests/platform/test_job_admission.py::test_concurrent_same_key_has_one_job_and_replay` |
| 전송 경합·접수 뒤에도 중복 실행 없음 | `tests/platform/test_dispatch_ledger.py::test_race_and_acceptance_do_not_execute` |
| 결과 기록 실패 뒤 다시 보내지 않음 | `tests/platform/test_jobs_transport.py::test_result_commit_failure_never_reposts` |
| 메일 수집: 시간대마다 메일함당 작업 한 번 | `tests/platform/test_mail_worker_db.py::test_dispatch_enqueues_one_task_per_connected_mailbox_once_per_slot` |
| 메일 수집: 같은 수집을 반복해도 새로 저장 안 함 | `tests/platform/test_mail_worker_db.py::test_sync_stores_mail_in_the_bucket_and_repeating_it_stores_nothing_new` |
| 메일 수집: 동시에 두 번 시작하지 않음 | `tests/platform/test_mail_sync_db.py::test_concurrent_runs_do_not_both_start` |
| 메일 발송: 요청 하나에 발송 의도·작업 하나 | `tests/platform/test_mail_send_worker_db.py::test_request_creates_one_intent_and_one_send_task` |
| 메일 발송: 같은 승인은 의도 하나, 다른 내용은 충돌 | `tests/platform/test_mail_send_db.py::test_same_approval_is_one_intent_and_other_content_conflicts` |

## 2. 체크포인트 재개

| 막는 것 | 검사 |
| --- | --- |
| 사람 확인까지 간 스레드를 다시 돌려도 모델을 부르지 않음 | `tests/platform/test_cs_worker_db.py::test_rerun_of_a_paused_thread_makes_no_calls` |
| 중간에 끊긴 스레드는 마지막 체크포인트부터 이어 감 | `tests/platform/test_cs_worker_db.py::test_rerun_continues_from_the_last_checkpoint` |
| 소유권을 잃은 C/S worker는 아무것도 쓰지 않음 | `tests/platform/test_cs_worker_db.py::test_a_worker_that_lost_its_lease_writes_nothing` |
| 소유권을 잃은 TFT worker도 아무것도 쓰지 않음 | `tests/platform/test_tft_worker_db.py::test_a_worker_that_lost_its_lease_writes_nothing` |
| 체크포인트는 새 연결·새 그래프에서도 남음 | `tests/cs/test_graph_db.py::test_checkpoint_survives_new_connection_and_graph` |
| 형식 위반 재호출 뒤 재개는 모델을 다시 부르지 않음 | `tests/cs/test_graph.py::test_invalid_output_is_retried_once_and_resume_does_not_call_again` |
| 발송 접수 뒤 그래프는 한 번만 재개 | `tests/cs/test_analysis_jobs_db.py::test_an_accepted_send_resumes_the_graph_once` |
| 재개 실패는 다시 시도한 뒤에만 포기 | `tests/cs/test_analysis_jobs_db.py::test_a_failed_resume_is_retried_before_it_is_given_up` |
| 소유자가 죽은 뒤 걸린 취소는 CANCELLED로 확정 | `tests/platform/test_cs_worker_db.py::test_cancel_requested_after_the_worker_died_ends_cancelled` |

## 3. 토큰·권한 철회

| 막는 것 | 검사 |
| --- | --- |
| 역할 회수·로그아웃 뒤 다음 업무 요청 차단 | `tests/platform/test_business_relay.py::test_role_revoke_and_session_logout_stop_next_business_request` |
| TFT 프로젝트 범위 회수는 다음 요청부터 적용 | `tests/platform/test_business_relay.py::test_tft_scope_is_enforced_and_revoked_on_next_request` |
| 접근을 잃으면 열린 작업 이벤트 스트림 종료 | `tests/platform/test_job_events.py::test_open_stream_stops_after_access_loss` |
| 로그아웃한 세션 재사용 거부 | `tests/platform/test_login_http.py::test_http_login_me_logout_and_replay_with_real_database` |
| 메일 토큰 401이면 새 토큰으로 한 번만 재시도 | `tests/platform/test_mail_send_db.py::test_unauthorized_is_retried_once_with_a_fresh_token` |
| 토큰 갱신 실패면 보내지 않음 | `tests/platform/test_mail_send_db.py::test_failed_refresh_after_unauthorized_does_not_send` |
| 연결 끊긴 메일함의 대기 발송은 실패로 확정 | `tests/platform/test_mail_send_worker_db.py::test_disconnected_mailbox_fails_the_waiting_intent` |
| 연결 끊긴 메일함은 수집하지 않음 | `tests/platform/test_mail_worker_db.py::test_sync_of_disconnected_mailbox_is_skipped` |

## 4. 메일 결과 불명

| 막는 것 | 검사 |
| --- | --- |
| 응답이 없으면 불명으로 두고 자동 재발송 안 함 | `tests/platform/test_mail_send_db.py::test_no_response_is_unknown_and_never_resent_automatically` |
| 보내는 도중 끊기면 불명 | `tests/platform/test_mail_send_db.py::test_interrupted_submission_becomes_unknown` |
| 불명 발송이 보낸편지함에 있으면 접수로 확정 | `tests/platform/test_mail_send_db.py::test_unknown_send_found_in_sent_items_becomes_accepted` |
| 접수된 메일은 다시 보내지 않음 | `tests/platform/test_mail_send_db.py::test_accepted_mail_cannot_be_resent` |
| 재발송은 늦은 미발견 확인과 승인자 사유가 있어야 함 | `tests/platform/test_mail_send_db.py::test_resend_needs_a_late_not_found_check_and_the_approvers_reason` |
| 불명 발송은 큐가 재시도하지 않음 | `tests/platform/test_mail_send_worker_db.py::test_unknown_send_is_not_retried_by_the_queue` |
| 멈추거나 등록이 빠진 발송은 정기 점검이 복구 | `tests/platform/test_mail_send_worker_db.py::test_sweep_recovers_stuck_and_unregistered_work` |
| 불명 발송은 확인 뒤 사유와 함께 재발송 | `tests/platform/test_mail_send_http_db.py::test_unknown_send_is_checked_then_resent_with_a_reason` |
| C/S: 불명 전달이 있으면 재시도로 의도를 찾을 때까지 종결 막음 | `tests/cs/test_customer_mail_db.py::test_an_unknown_hand_over_blocks_closing_until_a_retry_finds_the_intent` |
| 작업 전송: 불명 상태 확인은 읽기만 함 | `tests/platform/test_jobs_transport.py::test_inspect_unknown_is_read_only` |
| 작업 전송: 결과 기록 실패에도 불명 복구 경로 유지 | `tests/platform/test_dispatch_ledger.py::test_result_failure_preserves_unknown_recovery` |
| 작업 전송: 이미 보냈을 수 있는 상태는 다시 큐에 넣지 않음 | `tests/platform/test_dispatch_ledger.py::test_requeue_refuses_states_that_may_have_been_sent` |

## 5. DB 상태 불변식

공통 불변식(`tests/platform/runtime_invariants.py`): 끝난 작업에는 소유자가 없고, 상태와 같은 종료 이벤트가 정확히 하나 있다. 끝나지 않은 작업에는 종료 이벤트가 없다. 시작 이벤트 수는 인수 횟수와 같다. failure_code는 FAILED일 때만 있다.

| 막는 것 | 검사 |
| --- | --- |
| 정상 종료·복구·실행 중 취소·소유자 사망 뒤 취소 모두 불변식 유지 | `tests/platform/test_job_resilience_db.py::test_runtime_invariants_hold_after_each_scenario` |
| 불변식 검사가 깨진 상태를 실제로 잡음 | `tests/platform/test_job_resilience_db.py::test_the_invariant_check_reports_broken_states` |
| 인수 기록 실패는 인수 자체를 되돌림 | `tests/platform/test_dispatch_ledger.py::test_failed_insert_rolls_back_claim` |
| 실패는 고정 코드로만 기록 | `tests/platform/test_cs_worker_db.py::test_failures_are_recorded_with_a_fixed_code` |
| 같은 판본에 두 작성자가 써도 새 판본은 하나 | `tests/cs/test_comparison_writes_db.py::test_two_writers_on_the_same_version_make_one_new_version` |
| 같은 메일의 동시 등록은 오류가 아니라 충돌 | `tests/cs/test_customer_mail_db.py::test_a_concurrent_registration_of_the_same_mail_is_a_conflict_not_an_error` |

## 한계

- Cloud Tasks·Cloud Run Jobs의 실제 재전달은 부르지 않고, 같은 작업 ID를 worker에 다시 넣어 재현한다. 운영 재전달 확인은 KAN-159 배포 점검에서 한다.
- 동시 인수 경합은 스레드 8개를 장벽으로 동시에 출발시켜 재현한다. 잠금을 없애는 변형은 실행마다 1~2건 실패로 잡혔다(경합이라 매번 같은 수는 아니다).
