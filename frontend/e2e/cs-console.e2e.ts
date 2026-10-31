import { expect, test } from '@playwright/test';
import { REQUEST_ID, TICKET_ID, errorBody, json, openLive } from './helpers';

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
