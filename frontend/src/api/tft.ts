import type { paths } from './generated/tft';
import { request } from './client';

export type ProjectListResponse = paths['/api/v1/tft/projects']['get']['responses'][200]['content']['application/json'];
export type ProjectDetailResponse = paths['/api/v1/tft/projects/{project_id}']['get']['responses'][200]['content']['application/json'];
export type ProjectDetail = ProjectDetailResponse['data'];
export type RfpRequirement = ProjectDetail['rfp_requirements'][number];

export type ProjectSummary = ProjectListResponse['data']['items'][number];
export type ProjectFilters = { output_status?: string; judgement_status?: string; q?: string };
export function listProjects(filters: ProjectFilters = {}, cursor?: string) {
  const query = new URLSearchParams(Object.entries({ ...filters, cursor }).filter(([, value]) => value) as [string, string][]);
  const text = query.toString();
  return request<ProjectListResponse>(`/api/v1/tft/projects${text ? `?${text}` : ''}`);
}
export const getProject = (id: string) => request<ProjectDetailResponse>(`/api/v1/tft/projects/${encodeURIComponent(id)}`);
