-- KAN-235: 회신 후보 찾기 확장. P36, P23(20261029_KAN-75_mail_links.sql) 뒤. 재실행 가능.
-- 1) 사람 연결(HUMAN)에 무엇을 근거로 골랐는지 남긴다. basis: 플랫폼 후보의 근거(HEADER·THREAD·ADDRESS),
--    고객 연락처(CONTACT), 담당자가 번호로 찾음(SEARCH). evidence: 연결할 때 C/S가 확인한 근거 코드 목록
--    (contracts/cs/reply-candidates.md). 예전 사람 연결은 비어 있다.
-- 2) 회신 표시(헤더)가 없는 새 메일이라도 우리가 메일을 주고받은 주소에서 왔으면 사람 검토로 보낸다
--    (reason ADDRESS_MATCH). 자동 연결은 여전히 헤더·대화 묶음이 한 업무만 가리킬 때뿐이다.
-- 권한은 바꾸지 않는다(platform_app은 mail_links에 표 단위 INSERT가 있다).
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;
ALTER TABLE integration.mail_links
    ADD COLUMN IF NOT EXISTS basis text,
    ADD COLUMN IF NOT EXISTS evidence jsonb;
ALTER TABLE integration.mail_links
    DROP CONSTRAINT IF EXISTS mail_links_basis_check,
    DROP CONSTRAINT IF EXISTS mail_links_evidence_check;
ALTER TABLE integration.mail_links
    ADD CONSTRAINT mail_links_basis_check CHECK (
        basis IS NULL OR (method = 'HUMAN' AND basis IN ('HEADER', 'THREAD', 'ADDRESS', 'CONTACT', 'SEARCH'))
    ),
    ADD CONSTRAINT mail_links_evidence_check CHECK (
        evidence IS NULL OR (basis IS NOT NULL AND jsonb_typeof(evidence) = 'array')
    );
ALTER TABLE integration.mail_reply_reviews
    DROP CONSTRAINT IF EXISTS mail_reply_reviews_reason_check;
ALTER TABLE integration.mail_reply_reviews
    ADD CONSTRAINT mail_reply_reviews_reason_check CHECK (
        reason IN ('AMBIGUOUS', 'UNMATCHED_REPLY', 'CONFLICT', 'TRIAGE_DISAGREES', 'ADDRESS_MATCH')
    );
COMMIT;
