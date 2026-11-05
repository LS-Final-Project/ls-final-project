-- Synthetic local identities. Never run this file against the demonstration cloud DB.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL ROLE platform_migrator;
INSERT INTO identity.users(id,tid,oid,email,display_name,dept) VALUES
('00000000-0000-4000-8000-000000000001','11111111-1111-4111-8111-111111111111','00000000-0000-4000-8000-000000000001','cs@example.invalid','시연 C/S 담당자',NULL),
('00000000-0000-4000-8000-000000000002','11111111-1111-4111-8111-111111111111','00000000-0000-4000-8000-000000000002','dept@example.invalid','시연 변압기 기술지원 담당자','TS_TRF'),
('00000000-0000-4000-8000-000000000003','11111111-1111-4111-8111-111111111111','00000000-0000-4000-8000-000000000003','sales@example.invalid','시연 수주 담당자',NULL),
('00000000-0000-4000-8000-000000000004','11111111-1111-4111-8111-111111111111','00000000-0000-4000-8000-000000000004','manager@example.invalid','시연 관리자',NULL),
('00000000-0000-4000-8000-000000000005','11111111-1111-4111-8111-111111111111','00000000-0000-4000-8000-000000000005','rnd@example.invalid','시연 R&D 검토자','RND_FW'),
-- KAN-214: synthetic C/S agents so rule-based assignment visibly spreads (assumed data, no real
-- person). They exist only to receive tickets; nobody needs to log in as them.
('00000000-0000-4000-8000-000000000011','11111111-1111-4111-8111-111111111111','00000000-0000-4000-8000-000000000011','cs-agent-11@example.invalid','가정 상담원 1 (스페인어·포르투갈어)',NULL),
('00000000-0000-4000-8000-000000000012','11111111-1111-4111-8111-111111111111','00000000-0000-4000-8000-000000000012','cs-agent-12@example.invalid','가정 상담원 2 (프랑스어·이탈리아어·독일어)',NULL),
('00000000-0000-4000-8000-000000000013','11111111-1111-4111-8111-111111111111','00000000-0000-4000-8000-000000000013','cs-agent-13@example.invalid','가정 상담원 3 (태국어·베트남어·인도네시아어)',NULL),
('00000000-0000-4000-8000-000000000014','11111111-1111-4111-8111-111111111111','00000000-0000-4000-8000-000000000014','cs-agent-14@example.invalid','가정 상담원 4 (중국어·일본어)',NULL),
('00000000-0000-4000-8000-000000000015','11111111-1111-4111-8111-111111111111','00000000-0000-4000-8000-000000000015','cs-agent-15@example.invalid','가정 상담원 5 (터키어·아랍어·러시아어)',NULL),
('00000000-0000-4000-8000-000000000016','11111111-1111-4111-8111-111111111111','00000000-0000-4000-8000-000000000016','cs-agent-16@example.invalid','가정 상담원 6 (폴란드어·독일어·러시아어)',NULL)
ON CONFLICT (id) DO NOTHING;
INSERT INTO identity.user_roles(user_id,role) VALUES
('00000000-0000-4000-8000-000000000001','cs_agent'),
('00000000-0000-4000-8000-000000000002','dept_staff'),
('00000000-0000-4000-8000-000000000003','sales'),
('00000000-0000-4000-8000-000000000004','manager'),
('00000000-0000-4000-8000-000000000005','dept_staff'),
('00000000-0000-4000-8000-000000000011','cs_agent'),
('00000000-0000-4000-8000-000000000012','cs_agent'),
('00000000-0000-4000-8000-000000000013','cs_agent'),
('00000000-0000-4000-8000-000000000014','cs_agent'),
('00000000-0000-4000-8000-000000000015','cs_agent'),
('00000000-0000-4000-8000-000000000016','cs_agent')
ON CONFLICT DO NOTHING;
-- The demo manager manages the C/S department so reassignment can be shown (KAN-78).
INSERT INTO identity.manager_departments(user_id,dept_code) VALUES
('00000000-0000-4000-8000-000000000004','GCS_L1')
ON CONFLICT DO NOTHING;

-- KAN-214: agent skills (proposal p.23 employee table, assumed). Every body_language of the
-- provided tickets (15) and every product_master family (11) has at least one agent, and every
-- agent reads English for the FAMILY_ENGLISH_FALLBACK rule (contracts/cs/assignment.md).
SET LOCAL ROLE cs_migrator;
INSERT INTO cs.agent_skills(user_id,languages,families,data_origin) VALUES
('00000000-0000-4000-8000-000000000001','{en,ko}','{LV_BREAKER,CONTROL,METER,MV_EQUIP}','demo'),
('00000000-0000-4000-8000-000000000011','{en,es,pt}','{LV_BREAKER,CONTROL,METER,DRIVE,PLC,HMI}','demo'),
('00000000-0000-4000-8000-000000000012','{en,fr,it,de}','{MV_EQUIP,TRANSFORMER,LV_BREAKER}','demo'),
('00000000-0000-4000-8000-000000000013','{en,th,vi,id}','{LV_BREAKER,DRIVE,PLC,HMI,PV,ESS,UPS}','demo'),
('00000000-0000-4000-8000-000000000014','{en,zh,ja}','{DRIVE,PLC,HMI,METER,CONTROL}','demo'),
('00000000-0000-4000-8000-000000000015','{en,tr,ar,ru}','{TRANSFORMER,MV_EQUIP,ESS,UPS,PV}','demo'),
('00000000-0000-4000-8000-000000000016','{en,pl,de,ru}','{ESS,UPS,PV,TRANSFORMER,METER}','demo')
ON CONFLICT (user_id) DO NOTHING;
COMMIT;
