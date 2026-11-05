// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { request, setApiMode } from '../api/client';
import { jsonResponse, renderApp } from '../test/render';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });

const MESSAGE = '44444444-4444-4444-8444-444444444444';
const FILE = '55555555-5555-4555-8555-555555555555';
const PROJECT = '33333333-3333-4333-8333-333333333333';
const DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
const candidate = { message_id: MESSAGE, triage_version: 1, subject: '[RFP-91] B-plant switchgear RFP', from_address: 'buyer@customer.invalid', received_at: '2026-11-05T09:00:00Z', basis: '제목: RFP', attachments: [{ id: FILE, name: 'RFP-91_spec.docx', content_type: DOCX, size: 7 }] };

function live() {
  const calls: { url: string; method: string }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); const method = init?.method ?? 'GET';
    calls.push({ url, method });
    if (url.endsWith('/platform/me')) return new Response(JSON.stringify({ data: { id: 'u', roles: ['sales'] } }), { headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': 'csrf' } });
    if (url.endsWith('/rfp-candidates')) return jsonResponse(200, { data: { items: [candidate] } });
    if (url.includes(`/attachments/${FILE}`)) return new Response('PK-docx', { headers: { 'Content-Type': DOCX, 'Content-Disposition': 'attachment' } });
    if (url.endsWith('/tft/projects') && method === 'POST') return jsonResponse(201, { data: { id: PROJECT } });
    if (url.endsWith(`/projects/${PROJECT}/documents`)) return jsonResponse(201, { data: { document_version_id: 'v' } });
    if (url.endsWith(`/messages/${MESSAGE}/rfp-project`)) return jsonResponse(201, { data: { message_id: MESSAGE, project_id: PROJECT, created: true } });
    if (url.endsWith(`/messages/${MESSAGE}/triage`) && method === 'GET') return jsonResponse(200, { data: { message_id: MESSAGE, version: 1, mail_type: 'NEW_RFP', needs_review: false, link_suggestion: 'NEW_PROJECT', evidence: [], provenance: { kind: 'ai_draft' }, created_at: '2026-11-05T09:00:00Z' } });
    if (url.endsWith(`/messages/${MESSAGE}/triage`)) return jsonResponse(200, { data: { message_id: MESSAGE, version: 2, mail_type: 'OTHER', needs_review: false, link_suggestion: null, evidence: [], provenance: { kind: 'human_input' }, created_at: '2026-11-05T09:00:00Z' } });
    return jsonResponse(200, { data: { items: [], next_cursor: null, total: 0 } });
  }));
  setApiMode('live');
  return calls;
}

describe('RFP로 보이는 메일 (KAN-73)', () => {
  it('확인 후 등록하면 프로젝트를 만들고 첨부를 올린 뒤 메일을 연결하고, 분석은 시작하지 않는다', async () => {
    const calls = live();
    await request('/api/v1/platform/me');
    renderApp('/tft');
    fireEvent.click(await screen.findByRole('button', { name: '확인' }));
    const register = screen.getByRole('button', { name: 'RFP로 등록' }) as HTMLButtonElement;
    expect(register.disabled).toBe(true); // 고객 코드 전
    fireEvent.change(screen.getByLabelText('고객 코드'), { target: { value: 'CUST-91' } });
    fireEvent.click(register);
    await waitFor(() => expect(calls.some(c => c.url.endsWith('/rfp-project'))).toBe(true));
    const writes = calls.filter(c => c.method === 'POST').map(c => new URL(c.url, 'http://x').pathname);
    expect(writes).toEqual(['/api/v1/tft/projects', `/api/v1/tft/projects/${PROJECT}/documents`, `/api/v1/platform/mail/messages/${MESSAGE}/rfp-project`]);
    expect(calls.some(c => c.url.includes('/process'))).toBe(false);
  });

  it('RFP 아님은 읽은 판본으로 OTHER 재분류를 남긴다', async () => {
    const calls = live();
    await request('/api/v1/platform/me');
    renderApp('/tft');
    fireEvent.click(await screen.findByRole('button', { name: '확인' }));
    fireEvent.click(screen.getByRole('button', { name: 'RFP 아님' }));
    await waitFor(() => expect(calls.some(c => c.method === 'POST' && c.url.endsWith('/triage'))).toBe(true));
    expect(calls.some(c => c.url.includes('/tft/projects') && c.method === 'POST')).toBe(false);
  });
});
