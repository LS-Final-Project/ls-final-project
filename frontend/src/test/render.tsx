import type { ReactNode } from 'react';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import App from '../App';

// App 전체를 메모리 라우터로 띄운다. main.tsx와 같은 QueryClient 옵션(retry 없음)으로 오류 분기를 바로 확인한다.
export function renderApp(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 5000 } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><App /></MemoryRouter></QueryClientProvider>);
}

export function renderAt(path: string, pattern: string, element: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><Routes><Route path={pattern} element={element} /></Routes></MemoryRouter></QueryClientProvider>);
}

// 공통 응답 형식(docs/api-contract.md)으로 fetch 응답을 만든다.
export const jsonResponse = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
export const errorBody = (code: string, message: string, requestId = '11111111-1111-4111-8111-111111111199') => ({ error: { code, message, retryable: false }, meta: { request_id: requestId } });
