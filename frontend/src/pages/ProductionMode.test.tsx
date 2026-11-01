// @vitest-environment jsdom
import { afterEach, describe, expect, test, vi } from 'vitest';
import type { ReactNode } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { jsonResponse } from '../test/render';

// 운영 빌드(DEV=false)를 흉내 낸다. 업무 UI 설정과 API 모드는 모듈을 읽을 때 정해지므로 환경을 바꾼 뒤 모듈을 새로 읽는다.
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); vi.resetModules(); });
const ID = '22222222-2222-4222-8222-222222222222';
type Call = { url: string; method: string };

function production(businessUi = true) {
  vi.stubEnv('DEV', false);
  if (businessUi) vi.stubEnv('VITE_BUSINESS_UI_ENABLED', 'true');
  vi.resetModules();
  const calls: Call[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const path = String(url);
    calls.push({ url: path, method: init?.method ?? 'GET' });
    if (path.endsWith('/platform/me')) return new Response(JSON.stringify({ data: { id: 'u1', display_name: '담당', roles: ['cs_agent', 'dept_staff', 'manager'], dept: 'TS_LV' } }), { status: 200, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'synthetic-csrf' } });
    return jsonResponse(200, { data: { items: [], next_cursor: null } });
  }));
  return calls;
}
function show(path: string, pattern: string, element: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><Routes><Route path={pattern} element={element} /></Routes></MemoryRouter></QueryClientProvider>);
}
const PREPARING = /준비 중입니다|운영 인증 중계와 권한 검증|운영 연결을 준비 중/;

describe('운영 빌드에서 C/S·메일 화면을 연다', () => {
  test.each([
    ['발송 상태', '/mail/sent', 'MailSent', '/api/v1/platform/mail/send-intents'],
    ['받은 메일 검토', '/mail/inbox', 'MailInbox', '/api/v1/platform/mail/reply-reviews'],
    ['메일 계정', '/mail/account', 'MailAccount', '/api/v1/platform/auth/mailbox'],
    ['받은 요청', '/dept/inbox', 'DeptInbox', '/api/v1/cs/tickets'],
    ['운영 현황', '/cs/admin', 'CsAdmin', '/api/v1/cs/stats/'],
    ['문의 조회', '/cs/tickets', 'Tickets', '/api/v1/cs/tickets'],
  ])('%s 화면은 준비 중 대신 실제 API를 읽는다', async (_, path, page, api) => {
    const calls = production();
    const { default: Page } = await import(`./${page}.tsx`);
    show(path, path, <Page />);
    await waitFor(() => expect(calls.some(call => call.url.startsWith(api))).toBe(true));
    expect(screen.queryByText(PREPARING)).toBeNull();
  });

  test('고객 이력·검토 답변 화면도 준비 중 안내를 보이지 않는다', async () => {
    const calls = production();
    const { default: CsCustomers } = await import('./CsCustomers.tsx');
    show('/cs/customers', '/cs/customers', <CsCustomers />);
    await waitFor(() => expect(calls.some(call => call.url.endsWith('/platform/me'))).toBe(true));
    await waitFor(() => expect(screen.getByRole('button', { name: /조회/ })).toBeTruthy());
    expect(screen.queryByText(PREPARING)).toBeNull();
    cleanup();
    const { default: DeptResult } = await import('./DeptResult.tsx');
    show(`/dept/result/${ID}`, '/dept/result/:ticketId', <DeptResult />);
    await waitFor(() => expect(calls.some(call => call.url.startsWith(`/api/v1/cs/tickets/${ID}`))).toBe(true));
    expect(screen.queryByText(PREPARING)).toBeNull();
  });

  test('문의 처리 화면은 실제 API로 쓰기를 열고 장비 조회도 연다', async () => {
    const calls = production();
    vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const path = String(url);
      calls.push({ url: path, method: init?.method ?? 'GET' });
      if (path.endsWith('/platform/me')) return new Response(JSON.stringify({ data: { id: 'u1', roles: ['cs_agent'] } }), { status: 200, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'synthetic-csrf' } });
      if (path.endsWith('/workflow') || path.endsWith('/start-review')) return jsonResponse(200, { data: { ticket_id: ID, state: 'RECEIVED', version: 2, owner_user_id: null, closed_at: null, closed_by: null, customer_sent_at: null, dept_results: [], transfers: [] } });
      if (path.endsWith(`/tickets/${ID}`)) return jsonResponse(200, { data: { id: ID, external_id: 'T-1', subject: '합성 문의', body: '합성', body_language: 'en', channel: 'EMAIL', customer_external_id: 'C-1', product_code: 'P-1', received_at: '2026-10-28T00:00:00Z', known_at: '2026-10-28T00:00:00Z', workflow_state: 'RECEIVED', data_origin: 'synthetic', derivation_kind: 'synthetic', version: 1, resolution_case: null, error_code_reported: null, fw_version_reported: null } });
      return jsonResponse(404, { error: { code: 'NOT_FOUND', message: '없음' } });
    }));
    const { default: TicketDetail } = await import('./TicketDetail.tsx');
    show(`/cs/tickets/${ID}`, '/cs/tickets/:ticketId', <TicketDetail />);
    await screen.findByText('T-1');
    expect(screen.queryByText(PREPARING)).toBeNull();
    fireEvent.click(screen.getByText(/^기타 \d/));
    const start = () => screen.getByRole('menuitem', { name: /^검토 시작/ }) as HTMLButtonElement;
    await waitFor(() => expect(start().disabled).toBe(false));
    fireEvent.click(start());
    await screen.findByText('검토를 시작했습니다.');
    expect(calls.filter(call => call.method === 'POST').map(call => call.url)).toEqual([`/api/v1/cs/tickets/${ID}/workflow/start-review`]);
  });

  test('업무 UI 설정이 꺼진 운영 빌드는 화면을 열지 않고 업무 API를 부르지 않는다', async () => {
    const calls = production(false);
    const { default: MailSent } = await import('./MailSent.tsx');
    show('/mail/sent', '/mail/sent', <MailSent />);
    expect(await screen.findByText(/준비 중입니다/)).toBeTruthy();
    expect(calls).toHaveLength(0);
  });
});

describe('운영 빌드의 쓰기 호출', () => {
  test('문의·메일 쓰기는 운영 빌드의 실제 API에서 서버로 나간다', async () => {
    const calls = production();
    const client = await import('../api/client');
    expect(client.getApiMode()).toBe('live');
    await client.request('/api/v1/platform/me');
    const mail = await import('../api/mail');
    const workflow = await import('../api/cs-workflow');
    const signal = new AbortController().signal;
    // 응답 모양 검사는 이 시험의 관심이 아니다. 요청이 나갔는지만 본다.
    await Promise.allSettled([
      workflow.startReview(ID, 1, signal),
      mail.reclassifyMail(ID, { version: 1 } as never, signal),
      mail.checkSentCopy(ID, signal),
      mail.linkReply(ID, { owner_service: 'cs', business_ref: ID } as never, signal),
      mail.dismissReply(ID, '업무 아님', signal),
      mail.registerTicket(ID, { customer_external_id: 'C-1' } as never, signal),
    ]);
    expect(calls.filter(call => call.method === 'POST').map(call => call.url).sort()).toEqual([
      `/api/v1/cs/tickets/${ID}/workflow/start-review`,
      `/api/v1/platform/mail/messages/${ID}/register-ticket`,
      `/api/v1/platform/mail/messages/${ID}/triage`,
      `/api/v1/platform/mail/reply-reviews/${ID}/dismiss`,
      `/api/v1/platform/mail/reply-reviews/${ID}/link`,
      `/api/v1/platform/mail/send-intents/${ID}/check-sent-copy`,
    ]);
  });

  test('모의 응답 모드에서는 여전히 쓰기를 서버로 보내지 않는다', async () => {
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    const client = await import('../api/client');
    client.setApiMode('mock');
    const mail = await import('../api/mail');
    const workflow = await import('../api/cs-workflow');
    const signal = new AbortController().signal;
    await expect(workflow.startReview(ID, 1, signal)).rejects.toMatchObject({ status: 403 });
    await expect(mail.resendIntent(ID, '사유', signal)).rejects.toMatchObject({ status: 403 });
    await expect(mail.registerTicket(ID, { customer_external_id: 'C-1' } as never, signal)).rejects.toMatchObject({ status: 403 });
    await expect(mail.reclassifyMail(ID, { version: 1 } as never, signal)).rejects.toMatchObject({ status: 403 });
    expect(fetcher).not.toHaveBeenCalled();
  });
});
