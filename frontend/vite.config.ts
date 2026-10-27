import { loadEnv } from 'vite';
import { defineConfig } from 'vitest/config';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_');
  return {
    server: {
      proxy: {
        '/api/v1/cs': env.VITE_CS_API || 'http://127.0.0.1:8081',
        '/api/v1/tft': env.VITE_TFT_API || 'http://127.0.0.1:8082',
        '/api/v1/platform': env.VITE_PLATFORM_API || 'http://127.0.0.1:8083',
      },
    },
    // 화면 렌더 테스트(src/**/*.test.tsx). 정적 서버 테스트는 server.test.mjs가 node --test로 따로 돈다.
    // jsdom 초기화의 과도한 병렬화로 짧은 화면 대기가 만료되지 않도록 제한한다. 검사 범위/시간은 유지한다.
    test: { environment: 'jsdom', include: ['src/**/*.test.tsx'], maxWorkers: 2 },
  };
});
