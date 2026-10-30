import type { components } from './generated/tft';
import { request } from './client';
import { MOCK_COMPARISON, MOCK_DETAIL, MOCK_PROPOSALS, MOCK_REQUIREMENTS } from '../mocks/rfp-table';

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
const json = (key: string, body: unknown): RequestInit => ({
  method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(body),
});

export type RequirementFilters = {
  status?: RequirementStatus;
  necessity?: Necessity;
  needs_rereview?: boolean;
  has_open_question?: boolean;
};

export function listRequirements(projectId: string, filters: RequirementFilters, cursor?: string) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) if (value !== undefined) query.set(key, String(value));
  if (cursor) query.set('cursor', cursor);
  const text = query.toString();
  return request<Envelope<Schemas['RequirementList']>>(
    `/api/v1/tft/projects/${encodeURIComponent(projectId)}/requirements${text ? `?${text}` : ''}`,
    { mockFallback: mock({ items: MOCK_REQUIREMENTS, next_cursor: null }) },
  );
}

export const getRequirement = (id: string) => request<Envelope<RequirementDetail>>(
  `/api/v1/tft/requirements/${encodeURIComponent(id)}`, { mockFallback: mock(MOCK_DETAIL) },
);

export const getComparison = (id: string) => request<Envelope<Comparison>>(
  `/api/v1/tft/requirements/${encodeURIComponent(id)}/comparison`, { mockFallback: mock(MOCK_COMPARISON) },
);

export const listProposals = (projectId: string, requirementId: string) => request<Envelope<Schemas['ProposalList']>>(
  `/api/v1/tft/projects/${encodeURIComponent(projectId)}/proposals?requirement_id=${encodeURIComponent(requirementId)}`,
  { mockFallback: mock({ items: MOCK_PROPOSALS }) },
);

// 쓰기는 Idempotency-Key가 필수다. 호출자가 같은 시도에 같은 키를 쓴다(useAttemptKey).
export const createProposal = (projectId: string, body: ProposalCreate, key: string) => request<Envelope<Proposal>>(
  `/api/v1/tft/projects/${encodeURIComponent(projectId)}/proposals`,
  { ...json(key, body), mockFallback: mock(MOCK_PROPOSALS[0]) },
);

export const approveProposal = (id: string, version: number, key: string) => request<Envelope<RequirementDetail>>(
  `/api/v1/tft/proposals/${encodeURIComponent(id)}/approve`,
  { ...json(key, { version }), mockFallback: mock(MOCK_DETAIL) },
);

export const rejectProposal = (id: string, version: number, reason: string, key: string) => request<Envelope<Proposal>>(
  `/api/v1/tft/proposals/${encodeURIComponent(id)}/reject`,
  { ...json(key, { version, reason }), mockFallback: mock(MOCK_PROPOSALS[0]) },
);
