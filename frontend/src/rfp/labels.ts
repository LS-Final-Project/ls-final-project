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
export const LIFECYCLE_LABEL: Record<string, string> = { ACTIVE: '현행', PHASE_OUT: '단종 예정', DISCONTINUED: '단종' };
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
export const QUESTION_STATUS_LABEL: Record<string, string> = { DRAFT: '초안', OPEN: '회신 대기', REPLIED: '회신 기록됨' };
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
  const [, clause, order] = code.split(':');
  return clause ? `${clause}-${order}` : code;
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
