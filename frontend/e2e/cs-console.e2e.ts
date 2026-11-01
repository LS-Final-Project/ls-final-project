import { expect, test } from '@playwright/test';
import { REQUEST_ID, TICKET_ID, errorBody, example, json, openLive } from './helpers';

test('문의 조회에서 문의를 골라 문의 처리 화면으로 간다', async ({ page }) => {
  await openLive(page, '/cs/tickets');
  const table = page.getByRole('table', { name: /문의/ }).first();
  await expect(table.getByRole('row')).not.toHaveCount(0);
  await expect(page.locator('.kv-code', { hasText: 'CS0101' })).toBeVisible();
  await page.locator(`a[href="/cs/tickets/${TICKET_ID}"]`).first().click();
  await expect(page).toHaveURL(new RegExp(`/cs/tickets/${TICKET_ID}$`));
  await expect(page.locator('.kv-code', { hasText: 'CS0102' })).toBeVisible();
});

test('개선요청의 근거 문의 링크가 해당 문의 처리 화면으로 이어진다', async ({ page }) => {
  await openLive(page, `/cs/improvements/${REQUEST_ID}`);
  await expect(page.getByRole('heading', { name: '근거 문의' })).toBeVisible();
  const link = page.locator('a[href^="/cs/tickets/"]').first();
  const href = await link.getAttribute('href');
  expect(href).toMatch(/^\/cs\/tickets\/[0-9a-f-]{36}$/);
  await link.click();
  await expect(page).toHaveURL(new RegExp(`${href}$`));
  await expect(page.locator('.kv-code', { hasText: 'CS0102' })).toBeVisible();
});

test('접근이 거절되면(403) 권한 안내만 보이고 다시 시도 단추는 없다', async ({ page }) => {
  await openLive(page, '/cs/tickets', (route, url) => {
    if (url.pathname !== '/api/v1/cs/tickets') return false;
    route.fulfill(json(errorBody('FORBIDDEN', '권한이 없습니다.'), 403));
    return true;
  });
  await expect(page.getByText('권한이 없습니다').first()).toBeVisible();
  await expect(page.getByRole('button', { name: '다시 시도' })).toHaveCount(0);
});

test('통신이 끊기면 연결 안내가 보이고 다시 시도로 복구된다', async ({ page }) => {
  let down = true;
  await openLive(page, '/cs/tickets', (route, url) => {
    if (url.pathname !== '/api/v1/cs/tickets' || !down) return false;
    route.abort('failed');
    return true;
  });
  await expect(page.getByText('업무 서버에 연결할 수 없습니다').first()).toBeVisible();
  down = false;
  await page.getByRole('button', { name: '다시 시도' }).first().click();
  await expect(page.locator(`a[href="/cs/tickets/${TICKET_ID}"]`).first()).toBeVisible();
  await expect(page.getByText('업무 서버에 연결할 수 없습니다')).toHaveCount(0);
});

test('조회 단추를 연속으로 눌러도 진행 중에는 요청이 한 번만 나간다', async ({ page }) => {
  let listCalls = 0;
  await openLive(page, '/cs/tickets', async (route, url) => {
    if (url.pathname !== '/api/v1/cs/tickets') return false;
    listCalls += 1;
    await new Promise(resolve => setTimeout(resolve, 600));
    await route.fulfill(json({ data: { items: [], next_cursor: null } }));
    return true;
  });
  const button = page.getByRole('button', { name: /^조회/ });
  // 처음 목록이 도착해 조회가 끝난 뒤부터 센다.
  await expect(page.getByText(/조회 0건 중 0건/)).toBeVisible();
  await expect(button).toBeEnabled();
  const before = listCalls;
  await button.click();
  await button.click({ force: true });
  await button.click({ force: true });
  await expect(button).toBeEnabled();
  expect(listCalls - before).toBe(1);
});

// 문의 처리의 승인 발송: 확인 체크 전에는 보내지 않고, 승인하면 읽은 판본·사서함·받는 사람·초안 연결을 담아 한 번만 보낸다.
test('문의 처리: AI 초안을 확인·승인해야 발송하고, 플랫폼 응답 없음이면 새로 승인하지 말라고 안내한다', async ({ page }) => {
  const MB = '33333333-3333-4333-8333-333333333333';
  const posts: { url: string; body: Record<string, unknown> }[] = [];
  const detail = example('/api/v1/cs/tickets/22222222-2222-4222-8222-222222222222') as { data: Record<string, unknown> };
  const draft = { options: [{ label: 'INFO_FIRST', sentences: [{ text: 'We need the rating label photo.', kind: 'EVIDENCE', evidence_chunk_ids: ['c1'] }], info_requests: [{ info_code: 'label_photo', question: '정격 라벨 사진' }] }] };
  const run = { id: 'd1', analysis_id: 'a1', created_at: '2026-10-28T00:02:00Z', draft, error_code: null, input_version: 'v', job_id: null, model_version: 'm', outcome: 'VALID', prompt_version: 'p', provenance_kind: 'ai_draft', seq: 1, verification_status: 'VERIFIED', verifications: [] };
  const workflow = (sends: unknown[], version: number) => ({ data: { ticket_id: TICKET_ID, state: 'IN_REVIEW', version, owner_user_id: null, closed_at: null, closed_by: null, customer_sent_at: null, dept_results: [], transfers: [],
    mail_origin: { mailbox_id: MB, message_id: 'm', received_at: '2026-10-28T00:00:00Z', registered_by: 'u', sender_address: 'buyer@example.com' }, customer_sends: sends, customer_replies: [] } });
  let sends: unknown[] = [];
  await openLive(page, `/cs/tickets/${TICKET_ID}`, async (route, url) => {
    const p = url.pathname;
    if (p.endsWith('/auth/mailbox')) { await route.fulfill(json({ data: { items: [{ mailbox_id: MB, state: 'connected', revision: 1, updated_at: '2026-10-28T00:00:00Z', reconnect_required: false, reconnect_reason: null, reconnect_required_at: null }] } })); return true; }
    if (p.endsWith('/workflow/send')) {
      posts.push({ url: p, body: JSON.parse(route.request().postData() ?? '{}') });
      sends = [{ id: 's1', kind: 'INFO_REQUEST', body: 'We need the rating label photo.', body_type: 'text', approved_at: '2026-10-28T00:40:00Z', accepted_at: null, approved_by: '11111111-1111-4111-8111-111111111111', request_status: 'REQUEST_UNKNOWN', request_error: null, send_status: null, send_error_code: null, mailbox_id: MB }];
      await route.fulfill(json(workflow(sends, 6)));
      return true;
    }
    if (p.endsWith('/workflow')) { await route.fulfill(json(workflow(sends, 5))); return true; }
    if (p.endsWith('/analysis')) { await route.fulfill(json({ data: { fields: { urgency_flag: { ai_value: 'NONE', corrected_value: null, effective_value: 'NONE', source: 'ai_draft', corrected: false } }, drafts: [run], latest_valid_draft_id: 'd1', latest_valid_run_id: 'a1', runs: [], corrections: [], latest_run: null, return_subtype_stated: null, version: 0,
      missing_info: { ai: [], effective: [], error: null }, rules: { ai: null, effective: null, ai_error: null, effective_error: null, tier_note: null, first_response_due: { ai: null, corrected: null, effective: null, source: null }, resolution_due: { ai: null, corrected: null, effective: null, source: null } } } })); return true; }
    if (p === `/api/v1/cs/tickets/${TICKET_ID}`) { await route.fulfill(json({ data: { ...detail.data, workflow_state: 'IN_REVIEW' } })); return true; }
    return false;
  });
  await page.getByText(/^기타 \d/).click();
  await page.getByRole('menuitem', { name: /AI 초안 보기/ }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByLabel('보낼 사서함')).toHaveValue(MB);
  await expect(dialog.getByRole('textbox', { name: '받는 사람' })).toHaveValue('buyer@example.com');
  const send = dialog.getByRole('button', { name: '승인하고 보내기' });
  await expect(send).toBeDisabled();
  await dialog.getByLabel(/고객에게 메일이 나가는 것을 승인합니다/).check();
  await expect(send).toBeEnabled();
  await send.click();
  await expect(page.getByRole('alert').filter({ hasText: '새로 승인하지 말고' })).toBeVisible();
  expect(posts).toHaveLength(1);
  expect(posts[0].body).toMatchObject({ version: 5, kind: 'INFO_REQUEST', mailbox_id: MB, to: ['buyer@example.com'], draft_id: 'd1', draft_option: 'INFO_FIRST', requested_info_codes: ['label_photo'] });
  // 결과를 모르는 발송은 대화에서 같은 내용을 다시 요청하게 하고, 새 발송 단추는 막는다.
  await expect(page.getByRole('button', { name: '다시 요청' })).toBeVisible();
  const item = page.getByRole('menuitem', { name: /AI 초안 보기/ });
  if (!(await item.isVisible())) await page.getByText(/^기타 \d/).click();
  await item.click();
  await expect(page.getByRole('dialog').getByText(/결과가 정해지지 않은 발송이 있습니다/)).toBeVisible();
});
