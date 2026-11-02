// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { RfpDocumentReview } from './RfpDocumentReview';
import { MOCK_DETAIL, MOCK_REQUIREMENTS } from '../mocks/rfp-table';
import { getRequirement, listRequirements, type DocumentVersion } from '../api/rfp';
vi.mock('../api/rfp', async original => ({ ...await original<object>(), getRequirement: vi.fn(), listRequirements: vi.fn() }));
vi.mock('./OriginalDocument', () => ({ OriginalDocument: ({ document, origin }: { document: DocumentVersion; origin?: { quote: string } }) => <div data-testid="original">{document.title}<span data-testid="quote">{origin?.quote}</span></div> }));
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
  expect(listRequirements).toHaveBeenLastCalledWith('p', { document_version_id: 'v1' }, 10, undefined);
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

it('요구 페이지는 현재 문서의 커서로만 진행하고 필터 변경은 첫 페이지부터 시작한다', async () => {
  vi.mocked(listRequirements).mockImplementation(async (_p, _filters, _limit, cursor) => ({ data: { items: [MOCK_REQUIREMENTS[cursor ? 1 : 0]] as never, next_cursor: cursor ? null : 'next' } }));
  vi.mocked(getRequirement).mockImplementation(async id => ({ data: { ...MOCK_DETAIL, judgement: { ...MOCK_DETAIL.judgement, rnd_check: 'NOT_REQUIRED' as const }, requirement_id: id, origin: { ...MOCK_DETAIL.origin, document_version_id: 'v0' } } }));
  show();
  await screen.findByRole('button', { name: /R-01/ });
  fireEvent.click(screen.getByRole('button', { name: '다음 요구 목록' }));
  await screen.findByText('2쪽 · 1건');
  expect(listRequirements).toHaveBeenLastCalledWith('p', { document_version_id: 'v0' }, 10, 'next');
  fireEvent.click(screen.getByLabelText('미해결 필수만'));
  await screen.findByText('1쪽 · 1건');
  expect(listRequirements).toHaveBeenLastCalledWith('p', { document_version_id: 'v0', unresolved_mandatory: true }, 10, undefined);
});
