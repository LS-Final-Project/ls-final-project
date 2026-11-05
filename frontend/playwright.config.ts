import { defineConfig } from '@playwright/test';

// 브라우저 점검(KAN-154). 개발 서버를 띄우고 응답은 각 시험이 가로채므로 백엔드 없이 돈다.
// 설치된 Chrome을 쓴다. `npm test`에는 포함되지 않고 `npm run test:e2e`로 따로 돈다.
export default defineConfig({
  testDir: 'e2e',
  testMatch: '*.e2e.ts',
  timeout: 30_000,
  retries: 0,
  reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:5173', channel: 'chrome', viewport: { width: 1440, height: 900 } },
  webServer: { command: 'npm run dev', url: 'http://127.0.0.1:5173', reuseExistingServer: true, timeout: 60_000 },
});
