import { expect, test } from '@playwright/test';
import { errorBody, json, openLive } from './helpers';

// 평가 콘솔 점검만 한다(KAN-154, 오새결 댓글 11015). 평가 화면과 코드는 고치지 않는다.
const JOB = '33333333-3333-4333-8333-333333333333';
const RUN = '44444444-4444-4444-8444-444444444444';
const job = { data: { job_id: JOB, run_id: RUN, owner: 'platform', kind: 'evaluation.run', status: 'SUCCEEDED', run_status: 'SUCCEEDED', needs_review: false, version: 1, last_event_sequence: 0 } };
const isJob = (pathname: string) => pathname === `/api/v1/platform/jobs/${JOB}`;

test('평가 콘솔: 올바르지 않은 작업 ID는 조회하지 않고 안내한다', async ({ page }) => {
  const jobCalls: string[] = [];
  await openLive(page, '/evaluations', (route, url) => { if (url.pathname.includes('/platform/jobs/')) jobCalls.push(url.pathname); return false; });
  await expect(page.getByRole('heading', { name: '평가 실행' })).toBeVisible();
  await expect(page.getByText('확인할 작업을 선택하세요')).toBeVisible();
  await page.getByLabel(/작업 ID/).fill('not-a-job-id');
  await expect(page.getByRole('button', { name: '조회' })).toBeDisabled();
  await page.evaluate(() => { history.pushState({}, '', '/evaluations?job=abc'); dispatchEvent(new PopStateEvent('popstate')); });
  await expect(page.getByRole('alert').filter({ hasText: '올바른 작업 ID를 입력해 주세요.' })).toBeVisible();
  expect(jobCalls).toEqual([]);
});

test('평가 콘솔: 평가자가 아니면(403) 권한 안내만 보이고 상태는 보이지 않는다', async ({ page }) => {
  await openLive(page, '/evaluations', (route, url) => {
    if (!isJob(url.pathname)) return false;
    route.fulfill(json(errorBody('FORBIDDEN', '권한 없음'), 403));
    return true;
  });
  await page.evaluate(id => { history.pushState({}, '', `/evaluations?job=${id}`); dispatchEvent(new PopStateEvent('popstate')); }, JOB);
  await expect(page.getByText('지정된 평가자만 조회할 수 있습니다. 평가자 권한을 확인해 주세요.')).toBeVisible();
  await expect(page.locator('.evaluation-status')).toHaveCount(0);
});

// 같은 작업 요청이 개발 모드에서 두 번 나갈 수 있어(효과 재실행) 호출 횟수가 아니라 단계로 응답을 바꾼다.
test('평가 콘솔: 찾지 못한 작업(404)은 안내하고, 통신이 끊기면 연결 안내 뒤 새로고침으로 복구된다', async ({ page }) => {
  let phase: 'missing' | 'down' | 'ok' = 'missing';
  await openLive(page, '/evaluations', (route, url) => {
    if (!isJob(url.pathname)) return false;
    if (phase === 'missing') route.fulfill(json(errorBody('NOT_FOUND', '없음'), 404));
    else if (phase === 'down') route.abort('connectionfailed');
    else route.fulfill(json(job));
    return true;
  });
  await page.evaluate(id => { history.pushState({}, '', `/evaluations?job=${id}`); dispatchEvent(new PopStateEvent('popstate')); }, JOB);
  await expect(page.getByText('조회 가능한 작업을 찾지 못했습니다. 작업 ID와 접근 권한을 확인해 주세요.')).toBeVisible();
  phase = 'down';
  await page.getByRole('button', { name: '새로고침', exact: true }).click();
  // 통신이 끊기면 몇 번 다시 접속을 시도한 뒤 멈춘다.
  await expect(page.getByText('연결 오류는 평가 작업의 실패를 뜻하지 않습니다.')).toBeVisible({ timeout: 30_000 });
  phase = 'ok';
  await page.getByRole('button', { name: '새로고침', exact: true }).click();
  await expect(page.locator('.evaluation-status', { hasText: '실행 완료' })).toBeVisible();
});
