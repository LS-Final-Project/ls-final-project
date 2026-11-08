// RFP 화면 공용 라벨(Design 보드 TF_K_* 기준). 코드값은 docs/openapi/tft.json의 enum 그대로다. 없는 값은 코드를 보인다.
export const STATUS_LABEL: Record<string, string> = {
  EVIDENCE_FOUND: '근거 있음', CONDITIONAL: '조건부 검토', SPEC_GAP: '사양 차이', INSUFFICIENT_EVIDENCE: '근거 부족',
};
export const OUTPUT_LABEL: Record<string, string> = { INTAKE: '접수', ANALYZING: '분석·검토 중', REVIEW_DRAFT: '검토본 작성' };
export const JUDGEMENT_LABEL: Record<string, string> = {
  NEEDS_REVIEW: '검토 필요', CONDITION_CHECK: '조건 확인 필요', OWNER_CONFIRMED: '담당자 확인',
};
export const NECESSITY_LABEL: Record<string, string> = {
  MANDATORY: '필수', PREFERRED: '우선', CONDITION_BOUND: '조건 따름', UNDETERMINED: '미정',
};
export const PATH_LABEL: Record<string, string> = {
  OTHER_OWN_PRODUCT: '다른 자사 제품', OWN_DEVELOPMENT: '자사 개발', OWN_TERMS_REVIEW: '자사 조건 변경',
  ALTERNATIVE_SPEC: '대체 사양', EXTERNAL_PRODUCT: '외부 제품', TASK_SUBCONTRACT: '작업 외주',
  JOINT_PROPOSAL: '공동 제안', CUSTOMER_SPECIFIED_PROCUREMENT: '지정품 조달', NONE: '없음',
};
export const OWNER_LABEL: Record<string, string> = {
  SALES: '영업', TECH: '기술', PURCHASE: '구매', CUSTOMER: '고객', RND: 'R&D',
};
export const MATCH_LABEL: Record<string, string> = {
  MEETS: '충족', DIFFERS: '다름', NOT_COMPARABLE: '비교 불가', NO_DATA: '자료 없음',
};
export const LIFECYCLE_LABEL: Record<string, string> = { ACTIVE: '현행', MATURE: '공급 확인 필요', PHASE_OUT: '단종 예정', DISCONTINUED: '단종' };
// 제품 계열(vocabulary.EquipmentFamily). 협력사 product_family는 표시 문구라 그대로 쓴다.
export const FAMILY_LABEL: Record<string, string> = {
  LV_BREAKER: '저압 차단기', LV_SWITCHGEAR: '저압 배전반', MV_EQUIP: '중압 기기', TRANSFORMER: '변압기', DRIVE: '인버터',
  CONTROL: '제어 기기', PLC: 'PLC', HMI: 'HMI', METER: '계량기', ESS: 'ESS', PV: '태양광', UPS: 'UPS',
};
export const SCOPE_LABEL: Record<string, string> = { SUPPLY: '공급', ASSEMBLY: '조립', INSTALL: '설치', TEST: '시험', CALC: '계산', TRAINING: '교육' };
export const CONFIRMATION_LABEL: Record<string, string> = { UNCONFIRMED: '미확인', CONTACT_PLANNED: '확인 예정', CONFIRMED: '확인됨' };
export const CERT_KIND_LABEL: Record<string, string> = { LISTED: '등록(Listed)', TEST_REPORT: '시험 성적서', MARK: '인증 표시' };
export const PROPOSAL_STATUS_LABEL: Record<string, string> = { PENDING: '승인 대기', APPROVED: '승인', REJECTED: '기각' };
export const ORIGIN_LABEL: Record<string, string> = { web: '화면 수정', customer_reply: '고객 회신', pdf_annot: '메모 PDF' };
export const OPERATOR_LABEL: Record<string, string> = { EQ: '', GTE: '이상', LTE: '이하', RANGE: '범위', TEXT: '' };
export const label = (labels: Record<string, string>, code: string | null | undefined) =>
  code ? labels[code] ?? code : '';
export const DOCUMENT_KIND_LABEL: Record<string, string> = {
  RFP_SPEC: 'RFP 사양서', DRAWING: '도면', ADDENDUM: '추가 공문', DATASHEET: '데이터시트', ANNOTATED_PDF: '메모 PDF',
};
export const DATA_ORIGIN_LABEL: Record<string, string> = { synthetic: '연습 자료', demo: '시연 자료', real: '실제' };
export const PURPOSE_LABEL: Record<string, string> = { INITIAL_PROPOSAL: '초기 제안' };
// tft.department_kinds의 코드만 서버가 받는다(이름을 보내면 422). 코드 목록은 docs/openapi/tft-department-codes.json으로 대조한다.
export const DEPARTMENT_LABEL: Record<string, string> = {
  SALES_OPS: '영업운영', RND_FW: 'R&D', TS_TRF: '변압기 기술지원', TS_MV: '중압기기 기술지원', TS_LV: '저압기기 기술지원', CERT: '인증', SCM: '구매팀',
};
export const ISSUE_LABEL: Record<string, string> = {
  EMPTY_VALUE: '값 없음', CONFLICT: '상충', SCOPE_UNCLEAR: '범위 불명확', CONDITION_MISSING: '조건 없음',
  RND_FEASIBILITY: 'R&D 개발 가능성', TECH_ADVICE: '기술 자문', PURCHASE_TERMS: '구매 조건',
};
export const QUESTION_STATUS_LABEL: Record<string, string> = { DRAFT: '초안', OPEN: '회신 대기', REPLIED: '회신 도착' };
export const BLOCKED_LABEL: Record<string, string> = { OWN_DEVELOPMENT_FEASIBLE: '자사 개발 가능', OWN_DEVELOPMENT_NOT_FEASIBLE: 'R&D 개발 불가 회신', DESIGNATED_ITEM: '지정품 요구' };
export const RND_CHECK_LABEL: Record<string, string> = { NOT_REQUIRED: '확인 불필요', PENDING: 'R&D 확인 대기', CONFIRMED_FEASIBLE: '자사 개발 가능', CONFIRMED_NOT_FEASIBLE: '자사 개발 불가', CONFIRMED_SCHEDULE_MISMATCH: '일정 불일치', WAIVED: '확인 면제' };
export const FEASIBILITY_LABEL: Record<string, string> = {
  FEASIBLE: '개발 가능', FEASIBLE_WITH_SCHEDULE: '일정 조건부 가능', NOT_FEASIBLE: '개발 불가', UNDETERMINED: '판단 불가',
};
// tft.department_kinds에서 분류가 RND인 부서. R&D 답변 칸은 이 부서 대상 질문에만 서버가 받는다.
// ponytail: 분류를 알려 주는 API가 없어 손으로 적었다. 부서 코드는 tft-department-codes.json 대조에 걸리지만 분류는 안 걸리니, R&D 부서가 늘면 여기도 고친다.
export const RND_DEPARTMENTS = ['RND_FW'];
// 요구 code(판본앞8자:조항:순번)의 표시 번호. 원시 값은 화면에 내지 않는다.
export function requirementNo(code: string) {
  const stored = code.match(/^[^:]+:([^:]+):(\d+)$/i);
  if (stored && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(stored[1])) return '요구 번호 미기재';
  if (stored) return stored[1].startsWith('OCR-') ? `이미지 요구 ${stored[2]}` : `${stored[1]}-${stored[2]}`;
  if (!code || /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i.test(code)) return '요구 번호 미기재';
  return code;
}
// 표 칸에 맞는 짧은 일시(2026-10-26 14:20, 사용자 시간대).
export function shortDateTime(at: string) {
  const d = new Date(at);
  const two = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}`;
}
// attribute_key(backend/engine/rfp/vocabulary.py) → 화면 이름. 어휘에 없거나 OTHER면 원문 속성을 그대로 쓴다.
export const ATTRIBUTE_LABEL: Record<string, string> = {
  form: '형식', rated_max_voltage: '정격 최대전압', bil: '기준 충격 절연', short_time_withstand_current: '단시간 내전류', rated_current: '정격 전류',
  arc_resistant: '아크 내성', internal_arc_classification: '내부 아크 등급', secondary_voltage: '2차 전압', primary_voltage: '1차 전압', rated_power: '정격 용량',
  insulation_class: '절연 등급', impedance: '임피던스', tap: '탭', forced_air_capacity_increase: '강제 공랭 용량 증가', protection_functions: '보호 기능',
  arc_flash_reduction: '아크플래시 저감', winding_material: '권선 재질', winding_temperature_sensor: '권선 온도 센서', communication_interface: '통신 규격',
  warranty_months: '보증 기간', rated_operational_voltage: '정격 사용 전압', busbar_rated_current: '모선 정격 전류', power_frequency_withstand: '상용주파 내전압',
  breaking_capacity_icu: '정격 한계 차단 용량', breaking_capacity_ics: '정격 사용 차단 용량', rated_short_circuit_breaking_current: '정격 단락 차단 전류',
  frame_size: '프레임 크기', number_of_poles: '극수', vector_group: '결선', protection_degree: '보호 등급',
};
export const PROVENANCE_LABEL: Record<string, string> = { ai_draft: 'AI 작성', human_input: '직접 입력', system_record: '시스템' };
// 속성은 어휘에 있으면 한글, 없으면(OTHER·null) 원문 그대로다.
export const attributeName = (key: string | null | undefined, text: string | null | undefined) =>
  key && ATTRIBUTE_LABEL[key] ? ATTRIBUTE_LABEL[key] : text ?? '';
export const INTEGRATION_HOLDER_LABEL: Record<string, string> = {
  UNRESOLVED: '미확보', OWN: '자사 통합', SINGLE_PARTNER: '단일 협력사', JOINT: '공동 제안', HOLD: '참여 보류 검토',
};

export const OFF_LIST_PERMISSION_LABEL: Record<string, string> = { ALLOWED: '허용', FORBIDDEN: '금지', UNSTATED: '미확인' };

export const OCR_CLAUSE = /^OCR-(DOCX-|PDF-P)(\d+)(?:\.|$)/;
/** 조항 번호를 사람이 읽는 말로: 이미지 전사는 "본문 이미지 n"·"PDF n쪽", 나머지는 원문 번호(제n조.m 등) 그대로. */
export function clauseLabel(clauseNo?: string | null, page?: number | null) {
  const ocr = clauseNo?.match(OCR_CLAUSE);
  return !ocr ? clauseNo ?? '' : ocr[1] === 'DOCX-' ? `본문 이미지 ${Number(ocr[2])}` : `PDF ${page ?? Number(ocr[2])}쪽`;
}
/** 요구 코드(문서 판본 앞 8자:조항 번호:순번, 직접 추가는 m순번)를 조항 표시로 바꾼다. 형식이 다르면 그대로 둔다.
 * 직접 추가는 백엔드 bid_outputs.requirement_label과 같게 "제3조.2 직접 추가 1". */
export function codeLabel(code?: string | null) {
  const m = code?.match(/^[0-9a-f]{8}:(.+):(m?)(\d+)$/);
  if (!m) return clauseLabel(code);
  return m[2] ? `${clauseLabel(m[1])} 직접 추가 ${m[3]}` : clauseLabel(m[1]);
}
const VERDICT_WORD: Record<string, string> = {
  '값 다름': '값이 다릅니다', 미달: '요구에 못 미칩니다', 초과: '요구를 넘습니다', '범위 밖': '범위를 벗어납니다', 충족: '충족합니다',
};
// vocabulary.ConditionName. 서버 판단 문장은 영문 이름의 밑줄을 공백으로 바꿔 쓴다.
const CONDITION_WORD: Record<string, string> = {
  duration: '지속 시간', 'ambient temperature': '주위온도', 'temperature rise': '온도 상승', 'reference temperature': '기준온도',
  frequency: '주파수', altitude: '표고', voltage: '전압', 'current type': '전류 종류', duty: '사용 조건', cooling: '냉각 방식',
  'tap type': '탭 방식', 'arc current': '아크 전류',
};
// 서버가 쓰는 고정 문장을 화면 말로(비교기·등급 함수의 문구, KAN-217·220).
const FIXED: [RegExp, string][] = [
  [/요구값 미정\(자리표시자\)\.\s*충족 판정에서 제외\./, '원문에 값이 비어 있습니다. 고객 질의가 필요합니다.'],
  [/^검색 범위\([^)]*\)에서 자사 후보를 찾지 못함\..*$/, '자사 제품 자료에서 맞는 후보를 찾지 못했습니다.'],
  [/수행 과업 요구\.\s*수행 주체 확인\(자사\/협력사\) 필요\./, '수행 과업입니다. 자사가 할지 협력사가 할지 정하세요.'],
  [/값은 충족하나 조건이 달라 확인 필요\./, '값은 맞지만 조건이 다릅니다.'],
];
/**
 * 비교 판단 문장(서버 원문, 근거 ID·조항 코드·영문 속성 키 포함)을 화면용으로 줄인다. 원문은 title로 남긴다.
 * 예 "이미지 전사 원문 확인 필요. 원문 OCR-DOCX-0001.7 rated_voltage 요구 11 kV, 자사 25.8 kV(근거 DS-VCB 2.1): 값 다름."
 *  → "요구 11 kV, 자사 25.8 kV. 값이 다릅니다."
 */
export function reasonText(reason?: string | null) {
  if (!reason) return '';
  let short = reason
    .replace(/^이미지 전사 원문 확인 필요\.\s*/, '')
    .replace(/^원문\s+\S+(?:\s+(?:(?!요구 )[^:]){1,30})?:\s*/, '')
    .replace(/^원문\s+\S+\s+.*?(?=요구 )/, '')
    .replace(/\s*\(근거 [^)]*\)/g, '')
    .replace(/:\s*(값 다름|미달|초과|범위 밖|충족)\.?$/, (_, w: string) => `. ${VERDICT_WORD[w]}.`)
    .replace(/조건 차이:\s*([a-z][a-z ]*?) 요구/g, (_, n: string) => `${CONDITION_WORD[n] ?? n} 차이: 요구`)
    .replace(/,\s*(?:후보|자사 근거)\s/g, ', 자사 ');
  for (const [from, to] of FIXED) short = short.replace(from, to);
  return short.trim();
}

const PARTNER_WORD: Record<string, string> = { 충족: '충족', 미달: '미달', 초과: '초과', '값 다름': '값 다름', '범위 밖': '범위 밖' };
/**
 * 협력사 등급 사유(서버 문장, KAN-185 등급 함수)를 후보 행 한 줄로 줄인다. 원문은 full로 남겨 title에 쓴다.
 * 예 "원문 제2조.1 단시간 내전류 요구 25 kA 이상, 후보 20 kA(근거 SYN-DS-SB-01 2.2): 미달." → "단시간 내전류 20 kA, 요구 25 kA 이상 미달"
 *    "조립품 공급이라 통합 책임(연동)이 필요: 후보 미확인." 여러 줄 → "통합 책임 미확인: 연동·보증"
 */
export function partnerNote(grade?: { reasons?: string[]; exclusion_reasons?: string[] } | null) {
  // 제외 사유를 먼저, 나머지 사유를 뒤에 둔다(제외 행은 왜 못 고르는지가 먼저 보여야 한다).
  const summarize = (lines: string[]) => {
    const spec: string[] = [];
    const duty: Record<string, string[]> = {};
    for (const line of lines) {
      const d = line.match(/통합 책임\((\S+?)\)이 필요:\s*후보 (\S+?)\.?$/);
      if (d) { (duty[d[2]] ??= []).push(d[1]); continue; }
      const m = line.match(/^원문 \S+ (.+?) 요구 (.+?)(?:, 후보 ([^(:]+?))?(?:\(근거[^)]*\))?:\s*(.+?)\.?$/);
      if (!m) { spec.push(line); continue; }
      const [, attr, want, got, word] = m;
      spec.push(got ? `${attr} ${got.trim()}, 요구 ${want} ${PARTNER_WORD[word] ?? word}` : `${attr} 자료 없음, 확인 필요`);
    }
    if (duty['불가']) spec.push(`통합 책임 불가: ${duty['불가'].join('·')}`);
    if (duty['미확인']) spec.push(`통합 책임 미확인: ${duty['미확인'].join('·')}`);
    return spec;
  };
  const parts = [...new Set([...summarize(grade?.exclusion_reasons ?? []), ...summarize(grade?.reasons ?? [])])];
  return { text: parts.join(' / '), full: [...(grade?.exclusion_reasons ?? []), ...(grade?.reasons ?? [])].join('\n') };
}
export const PARTNER_GRADE_LABEL: Record<string, string> = { MANDATORY_CONFIRMED: '필수 조건 확인', REVIEW: '검토', REFERENCE_ONLY: '확인용', EXCLUDED: '제외' };
// 내려받는 출력 파일 이름: 서버가 준 이름이 없거나 `rfp-<uuid>`면 "프로젝트 번호_문서 종류"로 만든다.
export function exportFileName(projectNo: string | null | undefined, format: 'XLSX' | 'DOCX', serverName?: string | null): string {
  if (serverName && !/^rfp-[0-9a-f-]{36}\./i.test(serverName)) return serverName;
  return `${projectNo || 'RFP'}_${format === 'XLSX' ? '대응현황표' : '요약서'}.${format.toLowerCase()}`;
}
