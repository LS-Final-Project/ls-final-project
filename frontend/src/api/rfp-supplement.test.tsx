// @vitest-environment jsdom
import { afterEach, expect, test, vi } from 'vitest';
import { listRequirements } from './rfp';
import { clearSessionCsrfToken, request, setApiMode } from './client';
import { getSupplementRequirement, getSupplementOptions, listSupplementCandidates, listSupplementRequirements, selectSupplement, type SupplementSelection } from './rfp-supplement';

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

test('공용 요구 목록을 재사용해도 보완 화면은 로컬 모의 요구를 끼워 넣지 않는다', async () => {
  setApiMode('mock');
  const fetcher = vi.fn(async () => Response.json({ data: { items: [], next_cursor: null } }));
  vi.stubGlobal('fetch', fetcher);
  const actual = await listSupplementRequirements('actual-project');
  expect(fetcher).toHaveBeenCalledOnce();
  expect(actual.data.items).toEqual([]);
});

test('공용 요구 API 기존 호출자는 기본 모의 fallback 동작을 유지한다', async () => {
  setApiMode('mock');
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
  const legacy = await listRequirements('existing-project', {}, 20);
  expect(legacy.data.items.length).toBeGreaterThan(0);
  expect(fetcher).not.toHaveBeenCalled();
});

test('보완의 useMockFallback:false에서는 HTTP 실패를 가짜 요구로 바꾸지 않는다', async () => {
  setApiMode('mock');
  const fetcher = vi.fn(async () => Response.json({ error: { code: 'UNAVAILABLE', message: '실제 조회 실패' } }, { status: 503 }));
  vi.stubGlobal('fetch', fetcher);
  await expect(listSupplementRequirements('actual-project')).rejects.toMatchObject({ status: 503, message: '실제 조회 실패' });
  expect(fetcher).toHaveBeenCalledOnce();
});

test('보완 원문 상세도 실 API를 사용하고 필터를 공용 요구 조회로 전달한다', async () => {
  setApiMode('mock'); const f = vi.fn(async () => Response.json({ data: { items: [] } })); vi.stubGlobal('fetch', f);
  await getSupplementRequirement('clause/id');
  await listSupplementRequirements('project', undefined, { status: 'SPEC_GAP', necessity: 'MANDATORY' });
  expect(f.mock.calls.map(call => String((call as unknown[])[0]))).toEqual([
    '/api/v1/tft/requirements/clause%2Fid',
    '/api/v1/tft/projects/project/requirements?limit=20&status=SPEC_GAP&necessity=MANDATORY',
  ]);
});
