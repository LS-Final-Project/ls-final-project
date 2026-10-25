import csExamples from '../mocks/generated/cs.json';
import tftExamples from '../mocks/generated/tft.json';

export type ApiMode = 'mock' | 'live';
let mode: ApiMode = import.meta.env.DEV ? 'mock' : 'live';
export const getApiMode = () => mode;
export function setApiMode(value: ApiMode) {
  if (!import.meta.env.DEV && value === 'mock') throw new Error('모의 응답은 로컬 개발에서만 사용할 수 있습니다.');
  mode = value;
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public requestId?: string, public code?: string) { super(message); }
}

// mockFallback: 모의 응답 모드에서 생성된 예시가 없는 경로가 돌려줄 값. 실제 API 모드와 production 빌드에서는 쓰이지 않는다.
export type RequestOptions = RequestInit & { mockFallback?: () => unknown };

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { mockFallback, ...init } = options;
  const method = (init.method || 'GET').toUpperCase();
  if (import.meta.env.DEV && mode === 'mock') {
    const example = [...csExamples, ...tftExamples].find(item => item.path === path && item.method === method);
    if (example) return structuredClone(example.body) as T;
    if (mockFallback) return mockFallback() as T;
  }
  const response = await fetch(path, { ...init, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', ...init.headers } });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.error?.message || `HTTP ${response.status}`, body.meta?.request_id, body.error?.code);
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>;
}
