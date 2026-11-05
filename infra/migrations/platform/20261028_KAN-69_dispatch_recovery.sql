-- KAN-69: 운영자 명령으로만 쓰는 전송 장부 대조·재처리 기록. 관리자 연결로 적용한다.
-- 20261026_KAN-69_dispatch_ledger.sql과 20261026_KAN-69_dispatch_operation.sql 뒤에 적용한다.
-- MANUAL_CHECK는 operation 이름 없이 UNKNOWN이 된 전송의 종료 상태다. 자동 확보·재전송하지 않는다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;
DO $states$
BEGIN
    -- 상태 목록에 MANUAL_CHECK가 없을 때만 넓혀 재실행해도 같은 상태가 된다.
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'dispatch_outbox_state_check'
          AND conrelid = 'runtime.dispatch_outbox'::regclass
          AND pg_get_constraintdef(oid) LIKE '%MANUAL_CHECK%'
    ) THEN
        ALTER TABLE runtime.dispatch_outbox DROP CONSTRAINT IF EXISTS dispatch_outbox_state_check;
        ALTER TABLE runtime.dispatch_outbox ADD CONSTRAINT dispatch_outbox_state_check CHECK (
            state IN ('PENDING','CLAIMED','SENDING','ACCEPTED','REJECTED','UNKNOWN','BLOCKED',
                      'EXHAUSTED','MANUAL_CHECK')
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'dispatch_attempts_state_check'
          AND conrelid = 'runtime.dispatch_attempts'::regclass
          AND pg_get_constraintdef(oid) LIKE '%MANUAL_CHECK%'
    ) THEN
        ALTER TABLE runtime.dispatch_attempts DROP CONSTRAINT IF EXISTS dispatch_attempts_state_check;
        ALTER TABLE runtime.dispatch_attempts ADD CONSTRAINT dispatch_attempts_state_check CHECK (
            state IN ('CLAIMED','SENDING','ACCEPTED','REJECTED','UNKNOWN','EXPIRED','BLOCKED',
                      'MANUAL_CHECK')
        );
    END IF;
END
$states$;
-- 마지막 operation 대조 시각. 대조 결과 원문은 저장하지 않는다.
ALTER TABLE runtime.dispatch_attempts ADD COLUMN IF NOT EXISTS checked_at timestamptz;
-- 재처리 기록. 운영자 ID는 CLI 실행이라 남지 않는다.
ALTER TABLE runtime.dispatch_outbox
    ADD COLUMN IF NOT EXISTS attempt_limit integer CHECK (attempt_limit IS NULL OR attempt_limit > 0),
    ADD COLUMN IF NOT EXISTS requeue_count integer NOT NULL DEFAULT 0 CHECK (requeue_count >= 0),
    ADD COLUMN IF NOT EXISTS requeued_at timestamptz;
GRANT UPDATE (checked_at) ON runtime.dispatch_attempts TO platform_app;
GRANT UPDATE (attempt_limit, requeue_count, requeued_at) ON runtime.dispatch_outbox TO platform_app;
COMMIT;
