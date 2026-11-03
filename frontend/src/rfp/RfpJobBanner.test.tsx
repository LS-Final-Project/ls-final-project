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
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.useRealTimers(); });
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
  expect(screen.getByRole('status').textContent).toContain('AI가 RFP를 분석하고 있습니다');
  expect(screen.getByRole('status').textContent).not.toContain('접수');
  rerender(<MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status="RUNNING" view={{ connection: 'connected', progress: { ...progress, type: 'job.completed' } }} /></MemoryRouter>);
  expect(screen.getByRole('status').textContent).toContain('저장된 결과를 확인');expect(screen.queryByRole('link')).toBeNull();
});

it('shows only observed stages and stops motion on reconnect, denial and terminal confirmation', () => {
  const parse = { sequence: 2, occurredAt: '2026-11-02T12:00:01Z', type: 'job.progress', step: 'parse' };
  const extract = { ...parse, sequence: 3, step: 'extract', counts: { requirements: 5, proposals: 0 } };
  const view = { connection: 'connected' as const, progress: extract, activity: [parse, extract] };
  const ui = (v: typeof view | { connection: 'stopped' }, status = 'RUNNING') => <MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status={status} view={v} /></MemoryRouter>;
  const { container, rerender } = render(ui(view));
  expect(container.querySelector('.is-live')).toBeTruthy();
  expect(container.querySelectorAll('.rfp-analysis-stages .is-observed')).toHaveLength(1);
  expect(container.querySelector('[aria-current="step"]')?.textContent).toContain('요구 추출');
  expect(screen.getAllByText('아직 관측 안 됨')).toHaveLength(5);
  expect(container.querySelector('.rfp-analysis-history')?.textContent).toContain('2개 단계 관측');
  expect(document.body.textContent).not.toMatch(/%|ETA|문서 읽기 완료/);
  rerender(<MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status="RUNNING" view={{ ...view, connection: 'reconnecting' }} /></MemoryRouter>);
  expect(container.querySelector('.is-live')).toBeNull();
  expect(screen.getByText('마지막 수신 단계')).toBeTruthy();
  rerender(ui({ connection: 'stopped' }));
  expect(container.querySelector('.rfp-analysis-stages')).toBeNull();
  expect(container.querySelector('.rfp-analysis-history')).toBeNull();
  rerender(ui({ ...view, progress: { ...extract, type: 'job.completed' } }));
  expect(container.querySelector('.is-live')).toBeNull();
  expect(screen.getByRole('status').textContent).toContain('결과를 확인');
  expect(screen.queryByRole('link')).toBeNull();
  rerender(ui(view, 'SUCCEEDED'));
  expect(container.querySelector('.is-live')).toBeNull();
  expect(screen.getByRole('link')).toBeTruthy();
});


it('gives extraction more reading time than parsing without delaying confirmed completion', () => {
  vi.useFakeTimers();
  const events = ['parse', 'extract', 'search', 'compare', 'supplement', 'candidates', 'draft'].map((step, i) => ({ sequence: i + 2, occurredAt: '2026-11-02T12:00:01Z', type: 'job.progress', step }));
  const ui = (activity: typeof events, status = 'RUNNING') => <MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status={status} view={{ connection: 'connected', activity, progress: activity.at(-1) }} /></MemoryRouter>;
  const { container, rerender, unmount } = render(ui([]));
  rerender(ui(events));
  expect(container.querySelector('[aria-current="step"]')?.textContent).toContain('문서 읽기');
  act(() => vi.advanceTimersByTime(1099));
  expect(container.querySelector('[aria-current="step"]')?.textContent).toContain('문서 읽기');
  act(() => vi.advanceTimersByTime(1));
  expect(container.querySelector('[aria-current="step"]')?.textContent).toContain('요구 추출');
  act(() => vi.advanceTimersByTime(2199));
  expect(container.querySelector('[aria-current="step"]')?.textContent).toContain('요구 추출');
  act(() => vi.advanceTimersByTime(1));
  expect(container.querySelector('[aria-current="step"]')?.textContent).toContain('자사 자료 검색');
  rerender(ui(events, 'SUCCEEDED'));
  expect(screen.getByRole('status').textContent).toContain('처리가 완료');
  expect(screen.getByRole('link')).toBeTruthy();
  expect(screen.getByText(/완료된 분석 과정 다시보기/)).toBeTruthy();
  expect(container.querySelector('.is-live')).toBeNull();
  act(() => screen.getByRole('button', { name: '한 번에 보기' }).click());
  expect(container.querySelector('[aria-current="step"]')?.textContent).toContain('초안 작성');
  act(() => screen.getByRole('button', { name: '과정 다시보기' }).click());
  expect(container.querySelector('[aria-current="step"]')?.textContent).toContain('문서 읽기');
  unmount(); expect(vi.getTimerCount()).toBe(0);
});

it('holds a long extraction until the next real event, then advances without adding another fixed wait', () => {
  vi.useFakeTimers();
  const extract = { sequence: 3, occurredAt: '2026-11-03T00:00:01Z', type: 'job.progress', step: 'extract' };
  const ui = (activity: typeof extract[]) => <MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status="RUNNING" view={{ connection: 'connected', activity, progress: activity.at(-1) }} /></MemoryRouter>;
  const { container, rerender } = render(ui([extract]));
  act(() => vi.advanceTimersByTime(60000));
  expect(container.querySelector('[aria-current="step"]')?.textContent).toContain('요구 추출');
  expect(container.querySelectorAll('.is-observed')).toHaveLength(0);
  // Another extraction count does not restart the presentation deadline.
  rerender(ui([{ ...extract, sequence: 4, counts: { requirements: 9, proposals: 0 } } as typeof extract]));
  rerender(ui([extract, { ...extract, sequence: 5, step: 'search' }]));
  act(() => vi.advanceTimersByTime(0));
  expect(container.querySelector('[aria-current="step"]')?.textContent).toContain('자사 자료 검색');
});

it('keeps the analysis animation through draft observation and result confirmation until authoritative completion', () => {
  const progress = { sequence: 8, occurredAt: '2026-11-03T00:01:00Z', type: 'job.progress', step: 'draft' };
  const ui = (type: string, connection: 'connected' | 'reconnecting' | 'ended' | 'stopped', status = 'RUNNING') => <MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status={status} view={{ connection, progress: { ...progress, type }, activity: [progress] }} /></MemoryRouter>;
  const { container, rerender } = render(ui('job.progress', 'connected'));
  expect(container.querySelector('.is-processing')).toBeTruthy();
  rerender(ui('job.progress', 'reconnecting'));
  expect(container.querySelector('.is-processing')).toBeTruthy();
  expect(screen.getByRole('status').textContent).toContain('연결 확인 중');
  rerender(ui('job.completed', 'connected'));
  expect(container.querySelector('.is-processing')).toBeTruthy();
  expect(screen.queryByRole('link')).toBeNull();
  rerender(ui('job.completed', 'ended', 'SUCCEEDED'));
  expect(container.querySelector('.is-processing')).toBeNull();
  expect(screen.getByRole('link')).toBeTruthy();
  rerender(ui('job.failed', 'connected'));
  expect(container.querySelector('.is-processing')).toBeNull();
  rerender(ui('job.progress', 'stopped'));
  expect(container.querySelector('.is-processing')).toBeNull();
});

it('keeps a separately labelled replay animation through the draft card when a fast job already completed', () => {
  vi.useFakeTimers();
  const activity = ['parse', 'extract', 'search', 'compare', 'supplement', 'candidates', 'draft'].map((step, i) => ({ sequence: i + 2, occurredAt: '2026-11-03T00:00:01Z', type: 'job.progress', step }));
  const ui = (events: typeof activity, status: string) => <MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status={status} view={{ connection: status === 'SUCCEEDED' ? 'ended' : 'connected', activity: events, progress: events.at(-1) }} /></MemoryRouter>;
  const { container, rerender } = render(ui([], 'RUNNING'));
  rerender(ui(activity, 'SUCCEEDED'));
  expect(container.querySelector('[aria-current="step"]')?.textContent).toContain('문서 읽기');
  expect(container.querySelector('.is-processing')).toBeNull();
  expect(container.querySelector('.is-replaying')).toBeTruthy();
  expect(screen.getByRole('status').textContent).toContain('처리가 완료됐습니다.');
  expect(screen.getByRole('status').textContent).toContain('완료 · 과정 표시 중');
  expect(screen.getByRole('link')).toBeTruthy();
  for (const ms of [1100, 2200, 1000, 1800, 1400, 1200]) act(() => vi.advanceTimersByTime(ms));
  expect(container.querySelector('[aria-current="step"]')?.textContent).toContain('초안 작성');
  expect(container.querySelector('.is-replaying')).toBeTruthy();
  expect(screen.getByRole('img', { name: '완료된 과정 · AI 초안 작성 중' }).getAttribute('data-state')).toBe('speaking');
  act(() => vi.advanceTimersByTime(2199));
  expect(container.querySelector('.is-replaying')).toBeTruthy();
  act(() => vi.advanceTimersByTime(1));
  expect(container.querySelector('.is-replaying')).toBeNull();
  act(() => screen.getByRole('button', { name: '과정 다시보기' }).click());
  expect(container.querySelector('.is-replaying')).toBeTruthy();
  act(() => screen.getByRole('button', { name: '한 번에 보기' }).click());
  expect(container.querySelector('.is-replaying')).toBeNull();
});


it('maps the Halo to idle, thinking and speaking without animating completed or denied work', () => {
  const event = { sequence: 3, occurredAt: '2026-11-03T00:00:01Z', type: 'job.progress', step: 'extract' };
  const ui = (status: string, step?: string, connection: 'connected' | 'stopped' = 'connected') => <MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status={status} view={{ connection, ...(step ? { progress: { ...event, step }, activity: [{ ...event, step }] } : {}) }} /></MemoryRouter>;
  const { rerender } = render(ui('QUEUED'));
  expect(screen.getByRole('img', { name: 'AI 대기 중' }).getAttribute('data-state')).toBe('idle');
  rerender(ui('RUNNING', 'extract'));
  expect(screen.getByRole('img', { name: 'AI 분석 중' }).getAttribute('data-state')).toBe('thinking');
  rerender(ui('RUNNING', 'draft'));
  expect(screen.getByRole('img', { name: 'AI 초안 작성 중' }).getAttribute('data-state')).toBe('speaking');
  rerender(ui('SUCCEEDED', 'draft'));
  expect(screen.getByRole('img', { name: '분석 완료' }).getAttribute('data-paused')).toBe('true');
  rerender(ui('RUNNING', 'draft', 'stopped'));
  expect(screen.getByRole('img', { name: '분석 상태 확인 중' }).getAttribute('data-paused')).toBe('true');
  rerender(ui('FAILED', 'draft'));
  expect(screen.getByRole('img', { name: '분석 중단' }).getAttribute('data-paused')).toBe('true');
});


it('opens restored history at the current stage, and only replays it on explicit request', () => {
  const activity = ['parse', 'extract', 'compare'].map((step, i) => ({ sequence: i + 2, occurredAt: '2026-11-03T00:00:01Z', type: 'job.progress', step }));
  const ui = (status: string, restoredCount = 0) => <MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status={status} view={{ connection: 'connected', ...(restoredCount ? { activity, progress: activity.at(-1) } : {}), restoredCount }} /></MemoryRouter>;
  const { container, rerender } = render(ui('RUNNING'));
  rerender(ui('RUNNING', 3));
  expect(container.querySelector('[aria-current="step"]')?.textContent).toContain('자사 비교');
  expect(screen.queryByText('수신한 분석 단계를 순서대로 표시합니다')).toBeNull();
  rerender(ui('SUCCEEDED', 3));
  act(() => screen.getByRole('button', { name: '과정 다시보기' }).click());
  expect(container.querySelector('[aria-current="step"]')?.textContent).toContain('문서 읽기');
});
