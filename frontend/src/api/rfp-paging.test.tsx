import { afterEach, expect, it, vi } from 'vitest';
import { setApiMode } from './client';
import { listAllRequirements } from './rfp';

afterEach(() => { vi.unstubAllGlobals(); setApiMode('mock'); });

it('요구 목록은 서버 상한 100건으로 나눠 커서를 끝까지 따라 읽는다', async () => {
  setApiMode('live');
  const urls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    urls.push(url);
    const second = url.includes('cursor=c2');
    return Response.json({ data: { items: [{ requirement_id: second ? 'r2' : 'r1' }], next_cursor: second ? null : 'c2' } });
  }));
  const items = await listAllRequirements('p1', { excluded: true });
  expect(items.map(i => i.requirement_id)).toEqual(['r1', 'r2']);
  expect(urls).toHaveLength(2);
  for (const url of urls) { expect(url).toContain('limit=100'); expect(url).toContain('excluded=true'); }
});
