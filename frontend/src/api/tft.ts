import type { paths } from './generated/tft';
import { request } from './client';

export type ProjectListResponse = paths['/api/v1/tft/projects']['get']['responses'][200]['content']['application/json'];
export const listProjects = () => request<ProjectListResponse>('/api/v1/tft/projects');
