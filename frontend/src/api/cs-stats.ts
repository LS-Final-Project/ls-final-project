import type { paths } from './generated/cs';
import { request } from './client';
export type Overview = paths['/api/v1/cs/stats/overview']['get']['responses'][200]['content']['application/json'];
export type Owners = paths['/api/v1/cs/stats/owners']['get']['responses'][200]['content']['application/json'];
export type CustomerHistory = paths['/api/v1/cs/stats/customers/{customer_external_id}']['get']['responses'][200]['content']['application/json'];
export type StatsFilters = { start: string; end: string; dept: string; customer: string };
export function statsQuery(filters: StatsFilters, kind: 'overview'|'owners'|'customer') {
  const query = new URLSearchParams();
  if(filters.start) query.set('start', new Date(filters.start).toISOString());
  if(filters.end) query.set('end', new Date(filters.end).toISOString());
  if(kind !== 'customer' && filters.dept) query.set('dept',filters.dept);
  if(kind === 'overview' && filters.customer) query.set('customer',filters.customer);
  return query.size ? `?${query}` : '';
}
export const getOverview = (f: StatsFilters, signal: AbortSignal) => request<Overview>(`/api/v1/cs/stats/overview${statsQuery(f,'overview')}`,{signal});
export const getOwners = (f: StatsFilters, signal: AbortSignal) => request<Owners>(`/api/v1/cs/stats/owners${statsQuery(f,'owners')}`,{signal});
export const getCustomerHistory = (f: StatsFilters, signal: AbortSignal) => request<CustomerHistory>(`/api/v1/cs/stats/customers/${encodeURIComponent(f.customer)}${statsQuery(f,'customer')}`,{signal});
