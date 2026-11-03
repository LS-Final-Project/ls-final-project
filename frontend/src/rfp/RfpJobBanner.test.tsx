// @vitest-environment jsdom
import React from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { DocumentVersion } from '../api/rfp';
import { RfpJobBanner } from './RfpJobBanner';
import { useRfpJobEvents } from './useRfpJobEvents';
import { watchRfpJob } from '../api/rfp-job-events';
vi.mock('./RfpPersona', () => ({ RfpPersona: ({ state, paused, label, completed }: { state: string; paused: boolean; label: string; completed?: boolean }) => <div role="img" aria-label={label} data-state={state} data-paused={paused} data-completed={completed} /> }));
vi.mock('../api/rfp-job-events', async importOriginal => ({ ...await importOriginal<typeof import('../api/rfp-job-events')>(), watchRfpJob: vi.fn(async () => undefined) }));
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.useRealTimers(); });
it('shows immediate admission, observed stage/counts and separates reconnecting from job failure', () => {
  const { rerender } = render(<MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" /></MemoryRouter>);
  expect(screen.getByRole('status').textContent).toContain('접수했습니다');
  rerender(<MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status="RUNNING" view={{ connection: 'reconnecting', progress: { sequence: 2, occurredAt: '2026-11-02T12:00:00Z', type: 'job.progress', step: 'extract', counts: { requirements: 5, proposals: 0 }, needsReview: true } }} /></MemoryRouter>);
  expect(screen.getByLabelText('현재 분석 상태').textContent).toContain('요구 추출');
  expect(screen.getByText(/누적 발견 수/).textContent).toContain('(임시)');
  expect(screen.getByRole('status').textContent).toContain('작업 실패를 뜻하지 않습니다');
  expect(screen.queryByRole('link')).toBeNull();
  expect(document.body.textContent).not.toMatch(/%|ETA/);
  rerender(<MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status="PARTIAL" view={{ connection: 'ended' }} /></MemoryRouter>);
  expect(screen.getByRole('link').getAttribute('href')).toBe('#rfp-document-review');
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
  expect(screen.getByLabelText('현재 분석 상태').getAttribute('data-step')).toBe('extract');
  expect(container.querySelector('.rfp-analysis-stages')).toBeNull();
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


it('shows the newest stage immediately for a burst without replay timers or delayed completion', () => {
  vi.useFakeTimers();
  const activity = ['parse', 'extract', 'search', 'compare', 'supplement', 'candidates', 'draft'].map((step, i) => ({ sequence: i + 2, occurredAt: '2026-11-02T12:00:01Z', type: 'job.progress', step }));
  const ui = (events: typeof activity, status = 'RUNNING') => <MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status={status} view={{ connection: 'connected', activity: events, progress: events.at(-1) }} /></MemoryRouter>;
  const { rerender, unmount } = render(ui([]));
  rerender(ui(activity));
  expect(screen.getByLabelText('현재 분석 상태').getAttribute('data-step')).toBe('draft');
  expect(screen.getByRole('img').getAttribute('data-state')).toBe('speaking');
  expect(screen.getByRole('button', {name:/AI 작업 내역/}).textContent).toContain('초안 작성');
  expect(vi.getTimerCount()).toBe(0);
  rerender(ui(activity, 'SUCCEEDED'));
  expect(screen.getByLabelText('현재 분석 상태').textContent).toContain('처리가 완료');
  expect(screen.getByRole('img').getAttribute('data-completed')).toBe('true');
  expect(screen.getByRole('link')).toBeTruthy();
  expect(screen.queryByRole('button', {name:'과정 다시보기'})).toBeNull();
  unmount(); expect(vi.getTimerCount()).toBe(0);
});

it('holds a long extraction until the next real event, then advances without adding another fixed wait', () => {
  vi.useFakeTimers();
  const extract = { sequence: 3, occurredAt: '2026-11-03T00:00:01Z', type: 'job.progress', step: 'extract' };
  const ui = (activity: typeof extract[]) => <MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status="RUNNING" view={{ connection: 'connected', activity, progress: activity.at(-1) }} /></MemoryRouter>;
  const { container, rerender } = render(ui([extract]));
  act(() => vi.advanceTimersByTime(60000));
  expect(screen.getByLabelText('현재 분석 상태').textContent).toContain('요구 추출');
  expect(container.querySelectorAll('.is-observed')).toHaveLength(0);
  // Count updates retain the actual step; the next event changes it immediately.
  rerender(ui([{ ...extract, sequence: 4, counts: { requirements: 9, proposals: 0 } } as typeof extract]));
  rerender(ui([extract, { ...extract, sequence: 5, step: 'search' }]));
  act(() => vi.advanceTimersByTime(0));
  expect(screen.getByLabelText('현재 분석 상태').textContent).toContain('자사 자료 검색');
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

it('shows confirmed completion immediately even when all stage events arrive together', () => {
  const activity = ['parse', 'extract', 'draft'].map((step, i) => ({ sequence: i + 2, occurredAt: '2026-11-03T00:00:01Z', type: 'job.progress', step }));
  const { container } = render(<MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status="SUCCEEDED" view={{ connection:'ended', activity, progress:activity.at(-1) }} /></MemoryRouter>);
  expect(container.querySelector('.is-processing')).toBeNull();
  expect(container.querySelector('.is-replaying')).toBeNull();
  expect(screen.getByRole('status').textContent).toContain('처리가 완료됐습니다.');
  expect(screen.getByLabelText('현재 분석 상태').textContent).toContain('처리가 완료됐습니다.');
  expect(screen.getByRole('img', {name:'분석 완료'}).getAttribute('data-completed')).toBe('true');
  expect(screen.getByRole('button', {name:/AI 작업 내역/}).getAttribute('aria-expanded')).toBe('false');
  expect(screen.getByRole('link')).toBeTruthy();
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
  expect(screen.getByRole('img').getAttribute('data-completed')).toBe('false');
});


it('restores the current stage immediately and preserves it on reconnect without replay', () => {
  const activity = ['parse', 'extract', 'compare'].map((step, i) => ({ sequence: i + 2, occurredAt: '2026-11-03T00:00:01Z', type: 'job.progress', step }));
  const ui = (connection: 'connected' | 'reconnecting', restored = false) => <MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status="RUNNING" view={{ connection, ...(restored ? {activity, progress:activity.at(-1),restoredCount:3} : {}) }} /></MemoryRouter>;
  const {rerender}=render(ui('connected'));
  rerender(ui('connected',true));
  expect(screen.getByLabelText('현재 분석 상태').getAttribute('data-step')).toBe('compare');
  expect(screen.getByRole('button',{name:/AI 작업 내역/}).textContent).toContain('자사 비교');
  rerender(ui('reconnecting',true));
  expect(screen.getByLabelText('현재 분석 상태').getAttribute('data-step')).toBe('compare');
  expect(screen.getByText('마지막 수신 단계')).toBeTruthy();
  expect(screen.getByRole('img').getAttribute('data-paused')).toBe('true');
});

it('keeps Persona, current text and history heading on the same latest draft observation', () => {
  vi.useFakeTimers();
  const parse = { sequence: 2, occurredAt: '2026-11-03T00:00:01Z', type: 'job.progress', step: 'parse' };
  const draft = { ...parse, sequence: 8, step: 'draft' };
  const ui = (activity: typeof parse[], status = 'RUNNING') => <MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status={status} view={{ connection: 'connected', activity, progress: activity.at(-1) }} /></MemoryRouter>;
  const { container, rerender } = render(ui([]));
  rerender(ui([parse, draft]));
  expect(screen.getByLabelText('현재 분석 상태').textContent).toContain('초안 작성');
  expect(screen.getByRole('button', {name:/AI 작업 내역/}).textContent).toContain('초안 작성');
  expect(screen.getByRole('img', { name: 'AI 초안 작성 중' }).getAttribute('data-state')).toBe('speaking');
  expect(screen.getByRole('status').textContent).toContain('AI가 RFP 초안을 작성하고 있습니다');
  rerender(ui([parse, draft], 'SUCCEEDED'));
  expect(screen.getByRole('img', { name: '분석 완료' }).getAttribute('data-state')).toBe('idle');
  expect(screen.getByRole('img', { name: '분석 완료' }).getAttribute('data-paused')).toBe('true');
  expect(screen.getByRole('link')).toBeTruthy();
});

it('keeps work history open on completion and respects a user collapse across incoming events', () => {
  const parse = { sequence: 2, occurredAt: '2026-11-03T00:00:01Z', type: 'job.progress', step: 'parse' };
  const draft = { ...parse, sequence: 8, step: 'draft' };
  const scroll = vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined);
  const ui = (activity: typeof parse[], status = 'RUNNING') => <MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status={status} view={{ connection: 'connected', activity, progress: activity.at(-1) }} /></MemoryRouter>;
  const { rerender } = render(ui([parse]));
  expect(screen.getByRole('button', { name: /AI 작업 내역/ }).getAttribute('aria-expanded')).toBe('false');
  rerender(ui([parse, draft]));
  expect(screen.getByRole('button', { name: /AI 작업 내역/ }).getAttribute('aria-expanded')).toBe('false');
  act(() => screen.getByRole('button', { name: /AI 작업 내역/ }).click());
  rerender(ui([parse, draft], 'SUCCEEDED'));
  expect(screen.getByRole('button', { name: /AI 작업 내역/ }).getAttribute('aria-expanded')).toBe('true');
  expect(screen.getByText(/최종 저장 상태는 별도로 확인/)).toBeTruthy();
  expect(screen.getByText(/AI의 내부 추론이 아니며/)).toBeTruthy();
  expect(scroll).not.toHaveBeenCalled();
  scroll.mockRestore();
});


it('routes drawing-only completion to the original and connection candidates without treating interim counts as results', () => {
  const progress = {sequence:4,occurredAt:'2026-11-03T00:00:01Z',type:'job.completed',step:'draft',counts:{requirements:0,proposals:0}};
  const ui = (kinds: string[]) => <MemoryRouter><RfpJobBanner accepted documents={kinds.map(kind => ({kind})) as DocumentVersion[]} projectId="p" status="SUCCEEDED" view={{connection:'ended',progress,activity:[progress]}} /></MemoryRouter>;
  const scroll = vi.spyOn(window,'scrollTo').mockImplementation(()=>undefined);
  const {rerender}=render(ui(['DRAWING','DRAWING']));
  expect(screen.getByRole('link',{name:'도면 분석 결과 확인 →'}).getAttribute('href')).toBe('#rfp-document-review');
  expect(screen.getByText(/도면 원본과 연결 후보를 대조/)).toBeTruthy();
  expect(screen.getByLabelText('현재 분석 상태').textContent).not.toContain('누적 발견 수');
  expect(screen.getByLabelText('현재 분석 상태').querySelector('time')).toBeTruthy();
  expect(scroll).not.toHaveBeenCalled();
  rerender(ui(['DRAWING','RFP_SPEC']));
  expect(screen.getByRole('link').getAttribute('href')).toBe('#rfp-document-review');
  expect(screen.getByText(/미해결 요구와 원문 근거/)).toBeTruthy();
  rerender(ui([]));
  expect(screen.getByRole('link').getAttribute('href')).toBe('#rfp-document-review');
  scroll.mockRestore();
});

it('context steps remain live thinking after draft and do not claim approval or completion',()=>{
  const activity=[{sequence:8,occurredAt:'2026-11-03T00:00:00Z',type:'job.progress',step:'draft'},{sequence:9,occurredAt:'2026-11-03T00:00:01Z',type:'job.progress',step:'context_map'}];
  render(<MemoryRouter><RfpJobBanner accepted documents={[]} projectId="p" status="RUNNING" view={{connection:'connected',activity,progress:activity[1]}}/></MemoryRouter>);
  expect(screen.getByRole('img',{name:'AI 분석 중'}).getAttribute('data-state')).toBe('thinking');
  expect(screen.getByRole('img').getAttribute('data-completed')).toBe('false');
  expect(screen.getByLabelText('현재 분석 상태').textContent).toContain('검토 대상 정리');
  expect(screen.getByRole('status').textContent).toContain('설비와 업무별로 요구사항을 정리');expect(screen.queryByRole('link')).toBeNull();
});
