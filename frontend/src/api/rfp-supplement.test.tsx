// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest';
import { clearSessionCsrfToken, request, setApiMode } from './client';
import { getSupplementOptions, listSupplementCandidates, listSupplementRequirements, selectSupplement, type SupplementSelection } from './rfp-supplement';

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); clearSessionCsrfToken(); setApiMode('mock'); });
const payload: SupplementSelection = { version: 3, path: 'NONE', candidate_codes: [], role_split: '자사 확인', conditions: [], note: null, rnd_check_waiver_reason: null };
test('정식 읽기 경로·필터를 인코딩하고 응답을 그대로 반환한다', async () => {
  setApiMode('live');
  const fetcher = vi.fn(async () => Response.json({ data: { items: [] } }));
  vi.stubGlobal('fetch', fetcher);
  await listSupplementRequirements('project/id', 'cursor?x=1');
  await getSupplementOptions('requirement/id');
  await listSupplementCandidates('project/id', 'requirement/id');
  expect(fetcher.mock.calls.map(c => String((c as unknown[])[0]))).toEqual([
    '/api/v1/tft/projects/project%2Fid/requirements?limit=20&cursor=cursor%3Fx%3D1',
    '/api/v1/tft/requirements/requirement%2Fid/supplement-options',
    '/api/v1/tft/projects/project%2Fid/partner-candidates?requirement_id=requirement%2Fid',
  ]);
});
test('운영 CSRF가 없으면 전송하지 않고 기존 me 응답 토큰을 선택 요청에 사용한다', async () => {
  vi.stubEnv('DEV', false);
  setApiMode('live');
  let token = '';
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ data: { id: 'actor' } }), { headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token } }));
  vi.stubGlobal('fetch', fetcher);
  await expect(selectSupplement('requirement', payload, 'same-key-123', 'actor')).rejects.toThrow(/세션 확인/);
  expect(fetcher.mock.calls).toHaveLength(1);
  token = 'bound-token';
  await request('/api/v1/platform/me');
  await selectSupplement('requirement', payload, 'same-key-123', 'actor');
  const args = fetcher.mock.calls.at(-1) as unknown as [string, RequestInit];
  expect(args[1].method).toBe('POST');
  expect(args[1].body).toBe(JSON.stringify(payload));
  expect(args[1].credentials).toBe('same-origin');
  expect(args[1].headers).toMatchObject({ 'Idempotency-Key': 'same-key-123', 'X-CSRF-Token': token });
});
