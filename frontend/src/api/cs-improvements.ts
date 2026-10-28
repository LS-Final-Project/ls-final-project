import type { paths } from './generated/cs';
import { request } from './client';
export type ImprovementList = paths['/api/v1/cs/improvement-requests']['get']['responses'][200]['content']['application/json'];
export type ImprovementDetail = paths['/api/v1/cs/improvement-requests/{request_id}']['get']['responses'][200]['content']['application/json'];
export const listImprovements = (signal:AbortSignal) => request<ImprovementList>('/api/v1/cs/improvement-requests',{signal});
export const getImprovement = (id:string,signal:AbortSignal) => request<ImprovementDetail>(`/api/v1/cs/improvement-requests/${encodeURIComponent(id)}`,{signal});

export type ImprovementVersions = paths['/api/v1/cs/improvement-requests/{request_id}/versions']['get']['responses'][200]['content']['application/json'];
export const getImprovementVersions = (id:string,signal:AbortSignal) => request<ImprovementVersions>(`/api/v1/cs/improvement-requests/${encodeURIComponent(id)}/versions`,{signal});
