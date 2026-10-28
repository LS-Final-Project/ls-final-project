import type { paths } from './generated/cs';
import { ApiError, getApiMode, request, sessionCsrfToken } from './client';
import { me } from './platform';
export type ImprovementList = paths['/api/v1/cs/improvement-requests']['get']['responses'][200]['content']['application/json'];
export type ImprovementDetail = paths['/api/v1/cs/improvement-requests/{request_id}']['get']['responses'][200]['content']['application/json'];
export const listImprovements = (signal:AbortSignal) => request<ImprovementList>('/api/v1/cs/improvement-requests',{signal});
export const getImprovement = (id:string,signal:AbortSignal) => request<ImprovementDetail>(`/api/v1/cs/improvement-requests/${encodeURIComponent(id)}`,{signal});

export type ImprovementVersions = paths['/api/v1/cs/improvement-requests/{request_id}/versions']['get']['responses'][200]['content']['application/json'];
export const getImprovementVersions = (id:string,signal:AbortSignal) => request<ImprovementVersions>(`/api/v1/cs/improvement-requests/${encodeURIComponent(id)}/versions`,{signal});

export type ImprovementPatch = paths['/api/v1/cs/improvement-requests/{request_id}']['patch']['requestBody']['content']['application/json'];
export type ImprovementCreate = paths['/api/v1/cs/improvement-requests']['post']['requestBody']['content']['application/json'];
export async function createImprovement(body:ImprovementCreate,signal:AbortSignal):Promise<ImprovementDetail>{
  if(!import.meta.env.DEV||getApiMode()!=='live')throw new ApiError(403,'실제 API 개발 모드에서만 작성할 수 있습니다.');
  const identity=await me();
  if(signal.aborted)throw new DOMException('Aborted','AbortError');
  if(!identity?.data.roles.includes('cs_agent'))throw new ApiError(403,'C/S 담당자 세션이 필요합니다.');
  const token=sessionCsrfToken();
  if(!token)throw new ApiError(403,'세션 보호 정보를 확인할 수 없습니다. 다시 로그인해 주세요.');
  return request<ImprovementDetail>('/api/v1/cs/improvement-requests',{method:'POST',signal,headers:{'X-CSRF-Token':token},body:JSON.stringify(body)});
}
export async function updateImprovement(id:string,body:ImprovementPatch,signal:AbortSignal):Promise<ImprovementDetail>{
  if(!import.meta.env.DEV||getApiMode()!=='live')throw new ApiError(403,'실제 API 개발 모드에서만 수정할 수 있습니다.');
  const identity=await me();
  if(signal.aborted)throw new DOMException('Aborted','AbortError');
  if(!identity?.data.roles.includes('cs_agent'))throw new ApiError(403,'C/S 담당자 세션이 필요합니다.');
  const token=sessionCsrfToken();
  if(!token)throw new ApiError(403,'세션 보호 정보를 확인할 수 없습니다. 다시 로그인해 주세요.');
  return request<ImprovementDetail>(`/api/v1/cs/improvement-requests/${encodeURIComponent(id)}`,{method:'PATCH',signal,headers:{'X-CSRF-Token':token},body:JSON.stringify(body)});
}
