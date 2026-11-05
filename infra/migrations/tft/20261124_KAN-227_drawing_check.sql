-- KAN-227: 도면 확인 결정(confirm_drawing). 도면 OCR 조항에서 나온 요구를 규격서와 맞는다고 확인한다.
-- 판단(요구 판본)은 바꾸지 않고 requirement_decisions 한 행만 남긴다. 허용 집합만 넓히고 표·행은 바꾸지 않는다.
-- 20261122_KAN-225_manual_requirements.sql과 같은 방식이다. 다시 적용해도 깨지지 않는다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE tft_migrator;

ALTER TABLE tft.requirement_decisions DROP CONSTRAINT IF EXISTS requirement_decisions_action_check;
ALTER TABLE tft.requirement_decisions ADD CONSTRAINT requirement_decisions_action_check
    CHECK (action IN ('confirm_met', 'record_deviation', 'request_check', 'add_query', 'select_partner',
        'ask_rnd', 'assign', 'change_verdict', 'undo', 'review_confirm', 'review_return',
        'add_requirement', 'exclude_requirement', 'restore_requirement', 'confirm_drawing'));
COMMIT;
