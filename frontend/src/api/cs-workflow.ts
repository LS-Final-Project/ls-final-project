import type { paths } from './generated/cs';
import { ApiError, getApiMode, request, sessionCsrfToken } from './client';
import { me } from './platform';
import type { TicketDetailResponse } from './cs';

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
async function postTo(url: string, body: object, signal: AbortSignal, method: 'POST' | 'PUT' = 'POST', role: 'cs_agent' | 'dept_staff' | 'manager' = 'cs_agent') {
  if (getApiMode() !== 'live') throw new ApiError(403, '모의 응답 모드에서는 실행할 수 없습니다. 실제 API에서 실행하세요.');
  const identity = await me();
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
  if (!identity?.data.roles.includes(role)) throw new ApiError(403, role === 'dept_staff' ? '부서 담당자 세션이 필요합니다.' : role === 'manager' ? 'C/S 관리자 세션이 필요합니다.' : 'C/S 담당자 세션이 필요합니다.');
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
// 담당자 재배정(C/S 관리자). 서버는 manager 역할과 C/S 부서(GCS_L1) 관리 여부만 본다(cs_agent 역할은 필요 없음). 상태는 바뀌지 않는다. 새 담당자가 활성 cs_agent인지는 플랫폼 중계가 확인한다.
export type OwnerBody = paths[`${Wf}/owner`]['put']['requestBody']['content']['application/json'];
export const reassignOwner = (ticketId: string, body: OwnerBody, signal: AbortSignal) => postTo(`${base(ticketId)}/owner`, body, signal, 'PUT', 'manager');
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
// 담당자가 답변·정보 요청을 승인해 플랫폼에 넘긴다(업무 판본이 오른다). 문의 상태는 플랫폼이 접수(ACCEPTED)를 알려 refresh한 뒤에 바뀐다.
// 넘긴 결과는 HTTP 상태가 아니라 응답 customer_sends 마지막 항목의 request_status에 있다.
export type SendBody = paths[`${Wf}/send`]['post']['requestBody']['content']['application/json'];
export const sendCustomerMessage = (ticketId: string, body: SendBody, signal: AbortSignal) => post(ticketId, '/send', body, signal);
// 플랫폼 발송 상태를 읽어 저장한다. 처음 ACCEPTED면 문의가 답변 완료 또는 고객 회신 대기로 바뀐다.
export const refreshCustomerSend = (ticketId: string, sendId: string, signal: AbortSignal) => post(ticketId, `/sends/${encodeURIComponent(sendId)}/refresh`, {}, signal);
// 플랫폼이 답하지 않은 발송을 같은 판본·내용으로 다시 요청한다(플랫폼이 같은 의도로 알아봐 두 번 보내지 않는다).
export const retryCustomerSend = (ticketId: string, sendId: string, version: number, signal: AbortSignal) => post(ticketId, `/sends/${encodeURIComponent(sendId)}/retry`, { version }, signal);
export const isSendPending = (error: unknown) => error instanceof ApiError && error.status === 409 && error.code === 'CS_CUSTOMER_SEND_PENDING';
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

// 담당자가 문의를 직접 등록한다(수동 접수). 출처·판본·ID는 서버가 정하므로 보내지 않는다. 새 문의는 접수 상태로 시작한다.
export type TicketCreateBody = paths['/api/v1/cs/tickets']['post']['requestBody']['content']['application/json'];
export async function createTicket(body: TicketCreateBody, signal: AbortSignal) {
  if (getApiMode() !== 'live') throw new ApiError(403, '모의 응답 모드에서는 실행할 수 없습니다. 실제 API에서 실행하세요.');
  // 세션 확인은 등록 요청 전이다. 여기서 실패하면 문의가 만들어지지 않았으므로 다시 시도해도 되는 오류(4xx)로 알린다.
  let identity: Awaited<ReturnType<typeof me>>;
  try { identity = await me(); }
  catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(403, '세션을 확인하지 못해 등록하지 않았습니다. 잠시 후 다시 시도하세요.');
  }
  if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
  if (!identity?.data.roles.includes('cs_agent')) throw new ApiError(403, 'C/S 담당자 세션이 필요합니다.');
  const token = sessionCsrfToken();
  if (!token) throw new ApiError(403, '세션 보호 정보를 확인할 수 없습니다. 다시 로그인해 주세요.');
  return request<TicketDetailResponse>('/api/v1/cs/tickets', { method: 'POST', signal, headers: { 'X-CSRF-Token': token }, body: JSON.stringify(body) });
}
