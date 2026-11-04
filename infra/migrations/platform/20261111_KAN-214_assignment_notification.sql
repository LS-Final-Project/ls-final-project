-- KAN-214: allow the cs.assigned notification (new owner of a ticket).
-- P35, after P30. Re-runnable; only the kind/title CHECK sets grow, no privilege changes.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;
ALTER TABLE notify.notification_intents
    DROP CONSTRAINT IF EXISTS notification_intents_kind_check,
    DROP CONSTRAINT IF EXISTS notification_intents_title_check;
ALTER TABLE notify.notification_intents
    ADD CONSTRAINT notification_intents_kind_check CHECK (kind IN (
        'cs.review_wait','cs.safety','cs.reply','mail.unknown','cs.transfer',
        'rfp.done','rfp.failed','cs.assigned')),
    ADD CONSTRAINT notification_intents_title_check CHECK (title IN (
        'C/S 답변 확인 대기','안전 문의 확인 필요','고객 회신 재검토','메일 발송 결과 확인 필요',
        '이관 문의 수신','RFP 처리 완료','RFP 처리 완료, 검토 필요','RFP 처리 실패','새 문의 배정'));
COMMIT;
