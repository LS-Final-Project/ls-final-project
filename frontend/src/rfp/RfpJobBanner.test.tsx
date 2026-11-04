// @vitest-environment jsdom
import React from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RfpJobBanner } from './RfpJobBanner';
import { useRfpJobEvents } from './useRfpJobEvents';
import { watchRfpJob } from '../api/rfp-job-events';
vi.mock('../api/rfp-job-events', async importOriginal => ({ ...await importOriginal<typeof import('../api/rfp-job-events')>(), watchRfpJob: vi.fn(async () => undefined) }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
it('shows immediate admission, observed stage/counts and separates reconnecting from job failure', () => {
  const { rerender } = render(<MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" /></MemoryRouter>);
  expect(screen.getByRole('status').textContent).toContain('접수했습니다');
  rerender(<MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status="RUNNING" view={{ connection: 'reconnecting', progress: { sequence: 2, occurredAt: '2026-11-02T12:00:00Z', type: 'job.progress', step: 'extract', counts: { requirements: 5, proposals: 0 }, needsReview: true } }} /></MemoryRouter>);
  expect(screen.getByText(/최근 확인 단계/).textContent).toContain('요구 추출');
  expect(screen.getByText(/누적 발견 수/).textContent).toContain('(임시)');
  expect(screen.getByRole('status').textContent).toContain('작업 실패를 뜻하지 않습니다');
  expect(screen.queryByRole('link')).toBeNull();
  expect(document.body.textContent).not.toMatch(/%|ETA/);
  rerender(<MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status="PARTIAL" view={{ connection: 'ended' }} /></MemoryRouter>);
  expect(screen.getByRole('link').getAttribute('href')).toBe('/tft/projects/p/table');
  expect(screen.getByRole('status').textContent).toContain('일부');
});
it('aborts on actor/project/job change and rejects late updates from each former subscription', async () => {
  const query = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function Probe({ actor, project, job }: { actor: string; project: string; job: string }) {
    const v = useRfpJobEvents(job, project, actor, 'live');
    return <span>{v?.progress?.step ?? v?.connection}</span>;
  }
  const ui = (actor: string, project: string, job: string) => <QueryClientProvider client={query}><Probe actor={actor} project={project} job={job} /></QueryClientProvider>;
  const { rerender, unmount } = render(ui('a', 'p', 'j'));
  const calls = vi.mocked(watchRfpJob).mock.calls;
  await act(async () => calls[0][2]({ connection: 'connected', progress: { sequence: 1, occurredAt: '2026-11-02T12:00:00Z', type: 'job.progress', step: 'extract' } }));
  expect(screen.getByText('extract')).toBeTruthy();
  for (const [a,p,j] of [['b','p','j'],['b','q','j'],['b','q','k']]) {
    const previous = calls.at(-1)!;
    rerender(ui(a,p,j)); expect(previous[1].aborted).toBe(true);
    await act(async () => previous[2]({ connection: 'connected', progress: { sequence: 9, occurredAt: '2026-11-02T12:00:00Z', type: 'job.progress', step: 'stale' } }));
    expect(screen.queryByText('stale')).toBeNull(); expect(screen.getByText('connecting')).toBeTruthy();
  }
  const last=calls.at(-1)!;unmount();expect(last[1].aborted).toBe(true);
});
it('observed start supersedes an older QUEUED snapshot, but a terminal signal cannot claim completion', () => {
  const progress = { sequence: 1, occurredAt: '2026-11-02T12:00:00Z', type: 'job.started' };
  const { rerender } = render(<MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status="QUEUED" view={{ connection: 'connected', progress }} /></MemoryRouter>);
  expect(screen.getByRole('status').textContent).toContain('진행이 관측');
  expect(screen.getByRole('status').textContent).not.toContain('접수');
  rerender(<MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status="RUNNING" view={{ connection: 'connected', progress: { ...progress, type: 'job.completed' } }} /></MemoryRouter>);
  expect(screen.getByRole('status').textContent).toContain('저장된 결과를 확인');expect(screen.queryByRole('link')).toBeNull();
});
