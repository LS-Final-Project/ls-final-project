// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { RfpDocumentReview } from './RfpDocumentReview';
import { MOCK_COMPARISON, MOCK_DETAIL, MOCK_REQUIREMENTS } from '../mocks/rfp-table';
import { getComparison, getDocument, getRequirement, listProjectCandidates, listRequirements, type DocumentVersion } from '../api/rfp';
vi.mock('../api/rfp', async original => ({ ...await original<object>(), getRequirement: vi.fn(), listRequirements: vi.fn(), getComparison: vi.fn(), getDocument: vi.fn(), listProjectCandidates: vi.fn() }));
vi.mock('./OriginalDocument', () => ({ OriginalDocument: ({ document, origin }: { document: DocumentVersion; origin?: { quote: string } }) => <div data-testid="original">{document.title}<span data-testid="quote">{origin?.quote}</span></div> }));
beforeEach(() => { vi.mocked(getDocument).mockResolvedValue({ data: { document_version_id: 'v0', extraction_warnings: [] } as never }); vi.mocked(getComparison).mockResolvedValue({ data: MOCK_COMPARISON }); vi.mocked(listProjectCandidates).mockResolvedValue({ data: { items: [], manufacturer_list_basis: 'CURRENT_PROJECT_INPUT' } }); });
afterEach(() => { cleanup(); vi.clearAllMocks(); });
const docs = ['first','second'].map((title, i) => ({ title, document_id: `doc${i}`, document_version_id: `v${i}`, kind: 'RFP_SPEC', version: 1 })) as DocumentVersion[];
const show = () => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><RfpDocumentReview projectId="p" actorId="a" documents={docs} /></MemoryRouter></QueryClientProvider>);

it('문서별 요구 선택과 정확 판본 인용을 연결하고 문서를 옮기면 이전 내용을 버린다', async () => {
  vi.mocked(listRequirements).mockImplementation(async (_p, filters) => ({ data: { items: filters.document_version_id === 'v0' ? MOCK_REQUIREMENTS.slice(0, 2) as never : [], next_cursor: null } }));
  vi.mocked(getRequirement).mockImplementation(async id => ({ data: { ...MOCK_DETAIL, judgement: { ...MOCK_DETAIL.judgement, rnd_check: 'NOT_REQUIRED' as const }, requirement_id: id, origin: { ...MOCK_DETAIL.origin, document_version_id: 'v0', quote: id.endsWith('1') ? 'first quote' : 'second quote' } } }));
  show();
  await waitFor(() => expect(screen.getByTestId('quote').textContent).toBe('first quote'));
  fireEvent.click(screen.getByRole('button', { name: /R-02/ }));
  await waitFor(() => expect(screen.getByTestId('quote').textContent).toBe('second quote'));
  fireEvent.change(screen.getByLabelText('검토 문서'), { target: { value: 'v1' } });
  await screen.findByText('이 문서의 분석 결과가 없습니다. 처리 실행 후 확인하세요.');
  expect(screen.getByTestId('quote').textContent).toBe('');
  expect(listRequirements).toHaveBeenLastCalledWith('p', { document_version_id: 'v1' }, 100, undefined, { signal: expect.any(AbortSignal) });
});

it('권한 오류와 문서 판본 불일치에서 원문 하이라이트를 만들지 않는다', async () => {
  vi.mocked(listRequirements).mockResolvedValue({ data: { items: MOCK_REQUIREMENTS.slice(0, 1) as never, next_cursor: null } });
  vi.mocked(getRequirement).mockResolvedValue({ data: { ...MOCK_DETAIL, judgement: { ...MOCK_DETAIL.judgement, rnd_check: 'NOT_REQUIRED' } } });
  show();
  await screen.findByText('요구와 문서 판본이 일치하지 않습니다. 문서를 다시 선택하세요.');
  expect(screen.getByTestId('quote').textContent).toBe('');
  cleanup();
  vi.mocked(getRequirement).mockRejectedValue(new Error('접근 거절'));
  show();
  await screen.findByText('접근 거절');
  expect(screen.getByTestId('quote').textContent).toBe('');
});

it('전체 문서 커서를 읽고 선택한 장비의 번호 페이지를 이동하며 필터 변경은 처음부터 조회한다', async () => {
  const rows = Array.from({ length: 12 }, (_, i) => ({ ...MOCK_REQUIREMENTS[0], requirement_id: `r${i}`, code: `R-${String(i + 1).padStart(2, '0')}` }));
  vi.mocked(listRequirements).mockImplementation(async (_p, filters, _limit, cursor) => ({ data: { items: (filters.unresolved_mandatory ? rows.slice(0, 1) : cursor ? rows.slice(10) : rows.slice(0, 10)) as never, next_cursor: !filters.unresolved_mandatory && !cursor ? 'next' : null } }));
  vi.mocked(getRequirement).mockImplementation(async id => ({ data: { ...MOCK_DETAIL, judgement: { ...MOCK_DETAIL.judgement, rnd_check: 'NOT_REQUIRED' as const }, requirement_id: id, origin: { ...MOCK_DETAIL.origin, document_version_id: 'v0' } } }));
  show();
  await screen.findByText('세부 요구 12개');
  expect(listRequirements).toHaveBeenLastCalledWith('p', { document_version_id: 'v0' }, 100, 'next', { signal: expect.any(AbortSignal) });
  fireEvent.click(screen.getByRole('button', { name: '2' }));
  await screen.findByText('2쪽 · 2건 / 선택 묶음 12건');
  expect(screen.getByRole('button', { name: /R-11/ })).toBeTruthy();
  fireEvent.click(screen.getByLabelText('미해결 필수만'));
  await screen.findByText('1쪽 · 1건 / 선택 묶음 1건');
  expect(listRequirements).toHaveBeenLastCalledWith('p', { document_version_id: 'v0', unresolved_mandatory: true }, 100, undefined, { signal: expect.any(AbortSignal) });
});

it('장비를 바꾸면 해당 요구만 보여주고 OCR 근거에는 원본 확인을 요구한다', async () => {
  vi.mocked(listRequirements).mockResolvedValue({ data: { items: [MOCK_REQUIREMENTS[0], { ...MOCK_REQUIREMENTS[1], equipment: 'GIS', code: '1234abcd:OCR-PDF-P0001.1:1' }] as never, next_cursor: null } });
  vi.mocked(getRequirement).mockImplementation(async id => ({ data: { ...MOCK_DETAIL, requirement_id:id, judgement: { ...MOCK_DETAIL.judgement, rnd_check:'NOT_REQUIRED' }, origin:{...MOCK_DETAIL.origin, document_version_id:'v0',clause_no:id===MOCK_REQUIREMENTS[0].requirement_id?'1.1':'OCR-PDF-P0001.1'} } }));
  show();
  await screen.findByRole('button', { name: /장비 검토.*GIS/ });
  fireEvent.click(screen.getByRole('button', { name: /장비 검토.*GIS/ }));
  await screen.findByText(/이미지에서 읽은 텍스트/);
  expect(screen.queryByRole('button', { name: /R-01/ })).toBeNull();
  expect(within(screen.getByRole('group', { name:'분석 요구 선택' })).getByRole('button', { name: /OCR-PDF-P0001/ })).toBeTruthy();
  fireEvent.change(screen.getByLabelText('설비·장비 찾기'), { target:{value:'변압기'} });
  expect(screen.queryByRole('button', { name: /장비 검토.*GIS/ })).toBeNull();
});


it('문서 추출 경고를 숨기지 않고 보여주며 오래된 요구 판본은 하이라이트하지 않는다', async () => {
  vi.mocked(getDocument).mockResolvedValue({data:{document_version_id:'v0',extraction_warnings:['PDF 원문 2쪽: 이미지 전사 불확실']} as never});
  vi.mocked(listRequirements).mockResolvedValue({data:{items:[{...MOCK_REQUIREMENTS[0],version:2}] as never,next_cursor:null}});
  vi.mocked(getRequirement).mockResolvedValue({data:{...MOCK_DETAIL,version:1,judgement:{...MOCK_DETAIL.judgement,rnd_check:'NOT_REQUIRED'},origin:{...MOCK_DETAIL.origin,document_version_id:'v0'}}});
  show();
  await screen.findByText('PDF 원문 2쪽: 이미지 전사 불확실');
  await screen.findByText('요구와 문서 판본이 일치하지 않습니다. 문서를 다시 선택하세요.');
  expect(screen.getByTestId('quote').textContent).toBe('');
});


it.each(['different-version', 'request-error'])('추출 안내 %s는 현재 판본 확인 불가로 표시하고 문서와 요구는 유지한다', async failure => {
  if (failure === 'request-error') vi.mocked(getDocument).mockRejectedValue(new Error('문서 조회 오류'));
  else vi.mocked(getDocument).mockResolvedValue({ data: { document_version_id: 'newer-version', extraction_warnings: ['다른 판본의 경고'] } as never });
  vi.mocked(listRequirements).mockResolvedValue({ data: { items: MOCK_REQUIREMENTS.slice(0, 1) as never, next_cursor: null } });
  vi.mocked(getRequirement).mockResolvedValue({ data: { ...MOCK_DETAIL, judgement: { ...MOCK_DETAIL.judgement, rnd_check: 'NOT_REQUIRED' }, origin: { ...MOCK_DETAIL.origin, document_version_id: 'v0', quote: '현재 판본 인용' } } });
  show();
  await screen.findByText('현재 판본 추출 안내 확인 불가');
  expect(screen.queryByText('다른 판본의 경고')).toBeNull();
  expect(screen.getByTestId('original').textContent).toContain('first');
  await waitFor(() => expect(screen.getByTestId('quote').textContent).toBe('현재 판본 인용'));
  expect(screen.getByRole('button', { name: /R-01/ })).toBeTruthy();
});

it('같은 문서의 선택 판본이 바뀌면 추출 안내도 별도 조회한다', async () => {
  vi.mocked(listRequirements).mockResolvedValue({ data: { items: [], next_cursor: null } });
  vi.mocked(getDocument).mockResolvedValueOnce({ data: { document_version_id: 'v0', extraction_warnings: ['이전 판본 안내'] } as never })
    .mockResolvedValueOnce({ data: { document_version_id: 'v2', extraction_warnings: ['현재 판본 안내'] } as never });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  const view = (documents: DocumentVersion[]) => <QueryClientProvider client={client}><MemoryRouter><RfpDocumentReview projectId="p" actorId="a" documents={documents} /></MemoryRouter></QueryClientProvider>;
  const rendered = render(view([docs[0]]));
  await screen.findByText('이전 판본 안내');
  rendered.rerender(view([{ ...docs[0], document_version_id: 'v2', version: 2 }]));
  await screen.findByText('현재 판본 안내');
  expect(screen.queryByText('이전 판본 안내')).toBeNull();
  expect(getDocument).toHaveBeenCalledTimes(2);
});
