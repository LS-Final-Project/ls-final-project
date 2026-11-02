// @vitest-environment jsdom
import { webcrypto } from 'node:crypto';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { highlightQuote } from './document-highlight';
import { readOriginal } from './OriginalDocument';
import type { DocumentVersion } from '../api/rfp';

function page(html: string) { const root = document.createElement('div'); root.innerHTML = html; return root; }
afterEach(() => vi.unstubAllGlobals());

describe('원문 인용 위치', () => {
  it('서식 run을 가로지르는 유일한 인용과 줄바꿈을 연결하고 텍스트를 보존한다', () => {
    const root = page('<p>Rated <b>voltage</b> is\n  12 kV.</p>');
    const original = root.textContent;
    expect(highlightQuote(root, 'Rated voltage is 12 kV.')).toBe(1);
    expect(root.querySelectorAll('mark').length).toBe(3);
    expect(root.textContent).toBe(original);
    expect(root.querySelector('b')?.textContent).toBe('voltage');
    expect(highlightQuote(root, '12 kV.')).toBe(1);
    expect(root.textContent).toBe(original);
  });
  it('동일 문구 여러 곳이나 다른 정격을 임의로 연결하지 않는다', () => {
    const root = page('<p>12 kV</p><p>12 kV</p>');
    expect(highlightQuote(root, '12 kV')).toBe(2);
    expect(root.querySelector('mark')).toBeNull();
    expect(highlightQuote(root, '15 kV')).toBe(0);
    expect(highlightQuote(page('<p>1 2 kV</p>'), '12 kV')).toBe(0);
    expect(highlightQuote(page('<p>112 kV</p>'), '12 kV')).toBe(0);
    expect(highlightQuote(page('<p>12 kVA</p>'), '12 kV')).toBe(0);
    expect(highlightQuote(page('<p>112 kV or 12 kV</p>'), '12 kV')).toBe(1);
    expect(highlightQuote(page('<p>0.12 kV</p>'), '12 kV')).toBe(0);
    expect(highlightQuote(page('<p>1,000 kV</p>'), '000 kV')).toBe(0);
    expect(highlightQuote(page('<p>-12 kV</p>'), '12 kV')).toBe(0);
    expect(highlightQuote(page('<p>0.12 kV</p>'), '0')).toBe(0);
  });
  it('삭제값과 스타일 문자열을 현행 인용에 섞지 않는다', () => {
    const root = page('<style>.x{content:"100kV"}</style><p><del>100kV</del>12kV</p>');
    expect(highlightQuote(root, '100kV')).toBe(0);
    expect(highlightQuote(root, '12kV')).toBe(1);
    const mixed = page('<p>12<del>999</del> kV</p>');
    expect(highlightQuote(mixed, '12 kV')).toBe(1);
    expect(mixed.querySelector('del mark')).toBeNull();
    expect(highlightQuote(page('<p>12<del><br>999</del>kV</p>'), '12kV')).toBe(1);
  });
  it('PDF text layer의 br을 줄 경계로 보존해 앞줄 단어와 합치지 않는다', () => {
    const root = page('<span>Specification</span><br><span>Rated voltage</span><br><span>is 12 kV.</span>');
    expect(highlightQuote(root, 'Rated voltage is 12 kV.')).toBe(1);
    expect([...root.querySelectorAll('mark')].map(m => m.textContent)).toEqual(['Rated voltage', 'is 12 kV.']);
    expect(root.querySelectorAll('br')).toHaveLength(2);
  });
  it('빈 인용은 강조를 지우고 대조 판정을 만들지 않는다', () => {
    const root = page('<p>Hello</p>');
    highlightQuote(root, 'Hello');
    expect(highlightQuote(root, '')).toBe(0);
    expect(root.querySelector('mark')).toBeNull();
  });
});

describe('원문 파일 판본/세션 경계', () => {
  const bytes = new TextEncoder().encode('synthetic PDF');
  const doc = { document_id: 'doc', mime: 'application/pdf', size: bytes.length, sha256: '' } as DocumentVersion;
  async function setup() {
    vi.stubGlobal('crypto', webcrypto);
    const hash = Array.from(new Uint8Array(await webcrypto.subtle.digest('SHA-256', bytes)), x => x.toString(16).padStart(2, '0')).join('');
    const fetch = vi.fn(async () => new Response(bytes, { headers: { 'content-type': 'application/pdf' } }));
    vi.stubGlobal('fetch', fetch);
    return { fetch, document: { ...doc, sha256: hash } };
  }
  it('동일 출처 인증 GET과 크기·SHA가 맞을 때만 렌더 입력을 반환한다', async () => {
    const { fetch, document } = await setup();
    const signal = new AbortController().signal;
    expect((await readOriginal(document, signal)).byteLength).toBe(bytes.length);
    expect(fetch).toHaveBeenCalledWith('/api/v1/tft/documents/doc/file', { credentials: 'same-origin', redirect: 'error', signal });
  });
  it('최신 다운로드가 선택된 판본과 다르면 렌더하지 않는다', async () => {
    const { document } = await setup();
    await expect(readOriginal({ ...document, sha256: '0'.repeat(64) }, new AbortController().signal)).rejects.toThrow('판본');
    await expect(readOriginal({ ...document, size: 0 }, new AbortController().signal)).rejects.toThrow('크기');
  });
  it('권한 거절과 잘못된 MIME을 성공 원문으로 해석하지 않는다', async () => {
    await setup();
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 403 })));
    await expect(readOriginal(doc, new AbortController().signal)).rejects.toMatchObject({ status: 403 });
    vi.stubGlobal('fetch', vi.fn(async () => new Response('login', { headers: { 'content-type': 'text/html' } })));
    await expect(readOriginal(doc, new AbortController().signal)).rejects.toThrow('형식');
  });
});
