// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { parseRfpEvent, readRfpJob, watchRfpJob, type RfpWatch } from './rfp-job-events';
const target = { jobId: '11111111-1111-4111-8111-111111111111', projectId: '22222222-2222-4222-8222-222222222222', actorId: '33333333-3333-4333-8333-333333333333' };
const doc = '44444444-4444-4444-8444-444444444444';
const snap = (status = 'RUNNING', seq = 1, extra = {}) => ({ job_id: target.jobId, project_id: target.projectId, owner: 'tft', kind: 'rfp.process', status, version: 1, last_event_sequence: seq, ...extra });
const json = (data: unknown) => new Response(JSON.stringify({ data }), { headers: { 'Content-Type': 'application/json' } });
const event = (sequence = 1, type = 'job.progress', payload: unknown = { step: 'extract', document_version_id: doc, counts: { requirements: 2, proposals: 0 }, needs_review: true }) => ({ job_id: target.jobId, sequence, type, occurred_at: '2026-11-02T12:00:00Z', payload });
const frame = (e: Record<string, unknown> = event()) => [`id: ${e.sequence}`, `event: ${e.type}`, `data: ${JSON.stringify(e)}`];
const stream = (events: ReturnType<typeof event>[]) => new Response(events.map(e => frame(e).join('\r\n') + '\r\n\r\n').join(''), { headers: { 'Content-Type': 'text/event-stream' } });
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it('validates progress and strips fields outside the public RFP view', () => {
  const p = parseRfpEvent(frame(event(2, 'job.progress', { step: 'extract', counts: { requirements: 3, proposals: 0 }, secret: 'not forwarded' })), target.jobId);
  expect(p).toMatchObject({ sequence: 2, step: 'extract', counts: { requirements: 3, proposals: 0 } });
  expect(p).not.toHaveProperty('secret');
  expect(parseRfpEvent([': heartbeat'], target.jobId)).toBeUndefined();
});
it.each([
  { ...event(), job_id: doc }, { ...event(), sequence: true }, { ...event(), occurred_at: 'yesterday' },
  event(1, 'job.progress', { step: 'invented' }), event(1, 'job.progress', { counts: { requirements: -1, proposals: 0 } }),
  event(1, 'job.progress', { counts: { requirements: '2', proposals: 0 } }), event(1, 'job.progress', { document_version_id: 'bad' }),
  event(1, 'job.progress', { needs_review: 'true' }),
])('rejects malformed/cross-job public events %#', e => expect(() => parseRfpEvent(frame(e), target.jobId)).toThrow());
it('rejects duplicated id and mismatched event type', () => {
  expect(() => parseRfpEvent([...frame(), 'id: 1'], target.jobId)).toThrow();
  expect(() => parseRfpEvent(frame().map(x => x === 'event: job.progress' ? 'event: job.failed' : x), target.jobId)).toThrow();
});
it.each([{ owner: 'cs' }, { project_id: doc }, { job_id: doc }, { kind: 'evaluation.run' }, { version: true }, { last_event_sequence: -1 }])('checks GET job identity and schema %#', async extra => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(json({ id: target.actorId })).mockResolvedValueOnce(json(snap('RUNNING', 1, extra))));
  await expect(readRfpJob(target, new AbortController().signal)).rejects.toThrow();
});
it('stops changed actors before reading the job', async () => {
  const f = vi.fn().mockResolvedValue(json({ id: doc })); vi.stubGlobal('fetch', f);
  await expect(readRfpJob(target, new AbortController().signal)).rejects.toMatchObject({ status: 403 }); expect(f).toHaveBeenCalledTimes(1);
});
it('replays already emitted steps from zero without waiting for another event; abort suppresses all later updates', async () => {
  const controller = new AbortController(); const views: RfpWatch[] = [];
  const f = vi.fn(async (url: string) => url.endsWith('/me') ? json({ id: target.actorId }) : url.includes('/events') ? stream([event()]) : json(snap('RUNNING', 1)));
  vi.stubGlobal('fetch', f);
  await watchRfpJob(target, controller.signal, v => { views.push(v); if (v.progress) controller.abort(); });
  expect(f.mock.calls.some(([u]) => u.endsWith('/events?after=0'))).toBe(true);
  expect(views.at(-1)?.progress?.step).toBe('extract'); expect(views.at(-1)?.connection).toBe('connected');
  expect(f).toHaveBeenCalledTimes(3);
});
it('ignores duplicate/older events and confirms terminal with GET, not an event claim', async () => {
  let reads = 0; const views: RfpWatch[] = [];
  vi.stubGlobal('fetch', vi.fn(async (u: string) => u.endsWith('/me') ? json({ id: target.actorId }) : u.includes('/events') ? stream([event(), event(), event(2), event(1), event(3, 'job.completed', { state: 'SUCCEEDED' })]) : json(snap(++reads === 1 ? 'RUNNING' : 'SUCCEEDED', reads === 1 ? 2 : 3))));
  await watchRfpJob(target, new AbortController().signal, v => views.push(v));
  expect(views.filter(v => v.progress && v.connection === 'connected').map(v => v.progress!.sequence)).toEqual([1, 2, 3]);
  expect(views.find(v => v.progress?.type === 'job.completed')?.job?.status).toBe('RUNNING');
  expect(views.at(-1)).toMatchObject({ connection: 'ended', job: { status: 'SUCCEEDED' } }); expect(reads).toBe(2);
});
it('drained 204 confirms terminal through a new GET', async () => {
  let reads = 0; const views: RfpWatch[] = [];
  vi.stubGlobal('fetch', vi.fn(async (u: string) => u.endsWith('/me') ? json({ id: target.actorId }) : u.includes('/events') ? new Response(null, { status: 204 }) : json(snap(++reads === 1 ? 'RUNNING' : 'PARTIAL', 0))));
  await watchRfpJob(target, new AbortController().signal, v => views.push(v));
  expect(views.at(-1)).toMatchObject({ connection: 'ended', job: { status: 'PARTIAL' } });
});
it.each([401, 403, 404])('stops %s without reconnecting', async status => {
  const f = vi.fn(async (u: string) => u.endsWith('/me') ? json({ id: target.actorId }) : u.includes('/events') ? new Response('{}', { status }) : json(snap()));vi.stubGlobal('fetch', f);
  const views: RfpWatch[] = []; await watchRfpJob(target, new AbortController().signal, v => views.push(v));
  expect(views.at(-1)?.connection).toBe('stopped'); expect(f).toHaveBeenCalledTimes(3);
});
it.each(['gap', 'expired'])('recovers %s through snapshot and cursor; terminal still needs GET', async issue => {
  vi.useFakeTimers(); let streams = 0, reads = 0; const urls: string[] = [], views: RfpWatch[] = [];
  vi.stubGlobal('fetch', vi.fn(async (u: string) => { urls.push(u); if (u.endsWith('/me')) return json({ id: target.actorId }); if (u.includes('/events')) return ++streams === 1 ? issue === 'gap' ? stream([event(3)]) : new Response('{}', { status: 410 }) : new Response(null, { status: 204 }); return json(snap(++reads === 3 ? 'SUCCEEDED' : 'RUNNING', 3)); }));
  const run = watchRfpJob(target, new AbortController().signal, v => views.push(v));await vi.runAllTimersAsync(); await run;
  expect(urls.some(u => u.endsWith('/events?after=3'))).toBe(true);expect(views.at(-1)?.connection).toBe('ended');expect(views.some(v => v.connection === 'reconnecting')).toBe(true);
});
it('bounded reconnect failures hand off to polling rather than declare job failure', async () => {
  vi.useFakeTimers();const views: RfpWatch[] = [];
  vi.stubGlobal('fetch', vi.fn(async (u: string) => u.endsWith('/me') ? json({ id: target.actorId }) : u.includes('/events') ? new Response('{}', { status: 503 }) : json(snap())));
  const run = watchRfpJob(target, new AbortController().signal, v => views.push(v));await vi.runAllTimersAsync();await run;
  expect(views.at(-1)).toMatchObject({ connection: 'fallback', job: { status: 'RUNNING' } });
});
it('reconnects from the last received cursor and retains the prior observation while disconnected', async () => {
  vi.useFakeTimers();let streams=0, reads=0;const urls:string[]=[], views:RfpWatch[]=[];
  vi.stubGlobal('fetch',vi.fn(async(u:string)=>{urls.push(u);if(u.endsWith('/me'))return json({id:target.actorId});if(u.includes('/events'))return ++streams===1?stream([event()]):stream([event(2),event(3,'job.completed',{state:'SUCCEEDED'})]);return json(snap(++reads===3?'SUCCEEDED':'RUNNING',reads===1?1:3));}));
  const run=watchRfpJob(target,new AbortController().signal,v=>views.push(v));await vi.runAllTimersAsync();await run;
  expect(urls.some(u=>u.endsWith('/events?after=1'))).toBe(true);
  expect(views.find(v=>v.connection==='reconnecting')?.progress?.sequence).toBe(1);
  expect(views.at(-1)?.job?.status).toBe('SUCCEEDED');
});

it('retains distinct actual stage observations without mutating prior views or replaying duplicates', async () => {
  let reads = 0; const views: RfpWatch[] = [];
  const events = [event(1, 'job.started', {}), event(2, 'job.progress', { step: 'parse' }), event(3), event(3), event(4, 'job.progress', { step: 'extract', counts: { requirements: 8, proposals: 0 } }), event(5, 'job.completed', { state: 'SUCCEEDED' })];
  vi.stubGlobal('fetch', vi.fn(async (u: string) => u.endsWith('/me') ? json({ id: target.actorId }) : u.includes('/events') ? stream(events) : json(snap(++reads === 1 ? 'RUNNING' : 'SUCCEEDED', 5))));
  await watchRfpJob(target, new AbortController().signal, v => views.push(v));
  expect(views.find(v => v.progress?.sequence === 2)?.activity?.map(p => p.step)).toEqual(['parse']);
  expect(views.at(-1)?.activity?.map(p => p.step)).toEqual(['parse', 'extract']);
  expect(views.at(-1)?.activity?.at(-1)?.counts?.requirements).toBe(8);
  expect(views.at(-1)?.startedAt).toBe('2026-11-02T12:00:00Z');
});
it('clears activity and start time when a reconnect loses authorization', async () => {
  vi.useFakeTimers(); let reads = 0; const views: RfpWatch[] = [];
  vi.stubGlobal('fetch', vi.fn(async (u: string) => u.endsWith('/me') ? ++reads === 1 ? json({ id: target.actorId }) : new Response('{}', { status: 403 }) : u.includes('/events') ? stream([event(1, 'job.started', {}), event(2)]) : json(snap())));
  const run = watchRfpJob(target, new AbortController().signal, v => views.push(v));
  await vi.runAllTimersAsync(); await run;
  expect(views.some(v => v.activity?.length === 1)).toBe(true);
  expect(views.at(-1)).toMatchObject({ connection: 'stopped', activity: [] });
  expect(views.at(-1)?.startedAt).toBeUndefined();
});
