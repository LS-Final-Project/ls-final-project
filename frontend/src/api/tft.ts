import type { paths } from './generated/tft';
import { request } from './client';

export type ProjectListResponse = paths['/api/v1/tft/projects']['get']['responses'][200]['content']['application/json'];
export type ProjectDetailResponse = paths['/api/v1/tft/projects/{project_id}']['get']['responses'][200]['content']['application/json'];
export type ProjectDetail = ProjectDetailResponse['data'];
export type RequiredRole = ProjectDetail['required_roles'][number];
export type RfpRequirement = ProjectDetail['rfp_requirements'][number];

export const listProjects = () => request<ProjectListResponse>('/api/v1/tft/projects');
export const getProject = (id: string) => request<ProjectDetailResponse>(`/api/v1/tft/projects/${encodeURIComponent(id)}`);

// KAN-122의 병합된 OpenAPI 계약에서 요청·응답 타입을 생성한다.
export type StaffingRequestListResponse = paths['/api/v1/tft/projects/{project_id}/staffing-requests']['get']['responses'][200]['content']['application/json'];
export type StaffingRequestResponse = paths['/api/v1/tft/staffing-requests/{request_id}']['get']['responses'][200]['content']['application/json'];
export type StaffingRequest = StaffingRequestResponse['data'];
export type StaffingRequestStatus = StaffingRequest['status'];
export type Participation = StaffingRequest['participation'];
export type StaffingRequestPatch = paths['/api/v1/tft/staffing-requests/{request_id}']['patch']['requestBody']['content']['application/json'];

// 모의 응답 파일에는 인력요청 예시가 없으므로 모의 모드에서는 빈 목록을 돌려준다(임의 데이터를 만들지 않는다).
const emptyList = (): StaffingRequestListResponse => ({ data: { items: [] }, meta: { request_id: '00000000-0000-4000-8000-000000000000' } });
const ifMatch = (version: number) => ({ 'If-Match': `"${version}"` });

export const listStaffingRequests = (projectId: string) =>
  request<StaffingRequestListResponse>(`/api/v1/tft/projects/${encodeURIComponent(projectId)}/staffing-requests`, { mockFallback: emptyList });
export const getStaffingRequest = (id: string) => request<StaffingRequestResponse>(`/api/v1/tft/staffing-requests/${encodeURIComponent(id)}`);
export const updateStaffingRequest = (id: string, version: number, body: StaffingRequestPatch) =>
  request<StaffingRequestResponse>(`/api/v1/tft/staffing-requests/${encodeURIComponent(id)}`, { method: 'PATCH', headers: ifMatch(version), body: JSON.stringify(body) });
// 발행은 상태 전이 POST이므로 API 규약의 Idempotency-Key를 보낸다. 키는 요청서 id+version 단위로 보관해 같은 판본의 재시도에는
// 같은 키를 쓰고(서버가 첫 응답을 그대로 돌려준다), 판본이 바뀌면 새 키를 만들며, 발행이 성공하면 키를 버린다.
const issueKeys = new Map<string, string>();
export async function issueStaffingRequest(id: string, version: number): Promise<StaffingRequestResponse> {
  const slot = `${id}:${version}`;
  const key = issueKeys.get(slot) ?? crypto.randomUUID();
  issueKeys.set(slot, key);
  const result = await request<StaffingRequestResponse>(`/api/v1/tft/staffing-requests/${encodeURIComponent(id)}/issue`, { method: 'POST', headers: { ...ifMatch(version), 'Idempotency-Key': key } });
  issueKeys.delete(slot);
  return result;
}
