import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { errorBody, json, openLive, type Handler } from './helpers';

// 제안 지원(RFP) 화면 점검(KAN-154). 문의 화면과 같은 방식으로 응답을 가로채 백엔드 없이 돈다.
type Example = { path: string; method: string; body: { data: Record<string, unknown> } };
const tft: Example[] = JSON.parse(readFileSync(new URL('../src/mocks/generated/tft.json', import.meta.url), 'utf-8'));
const PROJECT_ID = '33333333-3333-4333-8333-333333333333';
const LIST = '/api/v1/tft/projects';
const listBody = tft.find(item => item.path === LIST)!.body;
const projectBody = tft.find(item => item.path === `${LIST}/${PROJECT_ID}`)!.body;
const sales = { data: { id: '00000000-0000-4000-8000-000000000003', display_name: '시연 수주 담당자', dept: '영업', roles: ['sales'] } };
const document = {
  document_id: 'd0000000-0000-4000-8000-000000000001', document_version_id: 'd0000000-0000-4000-8000-0000000000a1', version: 1,
  kind: 'RFP_SPEC', title: '합성 사양서', mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  sha256: 'a'.repeat(64), size: 1024, edit_trace_support: 'SUPPORTED', base_document_id: null, data_origin: 'synthetic',
  derivation_kind: 'PROVIDED', known_at: '2026-10-20T09:00:00Z', uploaded_by: sales.data.id, uploaded_at: '2026-10-20T09:00:00Z',
};

// 세션은 수주 담당자(sales). 목록·상세·문서는 예시로 주고, 시험이 handler로 일부를 바꾼다.
async function openRfp(page: Page, path: string, handler: Handler = () => false) {
  await openLive(page, path, async (route, url) => {
    if (await handler(route, url)) return true;
    const { pathname } = url;
    if (pathname.endsWith('/platform/me')) { await route.fulfill(json(sales, 200, { 'X-CSRF-Token': 'e2e' })); return true; }
    if (pathname === LIST) { await route.fulfill(json(listBody)); return true; }
    if (pathname === `${LIST}/${PROJECT_ID}`) { await route.fulfill(json(projectBody)); return true; }
    if (pathname === `${LIST}/${PROJECT_ID}/documents`) { await route.fulfill(json({ data: { items: [document] } })); return true; }
    return false;
  });
}

test('제안 지원: 프로젝트 목록에서 프로젝트를 골라 상세로 간다', async ({ page }) => {
  await openRfp(page, '/tft/projects');
  const link = page.locator(`a[href$="/tft/projects/${PROJECT_ID}"]`).first();
  await expect(link).toBeVisible();
  await link.click();
  await expect(page).toHaveURL(new RegExp(`/tft/projects/${PROJECT_ID}$`));
  await expect(page.getByText(String((projectBody.data as { name: string }).name)).first()).toBeVisible();
});

test('제안 지원: 접근이 거절되면(403) 권한 안내만 보이고 다시 시도 단추는 없다', async ({ page }) => {
  await openRfp(page, '/tft/projects', async (route, url) => {
    if (url.pathname !== LIST) return false;
    await route.fulfill(json(errorBody('FORBIDDEN', 'Access denied.'), 403));
    return true;
  });
  await expect(page.getByText('권한이 없습니다').first()).toBeVisible();
  await expect(page.getByRole('button', { name: '다시 시도' })).toHaveCount(0);
});

test('제안 지원: 통신이 끊기면 연결 안내가 보이고 다시 시도로 복구된다', async ({ page }) => {
  let down = true;
  await openRfp(page, '/tft/projects', async (route, url) => {
    if (url.pathname !== LIST || !down) return false;
    await route.abort('failed');
    return true;
  });
  await expect(page.getByText('업무 서버에 연결할 수 없습니다').first()).toBeVisible();
  down = false;
  await page.getByRole('button', { name: '다시 시도' }).first().click();
  await expect(page.locator(`a[href$="/tft/projects/${PROJECT_ID}"]`).first()).toBeVisible();
  await expect(page.getByText('업무 서버에 연결할 수 없습니다')).toHaveCount(0);
});

test('제안 지원: 처리 실행을 연속으로 눌러도 처리 요청은 한 번만 나간다', async ({ page }) => {
  const posts: string[] = [];
  await openRfp(page, `/tft/projects/${PROJECT_ID}`, async (route, url) => {
    if (route.request().method() !== 'POST' || !url.pathname.endsWith('/process')) return false;
    posts.push(url.pathname);
    await new Promise(resolve => setTimeout(resolve, 600));
    await route.fulfill(json({ data: { job_id: 'e0000000-0000-4000-8000-000000000001', status: 'QUEUED' } }, 202));
    return true;
  });
  await page.getByRole('button', { name: '처리 실행' }).first().click();
  const run = page.getByRole('button', { name: '처리 실행' }).nth(1);
  const reason = page.getByLabel('재실행 사유');
  if (await reason.count()) await reason.fill('합성 시험 재실행');
  await expect(run).toBeEnabled();
  await run.click();
  await run.click({ force: true }).catch(() => undefined);
  await run.click({ force: true }).catch(() => undefined);
  // 첫 요청이 응답(0.6초)을 기다리는 동안의 연속 클릭까지 지나간 뒤 센다.
  await expect.poll(() => posts.length).toBe(1);
  await page.waitForTimeout(1500);
  expect(posts).toHaveLength(1);
});
