// @vitest-environment jsdom
import { webcrypto } from 'node:crypto';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { highlightQuote, revealQuote } from './document-highlight';
import { originalQuote, readOriginal } from './OriginalDocument';
import type { DocumentVersion } from '../api/rfp';

function page(html: string) { const root = document.createElement('div'); root.innerHTML = html; return root; }
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

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
  it('별도 run으로 붙은 조항·목록 번호는 본문 단어로 보지 않되 같은 run의 숫자는 막는다', () => {
    const numbered = page('<p><span>6.2</span><span>도면 및 서류 </span><br><span> </span><span>6.2.1</span><span>계약상대자는 계획서를 제출한다.</span></p><p><span>②</span><span>일사량계 계측 범위는 0 W/㎡~1000 W/㎡로 한다.</span></p>');
    expect(highlightQuote(numbered, '계약상대자는 계획서를 제출한다.')).toBe(1);
    expect(numbered.querySelector('mark')?.textContent).toBe('계약상대자는 계획서를 제출한다.');
    expect(highlightQuote(numbered, '일사량계 계측 범위는 0 W/㎡~1000 W/㎡로 한다.')).toBe(1);
    expect(highlightQuote(numbered, '2.1 계약상대자는')).toBe(0);
    expect(highlightQuote(page('<p><span>1</span><span>12 kV</span></p>'), '12 kV')).toBe(0);
    expect(highlightQuote(page('<p><span>1.</span><span>5 kV</span></p>'), '5 kV')).toBe(0);
    expect(highlightQuote(page('<p><span>1.</span><span>5 kV</span></p>'), '1.5 kV')).toBe(1);
    expect(highlightQuote(page('<p><span>3.2</span><span>1 kV</span></p>'), '1 kV')).toBe(0);
    expect(highlightQuote(page('<p><span>정격 1</span><span>12 kV</span></p>'), '12 kV')).toBe(0);
    expect(highlightQuote(page('<p>6.2.1계약상대자는</p>'), '계약상대자는')).toBe(0);
  });
  it('페이지마다 반복되는 머리글·바닥글을 건너 페이지를 넘는 인용을 잇는다', () => {
    const pages = Array.from({ length: 4 }, (_, n) => `<section class="docx"><article><table><tr><td><p>사업명 90MW</p></td></tr></table><p>본문 ${n === 1 ? '최대한 많은' : n === 2 ? '부품을 조립한다.' : '기타'}</p><p>제 3장 기술규격서</p><p>페이지 ${n + 1} / 4</p></article></section>`).join('');
    const root = page(pages);
    expect(highlightQuote(root, '최대한 많은 본문 부품을 조립한다.')).toBe(1);
    expect([...root.querySelectorAll('mark')].map(m => m.textContent).join('|')).toBe('최대한 많은|본문 부품을 조립한다.');
    expect(highlightQuote(root, '제 3장 기술규격서')).toBe(0);
  });
  it('목차 줄과 본문에 같은 제목이 있으면 본문만, 본문이 둘이면 불확실로 둔다', () => {
    const root = page('<p>첨부 2 도면목록 ........ 40</p><p>정상 첨부 2 도면목록</p>');
    expect(highlightQuote(root, '첨부 2 도면목록')).toBe(1);
    expect(root.querySelector('mark')?.parentElement?.textContent).toBe('정상 첨부 2 도면목록');
    expect(highlightQuote(page('<p>계산식 ....... 40</p><p>첨부 1 계산식</p><p>1. 계산식</p>'), '계산식')).toBe(2);
    expect(highlightQuote(page('<p>계산식 ....... 40</p>'), '계산식')).toBe(1);
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


it('highlighting during a refresh never scrolls the document or its outer page', () => {
  const scroll = vi.fn();
  vi.stubGlobal('scrollTo', scroll);
  const old = Element.prototype.scrollIntoView;
  Element.prototype.scrollIntoView = scroll;
  try {
    const root = page('<p>Rated voltage 12 kV.</p>');
    expect(highlightQuote(root, '12 kV.')).toBe(1);
    expect(highlightQuote(root, '12 kV.')).toBe(1);
    expect(scroll).not.toHaveBeenCalled();
  } finally { Element.prototype.scrollIntoView = old; }
});


it('an explicit quote selection moves only the viewer scroll offset', () => {
  const root = page('<p>Rated voltage 12 kV.</p>');
  highlightQuote(root, '12 kV.');
  const scroller = document.createElement('div');
  scroller.scrollTop = 120;
  Object.defineProperty(scroller, 'clientHeight', { value: 300 });
  vi.spyOn(scroller, 'getBoundingClientRect').mockReturnValue({ top: 50 } as DOMRect);
  vi.spyOn(root.querySelector('mark')!, 'getBoundingClientRect').mockReturnValue({ top: 430 } as DOMRect);
  const outer = vi.fn(); vi.stubGlobal('scrollTo', outer);
  revealQuote(root, scroller);
  expect(scroller.scrollTop).toBe(400);
  expect(outer).not.toHaveBeenCalled();
});


it('이미지 전사는 native 텍스트 강조 대신 정확한 이미지/페이지 위치 안내를 준다', () => {
  const origin = { quote: '170 kV', clause_no: 'OCR-DOCX-0002.1', page: null } as Parameters<typeof originalQuote>[0];
  expect(originalQuote(origin)).toEqual({ quote: '', notice: expect.stringContaining('본문 이미지 순번 2') });
  expect(originalQuote({ ...origin!, clause_no: 'OCR-PDF-P0003.1', page: 3 })).toEqual({ quote: '', notice: expect.stringContaining('PDF 원문 3쪽') });
  expect(originalQuote({ ...origin!, clause_no: '1.3.1' })).toEqual({ quote: '170 kV', notice: undefined });
});
