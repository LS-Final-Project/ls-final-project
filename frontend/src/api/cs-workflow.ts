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
async function postTo(url: string, body: object, signal: AbortSignal, method: 'POST' | 'PUT' = 'POST', role: 'cs_agent' | 'dept_staff' = 'cs_agent') {
  if (!import.meta.env.DEV || getApiMode() !== 'live') throw new ApiError(403, '실제 API 개발 모드에서만 실행할 수 있습니다.');
  const identity = await me();
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
  if (!identity?.data.roles.includes(role)) throw new ApiError(403, role === 'dept_staff' ? '부서 담당자 세션이 필요합니다.' : 'C/S 담당자 세션이 필요합니다.');
  const token = sessionCsrfToken();
  if (!token) throw new ApiError(403, '세션 보호 정보를 확인할 수 없습니다. 다시 로그인해 주세요.');
  return request<WorkflowResponse>(url, { method, signal, headers: { 'X-CSRF-Token': token }, body: JSON.stringify(body) });
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
// 담당자 재배정(C/S 관리자). 상태는 바뀌지 않는다. 새 담당자가 활성 cs_agent인지는 플랫폼 중계가 확인한다.
export type OwnerBody = paths[`${Wf}/owner`]['put']['requestBody']['content']['application/json'];
export const reassignOwner = (ticketId: string, body: OwnerBody, signal: AbortSignal) => postTo(`${base(ticketId)}/owner`, body, signal, 'PUT');
// 설치 장비 연결을 후보·확정·제외로 기록한다. 새 연결은 version을 null로, 기존 연결은 읽은 판본을 보낸다. 업무 상태 판본은 바뀌지 않는다.
export type UnitLinkBody = paths['/api/v1/cs/tickets/{ticket_id}/units/{unit_id}']['put']['requestBody']['content']['application/json'];
export const saveUnitLink = (ticketId: string, unitId: string, body: UnitLinkBody, signal: AbortSignal) => postTo(`${ticketBase(ticketId)}/units/${encodeURIComponent(unitId)}`, body, signal, 'PUT');
export const isUnitLinkConflict = (error: unknown) => error instanceof ApiError && error.status === 409 && error.code === 'CS_UNIT_LINK_VERSION_CONFLICT';
export const isUnitAlreadyConfirmed = (error: unknown) => error instanceof ApiError && error.status === 409 && error.code === 'CS_TICKET_UNIT_ALREADY_CONFIRMED';
export const isUnitMismatch = (error: unknown) => error instanceof ApiError && error.status === 422 && error.code === 'CS_UNIT_MISMATCH';
// 이관받은 부서 담당자가 검토 답변을 보낸다(워크플로 ⑤). 문의는 검토중으로 돌아가고 답변은 C/S의 확인을 기다리는 SUBMITTED로 남는다.
export type DeptResultBody = paths[`${Wf}/dept-result`]['post']['requestBody']['content']['application/json'];
export const submitDeptResult = (ticketId: string, body: DeptResultBody, signal: AbortSignal) => postTo(`${base(ticketId)}/dept-result`, body, signal, 'POST', 'dept_staff');
// 이관받은 부서 담당자가 "우리 부서 일이 아님"으로 반송한다. 문의는 검토중으로 돌아가고 이관은 부서 반송(RETURNED_BY_DEPT)이 된다.
export const returnTransfer = (ticketId: string, body: { reason: string; version: number }, signal: AbortSignal) => postTo(`${base(ticketId)}/return`, body, signal, 'POST', 'dept_staff');
export const closeTicket = (ticketId: string, version: number, signal: AbortSignal) => post(ticketId, '/close', { version }, signal);

// 한국어 요약을 만든다. 같은 원문·프롬프트 판본의 성공한 요약이 있으면 모델을 부르지 않고 그것을 돌려주고(200), 없으면 모델을 한 번 불러 저장한다(201).
export const createTicketSummary = (ticketId: string, signal: AbortSignal) => postTo(`${ticketBase(ticketId)}/summary`, {}, signal);

// 담당자가 AI 분류를 정정한다. 바꾼 칸만 보내고 사유가 필수다. 읽은 분석 판본(version)과 기준 분석 실행(analysis_id)을 함께 보낸다. 업무 상태 판본은 바뀌지 않는다.
export type CorrectionBody = paths['/api/v1/cs/tickets/{ticket_id}/analysis/corrections']['post']['requestBody']['content']['application/json'];
export const correctAnalysis = (ticketId: string, body: CorrectionBody, signal: AbortSignal) => postTo(`${ticketBase(ticketId)}/analysis/corrections`, body, signal);
export const isResultVersionConflict = (error: unknown) => error instanceof ApiError && error.status === 409 && error.code === 'CS_DEPT_RESULT_VERSION_CONFLICT';
export const isResolutionExists = (error: unknown) => error instanceof ApiError && error.status === 409 && error.code === 'CS_RESOLUTION_CASE_EXISTS';
export const isInvalidOwner = (error: unknown) => error instanceof ApiError && error.status === 422 && error.code === 'CS_INVALID_OWNER';
export const isVersionConflict = (error: unknown) => error instanceof ApiError && error.status === 409 && error.code === 'CS_WORKFLOW_VERSION_CONFLICT';
