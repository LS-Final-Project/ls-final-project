-- KAN-234: 문의 배정의 NO_MATCH(맞는 담당자 없음)도 개선요청처럼 판단 기록으로 남긴다
-- (contracts/cs/assignment.md). C32, C29(20261111_KAN-214_agent_skills.sql) 뒤. 재실행 가능.
-- cs.ticket_assignments의 user_id NOT NULL과 사유 CHECK만 바꾸고 권한은 바꾸지 않는다.
-- NO_MATCH 행은 담당자가 없고, matched_language·matched_family에 아무도 맡지 않은 언어와 제품군을
-- 담는다(제품군 null = product_master에 없는 제품). 문의 상태·담당은 바뀌지 않으며, 관리자 화면은
-- 지금 미배정이고 최신 판단이 NO_MATCH인 문의를 보인다.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE cs_migrator;
ALTER TABLE cs.ticket_assignments
    ALTER COLUMN user_id DROP NOT NULL,
    DROP CONSTRAINT IF EXISTS ticket_assignments_reason_code_check,
    DROP CONSTRAINT IF EXISTS ticket_assignments_no_match_user_check;
ALTER TABLE cs.ticket_assignments
    ADD CONSTRAINT ticket_assignments_reason_code_check CHECK (
        reason_code IN ('LANGUAGE_AND_FAMILY', 'LANGUAGE_ONLY', 'FAMILY_ENGLISH_FALLBACK', 'NO_MATCH')
    ),
    ADD CONSTRAINT ticket_assignments_no_match_user_check CHECK (
        (reason_code = 'NO_MATCH') = (user_id IS NULL)
    );
COMMIT;
