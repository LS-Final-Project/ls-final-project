import type { components } from './generated/tft';
import { request } from './client';

type Schemas = components['schemas'];
export type Candidate = Schemas['Candidate'];
export type Recommendation = Schemas['CandidateRecommendation'];
export type StaffingRequest = Schemas['StaffingRequest'];
export type CheckStatus = 'UNSPECIFIED' | 'NEEDS_CHECK' | 'CONFIRMED' | 'UNMET';
type Envelope<T> = { data: T; meta: { request_id: string } };
export const departmentRequests = (cursor?: string) => request<Envelope<{items: StaffingRequest[]; next_cursor: string | null}>>(`/api/v1/tft/department-requests?limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`);
export const requestCandidates = (id: string, cursor?: string) => request<Envelope<{items: Candidate[]; next_cursor: string | null}>>(`/api/v1/tft/staffing-requests/${encodeURIComponent(id)}/candidates?limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`);
export type EvidenceKind = 'experience' | 'availability' | 'competency';
export type EvidencePayload = Schemas['ExperienceCreate'] | Schemas['AvailabilityCreate'] | Schemas['CompetencyCreate'];
// A failed/uncertain retry reuses the same key, version and normalized payload.
const attempts = new Map<string, string>();
async function write(path: string, version: number, payload: unknown) {
  const body = JSON.stringify(payload);
  const slot = `${path}:${version}:${body}`;
  const key = attempts.get(slot) ?? crypto.randomUUID();
  attempts.set(slot, key);
  const result = await request<Envelope<Schemas['CreatedRecord']>>(path, {method:'POST', headers:{'If-Match':`"${version}"`, 'Idempotency-Key':key}, body});
  attempts.delete(slot);
  return result;
}
export const recommendCandidate = (id: string, version: number, candidateId: string, rationale: string) => write(`/api/v1/tft/staffing-requests/${encodeURIComponent(id)}/recommendations`, version, {candidate_id:candidateId, rationale});
export const recordEvidence = (id: string, version: number, recommendation: string, kind: EvidenceKind, payload: EvidencePayload) => write(`/api/v1/tft/staffing-requests/${encodeURIComponent(id)}/recommendations/${encodeURIComponent(recommendation)}/${kind}`, version, payload);
