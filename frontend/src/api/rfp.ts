import { me } from './platform';
import type { ProjectDetailResponse } from './tft';
import type { components } from './generated/tft';
import { ApiError, getApiMode, request, sessionCsrfToken } from './client';
import { MOCK_CLAUSES, MOCK_COMPARISON, MOCK_DETAIL, MOCK_PROPOSALS, MOCK_QUESTIONS, MOCK_REQUIREMENTS } from '../mocks/rfp-table';

type Schemas = components['schemas'];
export type RequirementSummary = Schemas['RequirementSummary'];
export type RequirementDetail = Schemas['RequirementDetail'];
export type Comparison = Schemas['Comparison'];
export type Proposal = Schemas['Proposal'];
export type ProposalCreate = Schemas['ProposalCreate'];
export type RequirementStatus = RequirementSummary['status'];
export type Necessity = RequirementSummary['necessity'];

type Envelope<T> = { data: T };
const mock = <T,>(data: unknown) => () => ({ data }) as T;
const json = (key: string, body: unknown): RequestInit => {
  const csrf = sessionCsrfToken();
  return {
    method: 'POST',
    headers: { 'Idempotency-Key': key, ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
    body: JSON.stringify(body),
  };
};

export type DocumentVersion = Schemas['DocumentVersion'];
export type RequirementFilters = {
  document_version_id?: string;
  equipment?: string;
  task?: string;
  status?: RequirementStatus;
  necessity?: Necessity;
  needs_rereview?: boolean;
  needs_review?: boolean;
  has_open_question?: boolean;
  next_owner_field?: string;
  unresolved_mandatory?: boolean;
  excluded?: boolean;
};

// 서버 목록 상한은 100건이다. 작업 공간은 모든 요구가 필요하므로 커서를 따라 끝까지 읽는다.
export async function listAllRequirements(projectId: string, filters: RequirementFilters, signal?: AbortSignal) {
  const items: Schemas['RequirementList']['items'] = [];
  let cursor: string | undefined;
  do {
    const page = (await listRequirements(projectId, filters, 100, cursor, { useMockFallback: false, signal })).data;
    items.push(...page.items);
    cursor = page.next_cursor ?? undefined;
  } while (cursor);
  return items;
}

export function listRequirements(projectId: string, filters: RequirementFilters, limit: number, cursor?: string, options: { useMockFallback?: boolean; signal?: AbortSignal } = {}) {
  const query = new URLSearchParams({ limit: String(limit) });
  for (const [key, value] of Object.entries(filters)) if (value !== undefined) query.set(key, String(value));
  if (cursor) query.set('cursor', cursor);
  return request<Envelope<Schemas['RequirementList']>>(
    `/api/v1/tft/projects/${encodeURIComponent(projectId)}/requirements?${query}`,
    { signal: options.signal, ...(options.useMockFallback === false ? {} : { mockFallback: mock({ items: MOCK_REQUIREMENTS, next_cursor: null }) }) },
  );
}

export const getRequirement = (id: string) => request<Envelope<RequirementDetail>>(
  `/api/v1/tft/requirements/${encodeURIComponent(id)}`, { mockFallback: mock(MOCK_DETAIL) },
);

export const getComparison = (id: string, options: { useMockFallback?: boolean } = {}) => request<Envelope<Comparison>>(
  `/api/v1/tft/requirements/${encodeURIComponent(id)}/comparison`, options.useMockFallback === false ? {} : { mockFallback: mock(MOCK_COMPARISON) },
);

export const listDocuments = (projectId: string) => request<Envelope<Schemas['DocumentList']>>(
  `/api/v1/tft/projects/${encodeURIComponent(projectId)}/documents`, { mockFallback: mock({ items: [] }) },
);

export function listProposals(projectId: string, filters: { status?: string; origin?: string; requirement_id?: string } = {}) {
  const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value) as [string, string][]);
  const text = query.toString();
  return request<Envelope<Schemas['ProposalList']>>(
    `/api/v1/tft/projects/${encodeURIComponent(projectId)}/proposals${text ? `?${text}` : ''}`,
    { mockFallback: mock({ items: MOCK_PROPOSALS }) },
  );
}

// 쓰기는 Idempotency-Key가 필수다. 호출자가 같은 시도에 같은 키를 쓴다(useAttemptKey).
export const createProposal = (projectId: string, body: ProposalCreate, key: string) => request<Envelope<Proposal>>(
  `/api/v1/tft/projects/${encodeURIComponent(projectId)}/proposals`,
  { ...json(key, body), mockFallback: mock(MOCK_PROPOSALS[0]) },
);

export const approveProposal = (id: string, version: number, note: string, key: string) => request<Envelope<RequirementDetail>>(
  `/api/v1/tft/proposals/${encodeURIComponent(id)}/approve`,
  { ...json(key, note ? { version, note } : { version }), mockFallback: mock(MOCK_DETAIL) },
);

export const rejectProposal = (id: string, version: number, reason: string, key: string) => request<Envelope<Proposal>>(
  `/api/v1/tft/proposals/${encodeURIComponent(id)}/reject`,
  { ...json(key, { version, reason }), mockFallback: mock(MOCK_PROPOSALS[0]) },
);

export type ProjectCreate = Schemas['ProjectCreate'];
export type RfpProject = Schemas['RfpProjectDetail'];
export const createProject = (body: ProjectCreate, key: string) => request<Envelope<RfpProject>>(
  '/api/v1/tft/projects', { ...json(key, body), mockFallback: mock({ id: '33333333-3333-4333-8333-333333333333' }) },
);

export type ProjectConstraints = Pick<Schemas['ProjectUpdate'], 'supply_region' | 'customer_manufacturer_list' | 'off_list_permission'>;
export const updateProjectConstraints = (id: string, version: number, fields: ProjectConstraints) => {
  const csrf = sessionCsrfToken();
  return request<Envelope<RfpProject>>(`/api/v1/tft/projects/${encodeURIComponent(id)}`, {
    method: 'PATCH', headers: csrf ? { 'X-CSRF-Token': csrf } : {}, body: JSON.stringify({ version, ...fields }),
  });
};

export const UPLOAD_LIMIT = 25 * 1024 * 1024;
export type UploadFields = { file: File; kind: string; title: string; note: string; data_origin: string; supersedes_document_id?: string };

// 멀티파트는 브라우저가 경계를 넣어야 해 공용 request(JSON 고정)를 쓰지 않는다.
export async function uploadDocument(projectId: string, fields: UploadFields, key: string): Promise<DocumentVersion> {
  if (import.meta.env.DEV && getApiMode() === 'mock') return { title: fields.title } as DocumentVersion;
  const form = new FormData();
  for (const [name, value] of Object.entries(fields)) if (value) form.append(name, value);
  const csrf = sessionCsrfToken();
  const response = await fetch(`/api/v1/tft/projects/${encodeURIComponent(projectId)}/documents`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Idempotency-Key': key, ...(csrf ? { 'X-CSRF-Token': csrf } : {}) },
    body: form,
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, body?.error?.message || `HTTP ${response.status}`, body?.meta?.request_id, body?.error?.code);
  return body.data;
}

export const startProcess = (projectId: string, body: Schemas['ProcessRequest'], key: string) => request<Envelope<Schemas['JobAccepted']>>(
  `/api/v1/tft/projects/${encodeURIComponent(projectId)}/process`,
  { ...json(key, body), mockFallback: mock({ job_id: '77777777-7777-4777-8777-777777777771', status: 'QUEUED', status_url: '' }) },
);

export type JobStatus = { job_id: string; status: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'PARTIAL' | 'FAILED' | 'CANCELLED' };
export const getJob = (id: string) => request<Envelope<JobStatus>>(
  `/api/v1/platform/jobs/${encodeURIComponent(id)}`, { mockFallback: mock({ job_id: id, status: 'SUCCEEDED' }) },
);

export type Question = Schemas['Question'];
export const listQuestions = (projectId: string, status?: string, options: { useMockFallback?: boolean } = {}) => request<Envelope<Schemas['QuestionList']>>(
  `/api/v1/tft/projects/${encodeURIComponent(projectId)}/questions${status ? `?status=${status}` : ''}`,
  options.useMockFallback === false ? {} : { mockFallback: mock({ items: MOCK_QUESTIONS }) },
);
export const createQuestion = (projectId: string, body: Schemas['QuestionCreate'], key: string, options: { useMockFallback?: boolean } = {}) => request<Envelope<Question>>(
  `/api/v1/tft/projects/${encodeURIComponent(projectId)}/questions`,
  { ...json(key, body), ...(options.useMockFallback === false ? {} : { mockFallback: mock(MOCK_QUESTIONS[0]) }) },
);
export const confirmQuestion = (projectId: string, id: string, key: string) => request<Envelope<Question>>(
  `/api/v1/tft/projects/${encodeURIComponent(projectId)}/questions/${encodeURIComponent(id)}/confirm`,
  { ...json(key, {}), mockFallback: mock(MOCK_QUESTIONS[0]) },
);
export const replyQuestion = (id: string, body: Schemas['QuestionReply'], key: string) => request<Envelope<Schemas['QuestionReplyResult']>>(
  `/api/v1/tft/questions/${encodeURIComponent(id)}/reply`,
  { ...json(key, body), mockFallback: mock({ ...MOCK_QUESTIONS[0], created_proposal: null }) },
);

export type Clause = Schemas['Clause'];
// 경로의 document_id는 문서 판본 ID다. parent를 빼면 전체 조항을 문서 순서로 준다.
export const listClauses = (documentVersionId: string, cursor?: string) => request<Envelope<Schemas['ClauseList']>>(
  `/api/v1/tft/documents/${encodeURIComponent(documentVersionId)}/clauses?limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`,
  { mockFallback: mock({ items: MOCK_CLAUSES, next_cursor: null }) },
);

export const getDocument = (id: string) => request<Envelope<Schemas['DocumentDetail']>>(
  `/api/v1/tft/documents/${encodeURIComponent(id)}`, { mockFallback: mock({ clauses_count: 0, deleted_values_count: 0 }) },
);

export type UserName = { user_id: string; display_name: string };
// 이름 묶음 조회. 없는 ID는 응답에서 빠진다. 한 번에 100개까지다.
export const listUsers = (ids: string[]) => request<Envelope<{ items: UserName[] }>>(
  `/api/v1/platform/users?ids=${ids.map(encodeURIComponent).join(',')}`, { mockFallback: mock({ items: [] }) },
);

// 경로 4. 멱등 키가 없는 쓰기라 읽은 version으로 충돌을 막는다(409 TFT_VERSION_CONFLICT).
export const updateIntegration = (projectId: string, version: number, integration: Schemas['IntegrationUpdate']) => {
  const csrf = sessionCsrfToken();
  return request<Envelope<RfpProject>>(`/api/v1/tft/projects/${encodeURIComponent(projectId)}`, {
    method: 'PATCH',
    headers: csrf ? { 'X-CSRF-Token': csrf } : {},
    body: JSON.stringify({ version, integration_responsibility: integration }),
    mockFallback: mock({ id: projectId }),
  });
};

// 프로젝트 전체 협력사 후보. 요구별 조회는 rfp-supplement의 listSupplementCandidates다.
export const listProjectCandidates = (projectId: string, signal?: AbortSignal) => request<Envelope<Schemas['PartnerCandidateList']>>(
  `/api/v1/tft/projects/${encodeURIComponent(projectId)}/partner-candidates`, { signal, mockFallback: mock({ items: [] }) },
);

export type DiagramList = Schemas['DiagramList'];
export type DiagramItem = Schemas['DiagramItem'];
export const listDocumentDiagrams = (documentId: string, versionId: string, signal?: AbortSignal) => request<Envelope<DiagramList>>(
  `/api/v1/tft/documents/${encodeURIComponent(documentId)}/diagrams?${new URLSearchParams({ document_version_id: versionId })}`,
  { signal, mockFallback: mock({ document_id: documentId, document_version_id: versionId, processing_status: 'NOT_ANALYZED', items: [], warnings: [] }) },
);

// Explicit project access change. Preserve the caller's full department list;
// the existing project version contract rejects concurrent edits.
export async function setRfpReviewDepartments(projectId: string, version: number, departments: string[], actorId: string) {
  const actor = await me();
  if (!actor || actor.data.id !== actorId) throw new ApiError(409, '세션 사용자가 변경되었습니다. 프로젝트를 다시 확인하세요.', undefined, 'SESSION_CHANGED');
  const csrf = sessionCsrfToken();
  if (!csrf && !import.meta.env.DEV) throw new ApiError(403, '세션 확인 후 검토 부서를 다시 설정하세요.');
  return request<ProjectDetailResponse>(`/api/v1/tft/projects/${encodeURIComponent(projectId)}`, {
    method: 'PATCH', headers: csrf ? { 'X-CSRF-Token': csrf } : {},
    body: JSON.stringify({ version, review_departments: [...new Set(departments)] }),
  });
}
export const addRfpReviewDepartment = (projectId: string, version: number, departments: string[], actorId: string) =>
  setRfpReviewDepartments(projectId, version, [...departments, 'RND_FW'], actorId);

// 묶음 쓰기(경로 19-b·16-b). 질문 묶음은 한 건이라도 어긋나면 아무것도 저장하지 않는다(422 details[index]).
export type QuestionCreate = Schemas['QuestionCreate'];
export const createQuestionsBatch = (projectId: string, items: QuestionCreate[], key: string) => request<Envelope<{ items: Question[] }>>(
  `/api/v1/tft/projects/${encodeURIComponent(projectId)}/questions/batch`, json(key, { items }),
);
export type SupplementBatchBody = Schemas['SupplementBatchSelection'];
export type SupplementBatchResult = Schemas['SupplementBatchResult'];
// dry_run은 쓰지 않는다. 저장(dry_run false)만 멱등 키를 보낸다.
export const selectSupplementBatch = (projectId: string, body: SupplementBatchBody, key?: string) => {
  const csrf = sessionCsrfToken();
  return request<Envelope<SupplementBatchResult>>(`/api/v1/tft/projects/${encodeURIComponent(projectId)}/supplement-selections/batch`, {
    method: 'POST', headers: { ...(key ? { 'Idempotency-Key': key } : {}), ...(csrf ? { 'X-CSRF-Token': csrf } : {}) }, body: JSON.stringify(body),
  });
};
// 묶음 경로가 없는 서버(중계 허용 목록에 없음 405, 경로 없음 404 NOT_FOUND·코드 없음). 업무 404(TFT_*)는 해당하지 않는다.
export const unsupportedRoute = (error: unknown) => error instanceof ApiError && (error.status === 405 || error.status === 404 && (!error.code || error.code === 'NOT_FOUND'));

// 저장된 근거만 재비교한다. 모델 호출과 판정 자동 승인은 하지 않는다.
export const recompareRequirement = (id: string, version: number, key: string) => request<Envelope<{ comparison: Comparison; proposal: Proposal | null }>>(
  `/api/v1/tft/requirements/${encodeURIComponent(id)}/recompare`, json(key, { version }),
);

// KAN-219·220 결정 경로. 결정 하나를 제안 작성과 승인까지 한 번에 확정한다. 책임자 확인·되돌려 보내기도 같은 경로다.
export type DecisionRequest = Schemas['DecisionRequest'];
export type DecisionResult = Schemas['DecisionResult'];
export type BidVerdict = NonNullable<RequirementSummary['bid_verdict']>;
export const decideRequirement = (id: string, body: DecisionRequest, key: string) => request<Envelope<DecisionResult>>(
  `/api/v1/tft/requirements/${encodeURIComponent(id)}/decision`, json(key, body),
);

// 변경 이력(KAN-226). kind는 쉼표로 잇는다(중계가 같은 키 반복을 막는다).
export type HistoryItem = Schemas['HistoryEvent'];
export type HistoryKind = HistoryItem['kind'];
export type HistoryPage = Schemas['HistoryPage'];
const historyQuery = (kinds: HistoryKind[] = [], actor?: string, cursor?: string | null) => {
  const q = new URLSearchParams({ limit: '50' });
  if (kinds.length) q.set('kind', kinds.join(','));
  if (actor) q.set('actor', actor);
  if (cursor) q.set('cursor', cursor);
  return q.toString();
};
const MOCK_HISTORY: HistoryPage = { items: [
  { id: 'h2', at: '2026-11-04T10:12:00Z', kind: 'decision', actor_user_id: null, requirement_id: '00000000-0000-4000-8000-000000000016', requirement_version: 2, summary: '편차 기재', before: null, after: { action: 'record_deviation' } },
  { id: 'h1', at: '2026-11-04T09:00:00Z', kind: 'ai_verdict', actor_user_id: null, requirement_id: '00000000-0000-4000-8000-000000000016', requirement_version: 1, summary: 'AI 판정', before: null, after: { status: 'SPEC_GAP' } },
], next_cursor: null };
export const listRequirementHistory = (id: string, cursor?: string | null) => request<Envelope<HistoryPage>>(
  `/api/v1/tft/requirements/${encodeURIComponent(id)}/history?${historyQuery([], undefined, cursor)}`, { mockFallback: mock(MOCK_HISTORY) },
);
export const listProjectHistory = (id: string, kinds: HistoryKind[], actor?: string, cursor?: string | null) => request<Envelope<HistoryPage>>(
  `/api/v1/tft/projects/${encodeURIComponent(id)}/history?${historyQuery(kinds, actor, cursor)}`, { mockFallback: mock(MOCK_HISTORY) },
);

// 원문에서 요구 직접 추가(KAN-225). 422는 근거 불일치, 409는 같은 요구가 있음(allow_duplicate로 다시 보낸다).
export type ManualRequirementRequest = Schemas['ManualRequirementRequest'];
export const addRequirement = (projectId: string, body: ManualRequirementRequest, key: string) => request<Envelope<DecisionResult>>(
  `/api/v1/tft/projects/${encodeURIComponent(projectId)}/requirements`, json(key, body),
);

// 일괄 결정과 묶음 되돌리기. 한 건이라도 실패하면 아무것도 저장하지 않고 details[0]에 첫 실패 건(index·requirement_id·reason)이 온다.
export type BulkDecisionItem = Schemas['BulkDecisionItem'];
export type BulkDecisionResult = Schemas['BulkDecisionResult'];
export const bulkDecide = (projectId: string, items: BulkDecisionItem[], key: string) => request<Envelope<BulkDecisionResult>>(
  `/api/v1/tft/projects/${encodeURIComponent(projectId)}/decisions/bulk`, json(key, { items }),
);
export const undoBulk = (projectId: string, batchId: string, key: string) => request<Envelope<BulkDecisionResult>>(
  `/api/v1/tft/projects/${encodeURIComponent(projectId)}/decisions/bulk/${encodeURIComponent(batchId)}/undo`, json(key, {}),
);
// 질의서를 메일로 보낸 기록. 질문마다 처음 보낸 시각만 남는다.
export const markQuestionsSent = (projectId: string, questionIds: string[], key: string, mailRef?: string) => request<Envelope<unknown>>(
  `/api/v1/tft/projects/${encodeURIComponent(projectId)}/questions/sent`, json(key, { question_ids: questionIds, mail_ref: mailRef ?? null }),
);
export type Equipment = Schemas['Equipment'];
// 지난 처리·맥락 작업 목록과 다시 분석 방식(KAN-232). 재생은 플랫폼 작업 이벤트(after=0)로 한다.
export type AnalysisOverview = Schemas['AnalysisOverview'];
export const getAnalysis = (projectId: string) => request<Envelope<AnalysisOverview>>(
  `/api/v1/tft/projects/${encodeURIComponent(projectId)}/analysis`, { mockFallback: mock({ jobs: [], process_mode: null }) },
);
export const listEquipment = (projectId: string) => request<Envelope<Schemas['EquipmentList']>>(
  `/api/v1/tft/projects/${encodeURIComponent(projectId)}/equipment`, { mockFallback: mock({ source: 'requirements', items: [] }) },
);
