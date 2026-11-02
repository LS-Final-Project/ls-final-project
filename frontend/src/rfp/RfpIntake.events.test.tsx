// @vitest-environment jsdom
import React from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor, act } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import RfpIntake from './RfpIntake';
import { setApiMode } from '../api/client';
import { watchRfpJob } from '../api/rfp-job-events';
import examples from '../mocks/generated/tft.json';
const P = '33333333-3333-4333-8333-333333333333', J = '11111111-1111-4111-8111-111111111111', A = '00000000-0000-4000-8000-000000000003';
vi.mock('../components/BusinessAccess', () => ({ BusinessGate: () => null, useBusinessAccess: () => ({ ready: true, operational: true, enabled: true, user: { isSuccess: true, data: { data: { id: '00000000-0000-4000-8000-000000000003', roles: ['sales'] } } } }) }));
vi.mock('../api/rfp-job-events', async original => ({ ...await original<typeof import('../api/rfp-job-events')>(), watchRfpJob: vi.fn(async () => undefined) }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); setApiMode('mock'); });
function setup() {
  setApiMode('live');
  const detail = structuredClone(examples.find(e => e.path === `/api/v1/tft/projects/${P}`)!.body.data);
  const project = { ...detail, latest_job: { job_id: J, status: 'RUNNING' } };
  const fetcher = vi.fn(async (input: string) => {
    const path = input.split('?')[0];
    if (path === `/api/v1/platform/jobs/${J}`) return new Response('{}', { status: 403 });
    const data = path.endsWith('/me') ? { id: A } : path === `/api/v1/tft/projects/${P}` ? project : { items: [], next_cursor: null };
    return Response.json({ data });
  });
  vi.stubGlobal('fetch', fetcher);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/tft/projects/${P}`]}><Routes><Route path="/tft/projects/:projectId" element={<RfpIntake />} /></Routes></MemoryRouter></QueryClientProvider>);
  return { client, fetcher };
}
it('SSE denied state does not lock upload as though another actor job were running', async () => {
  setup();await waitFor(() => expect(vi.mocked(watchRfpJob)).toHaveBeenCalled());
  await act(async () => vi.mocked(watchRfpJob).mock.calls[0][2]({ connection: 'stopped', message: '조회 권한을 확인하세요.' }));
  expect((screen.getByRole('button', { name: '문서 올리기' }) as HTMLButtonElement).disabled).toBe(false);
  expect(screen.getByRole('status').textContent).toContain('조회가 중단');
  expect(screen.getByRole('status').textContent).not.toContain('시작');
  expect(screen.getByText('최근 작업').nextElementSibling?.textContent).toBe('조회 불가');
});
it('fallback GET denial removes stale RUNNING presentation and stops automatic refetch', async () => {
  const { client, fetcher } = setup();await waitFor(() => expect(vi.mocked(watchRfpJob)).toHaveBeenCalled());
  await act(async () => vi.mocked(watchRfpJob).mock.calls[0][2]({ connection: 'fallback', job: { job_id: J, project_id: P, owner: 'tft', kind: 'rfp.process', status: 'RUNNING', version: 1, last_event_sequence: 2 } }));
  await waitFor(() => expect(screen.getByRole('status').textContent).toContain('조회가 중단'));
  expect((screen.getByRole('button', { name: '문서 올리기' }) as HTMLButtonElement).disabled).toBe(false);
  expect(screen.getByText('최근 작업').nextElementSibling?.textContent).toBe('조회 불가');
  const reads = fetcher.mock.calls.filter(([u]) => u.includes(`/jobs/${J}`)).length;
  await act(async () => { await client.invalidateQueries({ queryKey: ['jobs'] }); });
  expect(fetcher.mock.calls.filter(([u]) => u.includes(`/jobs/${J}`))).toHaveLength(reads);
});
it('confirmed GET terminal snapshot triggers existing result invalidation after a watched active job', async () => {
  const {client}=setup();const invalidate=vi.spyOn(client,'invalidateQueries');
  await waitFor(()=>expect(vi.mocked(watchRfpJob)).toHaveBeenCalled());
  const update=vi.mocked(watchRfpJob).mock.calls[0][2];
  const job={job_id:J,project_id:P,owner:'tft' as const,kind:'rfp.process' as const,status:'RUNNING' as const,version:1,last_event_sequence:2};
  await act(async()=>update({connection:'connected',job}));
  expect((screen.getByRole('button',{name:'문서 올리기'}) as HTMLButtonElement).disabled).toBe(true);
  await act(async()=>update({connection:'ended',job:{...job,status:'SUCCEEDED',last_event_sequence:3}}));
  await waitFor(()=>expect(invalidate).toHaveBeenCalledWith({queryKey:['rfp-requirements','live']}));
  expect(invalidate).toHaveBeenCalledWith({queryKey:['rfp-draft',A,P]});
  expect((screen.getByRole('button',{name:'문서 올리기'}) as HTMLButtonElement).disabled).toBe(false);
});

it.each(['job.started', 'job.progress'])('recent job row agrees with %s, terminal signal, and confirmed GET without changing the cache', async type => {
  const { client } = setup();await waitFor(() => expect(vi.mocked(watchRfpJob)).toHaveBeenCalled());
  const update = vi.mocked(watchRfpJob).mock.calls[0][2];
  const job = { job_id: J, project_id: P, owner: 'tft' as const, kind: 'rfp.process' as const, status: 'QUEUED' as const, version: 1, last_event_sequence: 0 };
  const progress = { sequence: 1, occurredAt: '2026-11-02T12:00:00Z', type, step: 'extract' };
  const row = () => screen.getByText('최근 작업').nextElementSibling?.textContent;
  await act(async () => update({ connection: 'connected', job, progress }));
  expect(row()).toBe('실행 중 (최근 서버 관측)');
  expect(screen.getByRole('status').textContent).toContain('AI가 RFP를 분석하고 있습니다');
  expect(client.getQueryData(['jobs', 'live', A, P, J])).toEqual({ data: job });
  await act(async () => update({ connection: 'connected', job, progress: { ...progress, sequence: 2, type: 'job.completed' } }));
  expect(row()).toBe('결과 확인 중');
  expect(screen.getByRole('status').textContent).toContain('저장된 결과를 확인');
  expect(client.getQueryData(['jobs', 'live', A, P, J])).toEqual({ data: job });
  await act(async () => update({ connection: 'ended', job: { ...job, status: 'SUCCEEDED', last_event_sequence: 2 } }));
  expect(row()).toBe('완료');
});
