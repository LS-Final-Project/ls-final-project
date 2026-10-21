import type { paths } from './generated/cs';
import { request } from './client';

export type TicketListResponse = paths['/api/v1/cs/tickets']['get']['responses'][200]['content']['application/json'];
export const listTickets = () => request<TicketListResponse>('/api/v1/cs/tickets');
