// 개발 모의 응답용 합성 예시. 생성 예시(generated/tft.json)에 RFP 경로가 없어 화면 확인에 쓴다.
const REQUIREMENT_ID = '44444444-4444-4444-8444-444444444441';
const base = { version: 1, data_origin: 'synthetic', derivation_kind: 'ASSUMED', known_at: '2026-10-20T09:00:00Z' } as const;
const summary = (
  n: number, attribute: string, value_text: string,
  status: 'EVIDENCE_FOUND' | 'CONDITIONAL' | 'SPEC_GAP' | 'INSUFFICIENT_EVIDENCE',
) => ({
  requirement_id: `44444444-4444-4444-8444-44444444444${n}`, code: `R-0${n}`, equipment: '합성 변압기', task: null,
  attribute, value_text, necessity: 'MANDATORY' as const, status, path: null, open_questions: 0,
  needs_rereview: false, needs_review: false, is_designated_item: false, rnd_check: 'NOT_REQUIRED' as const, version: 1,
});

export const MOCK_REQUIREMENTS = [
  summary(1, '정격 용량', '1500 kVA 이상', 'SPEC_GAP'),
  summary(2, '절연 등급', 'F', 'EVIDENCE_FOUND'),
  summary(3, '임피던스', '5.75 %', 'CONDITIONAL'),
  summary(4, '권선 재질', '알루미늄', 'INSUFFICIENT_EVIDENCE'),
];

export const MOCK_DETAIL = {
  ...base,
  requirement_id: REQUIREMENT_ID,
  code: 'R-01',
  needs_rereview: false,
  needs_review: false,
  origin: {
    document_version_id: '55555555-5555-4555-8555-555555555551', clause_id: null, clause_no: '2.1.A', page: 3,
    quote: 'The transformer rating shall be 1500 kVA minimum.', deleted_values: [], edit_trace_support: true,
  },
  spec: {
    equipment: '합성 변압기', task: null, attribute: '정격 용량', value: '1500', unit: 'kVA', operator: 'GTE' as const,
    conditions: [], standard_system: 'IEEE' as const, necessity: 'MANDATORY' as const, designated_model: null,
    undetermined_items: [],
  },
  judgement: {
    status: 'SPEC_GAP' as const, reason: '원문 2.1.A 정격 용량 요구 1500 kVA 이상, 자사 1000 kVA: 미달.',
    unresolved: [], path: null, next_owner_field: 'TECH' as const, provenance_kind: 'ai_draft' as const,
    decided_by: null, decided_at: null,
  },
  links: {
    comparison_summary: null, supplement_summary: null, partner_candidate_ids: [], question_ids: [],
    pending_proposal_ids: [], draft_sentence_ids: [],
  },
};

export const MOCK_COMPARISON = {
  ...base,
  requirement_id: REQUIREMENT_ID,
  requirement_version: 1,
  provenance_kind: 'ai_draft' as const,
  search_trace: { filters: ['family=TRANSFORMER'], documents_searched: 2, search_failed: false },
  candidates: [{
    product_code: '합성-TRF-1000', variant: null, scope: [], lifecycle_status: 'ACTIVE',
    attribute_match: {
      matched_attribute: 'rated_power', our_value: '1000', our_unit: 'kVA', our_conditions: [], comparable: true,
      result: 'DIFFERS' as const,
      evidence: [{ document: '합성 데이터시트', clause: '2.2', version: null, applicability: null, limits: null }],
    },
  }],
};

export const MOCK_PROPOSALS = [{
  ...base,
  proposal_id: '66666666-6666-4666-8666-666666666691',
  origin: 'web' as const,
  source_ref: null,
  requirement_id: REQUIREMENT_ID,
  clause_unresolved: false,
  current: { status: 'SPEC_GAP' as const, necessity: 'MANDATORY' as const, reason: null, conditions: null, unresolved: null },
  proposed: { status: 'CONDITIONAL' as const, necessity: 'MANDATORY' as const, reason: '병렬 운전 조건이면 충족 가능', conditions: null, unresolved: null },
  rationale: '기술 검토 의견',
  proposer_user_id: null,
  proposer_kind: 'user' as const,
  status: 'PENDING' as const,
  decided_by: null,
  decided_at: null,
}];

export const MOCK_QUESTIONS = [{
  version: 1, data_origin: 'synthetic' as const, derivation_kind: 'PROVIDED' as const, known_at: '2026-10-20T09:00:00Z',
  question_id: '88888888-8888-4888-8888-888888888881', requirement_id: REQUIREMENT_ID, origin: { clause_id: null, page: 3 },
  issue_type: 'CONDITION_MISSING' as const, question_text: '합성 질문: 1500 kVA 기준 조건을 확인해 주세요.', internal_assumption: null,
  status: 'OPEN' as const, reply_received_at: null, created_by: '22222222-2222-4222-8222-222222222222', created_at: '2026-10-20T09:00:00Z',
  target: 'CUSTOMER' as const, target_dept: null, rnd_feasibility: null,
}];

export const MOCK_CLAUSES = [{
  clause_id: '99999999-9999-4999-8999-999999999991', clause_no: '2.1.A', parent_clause_id: null, depth: 1, title: '정격', page: 3,
  text: 'The transformer rating shall be 1500 kVA minimum.', deleted_values: [], flags: [], cross_references: [], requirement_ids: [REQUIREMENT_ID],
}];
