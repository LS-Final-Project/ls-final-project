-- KAN-232 다시 채점: 저장 요구를 모델 없이 자사 사양과 다시 비교한다(worker).
-- 사람이 정하지 않은 AI 판단은 새 판본(system_record)으로 올리고(requirements.latest_version 갱신),
-- 비교 사본(requirement_recomparisons)을 남긴다. 표·행은 더하거나 지우지 않는다. 다시 적용해도 깨지지 않는다.
-- (20261104_KAN-199_tft_worker.sql이 재적용될 때도 같은 권한을 복구한다.)
BEGIN;
GRANT UPDATE (latest_version) ON tft.requirements TO tft_worker;
GRANT INSERT ON tft.requirement_recomparisons TO tft_worker;
COMMIT;
