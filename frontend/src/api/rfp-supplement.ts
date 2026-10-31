import type { paths } from './generated/tft';
import { ApiError, request, sessionCsrfToken } from './client';
import { me } from './platform';
import { listRequirements, type RequirementSummary as SharedRequirementSummary } from './rfp';

type Json<T> = T extends { content: { 'application/json': infer Body } } ? Body : never;
type Options = paths['/api/v1/tft/requirements/{requirement_id}/supplement-options']['get'];
type Candidates = paths['/api/v1/tft/projects/{project_id}/partner-candidates']['get'];
type Selection = paths['/api/v1/tft/requirements/{requirement_id}/supplement-selection']['post'];
export type RequirementPage = Awaited<ReturnType<typeof listRequirements>>;
export type RequirementSummary = SharedRequirementSummary;
export type SupplementOptions = Json<Options['responses'][200]>['data'];
export type SupplementOption = SupplementOptions['options'][number];
export type PartnerCandidate = Json<Candidates['responses'][200]>['data']['items'][number];
export type SupplementSelection = Json<Selection['requestBody']>;
export type SupplementSelected = Json<Selection['responses'][200]>['data'];

// The supplement form reads real requirements: do not mix the table's local
// mock fallback with real options/candidates or duplicate its query construction.
export const listSupplementRequirements = (projectId: string, cursor?: string) =>
  listRequirements(projectId, {}, 20, cursor, { useMockFallback: false });
export const getSupplementOptions = (requirementId: string) =>
  request<Json<Options['responses'][200]>>(`/api/v1/tft/requirements/${encodeURIComponent(requirementId)}/supplement-options`);
export const listSupplementCandidates = (projectId: string, requirementId: string) =>
  request<Json<Candidates['responses'][200]>>(`/api/v1/tft/projects/${encodeURIComponent(projectId)}/partner-candidates?${new URLSearchParams({ requirement_id: requirementId })}`);
export async function selectSupplement(requirementId: string, payload: SupplementSelection, key: string, actorId: string) {
  const actor = await me();
  if (!actor || actor.data.id !== actorId) throw new ApiError(409, '세션 사용자가 변경되었습니다. 선택 내용을 다시 확인해 주세요.', undefined, 'SESSION_CHANGED');
  const csrf = sessionCsrfToken();
  if (!csrf && !import.meta.env.DEV) throw new ApiError(403, '세션 확인 후 다시 선택해 주세요.');
  return request<Json<Selection['responses'][200]>>(`/api/v1/tft/requirements/${encodeURIComponent(requirementId)}/supplement-selection`, {
    method: 'POST', headers: { 'Idempotency-Key': key, ...(csrf ? { 'X-CSRF-Token': csrf } : {}) }, body: JSON.stringify(payload),
  });
}
