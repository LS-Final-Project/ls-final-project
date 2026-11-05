// @vitest-environment jsdom
import { useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { cleanup, fireEvent, render, screen, waitFor, within, act } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import RfpResponseReview from './RfpResponseReview';
import { me } from '../api/platform';
import { getProject } from '../api/tft';
import { listDocuments, listRequirements, type DocumentVersion, type RequirementSummary } from '../api/rfp';
import { getSupplementRequirement } from '../api/rfp-supplement';
import { getContext, processContext, updateContext, type ContextEnvelope } from '../api/rfp-context';
import { getApiMode } from '../api/client';
import { MOCK_DETAIL, MOCK_REQUIREMENTS } from '../mocks/rfp-table';
vi.mock('../components/BusinessAccess', () => ({ useBusinessAccess: () => ({ ready: true }), BusinessGate: () => null }));
vi.mock('../api/platform', () => ({ me: vi.fn() }));
vi.mock('../api/tft', () => ({ getProject: vi.fn() }));
vi.mock('../api/rfp', async original => ({ ...await original<object>(), listDocuments: vi.fn(), listRequirements: vi.fn() }));
vi.mock('../api/rfp-supplement', async original => ({ ...await original<object>(), getSupplementRequirement: vi.fn() }));
vi.mock('../api/rfp-context', async original => ({ ...await original<object>(), getContext: vi.fn(), processContext: vi.fn(), updateContext: vi.fn() }));
vi.mock('./IntegrationPanel', () => ({ default: () => null }));
vi.mock('./OriginalDocument', () => ({ OriginalDocument: ({ document, origin }: { document: DocumentVersion; origin?: { quote: string } }) => <div data-testid="source">{document.document_id}:{document.document_version_id}<span data-testid="highlight">{origin?.quote}</span></div> }));
vi.mock('./ResponseDecision', () => ({ ResponseDecision: ({ requirement, actorId, projectId }: { requirement: RequirementSummary; actorId: string; projectId: string }) => { const [draft, setDraft] = useState(''); return <div data-testid="decision">{actorId}:{projectId}:{requirement.requirement_id}:v{requirement.version}<input aria-label="대응 선택 사유" value={draft} onChange={e => setDraft(e.target.value)} /></div>; } }));
const docs = [{ document_id: 'doc1', document_version_id: 'v1', title: '첫 문서', version: 1 }, { document_id: 'doc2', document_version_id: 'v2', title: '둘째 문서', version: 2 }] as DocumentVersion[];
const rows = Array.from({ length: 12 }, (_, i) => ({ ...MOCK_REQUIREMENTS[0], requirement_id: `r${i}`, code: `R-${String(i + 1).padStart(2, '0')}`, version: 3 })) as RequirementSummary[];
function context(): ContextEnvelope { return { project_id: 'p', version: 1, status: 'CURRENT', source_fingerprint: 'a'.repeat(64), current_source_fingerprint: 'a'.repeat(64), provenance: 'ai_draft', created_at: null, created_by: null, reason: null, requirements: [], source_refs: rows.map(r => ({ requirement_id: r.requirement_id, version: r.version, document_id: 'doc1', document_version_id: 'v1', code: r.code, page: 1, quote: '근거', clause_id: null })), result: { schema_version: 'rfp-context-v1', prompt_version: 'rfp-context-v1', outcome: 'VALID', issues: [], groups: [{ entity_id: 'transformer', name: '변압기 대응', kind: 'EQUIPMENT', evidence_requirement_ids: [], diagram_source_ids: [], needs_review: false }], assignments: rows.map((r, i) => ({ requirement_id: r.requirement_id, primary_category: i === 1 ? 'TASK' : 'EQUIPMENT', additional_categories: i === 0 ? ['QUALITY'] : [], group_ids: ['transformer'], relations: [], reason: '원문', evidence_requirement_ids: [], needs_review: false })) } }; }
function view(client: QueryClient, projectId = 'p') { return <QueryClientProvider client={client}><RfpResponseReviewRoute projectId={projectId} /></QueryClientProvider>; }
// The route parameter is supplied by the real router, including project switches.
function RfpResponseReviewRoute({ projectId }: { projectId: string }) { return <MemoryRouter key={projectId} initialEntries={[`/tft/projects/${projectId}/response`]}><Routes><Route path="/tft/projects/:projectId/response" element={<RfpResponseReview />} /></Routes></MemoryRouter>; }
function show() { const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } }); return { client, ...render(view(client)) }; }
async function openList() { const summary = await screen.findByText(/^요구 목록 ·/); if (!(summary.parentElement as HTMLDetailsElement).open) fireEvent.click(summary); }
beforeEach(() => {
  vi.mocked(me).mockResolvedValue({ data: { id: 'actor' } } as never);
  vi.mocked(getProject).mockResolvedValue({ data: { name: '프로젝트', sales_user_id: 'actor' } } as never);
  vi.mocked(listDocuments).mockResolvedValue({ data: { items: docs } } as never);
  vi.mocked(listRequirements).mockImplementation(async (_p, filters) => ({ data: { items: filters.document_version_id === 'v1' ? rows : [], next_cursor: null } }));
  vi.mocked(getContext).mockResolvedValue({ data: context() });
  vi.mocked(getSupplementRequirement).mockImplementation(async id => ({ data: { ...MOCK_DETAIL, requirement_id: id, version: 3, origin: { ...MOCK_DETAIL.origin, document_version_id: 'v1', quote: `인용 ${id}` } } } as never));
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });
it('실제 부모가 요구 판본과 원문을 연결하며 접기·너비 변경에도 선택 사유를 보존한다', async () => {
  show(); await waitFor(() => expect(screen.getByTestId('highlight').textContent).toBe('인용 r0'));
  await openList();
  fireEvent.click(within(screen.getByRole('navigation', { name: '요구 선택' })).getByRole('button', { name: /R-02/ }));
  await waitFor(() => expect(screen.getByTestId('highlight').textContent).toBe('인용 r1'));
  expect(screen.getByTestId('source').textContent).toContain('doc1:v1');
  expect(screen.getByTestId('decision').textContent).toBe('actor:p:r1:v3');
  fireEvent.change(screen.getByLabelText('대응 선택 사유'), { target: { value: '확인 중인 사유' } });
  fireEvent.click(screen.getByRole('button', { name: '원문 넓게' }));
  fireEvent.click(screen.getByRole('button', { name: '원문 접기' }));
  expect((screen.getByLabelText('대응 선택 사유') as HTMLInputElement).value).toBe('확인 중인 사유');
  fireEvent.click(screen.getByRole('button', { name: '원문 펼치기' }));
  expect(screen.getByTestId('highlight').textContent).toBe('인용 r1');
  expect(processContext).not.toHaveBeenCalled(); expect(updateContext).not.toHaveBeenCalled();
});
it('요구는 10건씩 이동하고 설비·추가 분류 선택은 첫 페이지와 해당 요구로 연결한다', async () => {
  show(); await screen.findByRole('option', { name: '변압기 대응 (12)' }); await openList();
  const buttons = () => within(screen.getByRole('navigation', { name: '요구 선택' })).getAllByRole('button');
  expect(buttons()).toHaveLength(10);
  fireEvent.click(screen.getByRole('button', { name: '2페이지' })); expect(buttons()).toHaveLength(2);
  await waitFor(() => expect(screen.getByTestId('decision').textContent).toContain('r10:v3'));
  fireEvent.change(screen.getByLabelText('설비·과업'), { target: { value: 'transformer' } });
  expect(buttons()).toHaveLength(10); expect(screen.getByRole('button', { name: '1페이지' }).getAttribute('aria-current')).toBe('page');
  fireEvent.change(screen.getByLabelText('분류'), { target: { value: 'QUALITY' } });
  expect(buttons()).toHaveLength(1); await waitFor(() => expect(screen.getByTestId('decision').textContent).toContain('r0:v3'));
});
it.each(['requirement', 'version', 'document'])('상세 %s 불일치 결과는 원문을 강조하지 않는다', async mismatch => {
  vi.mocked(getSupplementRequirement).mockResolvedValue({ data: { ...MOCK_DETAIL, requirement_id: mismatch === 'requirement' ? 'other' : 'r0', version: mismatch === 'version' ? 2 : 3, origin: { ...MOCK_DETAIL.origin, document_version_id: mismatch === 'document' ? 'other' : 'v1', quote: '오래된 인용' } } } as never);
  show(); await screen.findByText('선택 요구와 원문 판본이 일치하지 않습니다. 목록을 새로 조회하세요.');
  expect(screen.getByTestId('highlight').textContent).toBe('');
});
it('이전 요구의 늦은 응답은 현재 요구 하이라이트를 덮어쓰지 않는다', async () => {
  let finish!: (value: Awaited<ReturnType<typeof getSupplementRequirement>>) => void;
  vi.mocked(getSupplementRequirement).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  show(); await openList(); await screen.findByRole('button', { name: /R-02/ });
  fireEvent.click(screen.getByRole('button', { name: /R-02/ }));
  await waitFor(() => expect(screen.getByTestId('highlight').textContent).toBe('인용 r1'));
  await act(async () => finish({ data: { ...MOCK_DETAIL, requirement_id: 'r0', version: 3, origin: { ...MOCK_DETAIL.origin, document_version_id: 'v1', quote: '늦은 r0' } } } as never));
  expect(screen.getByTestId('highlight').textContent).toBe('인용 r1');
});
it('계정·프로젝트 변경은 같은 요구 ID라도 이전 상세 캐시와 편집 상태를 사용하지 않는다', async () => {
  const { client, rerender } = show(); await waitFor(() => expect(screen.getByTestId('highlight').textContent).toBe('인용 r0'));
  fireEvent.change(screen.getByLabelText('대응 선택 사유'), { target: { value: '이전 사용자 사유' } });
  vi.mocked(getSupplementRequirement).mockImplementation(async id => ({ data: { ...MOCK_DETAIL, requirement_id: id, version: 3, origin: { ...MOCK_DETAIL.origin, document_version_id: 'v1', quote: '새 사용자 인용' } } } as never));
  await act(async () => { client.setQueryData(['me'], { data: { id: 'other-actor' } }); });
  await waitFor(() => expect(screen.getByTestId('highlight').textContent).toBe('새 사용자 인용'));
  expect((screen.getByLabelText('대응 선택 사유') as HTMLInputElement).value).toBe('');
  expect(client.getQueryData(['rfp-response-detail', getApiMode(), 'actor', 'p', 'r0', 3])).toBeDefined();
  vi.mocked(getSupplementRequirement).mockImplementation(async id => ({ data: { ...MOCK_DETAIL, requirement_id: id, version: 3, origin: { ...MOCK_DETAIL.origin, document_version_id: 'v1', quote: '새 프로젝트 인용' } } } as never));
  rerender(view(client, 'other-project'));
  await waitFor(() => expect(screen.getByTestId('highlight').textContent).toBe('새 프로젝트 인용'));
  expect(screen.getByTestId('decision').textContent).toBe('other-actor:other-project:r0:v3');
});
it('맥락 재조회가 오래된 결과로 바뀌면 숨은 분류 조건으로 원문 요구를 제거하지 않는다', async () => {
  const { client } = show(); await screen.findByRole('option', { name: '변압기 대응 (12)' }); await openList();
  fireEvent.change(screen.getByLabelText('분류'), { target: { value: 'QUALITY' } });
  expect(within(screen.getByRole('navigation', { name: '요구 선택' })).getAllByRole('button')).toHaveLength(1);
  await act(async () => { client.setQueryData(['rfp-context', getApiMode(), 'p', 'actor'], { data: { ...context(), status: 'STALE' } }); });
  await waitFor(() => expect((screen.getByLabelText('분류') as HTMLSelectElement).disabled).toBe(true));
  expect(within(screen.getByRole('navigation', { name: '요구 선택' })).getAllByRole('button')).toHaveLength(10);
});
it('문서를 바꾸면 이전 인용을 비우고 새 문서의 빈 요구 상태를 표시한다', async () => {
  show(); await waitFor(() => expect(screen.getByTestId('highlight').textContent).toBe('인용 r0'));
  fireEvent.change(screen.getByLabelText('원문 문서'), { target: { value: 'v2' } });
  await screen.findByText('조건에 맞는 요구가 없습니다.');
  expect(screen.getByTestId('source').textContent).toBe('doc2:v2');
  expect(screen.getByTestId('highlight').textContent).toBe('');
  expect(screen.queryByTestId('decision')).toBeNull();
  expect(listRequirements).toHaveBeenLastCalledWith('p', { document_version_id: 'v2' }, 100, undefined, expect.objectContaining({ signal: expect.any(AbortSignal), useMockFallback: false }));
});
