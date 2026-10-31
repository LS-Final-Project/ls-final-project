import type { paths } from './generated/cs';
import { ApiError, getApiMode, request, sessionCsrfToken } from './client';
import { me } from './platform';

type Wf = '/api/v1/cs/tickets/{ticket_id}/workflow';
export type WorkflowResponse = paths[Wf]['get']['responses'][200]['content']['application/json'];
export type WorkflowRecord = WorkflowResponse['data'];
export type TransferBody = paths[`${Wf}/transfer`]['post']['requestBody']['content']['application/json'];
export type DeptCode = TransferBody['dept_code'];
export type ReviewBody = paths[`${Wf}/dept-results/{result_id}/review`]['post']['requestBody']['content']['application/json'];
export type DeptResultRow = WorkflowRecord['dept_results'][number];
export type ReturnBody = paths[`${Wf}/return-subtype`]['post']['requestBody']['content']['application/json'];

const ticketBase = (ticketId: string) => `/api/v1/cs/tickets/${encodeURIComponent(ticketId)}`;
const base = (ticketId: string) => `${ticketBase(ticketId)}/workflow`;

export const getTicketWorkflow = (ticketId: string, signal?: AbortSignal) => request<WorkflowResponse>(base(ticketId), { signal });

// 업무 상태를 바꾸는 호출은 개발 모드의 실제 API에서만 연다(개선요청 쓰기와 같은 기준). 운영 중계가 생기기 전까지 모의 응답으로 성공한 척하지 않는다.
// 모든 요청은 읽은 업무 판본(version)을 보낸다. 판본이 다르면 서버가 409 CS_WORKFLOW_VERSION_CONFLICT를 돌려준다.
async function postTo(url: string, body: object, signal: AbortSignal) {
  if (!import.meta.env.DEV || getApiMode() !== 'live') throw new ApiError(403, '실제 API 개발 모드에서만 실행할 수 있습니다.');
  const identity = await me();
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
  if (!identity?.data.roles.includes('cs_agent')) throw new ApiError(403, 'C/S 담당자 세션이 필요합니다.');
  const token = sessionCsrfToken();
  if (!token) throw new ApiError(403, '세션 보호 정보를 확인할 수 없습니다. 다시 로그인해 주세요.');
  return request<WorkflowResponse>(url, { method: 'POST', signal, headers: { 'X-CSRF-Token': token }, body: JSON.stringify(body) });
}
const post = (ticketId: string, action: string, body: object, signal: AbortSignal) => postTo(`${base(ticketId)}${action}`, body, signal);

export const startReview = (ticketId: string, version: number, signal: AbortSignal) => post(ticketId, '/start-review', { version }, signal);
export const transferTicket = (ticketId: string, body: TransferBody, signal: AbortSignal) => post(ticketId, '/transfer', body, signal);
export const recallTransfer = (ticketId: string, body: { reason: string; version: number }, signal: AbortSignal) => post(ticketId, '/recall', body, signal);
export const confirmReturn = (ticketId: string, body: ReturnBody, signal: AbortSignal) => post(ticketId, '/return-subtype', body, signal);
// 종결된 문의에 해결 사례를 한 건 등록한다(워크플로 ⑧). 상태 판본은 바뀌지 않는다.
export type ResolutionBody = paths['/api/v1/cs/tickets/{ticket_id}/resolution-case']['post']['requestBody']['content']['application/json'];
export const registerResolutionCase = (ticketId: string, body: ResolutionBody, signal: AbortSignal) => postTo(`${ticketBase(ticketId)}/resolution-case`, body, signal);
// 부서 답변을 확인(ACCEPT)하거나 같은 부서에 다시 요청(REWORK, 메모 필수)한다. 업무 판본과 결과 판본을 함께 보낸다.
export const reviewDeptResult = (ticketId: string, resultId: string, body: ReviewBody, signal: AbortSignal) => post(ticketId, `/dept-results/${encodeURIComponent(resultId)}/review`, body, signal);
export const closeTicket = (ticketId: string, version: number, signal: AbortSignal) => post(ticketId, '/close', { version }, signal);

export const isResultVersionConflict = (error: unknown) => error instanceof ApiError && error.status === 409 && error.code === 'CS_DEPT_RESULT_VERSION_CONFLICT';
export const isResolutionExists = (error: unknown) => error instanceof ApiError && error.status === 409 && error.code === 'CS_RESOLUTION_CASE_EXISTS';
export const isVersionConflict = (error: unknown) => error instanceof ApiError && error.status === 409 && error.code === 'CS_WORKFLOW_VERSION_CONFLICT';
