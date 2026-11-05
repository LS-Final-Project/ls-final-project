// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';
import IntegrationPanel from './IntegrationPanel';

const PATH = '/tft/projects/p1/supplement';
const ok = (data: unknown, status = 200) => jsonResponse(status, { data, meta: { request_id: 'x' } });
const integration = {
  required: true, holder: 'UNRESOLVED', holder_candidate_codes: [], basis_clause_nos: ['2.3.A', '2.9.B'], unresolved: ['통합 책임자 확인'], note: null, set_by: null, set_at: null,
  fallback_paths: [{ holder: 'OWN', condition: '자사가 맡을 수 있는지' }, { holder: 'HOLD', condition: '참여 보류' }],
};
type Call = { url: string; method: string; body: Record<string, never> | null; csrf: string | null };
let calls: Call[];
let project: Record<string, unknown>;
let write: () => Response;

beforeEach(() => {
  calls = []; project = { id: 'p1', version: 7, integration_responsibility: integration };
  write = () => ok(project);
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); const method = init?.method ?? 'GET';
    calls.push({ url, method, body: init?.body ? JSON.parse(init.body as string) : null, csrf: new Headers(init?.headers).get('X-CSRF-Token') });
    if (method === 'PATCH') return write();
    if (url.includes('/partner-candidates')) return ok({ items: [{ candidate_code: 'P-A', display_name: '합성 일괄', integrator_capable: true, overall_grade: 'REVIEW' }, { candidate_code: 'L-A', display_name: '합성 장비', integrator_capable: false, overall_grade: 'REVIEW' }, { candidate_code: 'P-B', display_name: '합성 제외', integrator_capable: true, overall_grade: 'EXCLUDED' }] });
    if (url.includes('/platform/users')) return ok({ items: [{ user_id: 'u1', display_name: '합성 담당' }] });
    return ok(project);
  }));
  setApiMode('live');
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });

const show = (canEdit = true) => renderAt(PATH, 'tft/projects/:projectId/supplement', <IntegrationPanel projectId="p1" canEdit={canEdit} />);
const open = async () => { show(); fireEvent.click(await screen.findByRole('button', { name: '책임자 지정' })); };
const saved = () => calls.filter(c => c.method === 'PATCH');

describe('통합 책임', () => {
  it('필요 없으면 보이지 않는다', async () => {
    project = { ...project, integration_responsibility: { ...integration, required: false } };
    const { container } = show();
    await waitFor(() => expect(calls.some(c => c.method === 'GET')).toBe(true));
    await new Promise(resolve => { setTimeout(resolve, 30); });
    expect(container.textContent).toBe('');
  });

  it('미확보면 막힘과 근거 조항, 처리 순서를 보인다', async () => {
    show();
    await screen.findByText('2.3.A, 2.9.B');
    expect(screen.getByText('미확보, 검토본 저장 막힘')).toBeTruthy();
    expect(screen.getByText('통합 책임자 확인')).toBeTruthy();
    expect(screen.getByText('자사 통합: 자사가 맡을 수 있는지')).toBeTruthy();
    expect(screen.getByText('참여 보류 검토: 참여 보류')).toBeTruthy();
  });

  it('권한이 없으면 지정 단추가 없다', async () => {
    show(false);
    await screen.findByText('담당 또는 책임자만 지정합니다.');
    expect(screen.queryByRole('button', { name: '책임자 지정' })).toBeNull();
  });

  it('지정한 사람과 시각을 이름으로 보이고 변경 단추로 바뀐다', async () => {
    project = { ...project, integration_responsibility: { ...integration, holder: 'OWN', fallback_paths: [], note: '합성 메모', set_by: 'u1', set_at: '2026-10-31T01:00:00Z' } };
    show();
    await screen.findByRole('button', { name: '책임자 변경' });
    await screen.findByText(/합성 담당, 2026-10-31/);
    expect(screen.getByText('합성 메모')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '책임자 변경' }));
    expect((screen.getByLabelText('책임자') as HTMLSelectElement).value).toBe('OWN');
    expect((screen.getByLabelText('메모') as HTMLInputElement).value).toBe('합성 메모');
  });

  it('저장은 읽은 판본과 칸을 보내고 폼을 닫으며 다시 읽는다', async () => {
    await open();
    fireEvent.change(screen.getByLabelText('책임자'), { target: { value: 'OWN' } });
    fireEvent.change(screen.getByLabelText('메모'), { target: { value: ' 자사 조립 ' } });
    const reads = () => calls.filter(c => c.method === 'GET' && c.url.endsWith('/projects/p1')).length;
    const before = reads();
    fireEvent.click(screen.getByRole('button', { name: '저장' }));
    await waitFor(() => expect(saved()).toHaveLength(1));
    expect(saved()[0].url).toMatch(/\/api\/v1\/tft\/projects\/p1$/);
    expect(saved()[0].body).toEqual({ version: 7, integration_responsibility: { holder: 'OWN', holder_candidate_codes: [], note: '자사 조립' } });
    await waitFor(() => expect(screen.queryByRole('button', { name: '저장' })).toBeNull());
    await waitFor(() => expect(reads()).toBeGreaterThan(before));
  });

  it('책임자로 고를 수 있는 값만 낸다(참여 보류는 처리 순서일 뿐이다)', async () => {
    await open();
    expect(within(screen.getByLabelText('책임자')).getAllByRole('option').map(o => o.textContent)).toEqual(['미확보', '자사 통합', '단일 협력사', '공동 제안']);
  });

  it('협력사는 단일·공동일 때만 묻고 통합 가능 후보만 낸다', async () => {
    await open();
    expect(screen.queryByText('합성 일괄')).toBeNull();
    fireEvent.change(screen.getByLabelText('책임자'), { target: { value: 'SINGLE_PARTNER' } });
    fireEvent.click(await screen.findByLabelText('합성 일괄'));
    expect(screen.queryByText('합성 장비')).toBeNull();
    expect(screen.queryByText('합성 제외')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '저장' }));
    await waitFor(() => expect(saved()).toHaveLength(1));
    expect(saved()[0].body?.integration_responsibility).toEqual({ holder: 'SINGLE_PARTNER', holder_candidate_codes: ['P-A'], note: null });
  });

  it('이미 고른 후보가 제외돼도 보여 사람이 뺄 수 있다', async () => {
    project = { ...project, integration_responsibility: { ...integration, holder: 'SINGLE_PARTNER', holder_candidate_codes: ['P-B'], fallback_paths: [] } };
    show();
    fireEvent.click(await screen.findByRole('button', { name: '책임자 변경' }));
    const box = await screen.findByLabelText('합성 제외') as HTMLInputElement;
    expect(box.checked).toBe(true);
    fireEvent.click(box);
    fireEvent.click(screen.getByRole('button', { name: '저장' }));
    await waitFor(() => expect(saved()).toHaveLength(1));
    expect((saved()[0].body?.integration_responsibility as unknown as { holder_candidate_codes: string[] }).holder_candidate_codes).toEqual([]);
  });

  it('협력사를 고른 뒤 자사로 바꾸면 후보를 보내지 않는다', async () => {
    await open();
    fireEvent.change(screen.getByLabelText('책임자'), { target: { value: 'JOINT' } });
    fireEvent.click(await screen.findByLabelText('합성 일괄'));
    fireEvent.change(screen.getByLabelText('책임자'), { target: { value: 'OWN' } });
    fireEvent.click(screen.getByRole('button', { name: '저장' }));
    await waitFor(() => expect(saved()).toHaveLength(1));
    expect(saved()[0].body?.integration_responsibility).toMatchObject({ holder: 'OWN', holder_candidate_codes: [] });
  });

  it('취소는 보내지 않고 다시 열면 저장된 값으로 시작한다', async () => {
    await open();
    fireEvent.change(screen.getByLabelText('메모'), { target: { value: '버릴 메모' } });
    fireEvent.click(screen.getByRole('button', { name: '취소' }));
    expect(screen.queryByLabelText('메모')).toBeNull();
    expect(saved()).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: '책임자 지정' }));
    expect((screen.getByLabelText('메모') as HTMLInputElement).value).toBe('');
  });

  it('판본 충돌은 입력을 지키고 다시 읽으면 새 판본으로 저장한다', async () => {
    write = () => jsonResponse(409, errorBody('TFT_VERSION_CONFLICT', 'x'));
    await open();
    fireEvent.change(screen.getByLabelText('메모'), { target: { value: '남는 메모' } });
    fireEvent.click(screen.getByRole('button', { name: '저장' }));
    expect((await screen.findByRole('alert')).textContent).toContain('입력한 내용은 남습니다');
    expect((screen.getByLabelText('메모') as HTMLInputElement).value).toBe('남는 메모');
    project = { ...project, version: 8 };
    fireEvent.click(screen.getByRole('button', { name: '다시 불러오기' }));
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
    write = () => ok(project);
    await waitFor(() => expect(calls.filter(c => c.method === 'GET' && c.url.endsWith('/projects/p1')).length).toBeGreaterThan(1));
    await new Promise(resolve => { setTimeout(resolve, 20); });
    fireEvent.click(screen.getByRole('button', { name: '저장' }));
    await waitFor(() => expect(saved()).toHaveLength(2));
    expect(saved()[1].body).toMatchObject({ version: 8 });
    expect((saved()[1].body?.integration_responsibility as unknown as { note: string }).note).toBe('남는 메모');
  });

  it('권한 오류 같은 실패는 알리고 폼을 지킨다', async () => {
    write = () => jsonResponse(403, errorBody('X', '권한 없음'));
    await open();
    fireEvent.click(screen.getByRole('button', { name: '저장' }));
    expect((await screen.findByRole('alert')).textContent).toContain('권한이 없습니다');
    expect(screen.getByRole('button', { name: '저장' })).toBeTruthy();
  });

  it('후보를 읽지 못하면 알리고 자사 지정은 막지 않는다', async () => {
    const inner = vi.mocked(fetch).getMockImplementation() as typeof fetch;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => String(input).includes('/partner-candidates') ? jsonResponse(500, errorBody('X', 'x')) : inner(input, init)));
    await open();
    fireEvent.change(screen.getByLabelText('책임자'), { target: { value: 'SINGLE_PARTNER' } });
    await screen.findByText('후보를 읽지 못했습니다.');
    expect((screen.getByRole('button', { name: '저장' }) as HTMLButtonElement).disabled).toBe(false);
    within(document.body).getByLabelText('책임자');
  });
});
