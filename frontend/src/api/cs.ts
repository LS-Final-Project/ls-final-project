import type { paths } from './generated/cs';
import { request } from './client';

export type TicketListResponse = paths['/api/v1/cs/tickets']['get']['responses'][200]['content']['application/json'];
export type TicketDetailResponse = paths['/api/v1/cs/tickets/{ticket_id}']['get']['responses'][200]['content']['application/json'];
export const listTickets = (cursor?:string, signal?:AbortSignal) => request<TicketListResponse>(`/api/v1/cs/tickets${cursor ? `?limit=50&cursor=${encodeURIComponent(cursor)}` : ''}`, {signal});
export const getTicket = (id: string, signal?:AbortSignal) => request<TicketDetailResponse>(`/api/v1/cs/tickets/${encodeURIComponent(id)}`, {signal});

export type TicketUnitsResponse = paths['/api/v1/cs/tickets/{ticket_id}/units']['get']['responses'][200]['content']['application/json'];
export type UnitHistoryResponse = paths['/api/v1/cs/tickets/{ticket_id}/unit-history']['get']['responses'][200]['content']['application/json'];
export const getTicketUnits = (id: string) => request<TicketUnitsResponse>(`/api/v1/cs/tickets/${encodeURIComponent(id)}/units`);
export const getUnitHistory = (id: string) => request<UnitHistoryResponse>(`/api/v1/cs/tickets/${encodeURIComponent(id)}/unit-history`);
