// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { downloadExport, getDraft, getExportJob, getRewriteJob, sendCommand, UncertainWrite, DOCX_MIME } from './rfp-draft';

const P = '11111111-1111-4111-8111-111111111111';
const JOB = '22222222-2222-4222-8222-222222222222';
const EXPORT = '33333333-3333-4333-8333-333333333333';
afterEach(() => vi.unstubAllGlobals());
const response = (data: unknown) => Response.json({ data });
const command = { actorId: P, kind: 'export' as const, key: 'fixed-command-key', body: { review_version: 4, format: 'DOCX' as const } };
it('세션·CSRF 확인 실패면 예약 POST를 보내지 않는다', async () => {
  const f = vi.fn(async () => new Response('{}', { status: 401 })); vi.stubGlobal('fetch', f);
  await expect(sendCommand(P, command)).rejects.toMatchObject({ status: 401 });
  expect(f).toHaveBeenCalledTimes(1);
});
it('잘못된 export 수락 응답은 불명확 상태로 남겨 동일 키 재확인을 허용한다', async () => {
  const f = vi.fn(async (url: string) => url.endsWith('/me') ? new Response(JSON.stringify({ data: { id: P } }), { headers: { 'X-CSRF-Token': 'csrf' } }) : response({ job_id: JOB, export_id: EXPORT, status_url: 'https://other.invalid/jobs' }));
  vi.stubGlobal('fetch', f);
  await expect(sendCommand(P, command)).rejects.toBeInstanceOf(UncertainWrite);
});
it('다른 프로젝트·종류의 작업을 완료로 소비하지 않는다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => response({ job_id: JOB, project_id: 'another-project', owner: 'tft', kind: 'rfp.export', status: 'SUCCEEDED' })));
  await expect(getExportJob(P, JOB)).rejects.toThrow('대상을 확인하지 못했습니다');
});
it.each([
  ['text/html', 'attachment; filename="a.docx"'], [DOCX_MIME, 'inline'],
])('파일 응답 MIME/attachment를 검증한다: %s %s', async (mime, disposition) => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('not a verified download', { headers: { 'Content-Type': mime, 'Content-Disposition': disposition } })));
  await expect(downloadExport(P, EXPORT)).rejects.toThrow('첨부파일 응답');
});
it('지정 판본은 실제 HTTP query로 조회한다', async () => {
  const f = vi.fn(async () => response({ project_id: P, version: 4 })); vi.stubGlobal('fetch', f);
  await getDraft(P, 4);
  expect(f.mock.calls[0]).toBeDefined();
  expect((f.mock.calls as unknown as [string, RequestInit][])[0][0]).toBe(`/api/v1/tft/projects/${P}/draft?version=4`);
});

it('rewrite 수락 응답은 export_id 없이도 고정 본문·키와 새 CSRF로 검증한다', async () => {
  const f = vi.fn(async (url: string) => url.endsWith('/me')
    ? new Response(JSON.stringify({ data: { id: P } }), { headers: { 'X-CSRF-Token': 'csrf' } })
    : response({ job_id: JOB, status: 'QUEUED', status_url: `/api/v1/platform/jobs/${JOB}` }));
  vi.stubGlobal('fetch', f);
  const result = await sendCommand(P, { actorId: P, kind: 'rewrite', key: 'rewrite-fixed-key', body: { version: 4 } });
  expect(result).toMatchObject({ job_id: JOB });
  expect(f.mock.calls[1][0]).toBe(`/api/v1/tft/projects/${P}/draft/rewrite`);
});

it('rewrite도 현재 actor가 바뀌면 POST를 보내지 않고 다른 종류의 작업을 거절한다', async () => {
  const f = vi.fn(async () => new Response(JSON.stringify({ data: { id: EXPORT } }), { headers: { 'X-CSRF-Token': 'new-csrf' } }));
  vi.stubGlobal('fetch', f);
  await expect(sendCommand(P, { actorId: P, kind: 'rewrite', key: 'actor-key-fixed', body: { version: 4 } })).rejects.toThrow('계정이 변경');
  expect(f).toHaveBeenCalledTimes(1);
  vi.stubGlobal('fetch', vi.fn(async () => response({ job_id: JOB, project_id: P, owner: 'tft', kind: 'rfp.export', status: 'SUCCEEDED' })));
  await expect(getRewriteJob(P, JOB)).rejects.toThrow('대상을 확인하지 못했습니다');
});
