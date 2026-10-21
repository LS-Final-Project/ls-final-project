import type { paths } from './generated/platform';
import { ApiError, request } from './client';

export type MeResponse = paths['/api/v1/platform/me']['get']['responses'][200]['content']['application/json'];
export type Identity = MeResponse['data'];
// Development endpoints are deliberately absent from the production contract export.
export const listAccounts = () => request<{data: {items: Identity[]}}>('/api/v1/platform/dev/accounts');
export const selectAccount = (id: string) => request<MeResponse>('/api/v1/platform/dev/session', {method: 'POST', body: JSON.stringify({user_id: id})});
export const logout = () => request<void>('/api/v1/platform/dev/session', {method: 'DELETE'});
export async function me(): Promise<MeResponse | null> {
  try { return await request<MeResponse>('/api/v1/platform/me'); }
  catch (error) { if (error instanceof ApiError && error.status === 401) return null; throw error; }
}
