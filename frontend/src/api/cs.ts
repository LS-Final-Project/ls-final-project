import type { paths } from './generated/cs';
import { request } from './client';

export type TicketListResponse = paths['/api/v1/cs/tickets']['get']['responses'][200]['content']['application/json'];
export type TicketDetailResponse = paths['/api/v1/cs/tickets/{ticket_id}']['get']['responses'][200]['content']['application/json'];
export const listTickets = (cursor?:string) => request<TicketListResponse>(`/api/v1/cs/tickets${cursor ? `?limit=50&cursor=${encodeURIComponent(cursor)}` : ''}`);
export const getTicket = (id: string) => request<TicketDetailResponse>(`/api/v1/cs/tickets/${encodeURIComponent(id)}`);
