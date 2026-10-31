// RFP 화면 공용 라벨(Design 보드 TF_K_* 기준). 코드값은 docs/openapi/tft.json의 enum 그대로다. 없는 값은 코드를 보인다.
export const STATUS_LABEL: Record<string, string> = {
  EVIDENCE_FOUND: '근거 있음', CONDITIONAL: '조건부 검토', SPEC_GAP: '사양 차이', INSUFFICIENT_EVIDENCE: '근거 부족',
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
