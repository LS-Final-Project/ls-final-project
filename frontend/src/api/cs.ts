import type { paths } from './generated/cs';
import { request } from './client';

export type TicketListResponse = paths['/api/v1/cs/tickets']['get']['responses'][200]['content']['application/json'];
export type TicketDetailResponse = paths['/api/v1/cs/tickets/{ticket_id}']['get']['responses'][200]['content']['application/json'];
export const listTickets = (cursor?:string, signal?:AbortSignal) => request<TicketListResponse>(`/api/v1/cs/tickets${cursor ? `?limit=50&cursor=${encodeURIComponent(cursor)}` : ''}`, {signal});
export const getTicket = (id: string, signal?:AbortSignal) => request<TicketDetailResponse>(`/api/v1/cs/tickets/${encodeURIComponent(id)}`, {signal});

export type TicketUnitsResponse = paths['/api/v1/cs/tickets/{ticket_id}/units']['get']['responses'][200]['content']['application/json'];
export type UnitHistoryResponse = paths['/api/v1/cs/tickets/{ticket_id}/unit-history']['get']['responses'][200]['content']['application/json'];
export const getTicketUnits = (id: string, signal?:AbortSignal) => request<TicketUnitsResponse>(`/api/v1/cs/tickets/${encodeURIComponent(id)}/units`, {signal});
export const getUnitHistory = (id: string, signal?:AbortSignal) => request<UnitHistoryResponse>(`/api/v1/cs/tickets/${encodeURIComponent(id)}/unit-history`, {signal});

export type TicketAnalysisResponse = paths['/api/v1/cs/tickets/{ticket_id}/analysis']['get']['responses'][200]['content']['application/json'];
export const getTicketAnalysis = (id: string, signal?:AbortSignal) => request<TicketAnalysisResponse>(`/api/v1/cs/tickets/${encodeURIComponent(id)}/analysis`, {signal, mockFallback: () => ({ data: { rules: { tier_note: null } } })});

export type DeptCode = NonNullable<NonNullable<paths['/api/v1/cs/tickets']['get']['parameters']['query']>['dept']>;
export type TransferStatus = NonNullable<NonNullable<paths['/api/v1/cs/tickets']['get']['parameters']['query']>['transfer_status']>;
export type TicketItem = TicketListResponse['data']['items'][number];

/**
 * 우리 부서로 이관된 적이 있는 문의 목록(부서 받은 요청). 항목의 transfer에 그 부서의 최신 이관이 온다.
 * Args: dept는 소속 부서 코드, status를 주면 그 부서 최신 회차의 상태만, cursor는 다음 쪽.
 */
export const listDeptRequests = (dept: DeptCode, status: TransferStatus | '', cursor?: string, signal?: AbortSignal) => {
  const query = new URLSearchParams({ limit: '100', dept });
  if (status) query.set('transfer_status', status);
  if (cursor) query.set('cursor', cursor);
  return request<TicketListResponse>(`/api/v1/cs/tickets?${query}`, { signal, mockFallback: () => ({ data: { items: [], next_cursor: null } }) });
};

export type TicketAuditResponse = paths['/api/v1/cs/tickets/{ticket_id}/audit']['get']['responses'][200]['content']['application/json'];
export const getTicketAudit = (id: string, signal?: AbortSignal) => request<TicketAuditResponse>(`/api/v1/cs/tickets/${encodeURIComponent(id)}/audit`, { signal });
