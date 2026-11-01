import type { paths } from './generated/cs';
import { ApiError, getApiMode, request, sessionCsrfToken } from './client';
import { me } from './platform';
export type ImprovementList = paths['/api/v1/cs/improvement-requests']['get']['responses'][200]['content']['application/json'];
export type ImprovementDetail = paths['/api/v1/cs/improvement-requests/{request_id}']['get']['responses'][200]['content']['application/json'];
export type ImprovementOrigin = 'SYSTEM_CANDIDATE' | 'MANUAL';
// origin을 주면 서버가 그 출처의 요청만 돌려준다(SYSTEM_CANDIDATE: 시스템이 모은 후보, MANUAL: 사람이 만든 요청).
export const listImprovements = (signal:AbortSignal, origin?:ImprovementOrigin) => request<ImprovementList>(`/api/v1/cs/improvement-requests${origin?`?origin=${origin}`:''}`,{signal});
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

// 상태 전이(KAN-116). 요청 상태는 사람이 고르지 않고 서버가 부서 상태에서 요약한다. 모든 호출은 읽은 요청 판본(version)을 보낸다.
type Body<P extends keyof paths> = paths[P] extends { post: { requestBody: { content: { 'application/json': infer B } } } } ? B : never;
export type DepartmentCode = NonNullable<Body<'/api/v1/cs/improvement-requests/{request_id}/send'>['dept_codes']>[number];
export const DEPARTMENT_CODES: DepartmentCode[] = ['TS_LV', 'TS_MV', 'TS_TRF', 'TS_AUTO', 'TS_ENERGY', 'RND_FW', 'QA_CLAIM', 'FIELD_SVC', 'CERT', 'SALES_OPS', 'SCM', 'WARRANTY_DESK'];
export type ReviewBody = Body<'/api/v1/cs/improvement-requests/{request_id}/review'>;
export type SendBody = Body<'/api/v1/cs/improvement-requests/{request_id}/send'>;
export type ResendBody = Body<'/api/v1/cs/improvement-requests/{request_id}/departments/{dept_code}/resend'>;
export type DeptReplyBody = Body<'/api/v1/cs/improvement-requests/{request_id}/departments/{dept_code}/reply'>;
export type DeptActionBody = Body<'/api/v1/cs/improvement-requests/{request_id}/departments/{dept_code}/action'>;
export type FollowupPlanBody = Body<'/api/v1/cs/improvement-requests/{request_id}/followup-plan'>;
export type CompleteBody = Body<'/api/v1/cs/improvement-requests/{request_id}/complete'>;

// 개발 모드의 실제 API에서만 연다(작성·수정과 같은 기준). 부서 동작은 부서 담당자 세션, 나머지는 C/S 담당자 세션이 필요하다.
async function transition(url: string, body: object, signal: AbortSignal, role: 'cs_agent' | 'dept_staff' = 'cs_agent'): Promise<ImprovementDetail> {
  if (!import.meta.env.DEV || getApiMode() !== 'live') throw new ApiError(403, '실제 API 개발 모드에서만 실행할 수 있습니다.');
  const identity = await me();
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
  if (!identity?.data.roles.includes(role)) throw new ApiError(403, role === 'dept_staff' ? '부서 담당자 세션이 필요합니다.' : 'C/S 담당자 세션이 필요합니다.');
  const token = sessionCsrfToken();
  if (!token) throw new ApiError(403, '세션 보호 정보를 확인할 수 없습니다. 다시 로그인해 주세요.');
  return request<ImprovementDetail>(url, { method: 'POST', signal, headers: { 'X-CSRF-Token': token }, body: JSON.stringify(body) });
}
const requestUrl = (id: string, tail: string) => `/api/v1/cs/improvement-requests/${encodeURIComponent(id)}${tail}`;
const deptUrl = (id: string, dept: string, action: string) => requestUrl(id, `/departments/${encodeURIComponent(dept)}/${action}`);

export const reviewImprovement = (id: string, body: ReviewBody, signal: AbortSignal) => transition(requestUrl(id, '/review'), body, signal);
export const sendImprovement = (id: string, body: SendBody, signal: AbortSignal) => transition(requestUrl(id, '/send'), body, signal);
export const resendToDepartment = (id: string, dept: string, body: ResendBody, signal: AbortSignal) => transition(deptUrl(id, dept, 'resend'), body, signal);
export const reviewRejection = (id: string, dept: string, version: number, signal: AbortSignal) => transition(deptUrl(id, dept, 'review-rejection'), { version }, signal);
export const planFollowup = (id: string, body: FollowupPlanBody, signal: AbortSignal) => transition(requestUrl(id, '/followup-plan'), body, signal);
export const completeImprovement = (id: string, body: CompleteBody, signal: AbortSignal) => transition(requestUrl(id, '/complete'), body, signal);
export const acknowledgeRequest = (id: string, dept: string, version: number, signal: AbortSignal) => transition(deptUrl(id, dept, 'acknowledge'), { version }, signal, 'dept_staff');
export const replyToRequest = (id: string, dept: string, body: DeptReplyBody, signal: AbortSignal) => transition(deptUrl(id, dept, 'reply'), body, signal, 'dept_staff');
export const recordDeptAction = (id: string, dept: string, body: DeptActionBody, signal: AbortSignal) => transition(deptUrl(id, dept, 'action'), body, signal, 'dept_staff');

// 반복 사례 비교표(CS0201). 활성 근거마다 한 행이고, 해결 기록이 없는 근거는 해결 칸이 null이다. 볼 수 없는 문의는 행이 빠지고 hidden_case_count로 수만 온다.
export type CaseComparison = paths['/api/v1/cs/improvement-requests/{request_id}/case-comparison']['get']['responses'][200]['content']['application/json'];
export const getCaseComparison = (id: string, signal: AbortSignal) => request<CaseComparison>(requestUrl(id, '/case-comparison'), { signal, mockFallback: () => ({ data: { request_id: id, items: [], hidden_case_count: 0 } }) });
export type EvidenceRemoveBody = Body<'/api/v1/cs/improvement-requests/{request_id}/evidence/{evidence_id}/remove'>;
// 근거를 뺀다(사유 필수). 행은 남고 제외자·시각·사유가 기록되며 새 판본이 된다.
export const removeEvidence = (id: string, evidenceId: string, body: EvidenceRemoveBody, signal: AbortSignal) => transition(requestUrl(id, `/evidence/${encodeURIComponent(evidenceId)}/remove`), body, signal);
