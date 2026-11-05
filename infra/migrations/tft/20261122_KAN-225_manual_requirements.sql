-- KAN-225: 원문에서 요구 직접 추가와 요구에서 빼기·되살리기.
-- 추가한 요구는 기존 요구 표에 사람 입력 판본(human_input, PROVIDED)으로 쓰고, 추가·빼기·되살리기는
-- requirement_decisions 한 행으로 남긴다(책임자 확인 대상, 멱등 결과). 빼도 요구 행은 지우지 않는다.
-- requirement_decisions의 연산 CHECK는 KAN-218만 정의한다(CREATE TABLE IF NOT EXISTS라 재적용해도 다시 정의하지
-- 않는다). 그래서 이 파일이 허용 집합을 넓혀도 이전 마이그레이션 재적용이 새 값의 행 때문에 깨지지 않는다.
-- 다시 적용해도 깨지지 않는다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;

ALTER TABLE tft.requirement_decisions DROP CONSTRAINT IF EXISTS requirement_decisions_action_check;
ALTER TABLE tft.requirement_decisions ADD CONSTRAINT requirement_decisions_action_check
    CHECK (action IN ('confirm_met', 'record_deviation', 'request_check', 'add_query', 'select_partner',
        'ask_rnd', 'assign', 'change_verdict', 'undo', 'review_confirm', 'review_return',
        'add_requirement', 'exclude_requirement', 'restore_requirement',
        -- KAN-227이 넓힌 값. 이 파일이 다시 적용될 때 좁혀 그 행을 깨지 않도록 같은 집합을 둔다.
        'confirm_drawing'));
-- 처리 작업의 초안·맥락은 뺀 요구를 넣지 않는다. 빼기 표시를 계산하는 열만 읽는다
-- (20261104_KAN-199_tft_worker.sql이 재적용될 때도 같은 권한을 복구한다).
GRANT SELECT (id, requirement_id, detail, created_at) ON tft.requirement_decisions TO tft_worker;
COMMIT;
