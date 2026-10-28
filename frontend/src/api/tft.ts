import type { paths } from './generated/tft';
import { request } from './client';

export type ProjectListResponse = paths['/api/v1/tft/projects']['get']['responses'][200]['content']['application/json'];
export type ProjectDetailResponse = paths['/api/v1/tft/projects/{project_id}']['get']['responses'][200]['content']['application/json'];
export type ProjectDetail = ProjectDetailResponse['data'];
export type RfpRequirement = ProjectDetail['rfp_requirements'][number];

export const listProjects = () => request<ProjectListResponse>('/api/v1/tft/projects');
export const getProject = (id: string) => request<ProjectDetailResponse>(`/api/v1/tft/projects/${encodeURIComponent(id)}`);
