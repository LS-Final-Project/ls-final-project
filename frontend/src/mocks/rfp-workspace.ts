import type { RequirementSummary } from '../api/rfp';

// 개발 모의 모드 전용. RFP-91 합성 시연 패키지(artifacts/kan188-demo-rfp)의 31건과 격리 시험 판정 분포를 따른다.
// [코드, 설비, 속성, 요구값, 입찰 판정]
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

export const MOCK_WORKSPACE_REQUIREMENTS: RequirementSummary[] = ROWS.map(([code, equipment, attribute, value, verdict], index) => ({
  requirement_id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`,
  code, version: 1, equipment, attribute, attribute_key: null, value_text: value, task: null,
  status: verdict === 'MET' ? 'EVIDENCE_FOUND' : verdict === 'CONFIRM' || verdict === 'DEVIATION' ? 'CONDITIONAL' : 'INSUFFICIENT_EVIDENCE',
  necessity: 'MANDATORY', provenance_kind: 'ai_draft', needs_rereview: false, needs_review: false, open_questions: 0,
  path: null, rnd_check: 'NOT_REQUIRED', is_designated_item: false, bid_verdict: verdict,
  decision: { needed: verdict !== 'MET', decided: false, reviewed: false, excluded: false, manually_added: false, change_reviewed: false },
}));
