# 서비스별 DB 권한 최초 구성

공용 DB와 로컬 초기화 기반은 준비돼 있다. 이 문서는 인프라 담당자가 새 DB를 최초 구성할 때 참고한다. 팀원의 로컬 시작은 [공통 온보딩](../../docs/team-start.md), 업무 테이블 추가는 서비스별 새 마이그레이션을 사용한다.

| 실행 계정 | 테이블 생성 및 변경 역할 | 소유 스키마 |
| --- | --- | --- |
| `cs_app` | `cs_migrator` | `cs`, `checkpoint_cs` |
| `tft_app` | `tft_migrator` | `tft`, `checkpoint_tft` |
| `platform_app` | `platform_migrator` | `identity`, `integration`, `catalog`, `evaluation`, `notify` |

실행 계정에는 자기 서비스 스키마의 USAGE와 앞으로 생성할 테이블의 SELECT, INSERT, UPDATE, DELETE, 시퀀스의 USAGE 및 SELECT, 함수의 EXECUTE를 부여한다. CREATE, TRUNCATE, 역할 관리와 DB 생성 권한은 부여하지 않는다. `public`은 관리자 설치 확장용으로 USAGE만 허용한다.

`notify`는 P30의 지정 열·메타데이터별 최소 권한을 적용하며 위 일반 CRUD 기본 권한을 부여하지 않는다.

마이그레이션 역할은 NOLOGIN이다. 관리자가 `SET ROLE`로 전환해 해당 서비스의 테이블을 생성하거나 변경한다. 앱 실행 계정에는 마이그레이션 역할을 부여하지 않는다. 배포용 마이그레이션 로그인 주체는 후속 CI/CD 설정에서 연결한다.

공통 `search`, `runtime`의 쓰기 및 업무별 접근 정책은 후속 마이그레이션에서 정의한다. 이 단계에서 모든 서비스에 해당 스키마의 무제한 권한을 부여하지 않는다. 이 초기 SQL은 역할·스키마만 구성한다. pgvector는 별도로 설치돼 있으며 업무 테이블과 검색 인덱스·임베딩 차원은 기능별 마이그레이션으로 추가한다.

## 실행

대상 DB는 `team4`다. DB 관리자 psql 세션을 사용하며 PostgreSQL 역할은 GCP의 runtime 서비스 계정과 별개다. 이 SQL은 GCP IAM을 변경하지 않는다.

새 DB를 구성할 때 저장소 루트에서 연 psql 세션을 사용한다. 기존 공용 DB에 최초 구성 SQL을 다시 실행하지 않는다.

```psql
\i infra/sql/001_service_roles.sql
\i infra/sql/002_verify_service_roles.sql
```

첫 파일은 전체 트랜잭션으로 실행한다. 기존 역할이나 스키마와 이름이 겹치면 중단하므로 기존 권한을 덮어쓰지 않는다. 최초 설정용이며 재실행 시 이미 존재하는 역할 오류가 정상적으로 발생한다. 오류가 나면 `ROLLBACK;`을 실행한 뒤 원인을 확인한다.

둘째 파일은 마이그레이션 역할로 검사 테이블, 시퀀스와 함수를 생성하고 각 앱 역할로 CRUD와 다른 스키마 접근 및 DDL 거부를 검증한다. 성공 시 PASS를 출력하고 검사 객체는 ROLLBACK으로 제거한다. 역할 멤버십도 검사한다. 관리자 세션의 SET ROLE 검증이므로 비밀번호 인증, GCP IAM 및 실제 앱 로그인까지 검증한 것은 아니다.

PASS 이후 psql의 비밀번호 입력창에서 서로 다른 비밀번호를 지정한다. 비밀번호를 SQL 문에 직접 적지 않는다.

```psql
\password cs_app
\password tft_app
\password platform_app
```

실행 계정은 생성 시 비밀번호가 없어 비밀번호 설정 전에는 비밀번호 인증으로 로그인할 수 없다. 로컬 앱의 비밀번호는 Git에서 제외된 `.env`, 배포 환경은 Secret Manager에서 관리한다. 관리자 비밀번호와 마이그레이션 권한을 앱에 전달하지 않는다.

## 후속 마이그레이션 규칙

관리자는 테이블 생성 전에 해당 역할로 전환한다. 기본 권한은 실제로 테이블을 생성한 역할에 적용되므로 postgres로 테이블을 만들면 앱에 위 기본 권한이 적용되지 않는다.

```sql
BEGIN;
SET LOCAL ROLE cs_migrator;
-- CREATE TABLE cs.example (...);
COMMIT;
```

LangGraph 체크포인터의 테이블 초기화 역시 마이그레이션 단계에서 해당 역할로 수행한다. 런타임 계정으로 `.setup()` 같은 DDL 초기화를 호출하지 않는다.

T13은 T19에서 추가한 `draft.rewrite` operation도 허용한다. T19 적용 후 명령 행이 있는 DB에
T13을 재적용해도 허용집합을 과거로 되돌리지 않는다. 정렬된 전체 재적용 묶음은 기존
명령 행을 보존하며 T19까지 적용한다. 미등록 operation은 계속 CHECK 제약으로 거부한다.
이 변경은 재적용 호환성 보완이며 공용 DB 적용 성공을 뜻하지 않는다.

## 전체 적용 순서

`infra/migrations/` 아래 서비스별 마이그레이션 76개의 적용 순서와 적용 전 확인 방법이다. cs·tft 마이그레이션은 platform의 표를 FK로 참조하지 않아 서비스 사이 순서 의존은 없다. 서비스 안에서는 파일명 정렬 순서가 아래 선행 조건을 모두 만족하며, 새 로컬 DB의 `infra/local/init.sh`도 001 다음 cs → tft → platform 순서로 각 폴더를 파일명 정렬로 적용한다. 표의 순서 번호는 추가된 차례라 파일명 정렬과 다를 수 있다(P20은 파일명상 P16 앞). 어느 쪽으로 적용해도 선행 조건을 만족한다.

**공용 상태 열: 운영자가 공용 DB(team4)에 적용·확인한 날짜를 이 열에 직접 기록한다.** 현재 이 저장소에는 공용 DB 적용 기록이 없어 모두 `미확인`이다. 기록 없이 적용됐다고 가정하지 않는다.

- 선행: 먼저 적용돼 있어야 하는 행. `(헤더)`는 파일 첫 줄 주석에 순서가 적혀 있다는 뜻이고, 나머지는 FK·권한 대상 객체로 정했다.
- 실행 역할: 파일 안에서 `SET LOCAL ROLE`로 전환하는 역할. postgres 행은 관리자 연결이 그대로 실행한다.
- 재실행: `가능`은 `IF NOT EXISTS`·확인 후 변경 블록으로 다시 실행해도 오류 없이 같은 상태가 된다. `불가`는 이미 적용된 DB에서 오류로 멈추므로(전체 트랜잭션이라 아무것도 남지 않음) 반복하지 않는다.
- 적용 전 확인: 아래 쿼리 형식으로 실행해 true면 이미 적용된 것이다.

| 순서 | 파일 | 선행 | 실행 역할 | 재실행 | 적용 전 확인 | 공용 상태 |
| --- | --- | --- | --- | --- | --- | --- |
| C1 | `cs/20261022_KAN-102_tickets.sql` | 001 | cs_migrator | 가능 | to_regclass('cs.tickets') | 미확인 |
| C2 | `cs/20261023_KAN-102_ticket_audit.sql` | C1 | cs_migrator | 가능 | to_regclass('cs.audit_events') | 미확인 |
| C3 | `cs/20261027_KAN-102_installed_units.sql` | C1 | cs_migrator | 가능 | to_regclass('cs.installed_units') | 미확인 |
| C4 | `cs/20261027_KAN-108_ticket_workflow.sql` | C1 | cs_migrator | 가능 | to_regclass('cs.ticket_workflows') | 미확인 |
| C5 | `cs/20261028_KAN-108_resolution_cases.sql` | C4 | cs_migrator | 가능 | 열 `cs.resolution_cases.dept_result_id` | 미확인 |
| C6 | `cs/20261028_KAN-112_improvement_requests.sql` | C1·C5 | cs_migrator | 가능 | to_regclass('cs.improvement_requests') | 미확인 |
| C7 | `cs/20261028_KAN-114_condition_comparisons.sql` | C6(헤더) | cs_migrator | 가능 | to_regclass('cs.condition_comparisons') | 미확인 |
| C8 | `cs/20261028_KAN-114_condition_comparisons_writes.sql` | C7(헤더) | cs_migrator | 가능 | 열 `cs.condition_comparison_items.reason` | 미확인 |
| C9 | `cs/20261028_KAN-114_condition_comparisons_writes_removals.sql` | C8(헤더) | cs_migrator | 가능 | to_regclass('cs.condition_comparison_removals') | 미확인 |
| C10 | `cs/20261029_KAN-99_search_chunks.sql` | 001 | cs_migrator | 가능 | to_regclass('cs.document_chunks') | 미확인 |
| C11 | `cs/20261030_KAN-193_return_subtype_confirmed.sql` | C4(헤더) | cs_migrator | 가능 | 열 `cs.ticket_workflows.return_subtype_confirmed` | 미확인 |
| C12 | `cs/20261029_KAN-194_checkpoint_schema.sql` | 001(`checkpoint_cs` 스키마) | cs_migrator | 가능 | to_regclass('checkpoint_cs.checkpoints') | 미확인 |
| C13 | `cs/20261030_KAN-110_customer_mail.sql` | C4(헤더) | cs_migrator | 가능 | to_regclass('cs.ticket_customer_sends') | 미확인 |
| C14 | `cs/20261030_KAN-107_ticket_analysis.sql` | C8(헤더, 감사 목록) | cs_migrator | 가능 | to_regclass('cs.ticket_analyses') | 미확인 |
| C15 | `cs/20261030_KAN-110_send_draft_link.sql` | C13·C14(헤더) | cs_migrator | 가능 | 열 `cs.ticket_customer_sends.draft_id` | 미확인 |
| C16 | `cs/20261031_KAN-117_followup_rounds.sql` | C6(헤더) | cs_migrator | 가능 | to_regclass('cs.request_rounds') | 미확인 |
| C17 | `cs/20261031_KAN-193_return_responsibility.sql` | C5·C11(헤더) | cs_migrator | 가능 | 열 `cs.ticket_workflows.return_responsibility` | 미확인 |
| C18 | `cs/20261101_KAN-107_worker_source.sql` | C10·C12·C13·C14(헤더) | 관리자(역할 생성) → cs_migrator | 가능 | 열 `cs.ticket_customer_sends.requested_info_codes` | 미확인 |
| C19 | `cs/20261102_KAN-107_attempt_usage.sql` | C14·C18(헤더) | cs_migrator | 가능 | 열 `cs.ticket_analyses.cost_usd` | 미확인 |
| C20 | `cs/20261103_KAN-99_chunk_embeddings.sql` | C10·C18, pgvector 확장(헤더) | cs_migrator | 가능(C18 재적용 뒤 다시 적용) | to_regclass('cs.chunk_embeddings') | 미확인 |
| C21 | `cs/20261104_KAN-200_draft_verification.sql` | C14·C18·C19(헤더) | cs_migrator | 가능 | to_regclass('cs.ticket_draft_verifications') | 미확인 |
| C22 | `cs/20261105_KAN-201_worker_notifications.sql` | C4·C18(헤더) | cs_migrator | 가능(C18 재적용 뒤 다시 적용) | cs_worker의 `cs.ticket_workflows.owner_user_id` SELECT | 미확인 |
| C23 | `cs/20261106_KAN-107_ticket_customer_tier.sql` | C1·C18(헤더) | cs_migrator | 가능(C18 재적용 뒤 다시 적용) | 열 `cs.tickets.customer_tier` | 미확인 |
| C24 | `cs/20261107_KAN-99_search_context.sql` | C10(헤더) | cs_migrator | 가능(표 재작성, 배타 잠금) | 열 `cs.document_chunks.search_context` | 미확인 |
| C25 | `cs/20261106_KAN-131_ticket_summaries.sql` | C1(헤더) | cs_migrator | 가능 | to_regclass('cs.ticket_summaries') | 미확인 |
| C26 | `cs/20261108_KAN-202_improvement_candidates.sql` | C6·C14(헤더) | cs_migrator | 가능 | 열 `cs.improvement_requests.candidate_reason_code` | 미확인 |
| T1 | `tft/20261023_KAN-119_projects.sql` | 001 | tft_migrator | 가능 | to_regclass('tft.organizations') | 미확인 |
| T2 | `tft/20261023_KAN-122_staffing_requests.sql` | T1(헤더) | tft_migrator | 가능 | to_regclass('tft.staffing_request_revisions') | 미확인 |
| T3 | `tft/20261025_KAN-123_competency_rechecks.sql` | T1 | postgres(SET ROLE 없음) | 가능 | to_regclass('tft.competency_checks_recommendation_competency_time') | 미확인 |
| T4 | `tft/20261025_KAN-123_recommendation_commands.sql` | T2 | tft_migrator | 가능 | to_regclass('tft.recommendation_commands') | 미확인 |
| T5 | `tft/20261025_KAN-127_decisions.sql` | T1 | tft_migrator | 가능 | to_regclass('tft.decision_commands') | 미확인 |
| T6 | `tft/20261029_KAN-99_search_chunks.sql` | 001 | tft_migrator | 가능 | to_regclass('tft.document_chunks') | 미확인 |
| T7 | `tft/20261029_KAN-182_rfp_response.sql` | T1 | tft_migrator | 가능 | to_regclass('tft.requirement_versions') | 미확인 |
| T8 | `tft/20261030_KAN-182_rfp_keys.sql` | T7(헤더) | tft_migrator | 가능 | 제약 `product_comparisons_version_key` | 미확인 |
| T9 | `tft/20261101_KAN-186_supplement_paths.sql` | T7(헤더) | tft_migrator | 가능 | 제약 `supplement_options_deficiency_cause_check`에 `CORE_EQUIPMENT_MISSING` | 미확인 |
| T10 | `tft/20261102_KAN-186_rnd_check.sql` | T7(헤더) | tft_migrator | 가능 | to_regclass('tft.department_kinds') | 미확인 |
| T11 | `tft/20261103_KAN-186_project_columns.sql` | T7·T10(헤더) | tft_migrator | 가능 | 열 `tft.projects.integration_holder` | 미확인 |
| T12 | `tft/20261105_KAN-199_requirement_assembly_clause.sql` | T7(헤더) | tft_migrator | 가능 | 열 `tft.requirement_versions.assembly_clause` | 미확인 |
| T13 | `tft/20261106_KAN-199_question_drafts.sql` | T10(헤더) | postgres(역할 없을 때) → tft_migrator | 가능 | 열 `tft.customer_questions.draft_key` | 미확인 |
| T14 | `tft/20261104_KAN-199_tft_worker.sql` | T7·T10·T11, P29(헤더) | postgres(역할 없을 때) → tft_migrator | 가능 | `has_table_privilege('tft_worker','tft.clauses','INSERT')` | 미확인 |
| T15 | `tft/20261107_KAN-188_requirement_semantic_fields.sql` | T7(헤더) | tft_migrator | 가능 | 열 `tft.requirement_versions.tolerance` | 미확인 |
| T16 | `tft/20261108_KAN-201_worker_notifications.sql` | T1·T14(헤더) | tft_migrator | 가능(T14 재적용 뒤 다시 적용) | tft_worker의 `tft.projects.responsible_user_id` SELECT | 미확인 |
| T17 | `tft/20261109_KAN-188_draft_execution.sql` | T7·T10~T15(헤더) | postgres(역할 확인) → tft_migrator | 가능(T14 재적용 뒤 다시 적용) | `draft_sentences.needs_rereview`·`draft_versions.source_job_id` 및 제한 worker 권한 | 미확인 |
| T18 | `tft/20261110_KAN-188_worker_output_status.sql` | T1·T7·T14(헤더) | tft_migrator | 가능(T14 재적용 뒤 다시 적용) | `has_column_privilege('tft_worker','tft.projects','output_status','UPDATE')` | 미적용 |
| T19 | `tft/20261111_KAN-188_draft_rewrite.sql` | T14·T17 | tft_migrator | 가능 | template_text·모델 사용량/대체 사유 저장 | 미적용 |
| T21 | `tft/20261113_KAN-188_rewrite_summary.sql` | T19 | tft_migrator | 가능 | `has_table_privilege('tft_app','tft.draft_rewrite_calls','SELECT')` | 미적용 |
| P1 | `platform/20261022_KAN-78_identity.sql` | 001 | platform_migrator | 가능 | to_regclass('identity.users') | 미확인 |
| P2 | `platform/20261023_KAN-77_oauth_authorizations.sql` | P1 | platform_migrator | 가능 | to_regclass('identity.oauth_authorizations') | 미확인 |
| P3 | `platform/20261023_KAN-77_sessions.sql` | P1 | platform_migrator | 가능 | to_regclass('identity.sessions') | 미확인 |
| P4 | `platform/20261023_KAN-78_manager_scopes.sql` | P1 | platform_migrator | 가능 | to_regclass('identity.departments') | 미확인 |
| P5 | `platform/20261023_KAN-85_evaluation_storage.sql` | P1 | platform_migrator | 가능 | to_regclass('evaluation.experiments') | 미확인 |
| P6 | `platform/20261024_KAN-78_user_department_code.sql` | P4 | platform_migrator | 가능 | 제약 `users_dept_fkey` | 미확인 |
| P7 | `platform/20261025_KAN-69_job_admission.sql` | P1 | platform_migrator | 불가 | to_regclass('runtime.jobs') | 미확인 |
| P8 | `platform/20261025_KAN-90_run_admission.sql` | P5·P7(헤더) | platform_migrator | 불가 | to_regclass('evaluation.run_control') | 미확인 |
| P9 | `platform/20261025_KAN-90_smoke_worker.sql` | P8 | platform_migrator | 가능 | 권한 `platform_app` UPDATE `evaluation.run_control.holder_id` | 미확인 |
| P10 | `platform/20261025_KAN-90_user_controls.sql` | P8 | platform_migrator | 불가 | to_regclass('evaluation.run_action_receipts') | 미확인 |
| P11 | `platform/20261026_KAN-69_dispatch_ledger.sql` | P7 | platform_migrator | 불가 | to_regclass('runtime.dispatch_attempts') | 미확인 |
| P12 | `platform/20261026_KAN-69_dispatch_operation.sql` | P11 | platform_migrator | 불가 | 열 `runtime.dispatch_attempts.operation_name` | 미확인 |
| P13 | `platform/20261027_KAN-81_mailbox_vault.sql` | P1 | 운영자 postgres(아래 KAN-81 절) | 불가 | to_regclass('mailbox_auth.connections') | 미확인 |
| P14 | `platform/20261027_KAN-90_worker_role.sql` | P5·P7·P8·P10 | postgres(CREATE ROLE) | 불가 | 역할 `evaluation_worker` | 미확인 |
| P15 | `platform/20261027_KAN-90_worker_usage_KAN-86.sql` | P14(헤더) | postgres | 가능 | 권한 `evaluation_worker` UPDATE `evaluation.runs.held_count` | 미확인 |
| P16 | `platform/20261028_KAN-71_mail_sync.sql` | P13(헤더) | platform_migrator(아래 KAN-71 절) | 불가 | to_regclass('integration.mail_sync_runs') | 미확인 |
| P17 | `platform/20261028_KAN-73_mail_triage.sql` | P16(헤더) | platform_migrator | 불가 | to_regclass('integration.mail_triage') | 미확인 |
| P18 | `platform/20261028_KAN-74_mail_send.sql` | P1·P13·P16(헤더) | platform_migrator(아래 KAN-74 절) | 불가 | to_regclass('integration.mail_send_intents') | 미확인 |
| P19 | `platform/20261028_KAN-81_mailbox_reconnect_state.sql` | P13·P16(헤더) | 운영자 postgres(아래 KAN-81 절) | 불가 | 열 `mailbox_auth.connections.reconnect_required_at` | 미확인 |
| P20 | `platform/20261028_KAN-69_dispatch_recovery.sql` | P11·P12(헤더) | platform_migrator | 가능 | 열 `runtime.dispatch_outbox.requeued_at` | 미확인 |
| P21 | `platform/20261028_KAN-86_attempt_usage_raw.sql` | P5·P15(헤더) | platform_migrator | 가능 | 열 `evaluation.case_attempts.usage_raw` | 미확인 |
| P22 | `platform/20261029_KAN-74_mail_send_column_grants.sql` | P18(헤더) | platform_migrator | 가능 | 권한 `platform_app` 표 단위 UPDATE `integration.mail_send_intents` 없음 | 미확인 |
| P23 | `platform/20261029_KAN-75_mail_links.sql` | P16·P18(헤더)·P17 | platform_migrator | 불가 | to_regclass('integration.mail_links') | 미확인 |
| P24 | `platform/20261029_KAN-182_tft_jobs.sql` | P7 | platform_migrator | 가능(다시 적용하면 P28도 이어서 다시 적용) | 열 `runtime.jobs.subject_id` | 미확인 |
| P25 | `platform/20261029_KAN-90_experiment_stage_knobs.sql` | P5 | platform_migrator | 가능 | 열 `evaluation.experiments.knobs` | 미확인 |
| P26 | `platform/20261030_KAN-110_mail_link_notify.sql` | P23(헤더) | platform_migrator | 가능 | 열 `integration.mail_links.business_notified_at` | 미확인 |
| P27 | `platform/20261030_KAN-90_receipt_origin.sql` | P8·P14(헤더) | platform_migrator | 가능 | 열 `evaluation.admission_receipts.origin` | 미확인 |
| P28 | `platform/20261031_KAN-194_cs_jobs.sql` | P11·P24(헤더) | postgres(CREATE ROLE `cs_worker`) → platform_migrator | 가능(P24 뒤에만, `jobs_owner_kind_route`는 이 파일이 cs까지 정의) | to_regclass('runtime.job_control') | 미확인 |
| P29 | `platform/20261031_KAN-199_tft_worker.sql` | P28(헤더) | postgres(CREATE ROLE `tft_worker`) → platform_migrator | 가능 | 역할 `tft_worker` | 미확인 |
| P30 | `platform/20261101_KAN-201_notifications.sql` | P1·P4·P7·P28·P29(헤더) | 운영자(CREATE SCHEMA) → platform_migrator | 가능 | to_regclass('notify.notification_intents') | 미확인 |
| P31 | `platform/20261102_KAN-201_web_push.sql` | P30 | platform_migrator | 가능 | notify.push_subscriptions / push_deliveries | 미확인 |
| P32 | `platform/20261108_KAN-159_job_reap.sql` | P28(헤더) | platform_migrator | 가능 | has_column_privilege('platform_app','runtime.job_control','failure_code','UPDATE') | 미적용 |
| P33 | `platform/20261109_KAN-188_draft_rewrite_jobs.sql` | P28 | platform_migrator | 가능 | rfp.draft_rewrite 접수 제약 | 미적용 |

적용 전 확인 쿼리(운영자 연결, 읽기 전용):

```sql
-- to_regclass 행: NULL이 아니면 이미 있다.
SELECT to_regclass('cs.tickets') IS NOT NULL;
-- 열 행
SELECT EXISTS (SELECT 1 FROM information_schema.columns
  WHERE table_schema = 'cs' AND table_name = 'resolution_cases' AND column_name = 'dept_result_id');
-- 제약 행(P6, T8)
SELECT EXISTS (SELECT 1 FROM pg_constraint
  WHERE conname = 'users_dept_fkey' AND conrelid = 'identity.users'::regclass);
SELECT EXISTS (SELECT 1 FROM pg_constraint
  WHERE conname = 'product_comparisons_version_key' AND conrelid = 'tft.product_comparisons'::regclass);
-- 권한 행(P9, P15)
SELECT has_column_privilege('platform_app', 'evaluation.run_control', 'holder_id', 'UPDATE');
SELECT has_column_privilege('evaluation_worker', 'evaluation.runs', 'held_count', 'UPDATE');
-- 권한 행(P22): 표 단위 UPDATE가 회수됐으면 true다.
SELECT NOT has_table_privilege('platform_app', 'integration.mail_send_intents', 'UPDATE');
-- 역할 행(P14)
SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'evaluation_worker');
```

적용 후 권한 확인(결과가 모두 false여야 한다). 검사 SQL 파일은 001·002, 003(플랫폼 인증), 004(KAN-81)만 있고 나머지는 아래 쿼리로 확인한다.

```sql
SELECT has_table_privilege('cs_app', 'cs.audit_events', 'UPDATE');
SELECT has_table_privilege('cs_app', 'cs.installed_units', 'INSERT');
SELECT has_table_privilege('cs_app', 'cs.condition_comparison_removals', 'DELETE');
SELECT has_table_privilege('tft_app', 'tft.staffing_request_revisions', 'UPDATE');
SELECT has_table_privilege('tft_app', 'tft.decision_history', 'DELETE');
SELECT has_table_privilege('tft_app', 'tft.requirement_versions', 'UPDATE'); -- T7 뒤
SELECT has_table_privilege('tft_app', 'runtime.jobs', 'INSERT'); -- P24 뒤(tft에는 runtime 권한 없음)
SELECT has_table_privilege('platform_app', 'evaluation.case_attempts', 'UPDATE');
SELECT has_table_privilege('platform_app', 'evaluation.metrics', 'DELETE');
SELECT has_column_privilege('platform_app', 'mailbox_auth.connections', 'owner_user_id', 'UPDATE'); -- P19 뒤
SELECT has_table_privilege('platform_app', 'integration.mail_triage', 'UPDATE'); -- P17 뒤
SELECT has_table_privilege('platform_app', 'integration.mail_send_events', 'UPDATE'); -- P18 뒤
SELECT has_schema_privilege('cs_app', 'runtime', 'USAGE');
SELECT has_schema_privilege('tft_app', 'mailbox_auth', 'USAGE');
SELECT has_schema_privilege('cs_app', 'integration', 'USAGE');
SELECT rolcanlogin FROM pg_roles WHERE rolname = 'evaluation_worker';
```

로컬 DB의 개별 적용(기존 로컬 DB에 새 파일만 더할 때, 위 순서대로 한 파일씩):

```bash
python3 scripts/workspace.py migrate cs 20261022_KAN-102_tickets.sql
python3 scripts/workspace.py migrate cs 20261023_KAN-102_ticket_audit.sql
python3 scripts/workspace.py migrate cs 20261027_KAN-102_installed_units.sql
python3 scripts/workspace.py migrate cs 20261027_KAN-108_ticket_workflow.sql
python3 scripts/workspace.py migrate cs 20261028_KAN-108_resolution_cases.sql
python3 scripts/workspace.py migrate cs 20261028_KAN-112_improvement_requests.sql
python3 scripts/workspace.py migrate cs 20261028_KAN-114_condition_comparisons.sql
python3 scripts/workspace.py migrate cs 20261028_KAN-114_condition_comparisons_writes.sql
python3 scripts/workspace.py migrate cs 20261028_KAN-114_condition_comparisons_writes_removals.sql
python3 scripts/workspace.py migrate cs 20261029_KAN-99_search_chunks.sql
python3 scripts/workspace.py migrate cs 20261030_KAN-193_return_subtype_confirmed.sql
python3 scripts/workspace.py migrate cs 20261029_KAN-194_checkpoint_schema.sql
python3 scripts/workspace.py migrate cs 20261030_KAN-110_customer_mail.sql
python3 scripts/workspace.py migrate cs 20261030_KAN-107_ticket_analysis.sql
python3 scripts/workspace.py migrate cs 20261030_KAN-110_send_draft_link.sql
python3 scripts/workspace.py migrate cs 20261031_KAN-117_followup_rounds.sql
python3 scripts/workspace.py migrate cs 20261031_KAN-193_return_responsibility.sql
python3 scripts/workspace.py migrate cs 20261101_KAN-107_worker_source.sql
python3 scripts/workspace.py migrate cs 20261102_KAN-107_attempt_usage.sql
python3 scripts/workspace.py migrate platform 20261030_KAN-110_mail_link_notify.sql
python3 scripts/workspace.py migrate platform 20261030_KAN-90_receipt_origin.sql
python3 scripts/workspace.py migrate tft 20261023_KAN-119_projects.sql
python3 scripts/workspace.py migrate tft 20261023_KAN-122_staffing_requests.sql
python3 scripts/workspace.py migrate tft 20261025_KAN-123_competency_rechecks.sql
python3 scripts/workspace.py migrate tft 20261025_KAN-123_recommendation_commands.sql
python3 scripts/workspace.py migrate tft 20261025_KAN-127_decisions.sql
python3 scripts/workspace.py migrate tft 20261029_KAN-182_rfp_response.sql
python3 scripts/workspace.py migrate tft 20261029_KAN-99_search_chunks.sql
python3 scripts/workspace.py migrate tft 20261030_KAN-182_rfp_keys.sql
python3 scripts/workspace.py migrate tft 20261101_KAN-186_supplement_paths.sql
python3 scripts/workspace.py migrate tft 20261102_KAN-186_rnd_check.sql
python3 scripts/workspace.py migrate tft 20261103_KAN-186_project_columns.sql
python3 scripts/workspace.py migrate platform 20261022_KAN-78_identity.sql
python3 scripts/workspace.py migrate platform 20261023_KAN-77_oauth_authorizations.sql
python3 scripts/workspace.py migrate platform 20261023_KAN-77_sessions.sql
python3 scripts/workspace.py migrate platform 20261023_KAN-78_manager_scopes.sql
python3 scripts/workspace.py migrate platform 20261023_KAN-85_evaluation_storage.sql
python3 scripts/workspace.py migrate platform 20261024_KAN-78_user_department_code.sql
python3 scripts/workspace.py migrate platform 20261025_KAN-69_job_admission.sql
python3 scripts/workspace.py migrate platform 20261025_KAN-90_run_admission.sql
python3 scripts/workspace.py migrate platform 20261025_KAN-90_smoke_worker.sql
python3 scripts/workspace.py migrate platform 20261025_KAN-90_user_controls.sql
python3 scripts/workspace.py migrate platform 20261026_KAN-69_dispatch_ledger.sql
python3 scripts/workspace.py migrate platform 20261026_KAN-69_dispatch_operation.sql
python3 scripts/workspace.py migrate platform 20261027_KAN-81_mailbox_vault.sql
python3 scripts/workspace.py migrate platform 20261027_KAN-90_worker_role.sql
python3 scripts/workspace.py migrate platform 20261027_KAN-90_worker_usage_KAN-86.sql
python3 scripts/workspace.py migrate platform 20261028_KAN-71_mail_sync.sql
python3 scripts/workspace.py migrate platform 20261028_KAN-73_mail_triage.sql
python3 scripts/workspace.py migrate platform 20261028_KAN-74_mail_send.sql
python3 scripts/workspace.py migrate platform 20261028_KAN-81_mailbox_reconnect_state.sql
python3 scripts/workspace.py migrate platform 20261028_KAN-69_dispatch_recovery.sql
python3 scripts/workspace.py migrate platform 20261028_KAN-86_attempt_usage_raw.sql
python3 scripts/workspace.py migrate platform 20261029_KAN-182_tft_jobs.sql
python3 scripts/workspace.py migrate platform 20261029_KAN-74_mail_send_column_grants.sql
python3 scripts/workspace.py migrate platform 20261029_KAN-75_mail_links.sql
python3 scripts/workspace.py migrate platform 20261029_KAN-90_experiment_stage_knobs.sql
```

공용 DB는 운영 승인 뒤 운영자 터미널에서 위 순서대로 한 파일씩 적용한다. 각 파일 전에 적용 전 확인 쿼리를 실행해 true면 건너뛰고, 적용 후 공용 상태 열에 날짜를 기록한다. 에이전트는 공용 DB에 적용하지 않는다. KAN-81·KAN-71은 아래 절의 추가 단계를 따른다.

```bash
psql -X -v ON_ERROR_STOP=1 -f infra/migrations/<서비스>/<파일>
```

## 사서함 보관 준비 (KAN-81)

현재 구현은 내부 보관 경계이며 공용 환경에는 미적용이다. 기존003 인증 점검과 기본
로그인은 새 보관 테이블·키 없이 유지한다. 아래는 별도 운영 승인 후 운영자 터미널에서
진행할 준비이며, 에이전트가 공유 DB나 Secret/IAM을 변경하지 않는다.

1. 현재 연결 대상이 공용 프로젝트의 team4 DB인지 읽기 전용으로 확인한다. 기존 역할·identity
   테이블과 새 mailbox_auth 스키마 존재/소유자를 확인하고, 기존 스키마가 있으면 덮어쓰거나
   마이그레이션을 반복하지 않는다. 임시 검사 DB는 kan77_auth_test만 허용한다.
2. 운영자 postgres 연결에서 `infra/migrations/platform/20261027_KAN-81_mailbox_vault.sql`을
   `psql -X -v ON_ERROR_STOP=1 -f ...`로 한 번 적용한다. 이 파일은 새 스키마를 생성하므로
   이미 있으면 실패한다. 플랫폼 런타임에는 새 스키마 USAGE와 테이블 DML만 부여하고
   C/S·TFT·평가 worker에는 접근을 부여하지 않는다.
3. platform_app 연결에서 `infra/sql/004_check_mailbox_vault.sql`을 같은 psql 검사 옵션으로
   실행한다. 스크립트는 BEGIN READ ONLY/ROLLBACK으로 테이블·최소 권한·서비스 격리를
   확인하며 캐시나 사용자 식별자를 출력하지 않는다. 기존003 점검에 보관 조건을 합치지 않는다.
   PostgreSQL 행 잠금에는 identity.users/user_roles/sessions의 SELECT·UPDATE 권한도 필요하므로
   기존 권한을 별도로 확인한다. 새 마이그레이션은 이 identity 권한을 추가하거나 확대하지 않는다.
4. 별도 승인으로 보관 전용 32바이트 임의 키를 만들고 기존 플랫폼 계정만 읽을 수 있는
   Secret Manager의 고정 버전에 등록한다. 표준 base64로 서버에 주입하며 시크릿 원문은
   터미널 출력·명령행 인자·채팅·.env 예시에 남기지 않는다. 로그인 client secret이나 Gemini
   키를 재사용하지 않는다. 실제 Secret 이름·버전/IAM·Cloud Run 주입은 운영자 작업이며 이번에 적용하지 않는다.
   플랫폼의 `MAILBOX_AUTH_ENCRYPTION_KEY`·`MAILBOX_AUTH_KEY_VERSION`으로 받으며 웹에는 키를 주입하지 않는다.
5. 초기 HTTP 경로는 웹·플랫폼의 `MAILBOX_AUTH_ENABLED=false`로 기본 차단된다. 기존 Web 앱에
   정확한 사서함 callback을 추가하고 플랫폼 `MAILBOX_AUTH_REDIRECT_URI`를 준비한 뒤 별도 승인으로
   배포·활성화한다. [HTTP/쿠키·CSRF 계약](../../contracts/platform/auth.md#사서함-초기-연결-http-기본-비활성-운영-미적용)을 따른다.
   소비자 getter·실제 Graph 검증은 별도이며 키/SQL 준비만으로 업무 수집을
   활성화하거나 KAN-80·81 전체 완료로 표시하지 않는다. 단일 키 버전이며 자동 키 회전은 없다.

재연결 필요 상태(`20261028_KAN-81_mailbox_reconnect_state.sql`)는 위 2단계와 KAN-71 수집 표 적용 뒤
운영자 postgres 연결에서 같은 방식으로 한 번 적용한다. 연결 표에 재연결 필요 시각·사유 열을 더하고
platform_app의 UPDATE를 열 단위로 바꾼다(소유자·Microsoft 식별자 열은 바꿀 수 없음). 적용 뒤 3단계의
`004_check_mailbox_vault.sql`을 다시 실행하면 열 단위 권한까지 확인한다(적용 전에도 같은 검사가 통과한다).

새 연결/해제/재연결은 현재 세션과 DB 권한을 확인해야 한다. 작업용 조회·갱신은 인증한
서버 작업의 소유자를 사용하며, 브라우저가 보낸 ID를 그대로 넘기지 않는다. 외부 토큰 갱신
중 DB 잠금을 유지하지 않고 저장 단계에서 현재 연결 개정을 재검사한다. 상세 호출 계약은
[플랫폼 인증](../../contracts/platform/auth.md#사서함-보관-계층-kan-81-부분-구현-운영-미적용)을 따른다.

## 메일 수집 표 적용 (KAN-71)

현재 구현은 서버 내부 수집 함수이며 공용 환경에는 미적용이다. 아래도 별도 운영 승인 후 운영자 터미널에서
진행하며, 에이전트가 공유 DB를 변경하지 않는다.

1. 위 사서함 보관 준비 1~3단계(KAN-81 마이그레이션 적용과 `004_check_mailbox_vault.sql` 확인)가 끝났는지 확인한다.
   KAN-71 표는 `mailbox_auth.connections`를 참조하므로 그 뒤에만 적용할 수 있다.
2. 운영자 postgres 연결에서 `infra/migrations/platform/20261028_KAN-71_mail_sync.sql`을
   `psql -X -v ON_ERROR_STOP=1 -f ...`로 한 번 적용한다. 로컬은 `python3 scripts/workspace.py migrate platform 20261028_KAN-71_mail_sync.sql`이다.
   전체가 한 트랜잭션이라 KAN-81 표가 없거나 수집 표가 이미 있으면 오류로 멈추고 아무것도 남기지 않는다.
   이때 순서를 바로잡아 다시 적용하며, 이미 적용된 DB에 반복하지 않는다.
3. 적용 뒤 `integration` 스키마에 `mail_sync_runs`·`mail_sync_cursors`·`mail_messages`·`mail_attachments` 네 표가 있고,
   `platform_app`은 SELECT·INSERT·UPDATE만, `cs_app`·`tft_app`은 권한이 없는지 읽기 전용으로 확인한다.
   이 확인용 검사 SQL은 아직 없다.
4. 연결 행은 수집 표가 참조하므로 지우지 않는다(해제는 `state='disconnected'`). 참조가 있는 연결의 DELETE는 FK로 거부된다.

표 적용만으로 수집이 켜지지 않는다. 수집을 부르는 워커·정기 실행은 KAN-72, 운영 객체 저장소는 후속 작업이다.
상세는 [메일 수집 계약](../../contracts/platform/mail.md)을 따른다.

## 회신 연결 표 적용 (KAN-75)

메일 발송 표(KAN-74, 아래)와 분류 표(KAN-73) 뒤에 `infra/migrations/platform/20261029_KAN-75_mail_links.sql`을 한 번 적용한다. `integration.mail_links`·`mail_reply_reviews`·`mail_reply_checks`가 생기고, `platform_app`은 세 표에 SELECT·INSERT, 검토의 결정 칸과 판정 표시의 결과 칸만 UPDATE한다(연결·후보 수정과 삭제 불가). 적용 전에도 수집 워커는 동작하고 회신 판정만 실패로 센다.

## 메일 발송 표 적용 (KAN-74)

위 메일 수집 표 적용(KAN-71)이 끝난 뒤, 같은 방식으로 `infra/migrations/platform/20261028_KAN-74_mail_send.sql`을
한 번 적용하고, 이어서 `20261029_KAN-74_mail_send_column_grants.sql`을 한 번 적용한다. 로컬은 `python3 scripts/workspace.py migrate platform <파일>`을 두 파일 순서대로 실행한다.
`integration.mail_send_intents`·`mail_send_events` 두 표가 생기고, `platform_app`은 의도에 SELECT·INSERT와 상태 열(`status`·`attempts`·`error_code`·`submitted_at`·`accepted_at`·`sent_*`·`updated_at`)만 UPDATE,
이력에 SELECT·INSERT만 가진다(승인 내용·이력 수정과 삭제 불가). `identity.users`와 `mailbox_auth.connections`를 참조한다.
표 적용만으로 발송이 켜지지 않는다. 상세는 [메일 수집 계약](../../contracts/platform/mail.md)의 발송 절을 따른다.

근거: [PostgreSQL 스키마 권한](https://www.postgresql.org/docs/17/ddl-schemas.html), [생성자별 기본 권한](https://www.postgresql.org/docs/17/sql-alterdefaultprivileges.html), [Cloud SQL 사용자 생성](https://docs.cloud.google.com/sql/docs/postgres/create-manage-users).


### KAN-200 초안 검증 적용 (C21)

C14(분석·초안), C18(worker 역할), C19(시도 사용량) 이후 C21을 적용하고 KAN-200 worker·C/S API를 배포한다.
파일명 정렬에서는 C20 뒤다. 새 로컬 DB와 C/S 테스트 fixture는 폴더 정렬로 포함한다.

```sh
python3 scripts/workspace.py migrate cs 20261104_KAN-200_draft_verification.sql
```

재실행은 표·인덱스를 유지하고 권한만 같은 형태로 재설정한다. `cs_worker`는 SELECT·INSERT만,
`cs_app`은 SELECT만 가지며 둘 다 UPDATE·DELETE·TRUNCATE할 수 없다. 기존 초안 행은 변경하지 않는다.
검증 결과는 기존 초안 조회의 `verification_status`·`verifications`에서 확인한다. 적용 및 DB 검사는
운영자/CI에서 확인해야 하며 이 문서의 표는 적용 성공 기록이 아니다.


### KAN-201 앱 내 알림 적용 (N1/N2/E1)

C22는 C4·C18 뒤, T16은 T1·T14 뒤에 적용한다. P30은 P1·P4·P7·P28·P29 이후에 적용한다.
서비스 간 순서는 C22/T16/P30 어느 쪽이 먼저여도 된다(기존 worker 역할을 이용하며 identity 조회 권한은 추가하지 않는다).
세 파일을 모두 적용한 뒤 N1/N2/E1 코드를 배포한다. C18 또는 T14를 다시 적용하면 해당 worker의 업무 표 권한이
초기화되므로 각각 C22 또는 T16도 다시 적용한다. P30은 notify 스키마의 알림 두 표 권한을 정리한다. 구버전 P30이 부여한 cs_app/tft_app의 runtime USAGE는 회수하며 기존 worker/platform의 runtime 권한은 유지한다.

```sh
python3 scripts/workspace.py migrate cs 20261105_KAN-201_worker_notifications.sql
python3 scripts/workspace.py migrate tft 20261108_KAN-201_worker_notifications.sql
python3 scripts/workspace.py migrate platform 20261101_KAN-201_notifications.sql
```

writer는 신규 intent의 지정 열 INSERT만 가지며 SELECT/RETURNING을 전제하지 않는다. 플랫폼만 펼침 메타데이터와
읽음 시각을 갱신한다. 최소 권한·중복 INSERT·동시 펼침·실패 시 rollback은 CI의 전용 DB 검사로 확인한다.
위 표의 공용 적용 상태는 실행 근거 없이 완료로 바꾸지 않는다.


P30은 운영자 연결에서 `CREATE SCHEMA IF NOT EXISTS notify AUTHORIZATION platform_migrator` 뒤 소유 역할로 전환한다.
새 포괄 CREATE 권한은 부여하지 않는다. 구버전 `runtime.notification_intents`·`runtime.user_notifications`가 있으면
`SET SCHEMA notify`로 옮겨 의도·수신·읽음 행과 PK/FK·인덱스를 보존한다. 새 설치와 2회 재적용을 지원한다.
양쪽 스키마에 동명 표가 있으면 `NOTIFICATION_SCHEMA_CONFLICT`로 전체 마이그레이션을 중단한다.
어느 이력도 삭제·자동 병합하지 않고 운영자가 충돌 원인을 확인한다. 두 표는 함께 notify로 이동하므로
업무 앱의 runtime 작업 표 접근은 여전히 허용하지 않는다.


### KAN-201 Web Push 적용 (P31)

P30 뒤 `python3 scripts/workspace.py migrate platform 20261102_KAN-201_web_push.sql`을 적용한다.
재실행은 기존 구독·전송 장부·PK/FK를 보존하고 신규 표 권한만 재설정한다. platform_app만 구독을
조회/등록/삭제하고 장부를 생성·갱신한다. 구독의 `lock_marker` 비업무 열 UPDATE만 추가 허용하여
PostgreSQL `FOR UPDATE` 권한 요구를 충족한다. 구독 id/소유권/endpoint/키/생성 시각 UPDATE는
허용하지 않고 기존 세대 잠금과 DELETE+INSERT 교체를 유지한다. 재적용은 누락 열을 추가하고
기존 구독 열 ACL도 회수 후 이 최소 권한만 재부여한다. cs_app/tft_app 및 worker에는 신규 권한이 없다.
실제 DB/역할/재적용 검사는 CI 전용 DB로 확인하며 여기의 미확인은 적용 성공을 뜻하지 않는다.

운영자 준비 순서(코딩 에이전트 실행 금지): O2 `infra/setup_notification_push.sh --prepare-identities`
preview 확인 → 운영자 `--prepare-identities --apply`로 전용 계정 준비 → O1 `infra/register_vapid_secret.py`
preview 확인 및 운영자 `--apply`로 키 버전 1 등록 → 공개키/운영 연락처/플랫폼 URL/프로젝트 번호와
플랫폼 digest 이미지 설정 → O2 preview 확인 및 운영자 `--apply` → DB/설정/실행 권한 확인 후
별도 활성화. O2는 기존 Scheduler를 첫 변경으로 pause하고 API를 OFF로 둔 채 Job을 배포한다.
Scheduler는 POST `/health` 비발송 대상으로 생성/갱신하고 다시 pause 및 PAUSED를 확인한다.
마지막 공개 설정도 OFF를 유지한다. 중간 실패 시 새로 활성화된 dispatch를 남기지 않는다.
운영자는 기존 진행 중 요청/Job과 모든 serving revision의 OFF를 확인한 후, paused Scheduler의
URI를 실제 내부 dispatch로 변경하고 PAUSED를 확인 → API 명시 활성화 → Scheduler 명시 resume한다.
준비 스크립트는 활성화/Job 실행을 하지 않으며 pause 이전에 시작한 작업을 소급 취소하지 않는다.
개인키는 Job 실행 계정에만 접근을 허용하고 API에는 공개키만 주입한다. 원래 공개키를 분실한 기존
Secret은 O1이 읽거나 교체하지 않는다. 해당 키와 맞는 기존 설정을 운영자가 확인한다.
운영 스크립트 작성·정적 검사와 실제 GCP 준비·푸시 수신 검증을 구분한다.

## worker 로그인 (KAN-159)

`cs_worker`·`tft_worker`는 NOLOGIN 역할이다. worker는 그 역할의 멤버 로그인으로 접속하고, 앱·마이그레이터 역할 멤버이면 시작을 거부한다. 공용 DB에는 관리자가 한 번 만든다. 비밀번호는 명령 기록에 남기지 않는다(`\password`).

```sql
CREATE ROLE cs_worker_login LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE ROLE tft_worker_login LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT cs_worker TO cs_worker_login;
GRANT tft_worker TO tft_worker_login;
-- 001_service_roles.sql이 PUBLIC 접속을 막고 앱·마이그레이터에만 CONNECT를 준다. worker 로그인은 따로 준다.
GRANT CONNECT ON DATABASE team4 TO cs_worker_login, tft_worker_login;
```

같은 비밀번호를 `CS_WORKER_DB_PASSWORD`·`TFT_WORKER_DB_PASSWORD`로 내보내 `infra/register_db_secrets.py`로 등록한다. 이 역할의 권한 마이그레이션(P29, T14 등)이 먼저 적용돼 있어야 한다.
