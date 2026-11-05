-- KAN-86: 기존 KAN-90_worker_role.sql 다음에 관리자 연결로 적용한다.
-- 합성 C/S 시도의 기록 열과 실패 카운터만 추가한다. 시도 읽기/수정/삭제 권한은 추가하지 않는다.
\set ON_ERROR_STOP on
BEGIN;
GRANT INSERT (model_version,finish_reasons,error_code,http_status,latency_ms,
    input_tokens,output_tokens,thought_tokens,cached_input_tokens,total_tokens)
    ON evaluation.case_attempts TO evaluation_worker;
GRANT SELECT (held_count,failed_count) ON evaluation.runs TO evaluation_worker;
GRANT UPDATE (held_count,failed_count) ON evaluation.runs TO evaluation_worker;
COMMIT;
