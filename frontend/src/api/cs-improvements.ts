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

type Base = '/api/v1/cs/improvement-requests/{request_id}';
type Body<P extends keyof paths, M extends 'post'|'put'> = paths[P][M] extends {requestBody:{content:{'application/json':infer B}}} ? B : never;
export type ReviewBody = Body<`${Base}/review`,'post'>;
export type SendBody = Body<`${Base}/send`,'post'>;
export type CompleteBody = Body<`${Base}/complete`,'post'>;
export type OwnerBody = Body<`${Base}/owner`,'put'>;
export type EvidenceAddBody = Body<`${Base}/evidence`,'post'>;
export type EvidenceRemoveBody = Body<`${Base}/evidence/{evidence_id}/remove`,'post'>;
export type VersionBody = Body<`${Base}/departments/{dept_code}/acknowledge`,'post'>;
export type ReplyBody = Body<`${Base}/departments/{dept_code}/reply`,'post'>;
export type ActionBody = Body<`${Base}/departments/{dept_code}/action`,'post'>;
export type ResendBody = Body<`${Base}/departments/{dept_code}/resend`,'post'>;
export type FollowupPlanBody = Body<`${Base}/followup-plan`,'post'>;
export type FollowupSignals = paths[`${Base}/followup-signals`]['get']['responses'][200]['content']['application/json'];
export type ImprovementRole = 'cs_agent'|'dept_staff'|'manager';

const path = (id:string, suffix='') => `/api/v1/cs/improvement-requests/${encodeURIComponent(id)}${suffix}`;

// 쓰기는 실제 API에서만 실행한다. 모의 응답으로 성공한 척하지 않는다. 역할은 세션으로 먼저 확인하고 최종 판단은 서버가 한다.
async function write(url:string,body:object,signal:AbortSignal,role:ImprovementRole,method:'POST'|'PUT'|'PATCH'='POST'):Promise<ImprovementDetail>{
  if(getApiMode()!=='live')throw new ApiError(403,'모의 응답에서는 저장하지 않습니다.');
  const identity=await me();
  if(signal.aborted)throw new DOMException('Aborted','AbortError');
  const roles=identity?.data?.roles;
  if(!Array.isArray(roles)||!roles.includes(role))throw new ApiError(403,role==='dept_staff'?'부서 담당자 세션이 필요합니다.':role==='manager'?'C/S 관리자 세션이 필요합니다.':'C/S 담당자 세션이 필요합니다.');
  const token=sessionCsrfToken();
  if(!token)throw new ApiError(403,'세션 보호 정보를 확인할 수 없습니다. 다시 로그인해 주세요.');
  return request<ImprovementDetail>(url,{method,signal,headers:{'X-CSRF-Token':token},body:JSON.stringify(body)});
}
export const createImprovement = (body:ImprovementCreate,signal:AbortSignal) => write('/api/v1/cs/improvement-requests',body,signal,'cs_agent');
export const updateImprovement = (id:string,body:ImprovementPatch,signal:AbortSignal) => write(path(id),body,signal,'cs_agent','PATCH');

export const reviewImprovement = (id:string,body:ReviewBody,signal:AbortSignal) => write(path(id,'/review'),body,signal,'cs_agent');
export const sendImprovement = (id:string,body:SendBody,signal:AbortSignal) => write(path(id,'/send'),body,signal,'cs_agent');
export const completeImprovement = (id:string,body:CompleteBody,signal:AbortSignal) => write(path(id,'/complete'),body,signal,'cs_agent');
export const planImprovementFollowup = (id:string,body:FollowupPlanBody,signal:AbortSignal) => write(path(id,'/followup-plan'),body,signal,'cs_agent');
export const assignImprovementOwner = (id:string,body:OwnerBody,signal:AbortSignal) => write(path(id,'/owner'),body,signal,'manager','PUT');
export const addImprovementEvidence = (id:string,body:EvidenceAddBody,signal:AbortSignal) => write(path(id,'/evidence'),body,signal,'cs_agent');
export const removeImprovementEvidence = (id:string,evidenceId:string,body:EvidenceRemoveBody,signal:AbortSignal) => write(path(id,`/evidence/${encodeURIComponent(evidenceId)}/remove`),body,signal,'cs_agent');
const dept = (id:string,code:string,action:string) => path(id,`/departments/${encodeURIComponent(code)}/${action}`);
export const acknowledgeImprovement = (id:string,code:string,body:VersionBody,signal:AbortSignal) => write(dept(id,code,'acknowledge'),body,signal,'dept_staff');
export const replyImprovement = (id:string,code:string,body:ReplyBody,signal:AbortSignal) => write(dept(id,code,'reply'),body,signal,'dept_staff');
export const recordImprovementAction = (id:string,code:string,body:ActionBody,signal:AbortSignal) => write(dept(id,code,'action'),body,signal,'dept_staff');
export const resendImprovement = (id:string,code:string,body:ResendBody,signal:AbortSignal) => write(dept(id,code,'resend'),body,signal,'cs_agent');
export const reviewImprovementRejection = (id:string,code:string,body:VersionBody,signal:AbortSignal) => write(dept(id,code,'review-rejection'),body,signal,'cs_agent');

export const getFollowupSignals = (id:string,signal:AbortSignal) => request<FollowupSignals>(path(id,'/followup-signals'),{signal});
