import type { paths } from './generated/cs';
import { request } from './client';

// 반복 사례 모음(CS0201). 저장된 시스템 후보 목록과 후보별 사례 비교표를 읽기만 한다.
export type RepeatCandidateList = paths['/api/v1/cs/improvement-requests']['get']['responses'][200]['content']['application/json'];
export type CaseComparison = paths['/api/v1/cs/improvement-requests/{request_id}/case-comparison']['get']['responses'][200]['content']['application/json'];
export const listRepeatCandidates = (signal:AbortSignal) => request<RepeatCandidateList>('/api/v1/cs/improvement-requests?origin=SYSTEM_CANDIDATE',{signal});
export const getCaseComparison = (id:string,signal:AbortSignal) => request<CaseComparison>(`/api/v1/cs/improvement-requests/${encodeURIComponent(id)}/case-comparison`,{signal});
