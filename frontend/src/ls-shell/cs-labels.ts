import type { TicketListResponse } from '../api/cs';
import type { ImprovementList } from '../api/cs-improvements';

// C/S 화면의 표시 이름. 캔버스 보드(문의 조회 CS0101, 개선요청)의 문구를 따른다.
export type TicketState = TicketListResponse['data']['items'][number]['workflow_state'];
export type TicketChannel = TicketListResponse['data']['items'][number]['channel'];
export type RequestSummary = ImprovementList['data']['items'][number];

export const STATE_LABEL: Record<TicketState, string> = {
  RECEIVED: '접수',
  IN_REVIEW: '검토중',
  WAITING_CUSTOMER: '고객 회신 대기',
  ANSWERED: '답변 완료',
  TRANSFERRED: '부서 검토 중',
  CLOSED: '종결',
};

export const CHANNEL_LABEL: Record<TicketChannel, string> = {
  EMAIL: '이메일',
  PARTNER_PORTAL: '파트너 포털',
  WEB_FORM: '웹 폼',
  PHONE: '전화',
  CHAT: '채팅',
};

export const REQUEST_TYPE_LABEL: Record<RequestSummary['request_type'], string> = {
  FAULT: '제품 불량',
  DOCUMENT: '자료 문서',
};

export const REQUEST_STATUS_LABEL: Record<RequestSummary['status'], string> = {
  DRAFT: '작성 중',
  REVIEWED: '검토 완료',
  SENT: '부서에 보냄',
  ACKNOWLEDGED: '부서 확인',
  ADOPTED: '채택',
  INFO_REQUESTED: '부서가 정보 요청',
  REJECTED: '반려',
  ACTION_TAKEN: '조치함',
  FOLLOW_UP: '재발 점검 중',
  COMPLETED: '완료',
};

// 2026-10-28 09:02 형식(브라우저 시간대).
export function formatStamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '-';
  const two = (value: number) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${two(date.getMonth() + 1)}-${two(date.getDate())} ${two(date.getHours())}:${two(date.getMinutes())}`;
}

export const dayOf = (iso: string) => formatStamp(iso).slice(0, 10);

export const DEPT_LABEL: Record<string, string> = {
  TS_LV: '저압기기 기술지원', TS_MV: '중압기기 기술지원', TS_TRF: '변압기 기술지원', TS_AUTO: '자동화 기술지원',
  TS_ENERGY: '에너지 기술지원', RND_FW: '펌웨어 개발', QA_CLAIM: '품질보증/클레임', FIELD_SVC: '현장 서비스',
  CERT: '인증', SALES_OPS: '영업 운영', SCM: '주문/물류', WARRANTY_DESK: '보증 데스크',
};
export const DEPT_CODES = Object.keys(DEPT_LABEL);
export const deptName = (code: string) => DEPT_LABEL[code] ? `${DEPT_LABEL[code]} (${code})` : code;

// 확인 근거(장비 연결의 derivation_kind). 가정·추정 자료를 확정처럼 적지 않는다.
export const DERIVATION_LABEL: Record<string, string> = { PROVIDED: '고객 제공', DERIVED: '추정', ASSUMED: '가정 자료', SYSTEM: '시스템' };
export const derivationName = (kind: string) => DERIVATION_LABEL[kind] ?? kind;

// 통계 API의 미확인 지표 이름.
export const UNCONFIRMED_LABEL: Record<string, string> = { SLA_DUE: 'SLA 기한', RULE_ASSIGNED_DEPT: '규칙상 담당 부서' };

// 분석 API의 주분류 코드(contracts/cs/tickets.md). 이름은 화면용이고 코드는 같이 적는다.
export const CATEGORY_LABEL: Record<string, string> = {
  TECH_SPEC: '기술 사양', INSTALL: '설치', WARRANTY: '보증', ORDER_LEAD: '주문·납기', MANUAL: '매뉴얼',
  DEFECT: '고장·불량', COMPLIANCE: '규격·인증', QUOTE: '견적', FW_UPDATE: '펌웨어', RETURN: '반품',
};
export const categoryName = (code: unknown) => typeof code === 'string' ? `${CATEGORY_LABEL[code] ?? code} (${code})` : '-';

// 반품: 접수 때 고객이 밝힌 사유 유형(주장). 검사 결과가 아니다.
export const RETURN_STATED_LABEL: Record<string, string> = { DEFECT: '품질·불량 반품', NON_DEFECT: '비불량 반품' };
export const RESPONSIBILITY_LABEL: Record<string, string> = { MANUFACTURING: '제조 책임', NON_MANUFACTURING: '제조 외 책임', UNDETERMINED: '검사로 정하지 못함' };
export const CAUSE_LABEL: Record<string, string> = { SITE_CONDITION: '현장 조건', HARDWARE: '하드웨어', SETTING: '설정', WEAR_PART: '소모품', FIRMWARE: '펌웨어' };

// 받은 자료·요청 정보 코드의 한국어 이름. 코드 목록은 eval/policy/cs_required_info.csv(영문 field_labels)와 같다.
export const INFO_LABEL: Record<string, string> = {
  application: '사용 조건(전압·부하·운전)', battery_age: '배터리 설치일·사용 기간', battery_status: '배터리 SOC·셀 온도',
  current_fw: '현재 펌웨어 버전', document_type: '요청 문서 종류', error_code: '오류 코드·표시 증상',
  failure_description: '고장 내용·발생 시각', incoterms: '인코텀즈·납품지', install_date: '설치·시운전 일자',
  load_profile: '부하 전류·온도 기록', logs: '이벤트·트립 로그', motor_nameplate: '모터 명판 정보',
  operating_condition: '고장 당시 운전 조건', packaging_condition: '도착 시 포장 상태', parameter_backup: '파라미터 백업 파일',
  photos: '제품·명판 사진', po_number: '발주 번호', product_model: '모델명·주문 코드', program_backup: 'PLC/HMI 프로젝트 백업',
  purchase_doc: '발주서·인보이스 번호', quantity: '수량', required_date: '희망 납기', return_reason: '반품 사유',
  serial_no: '시리얼 번호', site_condition: '현장 조건(온도·고도·습도)', string_config: 'PV 스트링 구성',
  target_country: '수출 국가·시장', target_standard: '요구 규격·인증', tool_version: '엔지니어링 툴 버전',
  wiring_diagram: '배선도·단선도',
};
export const infoName = (code: string) => INFO_LABEL[code] ?? code;

// 문의 언어(body_language의 주 언어). 제공 문의 15개 언어와 한국어. 없는 코드는 코드 그대로 보인다.
export const LANGUAGE_LABEL: Record<string, string> = {
  en: '영어', ko: '한국어', es: '스페인어', fr: '프랑스어', th: '태국어', id: '인도네시아어', it: '이탈리아어', zh: '중국어',
  tr: '튀르키예어', ru: '러시아어', ar: '아랍어', vi: '베트남어', pt: '포르투갈어', de: '독일어', ja: '일본어', pl: '폴란드어', nl: '네덜란드어', sv: '스웨덴어', hu: '헝가리어',
};
export const languageName = (tag: string) => LANGUAGE_LABEL[tag.trim().replace('_', '-').split('-')[0].toLowerCase()] ?? tag;

// product_master.csv의 family 코드(11개). 묶음(기술지원 부서)은 TS_LV·TS_MV·TS_TRF·TS_AUTO·TS_ENERGY.
export const FAMILY_LABEL: Record<string, string> = {
  LV_BREAKER: '저압 차단기', CONTROL: '모터 제어기기', METER: '전력량계', MV_EQUIP: '중압 차단기·배전반', TRANSFORMER: '변압기',
  DRIVE: '인버터', PLC: 'PLC', HMI: 'HMI', PV: '태양광 인버터', ESS: '에너지 저장장치', UPS: 'UPS',
};
export const familyName = (code: string) => FAMILY_LABEL[code] ?? code;

// 자동 배정 사유(contracts/cs/assignment.md). 규칙 결과이며 AI 판단이 아니다.
export const ASSIGN_REASON_LABEL: Record<string, string> = {
  LANGUAGE_AND_FAMILY: '언어·제품군 담당', LANGUAGE_ONLY: '언어 담당(제품군 담당 없음)',
  FAMILY_ENGLISH_FALLBACK: '제품군·영어 담당(언어 담당 없음)', NO_MATCH: '맞는 담당자 없음',
};

// 맞는 담당자 없음(KAN-234)의 사유. 서버가 준 판단 재료(아무도 맡지 않은 언어·제품군)를 옮긴다.
// 예: "헝가리어 가능자 없음 · 미등록 제품 PLC-NX-32", "폴란드어 가능자 없음 · PLC·영어 담당 없음"
export const unmatchedTicketText = (item: { language: string; family?: string | null; product_code: string }) =>
  `${languageName(item.language)} 가능자 없음 · ${item.family ? `${familyName(item.family)}·영어 담당 없음` : `미등록 제품 ${item.product_code}`}`;
// 개선요청은 언어를 보지 않고 제품군만 본다. 예: "PLC 제품군 담당자 없음"
export const unmatchedRequestText = (item: { family?: string | null; product_code: string }) =>
  item.family ? `${familyName(item.family)} 제품군 담당자 없음` : `미등록 제품 ${item.product_code}`;

type AssignmentLike = { reason_code: string; matched_language?: string | null; matched_family?: string | null; candidate_count: number; chosen_open_count?: number | null };
// 예: "자동 배정: 태국어·저압 차단기 담당, 2명 중 진행 1건으로 가장 적음"
export function assignmentText(item: AssignmentLike): string {
  if (item.reason_code === 'NO_MATCH') return '자동 배정 안 됨: 맞는 담당자 없음';
  const parts = item.reason_code === 'LANGUAGE_AND_FAMILY' ? [item.matched_language && languageName(item.matched_language), item.matched_family && familyName(item.matched_family)]
    : item.reason_code === 'LANGUAGE_ONLY' ? [item.matched_language && languageName(item.matched_language)]
      : [item.matched_family && familyName(item.matched_family), '영어'];
  const note = item.reason_code === 'LANGUAGE_ONLY' ? ' (제품군 담당 없음)' : item.reason_code === 'FAMILY_ENGLISH_FALLBACK' ? ' (문의 언어 담당 없음)' : '';
  const load = item.chosen_open_count ?? 0;
  const pick = item.candidate_count > 1 ? `${item.candidate_count}명 중 진행 ${load}건으로 가장 적음` : `진행 ${load}건`;
  return `자동 배정: ${parts.filter(Boolean).join('·')} 담당${note}, ${pick}`;
}
