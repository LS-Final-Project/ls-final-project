import { afterEach, expect, test, vi } from 'vitest';
import { cleanup, screen, within } from '@testing-library/react';
import TicketAudit from './TicketAudit';
import { setApiMode } from '../api/client';
import { errorBody, jsonResponse, renderAt } from '../test/render';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); setApiMode('mock'); });
const show = () => { setApiMode('live'); return renderAt('/t', '/t', <TicketAudit ticketId="t1" />); };

test('처리 기록을 순번 내림차순으로 보이고 등록과 정정을 한 줄로 줄여 보인다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: { items: [
    { id: 'a', action: 'create', version: 1, actor_user_id: null, occurred_at: '2026-10-28T00:02:00Z', changes: { channel: { to: 'EMAIL' }, product_code: { to: 'HMI' } } },
    { id: 'b', action: 'update', version: 2, actor_user_id: '22222222-2222-4222-8222-222222222222', occurred_at: '2026-10-28T00:06:00Z', changes: { product_code: { from: 'HMI', to: 'HMI-XGT' } } },
  ] } })));
  show();
  const rows = within(await screen.findByRole('table', { name: '처리 기록' })).getAllByRole('row').slice(1);
  expect(rows[0].textContent).toContain('수정');
  expect(rows[0].textContent).toContain('22222222');
  expect(rows[0].textContent).toContain('product_code HMI → HMI-XGT');
  expect(rows[1].textContent).toContain('시스템');
  expect(rows[1].textContent).toContain('channel EMAIL, product_code HMI');
});

test('긴 값은 줄여 보이고 변경이 비었으면 그렇게 적는다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: { items: [
    { id: 'a', action: 'update', version: 3, actor_user_id: null, occurred_at: '2026-10-28T00:06:00Z', changes: { subject: { from: 'x'.repeat(60), to: null } } },
    { id: 'b', action: 'other', version: 2, actor_user_id: null, occurred_at: '2026-10-28T00:05:00Z', changes: {} },
  ] } })));
  show();
  const rows = within(await screen.findByRole('table', { name: '처리 기록' })).getAllByRole('row').slice(1);
  expect(rows[0].textContent).toContain(`${'x'.repeat(40)}… → 없음`);
  expect(rows[1].textContent).toContain('바뀐 내용 없음');
  expect(rows[1].textContent).toContain('other');
});

test('조회 오류와 예상 밖 본문은 문구로 알리고 다시 시도할 수 있다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(500, errorBody('INTERNAL', '오류'))));
  show();
  expect(await screen.findByText(/처리 기록을 불러오지 못했습니다/)).toBeTruthy();
  expect(screen.getByRole('button', { name: '다시 시도' })).toBeTruthy();
  cleanup();
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: {} })));
  show();
  expect(await screen.findByText('처리 기록을 확인할 수 없습니다.')).toBeTruthy();
});

test('기록이 없으면 안내한다', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(200, { data: { items: [] } })));
  show();
  expect(await screen.findByText('처리 기록이 없습니다.')).toBeTruthy();
});
