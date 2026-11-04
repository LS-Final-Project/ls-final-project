import type { RequirementSummary } from '../api/rfp';

// 개발 모의 모드 전용. RFP-91 합성 시연 패키지(artifacts/kan188-demo-rfp)의 31건과 격리 시험 판정 분포를 따른다.
// [코드, 설비, 속성, 요구값, 입찰 판정]. 보드와 같은 31건 + 도면 확인 4건.
const ROWS: [string, string, string, string | null, NonNullable<RequirementSummary['bid_verdict']>][] = [
  ['제6조.1', 'MV-SWG-01', '형식', '인출형 VCB, metal-clad', 'CONFIRM'],
  ['제6조.2', 'MV-SWG-01', '정격 최대전압', '≥ 12 kV', 'MET'],
  ['제6조.3', 'MV-SWG-01', '모선 정격전류', '≥ 630 A', 'MET'],
  ['제6조.4', 'MV-SWG-01', '단시간 내전류', '≥ 25 kA 3 s', 'MET'],
  ['제6조.5', 'MV-SWG-01', 'BIL', '≥ 75 kV', 'MET'],
  ['제6조.6', 'MV-SWG-01', '상용주파 내전압', null, 'QUERY'],
  ['제6조.7', 'MV-SWG-01', '내부아크 등급', 'IAC AFLR 25 kA 1 s', 'MET'],
  ['제7조.1', 'TR-01', '형식', '몰드 건식', 'CONFIRM'],
  ['제7조.2', 'TR-01', '정격용량', '630 kVA AN', 'MET'],
  ['제7조.3', 'TR-01', '1차 전압', '11 kV', 'MET'],
  ['제7조.3', 'TR-01', '2차 전압', '415 V', 'MET'],
  ['제7조.4', 'TR-01', '결선', 'Dyn11', 'MET'],
  ['제7조.5', 'TR-01', '탭', '±2×2.5 % 무부하', 'MET'],
  ['제7조.6', 'TR-01', '절연 등급', '≥ F종', 'MET'],
  ['제7조.7', 'TR-01', '강제공랭 용량 증가', '≥ 30 % @100 K', 'MET'],
  ['제7조.8', 'TR-01', '임피던스', '6 % ±10 % @75 °C', 'DEVIATION'],
  ['제8조.1', 'LV-SWB-01', '배전반 형식', '형식시험 완료 금속폐쇄형', 'SUPPLEMENT'],
  ['제8조.2', 'LV-SWB-01', '주모선 정격전류', '≥ 1,250 A', 'SUPPLEMENT'],
  ['제8조.3', 'LV-SWB-01', '단시간 내전류', '≥ 25 kA 1 s', 'SUPPLEMENT'],
  ['제8조.4', 'LV-SWB-01', '인입 차단기 형식', '인출형 ACB', 'CONFIRM'],
  ['제8조.5', 'LV-SWB-01', '인입 차단기 정격전류', '≥ 1,250 A', 'CONFIRM'],
  ['제8조.6', 'LV-SWB-01', '한계차단용량 Icu', '≥ 25 kA @415 V', 'MET'],
  ['제8조.7', 'LV-SWB-01', '보호 기능', 'LSIG', 'MET'],
  ['제8조.8', 'LV-SWB-01', '통신', 'Modbus TCP', 'DEVIATION'],
  ['제9조.1', '공통', '보호협조 연계 설계', null, 'ASSIGN'],
  ['제10조.1', '공통', '공장시험', '입회, 14일 전 통보', 'ASSIGN'],
  ['제10조.2', '공통', '현장시험', '설치 후 시행', 'ASSIGN'],
  ['제11조.1', '공통', '승인용 도면', '계약 후 30일', 'ASSIGN'],
  ['제11조.2', '공통', '형식시험 성적서', '제안서와 함께', 'ASSIGN'],
  ['제11조.3', '공통', '운전, 유지보수 설명서', '출하 전', 'ASSIGN'],
  ['제4조.2', '공통', '현장 도착 납기', '2027-04-30', 'ASSIGN'],
];
// 도면(4쪽 단선도) OCR 조항 요구. 판정은 충족이어도 도면 확인 결정이 필요하다(decision.drawing_check).
const DRAWING: [string, string, string, string][] = [
  ['OCR-DOCX-4.1', 'TR-01', '용량, 결선', '630 kVA(AN), 11/0.415 kV Dyn11'],
  ['OCR-DOCX-4.2', 'MV-SWG-01', '구성', '인입 VCB, 변압기 VCB, PT·계량반'],
  ['OCR-DOCX-4.3', 'LV-SWB-01', '인입 ACB', '인입 ACB 1,250 A 이상, 피더 MCCB 8회로'],
  ['OCR-DOCX-4.4', '공통', '공급 범위 경계', '11 kV 케이블 발주처 시공(공급 제외), 버스덕트 공급 범위 포함'],
];

// 설비 계열(KAN-231 bucket): 태그 앞머리로 정한다. 맥락 묶음(group)은 없으므로 화면은 계열 이름(MV·TR·LV·공통)으로 묶는다(보드와 같다).
const bucketOf = (equipment: string): RequirementSummary['bucket'] => equipment.startsWith('MV-') ? 'MV' : equipment.startsWith('TR-') ? 'TR' : equipment.startsWith('LV-') ? 'LV' : 'COMMON';

export const MOCK_WORKSPACE_REQUIREMENTS: RequirementSummary[] = ROWS.map(([code, equipment, attribute, value, verdict], index): RequirementSummary => ({
  requirement_id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`,
  code, version: 1, equipment, bucket: bucketOf(equipment), attribute, attribute_key: null, value_text: value, task: null,
  status: verdict === 'MET' ? 'EVIDENCE_FOUND' : verdict === 'CONFIRM' || verdict === 'DEVIATION' ? 'CONDITIONAL' : 'INSUFFICIENT_EVIDENCE',
  necessity: 'MANDATORY', provenance_kind: 'ai_draft', needs_rereview: false, needs_review: false, open_questions: 0,
  path: null, rnd_check: 'NOT_REQUIRED', is_designated_item: false, bid_verdict: verdict,
  own_value: attribute === '임피던스' ? { product_code: 'TRF-DRY-630', variant: null, value: '6 % @120 °C', unit: null, conditions: [], result: 'DIFFERS' } : null,
  decision: { needed: verdict !== 'MET', decided: false, reviewed: false, excluded: false, manually_added: false, change_reviewed: false, drawing_check: false },
})).concat(DRAWING.map(([code, equipment, attribute, value], index): RequirementSummary => ({
  requirement_id: `00000000-0000-4000-8000-${String(ROWS.length + index + 1).padStart(12, '0')}`,
  code, version: 1, equipment, bucket: bucketOf(equipment), attribute, attribute_key: null, value_text: value, task: null, status: 'EVIDENCE_FOUND' as const,
  necessity: 'MANDATORY' as const, provenance_kind: 'ai_draft' as const, needs_rereview: false, needs_review: false, open_questions: 0,
  path: null, rnd_check: 'NOT_REQUIRED' as const, is_designated_item: false, bid_verdict: 'MET' as const, own_value: null,
  decision: { needed: true, decided: false, reviewed: false, excluded: false, manually_added: false, change_reviewed: false, drawing_check: true },
})));
