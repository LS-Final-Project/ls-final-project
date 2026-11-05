// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { request, setApiMode } from '../api/client';
import { jsonResponse, renderApp } from '../test/render';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });

const MESSAGE = '44444444-4444-4444-8444-444444444444';
const TICKET = '33333333-3333-4333-8333-333333333333';
const candidate = { message_id: MESSAGE, triage_version: 0, subject: '변압기 누유 문의', from_address: 'plant@customer.invalid', received_at: '2026-11-05T09:00:00Z', has_attachments: false, customer_external_ids: ['CUST-0374'] };

function live() {
  const calls: { url: string; method: string; body?: string }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); const method = init?.method ?? 'GET';
    calls.push({ url, method, body: init?.body as string | undefined });
    if (url.endsWith('/platform/me')) return new Response(JSON.stringify({ data: { id: 'u', display_name: '김상담', roles: ['cs_agent'] } }), { headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'csrf' } });
    if (url.endsWith('/mail/cs-candidates')) return jsonResponse(200, { data: { items: [candidate] } });
    if (url.endsWith(`/messages/${MESSAGE}/content`)) return jsonResponse(200, { data: { message_id: MESSAGE, subject: '변압기 누유 문의', from_address: candidate.from_address, received_at: candidate.received_at, body: '가스켓에서 기름이 샙니다.', truncated: false } });
    if (url.endsWith(`/messages/${MESSAGE}/register-ticket`)) return jsonResponse(201, { data: { message_id: MESSAGE, ticket_id: TICKET, created: true } });
    if (url.endsWith(`/messages/${MESSAGE}/triage`)) return jsonResponse(200, { data: { message_id: MESSAGE, version: 1, latest_version: 1, is_latest: true, mail_type: 'OTHER', needs_review: false, attachments_pending: false, link_suggestion: null, evidence: [], provenance_kind: 'human_input', reason: 'x', created_at: '2026-11-05T09:00:00Z' } });
    return jsonResponse(200, { data: { items: [], next_cursor: null, total: 0 } });
  }));
  setApiMode('live');
  return calls;
}

describe('문의로 보이는 메일 (KAN-237)', () => {
  it('업무 홈에 받은 시각·보낸 사람·고객 ID·제목을 보이고, 본문 확인 후 문의로 등록한다', async () => {
    const calls = live();
    await request('/api/v1/platform/me');
    renderApp('/cs');
    const section = await screen.findByLabelText('문의로 보이는 메일', { selector: 'div' });
    expect(within(section).getByText('문의로 보이는 메일 1건')).toBeTruthy();
    expect(within(section).getByText('plant@customer.invalid')).toBeTruthy();
    expect(within(section).getByText('CUST-0374')).toBeTruthy();
    expect(within(section).getByText('변압기 누유 문의')).toBeTruthy();
    fireEvent.click(within(section).getByRole('button', { name: '확인' }));
    expect(await screen.findByText('가스켓에서 기름이 샙니다.')).toBeTruthy();
    const register = screen.getByRole('button', { name: '문의로 등록' }) as HTMLButtonElement;
    expect(register.disabled).toBe(true); // 제품 코드 전
    expect((screen.getByLabelText('고객 ID') as HTMLInputElement).value).toBe('CUST-0374');
    expect((screen.getByLabelText('본문 언어') as HTMLInputElement).value).toBe('ko');
    fireEvent.change(screen.getByLabelText('제품 코드'), { target: { value: 'TRF-OIL-1000' } });
    fireEvent.click(register);
    await waitFor(() => expect(calls.some(c => c.url.endsWith('/register-ticket'))).toBe(true));
    const sent = calls.find(c => c.url.endsWith('/register-ticket'))!;
    expect(JSON.parse(sent.body!)).toEqual({ customer_external_id: 'CUST-0374', product_code: 'TRF-OIL-1000', body_language: 'ko' });
    expect(calls.some(c => c.method === 'POST' && c.url.endsWith('/triage'))).toBe(false);
  });

  it('문의 아님은 읽은 판본(분류 없으면 0)으로 OTHER 재분류를 남기고 접수하지 않는다', async () => {
    const calls = live();
    await request('/api/v1/platform/me');
    renderApp('/cs');
    fireEvent.click(await screen.findByRole('button', { name: '확인' }));
    fireEvent.click(screen.getByRole('button', { name: '문의 아님' }));
    await waitFor(() => expect(calls.some(c => c.method === 'POST' && c.url.endsWith('/triage'))).toBe(true));
    const sent = calls.find(c => c.method === 'POST' && c.url.endsWith('/triage'))!;
    expect(JSON.parse(sent.body!)).toMatchObject({ version: 0, mail_type: 'OTHER' });
    expect(calls.some(c => c.url.endsWith('/register-ticket'))).toBe(false);
  });
});
