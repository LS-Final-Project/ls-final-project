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
};

export function listRequirements(projectId: string, filters: RequirementFilters, limit: number, cursor?: string, options: { useMockFallback?: boolean } = {}) {
  const query = new URLSearchParams({ limit: String(limit) });
  for (const [key, value] of Object.entries(filters)) if (value !== undefined) query.set(key, String(value));
  if (cursor) query.set('cursor', cursor);
  return request<Envelope<Schemas['RequirementList']>>(
    `/api/v1/tft/projects/${encodeURIComponent(projectId)}/requirements?${query}`,
    options.useMockFallback === false ? {} : { mockFallback: mock({ items: MOCK_REQUIREMENTS, next_cursor: null }) },
  );
}

export const getRequirement = (id: string) => request<Envelope<RequirementDetail>>(
  `/api/v1/tft/requirements/${encodeURIComponent(id)}`, { mockFallback: mock(MOCK_DETAIL) },
);

export const getComparison = (id: string) => request<Envelope<Comparison>>(
  `/api/v1/tft/requirements/${encodeURIComponent(id)}/comparison`, { mockFallback: mock(MOCK_COMPARISON) },
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
export const listQuestions = (projectId: string, status?: string) => request<Envelope<Schemas['QuestionList']>>(
  `/api/v1/tft/projects/${encodeURIComponent(projectId)}/questions${status ? `?status=${status}` : ''}`,
  { mockFallback: mock({ items: MOCK_QUESTIONS }) },
);
export const createQuestion = (projectId: string, body: Schemas['QuestionCreate'], key: string) => request<Envelope<Question>>(
  `/api/v1/tft/projects/${encodeURIComponent(projectId)}/questions`,
  { ...json(key, body), mockFallback: mock(MOCK_QUESTIONS[0]) },
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
export const listProjectCandidates = (projectId: string) => request<Envelope<Schemas['PartnerCandidateList']>>(
  `/api/v1/tft/projects/${encodeURIComponent(projectId)}/partner-candidates`, { mockFallback: mock({ items: [] }) },
);
