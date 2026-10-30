import type { paths } from './generated/tft';
import { ApiError, request, sessionCsrfToken } from './client';

type Json<T> = T extends { content: { 'application/json': infer Body } } ? Body : never;
type Requirements = paths['/api/v1/tft/projects/{project_id}/requirements']['get'];
type Options = paths['/api/v1/tft/requirements/{requirement_id}/supplement-options']['get'];
type Candidates = paths['/api/v1/tft/projects/{project_id}/partner-candidates']['get'];
type Selection = paths['/api/v1/tft/requirements/{requirement_id}/supplement-selection']['post'];
export type RequirementPage = Json<Requirements['responses'][200]>;
export type RequirementSummary = RequirementPage['data']['items'][number];
export type SupplementOptions = Json<Options['responses'][200]>['data'];
export type SupplementOption = SupplementOptions['options'][number];
export type PartnerCandidate = Json<Candidates['responses'][200]>['data']['items'][number];
export type SupplementSelection = Json<Selection['requestBody']>;
export type SupplementSelected = Json<Selection['responses'][200]>['data'];

export const listSupplementRequirements = (projectId: string, cursor?: string) => {
  const query = new URLSearchParams({ limit: '20' });
  if (cursor) query.set('cursor', cursor);
  return request<RequirementPage>(`/api/v1/tft/projects/${encodeURIComponent(projectId)}/requirements?${query}`);
};
export const getSupplementOptions = (requirementId: string) =>
  request<Json<Options['responses'][200]>>(`/api/v1/tft/requirements/${encodeURIComponent(requirementId)}/supplement-options`);
export const listSupplementCandidates = (projectId: string, requirementId: string) =>
  request<Json<Candidates['responses'][200]>>(`/api/v1/tft/projects/${encodeURIComponent(projectId)}/partner-candidates?${new URLSearchParams({ requirement_id: requirementId })}`);
export async function selectSupplement(requirementId: string, payload: SupplementSelection, key: string) {
  const csrf = sessionCsrfToken();
  if (!csrf && !import.meta.env.DEV) throw new ApiError(403, '세션 확인 후 다시 선택해 주세요.');
  return request<Json<Selection['responses'][200]>>(`/api/v1/tft/requirements/${encodeURIComponent(requirementId)}/supplement-selection`, {
    method: 'POST', headers: { 'Idempotency-Key': key, ...(csrf ? { 'X-CSRF-Token': csrf } : {}) }, body: JSON.stringify(payload),
  });
}
