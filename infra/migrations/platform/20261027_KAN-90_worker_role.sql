-- KAN-90: 관리자 연결로 한 번 적용. 로그인/IAM 계정 연결은 운영자 별도 절차다.
\set ON_ERROR_STOP on
BEGIN;
CREATE ROLE evaluation_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA identity, evaluation, runtime TO evaluation_worker;
GRANT SELECT (id,is_active) ON identity.users TO evaluation_worker;
GRANT SELECT ON evaluation.experiments, evaluation.run_control TO evaluation_worker;
GRANT SELECT (experiment_id,target_ref,data_origin,attributes)
    ON evaluation.experiment_targets TO evaluation_worker;
GRANT SELECT (id,status,needs_review,job_id,requested_count,requested_by_user_id,
    provider,experiment_id,started_at,valid_output_count) ON evaluation.runs TO evaluation_worker;
GRANT SELECT (run_id) ON evaluation.admission_receipts TO evaluation_worker;
GRANT SELECT (id,run_id,experiment_id,target_ref,status) ON evaluation.cases TO evaluation_worker;
GRANT SELECT (id) ON runtime.jobs TO evaluation_worker;
GRANT SELECT (job_id,sequence) ON runtime.job_events TO evaluation_worker;
GRANT INSERT (job_id,sequence,type,payload) ON runtime.job_events TO evaluation_worker;
GRANT UPDATE (status,started_at,finished_at,valid_output_count,needs_review,failure_code)
    ON evaluation.runs TO evaluation_worker;
GRANT UPDATE (generation,holder_id,lease_expires_at,version)
    ON evaluation.run_control TO evaluation_worker;
GRANT UPDATE (status,final_output,completed_at) ON evaluation.cases TO evaluation_worker;
GRANT UPDATE (status) ON runtime.jobs TO evaluation_worker;
GRANT INSERT (id,case_id,step,attempt,is_final,outcome,provider,model_id,input_sha256,
    raw_output,parsed_output,started_at,finished_at) ON evaluation.case_attempts TO evaluation_worker;
COMMIT;
