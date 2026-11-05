import { expect, test } from '@playwright/test';
import { errorBody, json, openLive } from './helpers';

const intent = (id: string, status: string, over: Record<string, unknown> = {}) => ({
  intent_id: id, business_ref: 'SYNTH-' + id.slice(0, 4), business_version: 1, subject: `합성 발송 ${id.slice(0, 4)}`, status, error_code: null, attempts: 1,
  created_at: '2026-10-29T00:20:00Z', submitted_at: '2026-10-29T00:20:05Z', accepted_at: null, sent_copy_confirmed_at: null, resend_ready: false, ...over,
});
const sent = [intent('1111aaaa-1111-4111-8111-111111111111', 'ACCEPTED'), intent('2222bbbb-2222-4222-8222-222222222222', 'FAILED', { error_code: 'RECIPIENT_REJECTED', submitted_at: null })];
const review = { message_id: '44444444-4444-4444-8444-444444444444', received_at: '2026-10-29T08:41:00Z', created_at: '2026-10-29T08:42:00Z', reason: 'AMBIGUOUS', candidates: [{ owner_service: 'cs', business_ref: '66666666-6666-4666-8666-666666666666', method: 'HEADER' }] };
const mailbox = (over: Record<string, unknown> = {}) => ({ mailbox_id: '99999999-9999-4999-8999-999999999999', state: 'connected', revision: 1, updated_at: '2026-10-29T08:00:00Z', reconnect_required: false, reconnect_reason: null, reconnect_required_at: null, ...over });

test('발송 상태: 상태 필터를 고르면 그 상태만 서버에 요청하고, 보내는 단추는 잠겨 있다', async ({ page }) => {
  const queries: string[] = [];
  await openLive(page, '/mail/sent', (route, url) => {
    if (!url.pathname.endsWith('/mail/send-intents')) return false;
    queries.push(url.search);
    route.fulfill(json({ data: { items: url.searchParams.get('status') === 'FAILED' ? [sent[1]] : sent } }));
    return true;
  });
  await expect(page.locator('.kv-code', { hasText: 'MB0102' })).toBeVisible();
  await expect(page.getByRole('table', { name: '발송 상태' }).getByRole('row')).toHaveCount(3);
  await expect(page.getByRole('button', { name: '다시 보내기' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '보낸 편지함 확인' })).toBeDisabled();
  await page.getByLabel('상태', { exact: true }).selectOption('FAILED');
  await expect(page.getByRole('table', { name: '발송 상태' }).getByRole('row')).toHaveCount(2);
  expect(queries).toContain('?status=FAILED');
});

test('메일함: 연결은 후보를 고른 뒤에만, 업무 아님은 처리 메모를 적은 뒤에만 서버에 보낸다', async ({ page }) => {
  const posts: string[] = [];
  await openLive(page, '/mail/inbox', (route, url) => {
    if (url.pathname.endsWith('/mail/reply-reviews')) { route.fulfill(json({ data: { items: [review] } })); return true; }
    if (url.pathname.endsWith('/dismiss')) {
      posts.push(route.request().postData() ?? '');
      route.fulfill(json({ data: { message_id: review.message_id, decision: 'DISMISSED' } }));
      return true;
    }
    return false;
  });
  await expect(page.locator('.kv-code', { hasText: 'MB0100' })).toBeVisible();
  await expect(page.getByRole('button', { name: '이 문의에 연결' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '업무 아님' })).toBeDisabled();
  await page.getByRole('radio').first().check();
  await expect(page.getByRole('button', { name: '이 문의에 연결' })).toBeEnabled();
  await page.getByLabel('처리 메모').fill('광고 메일');
  await expect(page.getByRole('button', { name: '업무 아님' })).toBeEnabled();
  await page.getByRole('button', { name: '업무 아님' }).click();
  await expect.poll(() => posts.length).toBe(1);
  expect(JSON.parse(posts[0])).toMatchObject({ note: '광고 메일' });
});

test('메일함: 접근이 거절되면(403) 권한 안내만 보인다', async ({ page }) => {
  await openLive(page, '/mail/inbox', (route, url) => {
    if (!url.pathname.endsWith('/mail/reply-reviews')) return false;
    route.fulfill(json(errorBody('FORBIDDEN', '권한이 없습니다.'), 403));
    return true;
  });
  await expect(page.getByText('권한이 없습니다').first()).toBeVisible();
  await expect(page.getByRole('button', { name: '다시 시도' })).toHaveCount(0);
});

test('메일 계정: 끊긴 계정에 다시 연결 단추가 켜지고 통신이 끊겼다 돌아오면 복구된다', async ({ page }) => {
  let down = true;
  await openLive(page, '/mail/account', (route, url) => {
    if (!url.pathname.endsWith('/auth/mailbox')) return false;
    if (down) { route.abort('failed'); return true; }
    route.fulfill(json({ data: { items: [mailbox(), mailbox({ mailbox_id: '88888888-8888-4888-8888-888888888888', reconnect_required: true, reconnect_reason: 'invalid_grant' })] } }));
    return true;
  });
  await expect(page.getByText('업무 서버에 연결할 수 없습니다').first()).toBeVisible();
  down = false;
  await page.getByRole('button', { name: '다시 시도' }).first().click();
  const table = page.getByRole('table', { name: '메일 계정' });
  await expect(table.getByRole('row')).toHaveCount(3);
  await expect(table.getByText('끊김')).toBeVisible();
  await expect(page.getByRole('button', { name: '다시 연결' }).first()).toBeEnabled();
});

test('메일 메뉴: 세 화면이 사이드바 링크로 열린다', async ({ page }) => {
  await openLive(page, '/mail/sent', (route, url) => {
    if (url.pathname.includes('/platform/mail/') || url.pathname.endsWith('/auth/mailbox')) { route.fulfill(json({ data: { items: [] } })); return true; }
    return false;
  });
  const menu = page.getByRole('navigation', { name: '메뉴' });
  await menu.getByRole('link', { name: '메일함' }).click();
  await expect(page.locator('.kv-code', { hasText: 'MB0100' })).toBeVisible();
  await menu.getByRole('link', { name: '메일 계정' }).click();
  await expect(page.locator('.kv-code', { hasText: 'MB0103' })).toBeVisible();
  await menu.getByRole('link', { name: '발송 상태' }).click();
  await expect(page.locator('.kv-code', { hasText: 'MB0102' })).toBeVisible();
});

test('발송 상태: 결과 모름이고 사본 없음이 확인된 발송만 사유를 받아 다시 보낼 수 있다', async ({ page }) => {
  const posts: string[] = [];
  const unknownRow = intent('3333cccc-3333-4333-8333-333333333333', 'UNKNOWN', { error_code: 'SUBMIT_TIMEOUT', resend_ready: true });
  const waiting = intent('4444dddd-4444-4444-8444-444444444444', 'UNKNOWN', { error_code: 'SUBMIT_TIMEOUT', resend_ready: false });
  await openLive(page, '/mail/sent', (route, url) => {
    if (url.pathname.endsWith('/resend')) {
      posts.push(route.request().postData() ?? '');
      route.fulfill(json({ data: { intent_id: unknownRow.intent_id, status: 'QUEUED', task_registered: true } }));
      return true;
    }
    if (url.pathname.endsWith('/mail/send-intents')) { route.fulfill(json({ data: { items: [unknownRow, waiting] } })); return true; }
    return false;
  });
  await expect(page.getByRole('button', { name: '다시 보내기' })).toBeDisabled();
  await page.getByLabel(`${waiting.subject} 선택`).check();
  await expect(page.getByRole('button', { name: '다시 보내기' })).toBeDisabled();
  await page.getByLabel(`${unknownRow.subject} 선택`).check();
  await expect(page.getByRole('button', { name: '다시 보내기' })).toBeEnabled();
  await page.getByRole('button', { name: '다시 보내기' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: '다시 보내기' }).click();
  expect(posts).toHaveLength(0);
  await dialog.getByLabel('사유').fill('사본이 없어 재전송');
  await dialog.getByRole('button', { name: '다시 보내기' }).click();
  await expect(page.getByText('다시 보내기를 등록했습니다.')).toBeVisible();
  expect(posts).toEqual([JSON.stringify({ reason: '사본이 없어 재전송' })]);
});
