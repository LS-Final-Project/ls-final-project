import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import RunAdmission from './RunAdmission';
import { errorBody, jsonResponse, renderAt } from '../test/render';

const BASE = '/api/v1/platform/evaluations';
const READY = '33333333-3333-4333-8333-333333333331';
const FINAL = '33333333-3333-4333-8333-333333333332';
const RUN = '44444444-4444-4444-8444-444444444441';
const JOB = '55555555-5555-4555-8555-555555555551';
const NEW_RUN = '44444444-4444-4444-8444-444444444442';
const NEW_JOB = '55555555-5555-4555-8555-555555555552';
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const experiment = (id: string, name: string, blocked: string | null) => ({
  experiment_id: id, name, purpose: 'smoke', data_split: blocked === 'FINAL_TEST_LOCKED' ? 'test' : null, data_origin: 'synthetic',
  model_id: 'fake', target_count: 2, created_at: '2026-10-28T01:00:00Z', admissible: blocked === null, blocked_reason: blocked,
});
const run = (status: string) => ({
  run_id: RUN, job_id: JOB, experiment_id: READY, status, requested_count: 2, needs_review: false, failure_code: null, created_at: '2026-10-28T02:00:00Z',
});
type Post = (init: RequestInit) => Response | Promise<Response>;

/**
 * 정책·목록·세션·접수 경로로 나눈 fetch 스텁과 함께 구역을 그린다.
 * Args: available은 서버 접수 가능 여부, runs는 실행 목록, post는 접수 응답, experiments는 실험 목록이다.
 * Returns: fetch 스텁과 열기 함수.
 */
function view({ available = true, runs = [run('FAILED')], post = (() => jsonResponse(202, { data: { run_id: NEW_RUN, job_id: NEW_JOB, status: 'QUEUED', status_url: `${BASE}/runs/${NEW_RUN}` } })) as Post,
  experiments = [experiment(READY, '합성 연결 검사', null), experiment(FINAL, '최종 시험', 'FINAL_TEST_LOCKED')] } = {}) {
  const fetchMock = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url);
    if (path === '/api/v1/platform/me') return new Response(null, { status: 200, headers: { 'X-CSRF-Token': 'session-csrf' } });
    if (path === `${BASE}/policy`) return jsonResponse(200, { data: { admission_available: available } });
    if (path === `${BASE}/experiments`) return jsonResponse(200, { data: { items: experiments } });
    if (path === `${BASE}/runs` && init?.method === 'POST') return post(init);
    if (path === `${BASE}/runs`) return jsonResponse(200, { data: { items: runs } });
    throw new Error('unexpected ' + path);
  });
  vi.stubGlobal('fetch', fetchMock);
  const onOpen = vi.fn();
  renderAt('/evaluations', '/evaluations', <RunAdmission onOpen={onOpen} />);
  return { fetchMock, onOpen };
}
const posts = (fetchMock: ReturnType<typeof vi.fn>) => fetchMock.mock.calls.filter(([, init]) => (init as RequestInit | undefined)?.method === 'POST');
const key = (call: unknown[]) => ((call[1] as RequestInit).headers as Record<string, string>)['Idempotency-Key'];

test('서버 접수가 꺼져 있으면 폼을 닫고 실제 모델·데이터는 실행기 연결 뒤라고 안내한다', async () => {
  view({ available: false });
  expect(await screen.findByText('서버의 평가 접수가 꺼져 있어 새 접수를 할 수 없습니다.')).toBeTruthy();
  expect(screen.getByRole('note').textContent).toContain('실제 모델·실제 데이터 접수는 실행기 연결 뒤 가능합니다');
  expect((screen.getByLabelText(/실험/) as HTMLSelectElement).disabled).toBe(true);
  expect((screen.getByRole('button', { name: '접수' }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByRole('button', { name: '같은 실험으로 다시 접수' })).toBeNull();
});

test('접수할 수 없는 실험은 사유와 함께 막고 고정된 모델·설정을 읽기 전용으로 보여준다', async () => {
  view();
  const select = await screen.findByLabelText(/실험/) as HTMLSelectElement;
  const final = [...select.options].find(option => option.value === FINAL)!;
  expect(final.disabled).toBe(true);
  expect(final.textContent).toBe('최종 시험 (최종 시험 잠금)');
  fireEvent.change(select, { target: { value: READY } });
  expect(screen.getByLabelText('선택한 실험').textContent).toContain('fake');
  expect(screen.getByLabelText('선택한 실험').textContent).toContain('synthetic');
});

test('접수는 세션 CSRF와 새 멱등 키로 실험 ID 하나만 보내고 상태 화면을 연다', async () => {
  const { fetchMock, onOpen } = view();
  fireEvent.change(await screen.findByLabelText(/실험/), { target: { value: READY } });
  fireEvent.click(screen.getByRole('button', { name: '접수' }));
  await waitFor(() => expect(onOpen).toHaveBeenCalledWith(NEW_JOB));
  const [call] = posts(fetchMock);
  const init = call[1] as RequestInit;
  expect(init.body).toBe(JSON.stringify({ experiment_id: READY }));
  expect(init.headers).toMatchObject({ 'Content-Type': 'application/json', 'X-CSRF-Token': 'session-csrf' });
  expect(key(call)).toMatch(/^[0-9a-f-]{36}$/);
  // 접수 뒤 목록을 다시 읽는다.
  await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => String(url) === `${BASE}/runs`).length).toBeGreaterThanOrEqual(3));
});

test('결과가 불명확하면 같은 키로 다시 확인하고, 거절되면 다음 접수는 새 키를 쓴다', async () => {
  let attempt = 0;
  const { fetchMock, onOpen } = view({
    post: () => {
      attempt += 1;
      if (attempt === 1) return jsonResponse(503, errorBody('DEPENDENCY_UNAVAILABLE', 'x'));
      if (attempt === 2) return jsonResponse(202, { data: { run_id: NEW_RUN, job_id: NEW_JOB, status: 'QUEUED', status_url: '' } });
      return jsonResponse(409, errorBody('IDEMPOTENCY_CONFLICT', 'x'));
    },
  });
  fireEvent.change(await screen.findByLabelText(/실험/), { target: { value: READY } });
  fireEvent.click(screen.getByRole('button', { name: '접수' }));
  fireEvent.click(await screen.findByRole('button', { name: '같은 접수로 다시 확인' }));
  await waitFor(() => expect(onOpen).toHaveBeenCalledWith(NEW_JOB));
  const [first, second] = posts(fetchMock);
  expect(key(second)).toBe(key(first));
  fireEvent.click(await screen.findByRole('button', { name: '접수' }));
  expect(await screen.findByRole('alert')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '접수' }));
  await waitFor(() => expect(posts(fetchMock)).toHaveLength(4));
  const [, , third, fourth] = posts(fetchMock);
  expect(new Set([key(first), key(third), key(fourth)]).size).toBe(3);
});

test('실행 목록에서 상태 화면을 열고 실패한 실행은 같은 실험으로 새 키 재접수한다', async () => {
  const { fetchMock, onOpen } = view();
  fireEvent.click(await screen.findByRole('button', { name: '열기' }));
  expect(onOpen).toHaveBeenCalledWith(JOB);
  expect(screen.getByRole('table', { name: '내 실행' }).textContent).toContain('실패');
  fireEvent.click(screen.getByRole('button', { name: '같은 실험으로 다시 접수' }));
  await waitFor(() => expect(onOpen).toHaveBeenCalledWith(NEW_JOB));
  const [call] = posts(fetchMock);
  expect((call[1] as RequestInit).body).toBe(JSON.stringify({ experiment_id: READY }));
});

test('성공·진행 중 실행에는 재접수 버튼이 없고 막힌 실험의 실패 실행도 재접수하지 않는다', async () => {
  view({ runs: [run('SUCCEEDED')] });
  await screen.findByRole('button', { name: '열기' });
  expect(screen.queryByRole('button', { name: '같은 실험으로 다시 접수' })).toBeNull();
  cleanup();
  view({ experiments: [experiment(READY, '합성 연결 검사', 'EXPERIMENT_NOT_APPROVED')] });
  await screen.findByRole('button', { name: '열기' });
  expect(screen.queryByRole('button', { name: '같은 실험으로 다시 접수' })).toBeNull();
});

test('막힌 실험 접수 거절은 사유로 안내하고 목록 응답이 틀리면 오류로 보여준다', async () => {
  view({ post: () => jsonResponse(403, errorBody('EXPERIMENT_NOT_APPROVED', 'x')) });
  fireEvent.change(await screen.findByLabelText(/실험/), { target: { value: READY } });
  fireEvent.click(screen.getByRole('button', { name: '접수' }));
  expect((await screen.findByRole('alert')).textContent).toBe('접수할 수 없는 실험입니다: 승인되지 않은 구성.');
  cleanup();
  view({ experiments: [{ ...experiment(READY, 'x', null), admissible: false }] });
  expect((await screen.findByRole('alert')).textContent).toBe('실험 목록을 확인할 수 없습니다.');
});
