import type { paths } from './generated/tft';
import { request } from './client';

export type ProjectListResponse = paths['/api/v1/tft/projects']['get']['responses'][200]['content']['application/json'];
export type ProjectDetailResponse = paths['/api/v1/tft/projects/{project_id}']['get']['responses'][200]['content']['application/json'];
export type ProjectDetail = ProjectDetailResponse['data'];
export type RequiredRole = ProjectDetail['required_roles'][number];
export type RfpRequirement = ProjectDetail['rfp_requirements'][number];

export const listProjects = () => request<ProjectListResponse>('/api/v1/tft/projects');
export const getProject = (id: string) => request<ProjectDetailResponse>(`/api/v1/tft/projects/${encodeURIComponent(id)}`);

// TODO(KAN-122 PR #31 병합 후 교체): 인력요청 API는 병렬 구현 중이라 docs/openapi/tft.json에 아직 없다. 아래 StaffingRequest*
// 타입은 KAN-134에서 합의한 계약을 손으로 옮긴 것이다. PR #31이 dev에 병합되면 `npm run types:tft`로 generated/tft.ts를 다시
// 만들고 이 손 작성 타입을 `paths['/api/v1/tft/projects/{project_id}/staffing-requests']...` 형태의 생성 타입으로 바꾼 뒤
// `npm run build`·`npm run test:ui`로 필드 이름 차이를 확인한다. 필드·오류 코드를 여기서 임의로 바꾸지 않는다.
export type StaffingRequestStatus = 'DRAFT' | 'ISSUED' | 'ANSWERED' | 'CLOSED';
export type Participation = 'LEAD' | 'SUPPORT';
export type StaffingRequest = {
  id: string;
  project_id: string;
  required_role_id: string;
  role_name: string;
  organization_id: string;
  organization_name: string;
  organization_dept_code: string | null;
  participation: Participation;
  status: StaffingRequestStatus;
  version: number;
  due_at: string | null;
  due_undecided: boolean;
  message: string | null;
  issued_by_user_id: string | null;
  issued_at: string | null;
  created_at: string;
  updated_at: string;
  data_origin: 'synthetic' | 'demo' | 'real';
  derivation_kind: 'PROVIDED' | 'DERIVED' | 'SYSTEM' | 'ASSUMED';
};
export type StaffingRequestListResponse = { data: { items: StaffingRequest[] }; meta: { request_id: string } };
export type StaffingRequestResponse = { data: StaffingRequest; meta: { request_id: string } };
export type StaffingRequestPatch = {
  organization_id?: string;
  participation?: Participation;
  due_at?: string | null;
  due_undecided?: boolean;
  message?: string;
};

// 모의 응답 파일에는 인력요청 예시가 없으므로 모의 모드에서는 빈 목록을 돌려준다(임의 데이터를 만들지 않는다).
const emptyList = (): StaffingRequestListResponse => ({ data: { items: [] }, meta: { request_id: '00000000-0000-4000-8000-000000000000' } });
const ifMatch = (version: number) => ({ 'If-Match': `"${version}"` });

export const listStaffingRequests = (projectId: string) =>
  request<StaffingRequestListResponse>(`/api/v1/tft/projects/${encodeURIComponent(projectId)}/staffing-requests`, { mockFallback: emptyList });
export const getStaffingRequest = (id: string) => request<StaffingRequestResponse>(`/api/v1/tft/staffing-requests/${encodeURIComponent(id)}`);
export const updateStaffingRequest = (id: string, version: number, body: StaffingRequestPatch) =>
  request<StaffingRequestResponse>(`/api/v1/tft/staffing-requests/${encodeURIComponent(id)}`, { method: 'PATCH', headers: ifMatch(version), body: JSON.stringify(body) });
// 발행은 상태 전이 POST이므로 API 규약의 Idempotency-Key도 함께 보낸다.
export const issueStaffingRequest = (id: string, version: number) =>
  request<StaffingRequestResponse>(`/api/v1/tft/staffing-requests/${encodeURIComponent(id)}/issue`, { method: 'POST', headers: { ...ifMatch(version), 'Idempotency-Key': crypto.randomUUID() } });
