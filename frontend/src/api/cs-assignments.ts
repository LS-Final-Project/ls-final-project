import type { paths } from './generated/cs';
import { ApiError, getApiMode, request, sessionCsrfToken } from './client';

// 담당자 자동 배정(KAN-214, contracts/cs/assignment.md). 규칙 결과이며 AI가 정하지 않는다.
type Base = '/api/v1/cs/assignments';
export type AgentListResponse = paths[`${Base}/agents`]['get']['responses'][200]['content']['application/json'];
export type AgentSkillRow = AgentListResponse['data']['items'][number];
export type PreviewResponse = paths[`${Base}/preview`]['post']['responses'][200]['content']['application/json'];
export type ProposalRow = PreviewResponse['data']['items'][number];
export type ApplyBody = paths[`${Base}/apply`]['post']['requestBody']['content']['application/json'];
export type ApplyResponse = paths[`${Base}/apply`]['post']['responses'][200]['content']['application/json'];
export type ApplyRow = ApplyResponse['data']['items'][number];

export const getAssignmentAgents = (signal?: AbortSignal) => request<AgentListResponse>('/api/v1/cs/assignments/agents', { signal });

// 미리보기도 POST라 쓰기와 같이 실제 API 모드·세션 보호 정보가 필요하다(서버는 아무것도 쓰지 않는다).
function post<T>(path: string, body: object, signal: AbortSignal): Promise<T> {
  if (getApiMode() !== 'live') throw new ApiError(403, '모의 응답 모드에서는 실행할 수 없습니다. 실제 API에서 실행하세요.');
  const token = sessionCsrfToken();
  if (!token) throw new ApiError(403, '세션 보호 정보를 확인할 수 없습니다. 다시 로그인해 주세요.');
  return request<T>(path, { method: 'POST', signal, headers: { 'X-CSRF-Token': token }, body: JSON.stringify(body) });
}

export const previewAssignments = (limit: number, signal: AbortSignal) => post<PreviewResponse>('/api/v1/cs/assignments/preview', { limit }, signal);
// 미리보기의 문의·담당 쌍을 그대로 보낸다. 서버가 다시 결정해 같은 것만 쓴다.
export const applyAssignments = (body: ApplyBody, signal: AbortSignal) => post<ApplyResponse>('/api/v1/cs/assignments/apply', body, signal);
