import type { paths } from './generated/cs';
import { ApiError, getApiMode, request, sessionCsrfToken } from './client';
import type { ImprovementDetail } from './cs-improvements';
import { me } from './platform';

// 반복 사례 모음(CS0201). 저장된 시스템 후보, 후보별 사례 비교표, 근거 추가·빼기를 다룬다.
export type RepeatCandidateList = paths['/api/v1/cs/improvement-requests']['get']['responses'][200]['content']['application/json'];
export type CaseComparison = paths['/api/v1/cs/improvement-requests/{request_id}/case-comparison']['get']['responses'][200]['content']['application/json'];
export type CandidateList = paths['/api/v1/cs/improvement-candidates']['get']['responses'][200]['content']['application/json'];
export type EvidenceAdd = paths['/api/v1/cs/improvement-requests/{request_id}/evidence']['post']['requestBody']['content']['application/json'];
export type EvidenceRemove = paths['/api/v1/cs/improvement-requests/{request_id}/evidence/{evidence_id}/remove']['post']['requestBody']['content']['application/json'];
export const listRepeatCandidates = (signal:AbortSignal) => request<RepeatCandidateList>('/api/v1/cs/improvement-requests?origin=SYSTEM_CANDIDATE',{signal});
export const getCaseComparison = (id:string,signal:AbortSignal) => request<CaseComparison>(`/api/v1/cs/improvement-requests/${encodeURIComponent(id)}/case-comparison?include_candidates=true`,{signal});
// cs_agent와 C/S 부서 manager만 읽는다. 다른 역할은 403이다.
export const listImprovementCandidates = (signal:AbortSignal) => request<CandidateList>('/api/v1/cs/improvement-candidates',{signal});

// createImprovement·updateImprovement와 같은 확인(실제 API 모드, cs_agent 세션, CSRF)을 거친다.
// 목업 모드에서는 보내지 않는다. 운영 빌드는 항상 실제 API 모드다.
async function writeEvidence(id:string,path:string,body:EvidenceAdd|EvidenceRemove,signal:AbortSignal):Promise<ImprovementDetail>{
  if(getApiMode()!=='live')throw new ApiError(403,'모의 응답에서는 근거를 바꾸지 않습니다.');
  const identity=await me();
  if(signal.aborted)throw new DOMException('Aborted','AbortError');
  if(!identity?.data.roles.includes('cs_agent'))throw new ApiError(403,'C/S 담당자 세션이 필요합니다.');
  const token=sessionCsrfToken();
  if(!token)throw new ApiError(403,'세션 보호 정보를 확인할 수 없습니다. 다시 로그인해 주세요.');
  const result=await request<ImprovementDetail>(path,{method:'POST',signal,headers:{'X-CSRF-Token':token},body:JSON.stringify(body)});
  // 다른 요청의 응답이나 빈 응답을 성공으로 쓰지 않는다. ApiError가 아니므로 화면은 결과 불명으로 다룬다.
  if(result?.data?.request?.id!==id||!Array.isArray(result.data.evidence))throw new Error('저장 결과를 확인할 수 없습니다.');
  return result;
}
export const addEvidence = (id:string,body:EvidenceAdd,signal:AbortSignal) =>
  writeEvidence(id,`/api/v1/cs/improvement-requests/${encodeURIComponent(id)}/evidence`,body,signal);
export const removeEvidence = (id:string,evidenceId:string,body:EvidenceRemove,signal:AbortSignal) =>
  writeEvidence(id,`/api/v1/cs/improvement-requests/${encodeURIComponent(id)}/evidence/${encodeURIComponent(evidenceId)}/remove`,body,signal);
