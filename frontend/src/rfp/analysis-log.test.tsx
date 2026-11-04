import { afterEach, expect, it, vi } from 'vitest';
import { replayRfpJob } from '../api/rfp-job-events';
import { clauseLabel, codeLabel } from './labels';

afterEach(() => { vi.unstubAllGlobals(); });

it('요구 코드와 조항 번호는 사람이 읽는 조항 표시로 바뀐다', () => {
  expect(codeLabel('cb11aed0:OCR-DOCX-0001.8:1')).toBe('본문 이미지 1');
  expect(codeLabel('cb11aed0:제10조.1:2')).toBe('제10조.1');
  expect(codeLabel('3fcc30b7:1.3.3:2')).toBe('1.3.3');
  expect(clauseLabel('OCR-PDF-P0003', 4)).toBe('PDF 4쪽');
  expect(codeLabel('R-21')).toBe('R-21');
});

it('끝난 작업은 저장된 진행 이벤트를 처음부터 재생해 단계별 기록을 돌려준다', async () => {
  const job = '11111111-1111-4111-8111-111111111111', project = '22222222-2222-4222-8222-222222222222', actor = '33333333-3333-4333-8333-333333333333';
  const ev = (n: number, type: string, payload: object) => `id: ${n}\nevent: ${type}\ndata: ${JSON.stringify({ job_id: job, sequence: n, type, occurred_at: '2026-11-04T12:30:00Z', payload })}\n\n`;
  const sse = ev(1, 'job.started', {}) + ev(2, 'job.progress', { step: 'parse' }) + ev(3, 'job.progress', { step: 'extract', counts: { requirements: 87, proposals: 0 } }) + ev(4, 'job.completed', { state: 'SUCCEEDED' });
  const urls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    urls.push(url);
    if (url === '/api/v1/platform/me') return Response.json({ data: { id: actor } });
    if (url.endsWith(`/jobs/${job}`)) return Response.json({ data: { job_id: job, project_id: project, owner: 'tft', kind: 'rfp.process', status: 'SUCCEEDED', version: 3, last_event_sequence: 4 } });
    return new Response(sse, { headers: { 'Content-Type': 'text/event-stream' } });
  }));
  const out = await replayRfpJob({ jobId: job, projectId: project, actorId: actor }, new AbortController().signal);
  expect(urls).toContain(`/api/v1/platform/jobs/${job}/events?after=0`);
  expect(out.activity.map(a => a.step)).toEqual(['parse', 'extract']);
  expect(out.startedAt).toBe('2026-11-04T12:30:00Z');
  expect(out.job.status).toBe('SUCCEEDED');
});
