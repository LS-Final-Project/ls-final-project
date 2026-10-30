// RFP 화면 공용 라벨. 코드값은 docs/openapi/tft.json의 enum 그대로다. 없는 값은 코드를 그대로 보인다.
export const STATUS_LABEL: Record<string, string> = {
  EVIDENCE_FOUND: '근거 확인', CONDITIONAL: '조건부', SPEC_GAP: '사양 차이', INSUFFICIENT_EVIDENCE: '근거 부족',
};
export const NECESSITY_LABEL: Record<string, string> = {
  MANDATORY: '필수', PREFERRED: '선호', CONDITION_BOUND: '조건부 필수', UNDETERMINED: '미정',
};
export const PATH_LABEL: Record<string, string> = {
  OTHER_OWN_PRODUCT: '다른 자사 제품', OWN_DEVELOPMENT: '자사 개발·수행', OWN_TERMS_REVIEW: '자사 조건 변경',
  ALTERNATIVE_SPEC: '대체 제안', EXTERNAL_PRODUCT: '외부 제품', TASK_SUBCONTRACT: '과업 위탁',
  JOINT_PROPOSAL: '공동 제안', CUSTOMER_SPECIFIED_PROCUREMENT: '지정품 조달', NONE: '경로 없음',
};
export const OWNER_LABEL: Record<string, string> = {
  SALES: '영업', TECH: '기술', PURCHASE: '구매', CUSTOMER: '고객', RND: 'R&D',
};
export const MATCH_LABEL: Record<string, string> = {
  MEETS: '충족', DIFFERS: '차이', NOT_COMPARABLE: '비교 불가', NO_DATA: '자료 없음',
};
export const OPERATOR_LABEL: Record<string, string> = { EQ: '', GTE: '이상', LTE: '이하', RANGE: '범위', TEXT: '' };
export const label = (labels: Record<string, string>, code: string | null | undefined) =>
  code ? labels[code] ?? code : '-';
